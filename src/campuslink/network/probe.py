"""互联网连通性探测。

用 captive-portal 探测端点区分三件事：
"连上了 Wi-Fi" / "校园网已认证" / "互联网真的可用"。
探测目标全部是校外站点，不向认证主机发任何凭据。

重点：校园网可能对 `generate_204` 这类 HTTP 探测地址"放行"（即使未认证也
返回 204），因此**不能只看 HTTP 204**。默认探测优先要求一条真实 HTTPS
请求成功（TLS 证书校验通过，门户无法伪造），HTTP 探测仅作为辅助判断。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Protocol

DEFAULT_PROBES: Final[tuple[tuple[str, int, bytes | None], ...]] = (
    # HTTPS 强证据：TLS 校验通过才可能成功，Captive Portal 无法伪造。
    ("https://www.baidu.com/", 200, None),
    ("https://www.taobao.com/", 200, None),
    # HTTP 辅助探测：若 HTTPS 全部失败而 HTTP 返回 204，判定为疑似劫持。
    ("http://connect.rom.miui.com/generate_204", 204, None),
    ("http://wifi.vivo.com.cn/generate_204", 204, None),
    ("http://www.msftconnecttest.com/connecttest.txt", 200, b"Microsoft Connect Test"),
)

_REDIRECT_STATUSES: Final[frozenset[int]] = frozenset({301, 302, 303, 307, 308})


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
        self._session = None

    def _timeout_for(self, url: str) -> float:
        # HTTPS 首次握手可能较慢，给它略宽的时间；HTTP 保持原超时。
        return max(self._timeout, 6.0) if url.startswith("https://") else self._timeout

    def _get(self, url: str):
        if self._http_get is not None:
            return self._http_get(url, allow_redirects=False, timeout=self._timeout_for(url))
        import requests

        # 复用连接：HTTPS 首次 TLS 握手可能要数秒，之后轮询可毫秒级完成。
        # 只发无凭据的 GET；不读取系统代理。
        if self._session is None:
            session = requests.Session()
            session.trust_env = False
            self._session = session
        return self._session.get(
            url, allow_redirects=False, timeout=self._timeout_for(url)
        )

    def close(self) -> None:
        session = self._session
        self._session = None
        if session is not None:
            try:
                session.close()
            except Exception:
                pass

    def check(self) -> ProbeResult:
        intercepted = False
        failures: list[str] = []
        secure_configured = any(url.startswith("https://") for url, _, _ in self._probes)
        insecure_http_ok: str | None = None

        for url, expect_status, expect_body in self._probes:
            host = url.split("/")[2]
            try:
                response = self._get(url)
            except Exception as exc:
                failures.append(host + ":" + type(exc).__name__)
                continue
            status = int(getattr(response, "status_code", 0))
            if status in _REDIRECT_STATUSES:
                intercepted = True
                failures.append(host + ":redirect")
                continue
            if status != expect_status:
                failures.append(host + ":http" + str(status))
                continue
            if expect_body is not None:
                body = getattr(response, "content", b"") or b""
                if expect_body not in body:
                    intercepted = True
                    failures.append(host + ":body-mismatch")
                    continue

            if url.startswith("https://"):
                # TLS 校验已通过 + 预期状态码 = 强证据。
                return ProbeResult(
                    internet_available=True,
                    portal_interception=intercepted,
                    detail="HTTPS 探测通过：" + host,
                )
            if not secure_configured:
                return ProbeResult(
                    internet_available=True,
                    portal_interception=intercepted,
                    detail="探测通过：" + host,
                )
            # 配置了 HTTPS 强证据时，HTTP 成功不足以宣告互联网可用。
            insecure_http_ok = host
            failures.append(host + ":http-ok-but-no-https")
            break

        if secure_configured and insecure_http_ok is not None:
            return ProbeResult(
                internet_available=False,
                portal_interception=True,
                detail=(
                    "HTTP 探测通过（" + insecure_http_ok + "）但 HTTPS 全部失败，"
                    "疑似门户劫持或网络受限：" + ", ".join(failures)
                ),
            )
        return ProbeResult(
            internet_available=False,
            portal_interception=intercepted,
            detail="探测失败：" + ", ".join(failures),
        )
