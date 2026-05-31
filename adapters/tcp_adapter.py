"""
Generic TCP Forward Adapter for S2Pass.

Listens on a local TCP port and, for each incoming client connection,
opens a TCP connection to the configured target and bidirectionally
forwards the byte stream.  The adapter manages an asyncio event loop
on a background thread so that the public interface stays synchronous
(start / stop), consistent with AdapterBase.

Does NOT construct or parse S2Pass protocol JSON.
Does NOT import server.py or network_core.py.
"""

import asyncio
import logging
import sys
import threading
from typing import Dict, Any, Optional, Set, Tuple

from adapters.base import AdapterBase
from adapters.profile import GameProfile

logger = logging.getLogger(__name__)


class GenericTcpForwardAdapter(AdapterBase):
    """
    Bidirectional TCP forwarder.

    Config (via GameProfile fields or constructor overrides):
        listen_host   – local address to listen on   (default "127.0.0.1")
        listen_port   – local port; 0 = ephemeral    (required)
        target_host   – remote address to connect to  (required)
        target_port   – remote port                   (required)
        buffer_size   – read chunk size in bytes       (default 65536)
        connection_timeout – seconds to wait for target connect (default 10)
    """

    def __init__(
        self,
        profile: GameProfile,
        *,
        listen_host: Optional[str] = None,
        listen_port: Optional[int] = None,
        target_host: Optional[str] = None,
        target_port: Optional[int] = None,
        buffer_size: int = 65536,
        connection_timeout: float = 10.0,
    ):
        super().__init__(profile)

        # Resolve config: explicit kwargs > profile fields > defaults
        self._listen_host: str = (
            listen_host
            or self.profile.local_bind_host
            or "127.0.0.1"
        )
        self._listen_port: int = (
            listen_port
            if listen_port is not None
            else (self.profile.local_bind_port if self.profile.local_bind_port is not None else 0)
        )
        self._target_host: str = target_host or self.profile.remote_target_host or ""
        self._target_port: int = (
            target_port
            if target_port is not None
            else (self.profile.remote_target_port if self.profile.remote_target_port is not None else 0)
        )
        self._buffer_size: int = buffer_size
        self._connection_timeout: float = connection_timeout

        # Validate required fields
        if not self._target_host:
            raise ValueError("target_host is required for GenericTcpForwardAdapter")
        if not self._target_port:
            raise ValueError("target_port is required for GenericTcpForwardAdapter")

        # Runtime state
        self._is_running: bool = False
        self._actual_port: Optional[int] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._server: Optional[asyncio.AbstractServer] = None
        self._tasks: Set[asyncio.Task] = set()
        self._ready_event = threading.Event()
        self._start_error: Optional[Exception] = None

        # Stats
        self.active_connections: int = 0
        self.total_connections: int = 0
        self.bytes_forwarded_to_target: int = 0
        self.bytes_forwarded_to_client: int = 0
        self.last_error: Optional[str] = None

    # ------------------------------------------------------------------
    # AdapterBase interface
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the TCP listener on the background event-loop thread."""
        if self._is_running:
            return  # idempotent

        self._ready_event.clear()
        self._start_error = None

        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

        # Wait for the server to be ready (or for an error)
        self._ready_event.wait(timeout=10.0)

        if self._start_error is not None:
            # Thread will exit on its own; join it.
            self._thread.join(timeout=5.0)
            self._thread = None
            raise self._start_error

        if not self._is_running:
            self._thread.join(timeout=5.0)
            self._thread = None
            raise RuntimeError("TCP adapter failed to start (timeout)")

    def stop(self) -> None:
        """Stop accepting connections, close all sockets, shut down the loop."""
        if not self._is_running:
            return  # idempotent

        loop = self._loop
        if loop is not None and loop.is_running():
            future = asyncio.run_coroutine_threadsafe(self._async_stop(), loop)
            try:
                future.result(timeout=10.0)
            except Exception as e:
                self.last_error = f"{e.__class__.__name__}: {e}"
                logger.warning("Error stopping TCP adapter: %s", self.last_error)
            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:
                pass

        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

        self._is_running = False
        self._actual_port = None
        self._loop = None
        self._server = None

    def is_running(self) -> bool:
        return self._is_running

    def get_pid(self) -> Optional[int]:
        """TCP adapter does not spawn a subprocess."""
        return None

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def get_local_addr(self) -> Tuple[Optional[str], Optional[int]]:
        """Return the (host, port) actually bound by the listener."""
        if self._is_running:
            return self._listen_host, self._actual_port
        return None, None

    def get_stats(self) -> Dict[str, Any]:
        return {
            "running": self._is_running,
            "listen_host": self._listen_host if self._is_running else None,
            "listen_port": self._actual_port if self._is_running else None,
            "target_host": self._target_host,
            "target_port": self._target_port,
            "active_connections": self.active_connections,
            "total_connections": self.total_connections,
            "bytes_forwarded_to_target": self.bytes_forwarded_to_target,
            "bytes_forwarded_to_client": self.bytes_forwarded_to_client,
            "last_error": self.last_error,
        }

    # ------------------------------------------------------------------
    # Internal: event-loop management
    # ------------------------------------------------------------------

    def _run_loop(self) -> None:
        """Entry point for the background thread.  Creates an event loop,
        starts the TCP server, and runs until stopped."""
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._async_start())
            if self._is_running:
                loop.run_forever()
        except Exception as exc:
            self._start_error = exc
            self._ready_event.set()
        finally:
            # Final cleanup of any remaining tasks
            try:
                pending = asyncio.all_tasks(loop)
                for t in pending:
                    t.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
            except Exception:
                pass
            loop.close()

    async def _async_start(self) -> None:
        """Create and start the TCP server (runs inside the loop thread)."""
        try:
            self._server = await asyncio.start_server(
                self._handle_client,
                host=self._listen_host,
                port=self._listen_port,
                reuse_address=True,
            )
        except Exception as exc:
            self._start_error = RuntimeError(
                f"Failed to bind TCP socket to {self._listen_host}:{self._listen_port}: {exc}"
            )
            self._ready_event.set()
            return

        # Determine actual bound port
        sockets = self._server.sockets
        if sockets:
            self._actual_port = sockets[0].getsockname()[1]
        else:
            self._actual_port = self._listen_port

        self._is_running = True
        logger.info(
            "TCP adapter started on %s:%s -> %s:%s",
            self._listen_host, self._actual_port,
            self._target_host, self._target_port,
        )
        self._ready_event.set()

    async def _async_stop(self) -> None:
        """Graceful shutdown sequence."""
        # 1. Stop accepting new connections
        if self._server is not None:
            self._server.close()

        # Cancel all active _handle_client tasks first so they clean up
        # their c2t/t2c copy tasks and close writer pairs, releasing
        # wait_closed() below.
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        if self._server is not None:
            try:
                await self._server.wait_closed()
            except Exception:
                pass
            self._server = None

        logger.info("TCP adapter stopped")



    # ------------------------------------------------------------------
    # Internal: per-connection handling
    # ------------------------------------------------------------------

    async def _handle_client(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        """Called for each accepted client.  Creates the forwarding task."""
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)

        peer = client_writer.get_extra_info("peername")
        logger.info("Client connected from %s", peer)
        self.total_connections += 1
        self.active_connections += 1

        target_reader: Optional[asyncio.StreamReader] = None
        target_writer: Optional[asyncio.StreamWriter] = None
        c2t: Optional[asyncio.Task] = None
        t2c: Optional[asyncio.Task] = None
        try:
            # Connect to target
            try:
                target_reader, target_writer = await asyncio.wait_for(
                    asyncio.open_connection(self._target_host, self._target_port),
                    timeout=self._connection_timeout,
                )
            except Exception as exc:
                err = f"Target connect failed ({self._target_host}:{self._target_port}): {exc}"
                logger.warning(err)
                self.last_error = err
                return

            logger.info(
                "Target connected %s:%s for client %s",
                self._target_host, self._target_port, peer,
            )

            c2t = asyncio.create_task(
                self._copy_stream(client_reader, target_writer, "client->target")
            )
            t2c = asyncio.create_task(
                self._copy_stream(target_reader, client_writer, "target->client")
            )

            done, pending = await asyncio.wait(
                {c2t, t2c},
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Case 1: client->target finished normally.
            # This usually means the client half-closed its write side but may
            # still be waiting for a response.  Do NOT cancel target->client.
            if c2t in done and not c2t.cancelled() and c2t.exception() is None:
                await asyncio.gather(t2c, return_exceptions=True)

            # Case 2: target->client finished first, or c2t failed / was
            # cancelled.  Cancel the remaining direction.
            else:
                for t in pending:
                    t.cancel()
                await asyncio.gather(c2t, t2c, return_exceptions=True)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.last_error = str(exc)
            logger.warning("Connection error for %s: %s", peer, exc)
        finally:
            # Cancel any still-pending copy tasks for stop() / cleanup safety.
            for copy_task in (c2t, t2c):
                if copy_task is not None and not copy_task.done():
                    copy_task.cancel()
            # Close both writers
            for writer in (client_writer, target_writer):
                if writer is not None:
                    try:
                        writer.close()
                    except Exception:
                        pass
            self.active_connections -= 1
            logger.info("Connection closed for %s", peer)
            if task is not None:
                self._tasks.discard(task)

    async def _copy_stream(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        direction: str,
    ) -> None:
        """Copy bytes from *reader* to *writer* until EOF or error."""
        try:
            while True:
                data = await reader.read(self._buffer_size)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
                if direction == "client->target":
                    self.bytes_forwarded_to_target += len(data)
                else:
                    self.bytes_forwarded_to_client += len(data)
            if writer.can_write_eof():
                writer.write_eof()
        except (ConnectionError, asyncio.CancelledError, OSError):
            pass
