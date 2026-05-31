from abc import ABC, abstractmethod
from typing import Optional
from adapters.profile import GameProfile

class AdapterBase(ABC):
    def __init__(self, profile: GameProfile):
        self.profile = profile

    @abstractmethod
    def start(self) -> None:
        """Start the adapter and/or the game process."""
        pass

    @abstractmethod
    def stop(self) -> None:
        """Stop the adapter and/or terminate the launched process."""
        pass

    @abstractmethod
    def is_running(self) -> bool:
        """Check if the process started by this adapter is still running."""
        pass

    @abstractmethod
    def get_pid(self) -> Optional[int]:
        """Get the process ID of the launched process, or None if not running."""
        pass
