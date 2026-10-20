"""无线网络状态探测。

只读调用 netsh，不修改任何网络配置。
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

CREATE_NO_WINDOW: Final[int] = 0x08000000
DEFAULT_TIMEOUT_S: Final[float] = 10.0


@dataclass(frozen=True, slots=True)
class WifiStatus:
    """无线适配器的只读快照。ssids 保存所有接口的 SSID（可能多个）。"""

    readable: bool
    ssid: str | None
    raw_state: str | None
    ssids: tuple[str, ...] = ()

    def matches(self, candidates: Sequence[str]) -> bool:
        """任一接口的 SSID 命中即可（Wi-Fi Direct/第二网卡不会被漏判）。"""
        targets = [self.ssid] if self.ssid else []
        targets.extend(self.ssids)
        for target in targets:
            if not target:
                continue
            normalized = target.strip().lower()
            if any(normalized == str(c).strip().lower() for c in candidates):
                return True
        return False


def _default_runner() -> tuple[int, str]:
    creationflags = CREATE_NO_WINDOW if sys.platform == "win32" else 0
    completed = subprocess.run(
        ["netsh", "wlan", "show", "interfaces"],
        capture_output=True,
        timeout=DEFAULT_TIMEOUT_S,
        creationflags=creationflags,
        check=False,
    )
    raw = completed.stdout or b""
    for encoding in ("utf-8", "gb18030"):
        try:
            return completed.returncode, raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return completed.returncode, raw.decode("utf-8", errors="replace")


def parse_netsh_output(text: str) -> WifiStatus:
    """从 netsh 输出里取 SSID。注意要跳过 BSSID 行，并保留多接口的 SSID。"""
    if not text or "SSID" not in text:
        return WifiStatus(readable=False, ssid=None, raw_state=None)
    ssids: list[str] = []
    state: str | None = None
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key_l = key.strip().lower()
        value = value.strip()
        if key_l == "ssid" and value and value not in ssids:
            ssids.append(value)
        elif key_l in {"state", "\u72b6\u6001"} and value:
            state = value
    return WifiStatus(
        readable=True,
        ssid=ssids[0] if ssids else None,
        raw_state=state,
        ssids=tuple(ssids),
    )


class WifiInspector:
    """可注入 runner，便于用假输出做单元测试。"""

    def __init__(self, runner: Callable[[], tuple[int, str]] | None = None) -> None:
        self._runner = runner or _default_runner

    def current_status(self) -> WifiStatus:
        try:
            _code, text = self._runner()
        except Exception:
            return WifiStatus(readable=False, ssid=None, raw_state=None)
        return parse_netsh_output(text)
