"""把三类客观事实组合成一个可展示的状态。

任务书要求严格区分三件事，本模块用 NetworkSnapshot 分开记录：
1. 是否连上了目标校园网（Wi-Fi SSID 命中 / 认证主机可达）；
2. 校园网门户是否已认证（PortalState）；
3. 互联网是否真的可用（ConnectivityProbe）。
随后才推导出一个 UI 状态，避免把"连着 Wi-Fi"当成"能上网"。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from ..auth.models import PortalState


class LinkState(StrEnum):
    """界面状态。"""

    UNKNOWN = "unknown"
    DISCONNECTED = "disconnected"
    CHECKING = "checking"
    AUTH_REQUIRED = "auth_required"
    SIGNING_IN = "signing_in"
    AUTHENTICATED = "authenticated"
    INTERNET_UNAVAILABLE = "internet_unavailable"
    AUTH_FAILED = "auth_failed"


STATE_LABELS: Final[dict[LinkState, str]] = {
    LinkState.UNKNOWN: "未检测",
    LinkState.DISCONNECTED: "未连接校园网",
    LinkState.CHECKING: "检测中",
    LinkState.AUTH_REQUIRED: "需要认证",
    LinkState.SIGNING_IN: "登录中",
    LinkState.AUTHENTICATED: "认证成功",
    LinkState.INTERNET_UNAVAILABLE: "互联网不可用",
    LinkState.AUTH_FAILED: "认证失败",
}


@dataclass(frozen=True, slots=True)
class NetworkSnapshot:
    """一次网络状态快照。不含任何凭据。"""

    wifi_connected: bool
    wifi_ssid: str | None
    portal_state: PortalState
    internet_available: bool
    portal_interception: bool
    checked_at: float
    detail: str = ""

    def describe(self) -> str:
        parts = [
            "wifi=" + ("on" if self.wifi_connected else "off"),
            "ssid=" + (self.wifi_ssid or "-"),
            "portal=" + self.portal_state.value,
            "internet=" + ("yes" if self.internet_available else "no"),
        ]
        if self.detail:
            parts.append("detail=" + self.detail)
        return " ".join(parts)


def derive_link_state(snapshot: NetworkSnapshot) -> LinkState:
    """由快照推导界面状态。

    优先看"互联网是否可用"，再看门户怎么说，最后才看 Wi-Fi——
    这样不会因为没连 Wi-Fi（比如有线）就误报未连接。
    """
    portal = snapshot.portal_state
    if snapshot.internet_available:
        return LinkState.AUTHENTICATED
    if portal is PortalState.LOGIN_REQUIRED:
        return LinkState.AUTH_REQUIRED
    if portal is PortalState.UNREACHABLE:
        return LinkState.DISCONNECTED if not snapshot.wifi_connected else LinkState.INTERNET_UNAVAILABLE
    if portal is PortalState.ONLINE:
        return LinkState.INTERNET_UNAVAILABLE
    return LinkState.UNKNOWN


def is_campus_wifi(ssid: str | None, candidates: Sequence[str]) -> bool:
    if not ssid:
        return False
    target = ssid.strip().lower()
    return any(target == str(item).strip().lower() for item in candidates)
