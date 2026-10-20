"""开机自启（HKCU Run，默认关闭）。"""

from __future__ import annotations

from .registry import (
    VALUE_NAME,
    AutostartBackend,
    AutostartManager,
    InMemoryAutostartBackend,
    RegistryAutostartBackend,
    build_launch_command,
)

__all__ = [
    "VALUE_NAME",
    "AutostartBackend",
    "AutostartManager",
    "InMemoryAutostartBackend",
    "RegistryAutostartBackend",
    "build_launch_command",
]
