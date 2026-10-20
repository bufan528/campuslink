"""测试公用设施（被 conftest 与各测试模块共享）。

设计要点：认证适配器只认白名单主机 192.0.2.10 / 192.0.2.11，
而测试服务器只能监听 127.0.0.1。因此这里用两个东西：

1. FakeTransport —— 直接实现 Transport 协议，脚本化返回响应，
   用来测"页面不符预期 / 重定向到外部 / 超时"等分支；
2. RewritingAdapter —— 真的走 requests，但把出站 URL 的 host
   换成 127.0.0.1 的端口，**逻辑 URL 仍然是 192.0.2.10**，
   因此白名单校验、Cookie、重定向处理都是真实代码路径。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit, urlunsplit

import requests

from campuslink.auth.models import HttpResponse

LOGIN_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Campus 校园网认证</title></head>
<body>
<div id="msg"></div>
<form name="f1" method="post" action="0.htm">
  <input type="text" name="DDDDD" value="" maxlength="32" />
  <input type="password" name="upass" value="" maxlength="32" />
  <input type="hidden" name="0MKKey" value="XZLogin" />
  <input type="submit" name="Submit" value="登录" />
</form>
</body></html>
"""

ONLINE_HTML = """<html><head><meta charset="utf-8"><title>Dr.COMUserWeb</title></head>
<body><div>您已在线</div><a href="F.htm">注销</a></body></html>
"""

LOGOUT_OK_HTML = "<html><body>注销成功，感谢使用</body></html>"

# 以下两个 fixture 为**示例门户结构**（不含任何真实页面副本、账号、网络地址或单位信息），
# 仅用于解析器/标记判定的回归测试。
REAL_LOGIN_HTML = """<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta http-equiv="Content-Type" content="text/html; charset=GB2312" />
<title>示例校园网</title>
<!--Dr.COMWebLoginID_0.htm-->
<script language="JavaScript">
sv=0;sv1=0;v4serip='192.0.2.10';m46=0;v46ip='0.0.0.0';uid='';pwd='';
</script>
</head>
<body>
<form name="form1" method="post" action="" onSubmit="return ee()">
<input id="username" name="DDDDD" type="text" placeholder="请输入账号" maxlength="26">
<input id="password" name="upass" type="password" placeholder="请输入密码" maxlength="16">
<input id="btn" type="submit" name="0MKKey" value="登 录　Login" style="margin-left:34px;">
<input type="hidden" name="v6ip" value="">
<input id="btn" type="reset" value="重 置　Reset" style="margin-left:15px;">
</form>
<form name="f0" method="post" action="">
<input type="hidden" name="DDDDD" value="0"><input type="hidden" name="upass" value="0">
<input type="hidden" name="R1" value="0"><input type="hidden" name="R2" value="0">
<input type="hidden" name="0MKKey" value="123456"><input type="hidden" name="v6ip" value="">
</form>
</body></html>
"""

REAL_STATUS_HTML = """<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta http-equiv="Content-Type" content="text/html; charset=GB2312" />
<title>示例校园网</title>
<!--Dr.COMWebLoginID_1.htm-->
<script language="JavaScript">
time='0';flow='0';fsele=1;fee='0';uid='';pwd='';v4ip='0.0.0.0';v6ip='::';
portalname='示例校园网';
function wc(){if(window.confirm("确定注销?")){s2=1;window.location=url4}}
</script>
</head>
<body><div>已使用流量 : 0 MByte</div><a onclick="wc()">注销</a></body></html>
"""


def response(
    status: int = 200,
    body: bytes = b"",
    headers: dict[str, str] | None = None,
    url: str = "http://192.0.2.10:80/0.htm",
) -> HttpResponse:
    return HttpResponse(status=status, url=url, headers=dict(headers or {}), body=body)


class FakeTransport:
    """脚本化传输层，不碰真实网络。"""

    def __init__(
        self,
        *,
        get: Callable[[str], Any] | None = None,
        post: Callable[[str, dict[str, str]], Any] | None = None,
    ) -> None:
        self._get = get
        self._post = post
        self.get_urls: list[str] = []
        self.post_calls: list[tuple[str, dict[str, str]]] = []
        self.closed = False

    def get(self, url: str, *, timeout: float) -> HttpResponse:
        self.get_urls.append(url)
        if self._get is None:
            raise AssertionError("未预期的 GET:" + url)
        result = self._get(url)
        if isinstance(result, Exception):
            raise result
        return result

    def post_form(self, url: str, data, *, timeout: float) -> HttpResponse:
        self.post_calls.append((url, dict(data)))
        if self._post is None:
            raise AssertionError("未预期的 POST:" + url)
        result = self._post(url, dict(data))
        if isinstance(result, Exception):
            raise result
        return result

    def close(self) -> None:
        self.closed = True


