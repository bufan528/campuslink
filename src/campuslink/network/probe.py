"""互联网连通性探测。

用 captive-portal 探测端点区分三件事：
"连上了 Wi-Fi" / "校园网已认证" / "互联网真的可用"。
探测目标全部是校外站点，不向认证主机发任何凭据。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Protocol

DEFAULT_PROBES: Final[tuple[tuple[str, int, bytes | None], ...]] = (
    ("http://connect.rom.miui.com/generate_204", 204, None),
    ("http://wifi.vivo.com.cn/generate_204", 204, None),
    ("http://www.msftconnecttest.com/connecttest.txt", 200, b"Microsoft Connect Test"),
)


class _RawResponse(Protocol):
    status: int
    content: bytes


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """连通性探测结果。"""

    internet_available: bool
    portal_interception: bool
    detail: str

    def describe(self) -> str:
        if self.internet_available:
            return "互联网可用"
        if self.portal_interception:
            return "互联网不可用（探测请求被门户劫持）"
        return "互联网不可用（探测端点全部失败）"


class ConnectivityProbe:
    """可注入 HTTP 客户端，默认使用 requests。"""

    def __init__(
        self,
        probes: tuple[tuple[str, int, bytes | None], ...] = DEFAULT_PROBES,
        *,
        timeout: float = 5.0,
        http_get=None,
    ) -> None:
        self._probes = probes
        self._timeout = float(timeout)
        self._http_get = http_get

    def _get(self, url: str):
        if self._http_get is not None:
            return self._http_get(url, allow_redirects=False, timeout=self._timeout)
        import requests

        session = requests.Session()
        session.trust_env = False
        try:
            return session.get(url, allow_redirects=False, timeout=self._timeout)
        finally:
            session.close()

    def check(self) -> ProbeResult:
        intercepted = False
        failures: list[str] = []
        for url, expect_status, expect_body in self._probes:
            try:
                response = self._get(url)
            except Exception as exc:
                failures.append(url.split("/")[2] + ":" + type(exc).__name__)
                continue
            status = int(getattr(response, "status_code", 0))
            if status in (301, 302, 303, 307, 308):
                intercepted = True
                failures.append(url.split("/")[2] + ":redirect")
                continue
            if status != expect_status:
                failures.append(url.split("/")[2] + ":http" + str(status))
                continue
            if expect_body is not None:
                body = getattr(response, "content", b"") or b""
                if expect_body not in body:
                    intercepted = True
                    failures.append(url.split("/")[2] + ":body-mismatch")
                    continue
            return ProbeResult(
                internet_available=True,
                portal_interception=intercepted,
                detail="探测通过：" + url.split("/")[2],
            )
        return ProbeResult(
            internet_available=False,
            portal_interception=intercepted,
            detail="探测失败：" + ", ".join(failures),
        )
