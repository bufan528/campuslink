"""网络状态探测与自动重连退避。"""

from __future__ import annotations

from .probe import ConnectivityProbe, ProbeResult
from .reconnect import BackoffController, BackoffPolicy, ReconnectState
from .wifi import WifiInspector, WifiStatus, parse_netsh_output

__all__ = [
    "BackoffController",
    "BackoffPolicy",
    "ConnectivityProbe",
    "ProbeResult",
    "ReconnectState",
    "WifiInspector",
    "WifiStatus",
    "parse_netsh_output",
]
