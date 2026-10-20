"""门户认证适配器。

职责边界：
* 只和 auth.endpoints 白名单里的主机通信；URL 由白名单模块拼装。
* 登录 POST 不跟随重定向；重定向目标不在白名单即终止。
* 提交前核对页面指纹；页面不符预期就**不提交凭据**。
* 结果判定交给 auth.markers，未命中显式标记时判 UNKNOWN。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final, Protocol
from urllib.parse import urljoin, urlsplit

from ..logging_setup.logger import get_logger
from ..logging_setup.redact import DEFAULT_REDACTOR, Redactor
from . import endpoints, markers, portal
from .errors import (
    CampusLinkError,
    CredentialTargetRefused,
    PortalProtocolMismatch,
    PortalUnreachable,
    RedirectRefused,
    UnexpectedResponse,
)
from .models import AuthAttempt, AuthOutcome, HttpResponse, PortalState

_log = get_logger("auth.adapter")

REDIRECT_STATUSES: Final[frozenset[int]] = frozenset({301, 302, 303, 307, 308})


class Transport(Protocol):
    """HTTP 传输抽象，便于用本地 Mock 服务器做测试。"""

    def get(self, url: str, *, timeout: float) -> HttpResponse: ...

    def post_form(self, url: str, data: Mapping[str, str], *, timeout: float) -> HttpResponse: ...

    def close(self) -> None: ...


def _to_response(raw) -> HttpResponse:
    return HttpResponse(
        status=int(raw.status_code),
        url=str(raw.url),
        headers={str(k): str(v) for k, v in raw.headers.items()},
        body=raw.content or b"",
    )


class RequestsTransport:
    """基于 requests.Session 的传输实现（保留 Cookie 会话）。"""

    def __init__(self, session=None) -> None:
        if session is None:
            import requests

            session = requests.Session()
        self._session = session
        # 不读取环境里的 HTTP_PROXY 等设置：凭据不应被本机代理中转。
        self._session.trust_env = False
        self._session.headers.update(
            {"User-Agent": "CampusLink/0.1 (local campus portal client)"}
        )

    @property
    def session(self):
        return self._session

    def get(self, url: str, *, timeout: float) -> HttpResponse:
        return _to_response(
            self._session.get(url, timeout=timeout, allow_redirects=False)
        )

    def post_form(self, url: str, data: Mapping[str, str], *, timeout: float) -> HttpResponse:
        return _to_response(
            self._session.post(url, data=dict(data), timeout=timeout, allow_redirects=False)
        )

    def close(self) -> None:
        try:
            self._session.close()
        except Exception:
            pass


def _redact(redactor: Redactor, text: str) -> str:
    return redactor.redact_text(text)


class PortalAuthAdapter:
    """一个门户入口的认证会话。"""

    def __init__(
        self,
        host: str,
        *,
        transport: Transport | None = None,
        timeout: float = 8.0,
        login_path: str | None = None,
        redactor: Redactor = DEFAULT_REDACTOR,
    ) -> None:
        self._portal = endpoints.require_portal(host)
        self._host = host
        self._transport: Transport = transport or RequestsTransport()
        self._timeout = float(timeout)
        self._redactor = redactor
        path = login_path or self._portal.default_login_path
        if path not in self._portal.login_paths:
            raise CredentialTargetRefused("登录路径不在该入口的已知路径内")
        self._login_path = path

    @property
    def host(self) -> str:
        return self._host

    @property
    def portal_label(self) -> str:
        return self._portal.label

    @property
    def login_path(self) -> str:
        return self._login_path

    def close(self) -> None:
        self._transport.close()

    def __enter__(self) -> PortalAuthAdapter:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ---- 内部工具 -------------------------------------------------

    def _login_url(self) -> str:
        return endpoints.build_url(self._host, self._login_path)

    @staticmethod
    def _safe_urljoin(base: str, reference: str) -> str:
        """urljoin 对畸形 URL（如未闭合的 IPv6 括号）会抛 ValueError。"""
        try:
            return urljoin(base, reference)
        except ValueError as exc:
            raise RedirectRefused("URL 无法解析，已终止：" + str(exc)) from None

    def _guard_redirect(self, response: HttpResponse) -> None:
        """重定向必须在白名单内；否则立刻终止，绝不跟过去。"""
        if response.status not in REDIRECT_STATUSES:
            return
        location = response.location
        if not location:
            raise RedirectRefused("认证主机返回重定向但缺少 Location")
        target = self._safe_urljoin(response.url, location)
        try:
            endpoints.require_same_origin(target)
        except CredentialTargetRefused as exc:
            raise RedirectRefused("重定向目标不在白名单内，已终止：" + str(exc)) from None

    def _resolve_form_target(self, action: str | None) -> str:
        if not action:
            return self._login_url()
        base = self._login_url()
        try:
            target = self._safe_urljoin(base, action)
        except RedirectRefused as exc:
            raise CredentialTargetRefused(str(exc)) from None
        endpoints.require_same_origin(target)
        return target

    def _request_get(self, url: str) -> HttpResponse:
        try:
            return self._transport.get(url, timeout=self._timeout)
        except Exception as exc:
            raise PortalUnreachable(
                "认证主机不可达（" + type(exc).__name__ + "）"
            ) from None

    def _request_post(self, url: str, payload: Mapping[str, str]) -> HttpResponse:
        try:
            return self._transport.post_form(url, payload, timeout=self._timeout)
        except Exception as exc:
            raise PortalUnreachable(
                "向认证主机提交失败（" + type(exc).__name__ + "）"
            ) from None

    def _fetch(self, path: str) -> HttpResponse:
        """GET 一个站点内路径；白名单内的重定向最多跟一次。"""
        response = self._request_get(endpoints.build_url(self._host, path))
        if response.status in REDIRECT_STATUSES:
            self._guard_redirect(response)
            target = self._safe_urljoin(response.url, response.location or "")
            endpoints.require_same_origin(target)
            response = self._request_get(target)
            if response.status in REDIRECT_STATUSES:
                self._guard_redirect(response)
        return response

    # ---- 对外能力 -------------------------------------------------

    def fetch_login_page(self):
        """获取并解析登录页。"""
        response = self._fetch(self._login_path)
        text, encoding = response.decode()
        if response.status >= 400:
            raise UnexpectedResponse("登录页返回 HTTP " + str(response.status))
        return portal.parse_login_page(text, encoding)

    def probe_state(self) -> PortalState:
        """判断门户当前状态：需要认证 / 已认证 / 不可识别 / 不可达。

        某些门户在已登录时仍会在登录页渲染登录表单，因此**不能**只用
        "有没有密码框"判断是否需要认证。这里探测站点根路径：
        已登录时通常返回状态页（含注销入口），没有密码框。
        """
        try:
            response = self._fetch("/")
        except PortalUnreachable:
            return PortalState.UNREACHABLE
        except (RedirectRefused, UnexpectedResponse, CredentialTargetRefused):
            return PortalState.UNRECOGNIZED
        except CampusLinkError:
            return PortalState.UNRECOGNIZED
        if response.status >= 400:
            return PortalState.UNRECOGNIZED
        text, encoding = response.decode()
        page = portal.parse_login_page(text, encoding)
        if page.fingerprint.has_password_field:
            return PortalState.LOGIN_REQUIRED
        if markers.looks_like_online_page(text, False):
            return PortalState.ONLINE
        if markers.is_login_page(text):
            return PortalState.LOGIN_REQUIRED
        return PortalState.UNRECOGNIZED

    def login(self, account: str, password: str) -> AuthAttempt:
        """执行一次认证。返回的 AuthAttempt 表明是否真的提交了凭据。"""
        if not account or not password:
            return AuthAttempt(
                outcome=AuthOutcome.UNKNOWN,
                message="账号或密码为空，未发起认证。",
                submitted=False,
            )

        # 0) 先看根路径状态：有些门户在已登录时仍会在登录页渲染登录表单，
        #    因此“登录页有密码框”不能证明需要认证。此前只有 gateway 在
        #    联网探测成功时才做这个判断；适配器被直接调用时仍会重复提交。
        #    （重新登录可能导致已有会话被踢下线，因此这里补一道前置检查。）
        portal_state = self.probe_state()
        if portal_state is PortalState.ONLINE:
            return AuthAttempt(
                outcome=AuthOutcome.ALREADY_ONLINE,
                message="门户根路径显示当前已在线，未重复提交认证。",
                submitted=False,
            )

        # 1) 取页面
        try:
            page = self.fetch_login_page()
        except RedirectRefused as exc:
            return AuthAttempt(
                outcome=AuthOutcome.PROTOCOL_MISMATCH,
                message="登录页重定向到非白名单地址，已终止：" + str(exc),
                submitted=False,
            )
        except PortalUnreachable as exc:
            return AuthAttempt(
                outcome=AuthOutcome.NETWORK_ERROR,
                message=str(exc),
                submitted=False,
            )
        except CredentialTargetRefused as exc:
            return AuthAttempt(
                outcome=AuthOutcome.TARGET_REFUSED,
                message=str(exc),
                submitted=False,
            )
        except CampusLinkError as exc:
            return AuthAttempt(
                outcome=AuthOutcome.PROTOCOL_MISMATCH,
                message=str(exc),
                submitted=False,
            )

        # 2) 已登录就别重复提交
        if markers.looks_like_online_page(
            page.raw_text, page.fingerprint.has_password_field
        ):
            return AuthAttempt(
                outcome=AuthOutcome.ALREADY_ONLINE,
                message="门户显示当前已处于登录状态，未重复提交认证。",
                submitted=False,
                detail={"fingerprint": page.fingerprint.describe()},
            )

        # 3) 指纹核对：不认识就不提交
        if not page.is_recognized:
            detail = page.fingerprint.describe()
            return AuthAttempt(
                outcome=AuthOutcome.PROTOCOL_MISMATCH,
                message="登录页与已知协议不一致（未找到账号/密码字段），未提交凭据。",
                submitted=False,
                detail={"fingerprint": detail},
            )
        if (page.fingerprint.method or "").lower() != "post":
            return AuthAttempt(
                outcome=AuthOutcome.PROTOCOL_MISMATCH,
                message="登录表单不是 POST（method="
                + repr(page.fingerprint.method)
                + "），与已知协议不一致，未提交凭据。",
                submitted=False,
                detail={"fingerprint": page.fingerprint.describe()},
            )

        # 4) 解析提交目标
        try:
            target = self._resolve_form_target(page.fingerprint.action)
        except CredentialTargetRefused as exc:
            return AuthAttempt(
                outcome=AuthOutcome.TARGET_REFUSED,
                message="登录表单指向非白名单地址，已终止：" + str(exc),
                submitted=False,
            )

        # 5) 提交
        payload, mkkey_source = portal.build_login_payload(account, password, page)
        self._redactor.add(password)
        self._redactor.add(account)
        try:
            response = self._request_post(target, payload)
            self._guard_redirect(response)
        except RedirectRefused as exc:
            return AuthAttempt(
                outcome=AuthOutcome.PROTOCOL_MISMATCH,
                message="认证响应重定向到非白名单地址，已终止：" + str(exc),
                submitted=True,
            )
        except PortalUnreachable as exc:
            return AuthAttempt(
                outcome=AuthOutcome.NETWORK_ERROR,
                message=str(exc),
                submitted=True,
            )
        except CredentialTargetRefused as exc:
            return AuthAttempt(
                outcome=AuthOutcome.TARGET_REFUSED,
                message=str(exc),
                submitted=True,
            )

        # 明确的 HTTP 错误状态绝不允许被正文里的“成功”字样翻案。
        # 认证结果的判定仍只认显式标记，但 4xx/5xx 先按“未确认”处理。
        if response.status >= 400:
            return AuthAttempt(
                outcome=AuthOutcome.UNKNOWN,
                message="认证响应 HTTP " + str(response.status) + "，认证结果未确认。",
                http_status=response.status,
                submitted=True,
                detail={
                    "mkkey_source": mkkey_source,
                    "http_status": str(response.status),
                },
            )

        text, encoding = response.decode()
        outcome, reason = markers.classify_login_body(text)
        detail = {
            "mkkey_source": mkkey_source,
            "encoding": encoding,
            "http_status": str(response.status),
        }
        if response.location:
            detail["location_present"] = "yes"
        _log.info(
            "认证提交完成 status=%s outcome=%s mkkey=%s",
            response.status,
            outcome.value,
            mkkey_source,
        )
        return AuthAttempt(
            outcome=outcome,
            message=reason,
            http_status=response.status,
            submitted=True,
            detail=detail,
        )

    def logout(self) -> AuthAttempt:
        """请求注销（公开页面结构：GET /F.htm）。结果只按显式标记判定。"""
        url = endpoints.build_url(self._host, self._portal.logout_path)
        try:
            response = self._request_get(url)
            self._guard_redirect(response)
        except (PortalUnreachable, RedirectRefused, CredentialTargetRefused) as exc:
            return AuthAttempt(
                outcome=AuthOutcome.NETWORK_ERROR,
                message="注销请求失败：" + str(exc),
                submitted=False,
            )
        text, _encoding = response.decode()
        if response.status >= 400:
            return AuthAttempt(
                outcome=AuthOutcome.UNKNOWN,
                message="注销响应 HTTP " + str(response.status) + "，注销结果未确认。",
                http_status=response.status,
                submitted=True,
            )
        ok, reason = markers.classify_logout_body(text)
        if ok is True:
            return AuthAttempt(
                outcome=AuthOutcome.SUCCESS,
                message=reason,
                http_status=response.status,
                submitted=True,
            )
        if ok is False:
            return AuthAttempt(
                outcome=AuthOutcome.REJECTED,
                message=reason,
                http_status=response.status,
                submitted=True,
            )
        return AuthAttempt(
            outcome=AuthOutcome.UNKNOWN,
            message="已发送注销请求，但" + reason + "（注销接口仍待真实环境验证）。",
            http_status=response.status,
            submitted=True,
        )
