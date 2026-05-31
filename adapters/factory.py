"""
Minimal adapter factory.

Maps GameProfile.adapter_type to the matching AdapterBase subclass.
Backend code should use this instead of importing adapter classes directly.
"""
from adapters.base import AdapterBase
from adapters.launch_adapter import LaunchAdapter
from adapters.profile import GameProfile
from adapters.tcp_adapter import GenericTcpForwardAdapter
from adapters.udp_adapter import GenericUdpForwardAdapter

_ADAPTER_TYPE_MAP = {
    "launch_only": LaunchAdapter,
    "generic_udp_forward": GenericUdpForwardAdapter,
    "tcp_forward": GenericTcpForwardAdapter,
}


def create_adapter(profile: GameProfile, **overrides) -> AdapterBase:
    """Return the adapter instance for *profile*.

    Extra keyword arguments are forwarded to the adapter constructor.
    """
    adapter_type = getattr(profile, "adapter_type", None) or "launch_only"
    cls = _ADAPTER_TYPE_MAP.get(adapter_type)
    if cls is None:
        raise ValueError(
            f"Unknown adapter_type: {adapter_type!r}. "
            f"Supported: {sorted(_ADAPTER_TYPE_MAP)}"
        )
    if cls is LaunchAdapter:
        return cls(profile)
    return cls(profile, **overrides)