@dataclass
class PortalBehaviour:
    """模拟门户的行为开关。"""

    login_page: bytes = field(default_factory=lambda: LOGIN_HTML.encode("utf-8"))
    login_status: int = 200
    login_charset: str = "utf-8"
    set_cookie_on_get: str | None = None
    login_location: str | None = None
    require_cookie: bool = False
    post_status: int = 200
    post_body: bytes = field(default_factory=lambda: "认证成功".encode("utf-8"))
    post_location: str | None = None
    post_delay_s: float = 0.0
    get_delay_s: float = 0.0
    logout_body: bytes = field(default_factory=lambda: LOGOUT_OK_HTML.encode("utf-8"))


class PortalMockServer:
    """本地示例门户，用于真实 HTTP 路径测试。"""

    def __init__(self) -> None:
        self.behaviour = PortalBehaviour()
        self.post_bodies: list[dict[str, list[str]]] = []
        self.cookies_seen: list[str] = []
        self.request_paths: list[tuple[str, str]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:
                return

            def _send(self, status: int, body: bytes, extra: dict[str, str] | None = None) -> None:
                self.send_response(status)
                self.send_header(
                    "Content-Type",
                    "text/html; charset=" + outer.behaviour.login_charset,
                )
                self.send_header("Content-Length", str(len(body)))
                for key, value in (extra or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                if outer.behaviour.get_delay_s:
                    time.sleep(outer.behaviour.get_delay_s)
                path = urlsplit(self.path).path
                outer.request_paths.append(("GET", path))
                outer.cookies_seen.append(self.headers.get("Cookie", ""))
                if path in ("/0.htm", "/a30.htm", "/"):
                    extra: dict[str, str] = {}
                    if outer.behaviour.set_cookie_on_get:
                        extra["Set-Cookie"] = outer.behaviour.set_cookie_on_get
                    if outer.behaviour.login_location:
                        extra["Location"] = outer.behaviour.login_location
                    self._send(outer.behaviour.login_status, outer.behaviour.login_page, extra)
                    return
                if path == "/F.htm":
                    self._send(200, outer.behaviour.logout_body)
                    return
                self._send(404, b"not found")

            def do_POST(self) -> None:  # noqa: N802
                if outer.behaviour.post_delay_s:
                    time.sleep(outer.behaviour.post_delay_s)
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length).decode("utf-8", errors="replace")
                path = urlsplit(self.path).path
                outer.request_paths.append(("POST", path))
                outer.cookies_seen.append(self.headers.get("Cookie", ""))
                outer.post_bodies.append(parse_qs(raw, keep_blank_values=True))
                if outer.behaviour.require_cookie and not self.headers.get("Cookie"):
                    self._send(403, b"no session")
                    return
                extra: dict[str, str] = {}
                if outer.behaviour.post_location:
                    extra["Location"] = outer.behaviour.post_location
                self._send(outer.behaviour.post_status, outer.behaviour.post_body, extra)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    @property
    def base(self) -> str:
        return "http://127.0.0.1:" + str(self.port)

    def start(self) -> PortalMockServer:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


class RewritingAdapter(requests.adapters.HTTPAdapter):
    """把请求真的发到本地服务器，但保留 192.0.2.10 的逻辑 URL。"""

    def __init__(self, base: str, **kwargs: Any) -> None:
        self._base = urlsplit(base)
        super().__init__(**kwargs)

    def send(self, request, **kwargs):  # type: ignore[override]
        """只在真正发包的瞬间替换目标主机，发完立刻还原。

        还原很重要：requests 根据 response.request.url 决定 Cookie 记到哪个域下，
        若不还原，Cookie 会被记到 127.0.0.1 而不是逻辑主机 192.0.2.10，
        后续请求就带不上会话 Cookie，测不出真实的 Cookie 复用行为。
        """
        logical = request.url
        parts = urlsplit(logical)
        request.url = urlunsplit(
            (self._base.scheme, self._base.netloc, parts.path, parts.query, parts.fragment)
        )
        try:
            return super().send(request, **kwargs)
        finally:
            request.url = logical


def wire_session(server: PortalMockServer) -> requests.Session:
    """把白名单主机的出站请求路由到本地模拟服务器。"""
    session = requests.Session()
    adapter = RewritingAdapter(server.base)
    for prefix in (
        "http://192.0.2.10:80/",
        "http://192.0.2.10/",
        "http://192.0.2.11:80/",
        "http://192.0.2.11/",
    ):
        session.mount(prefix, adapter)
    return session
