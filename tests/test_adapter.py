"""适配器分支测试（页面获取、已登录、提交、重定向、异常）。"""

from __future__ import annotations

import pytest
from helpers import LOGIN_HTML, LOGOUT_OK_HTML, ONLINE_HTML, FakeTransport, response

from campuslink.auth.adapter import PortalAuthAdapter
from campuslink.auth.errors import CredentialTargetRefused
from campuslink.auth.models import AuthOutcome, PortalState

ACCOUNT = "00000000"
PASSWORD = "P@ssw0rd!"


def make(transport: FakeTransport, **kwargs) -> PortalAuthAdapter:
    return PortalAuthAdapter("192.0.2.10", transport=transport, timeout=1.0, **kwargs)


def login_page_ok(url: str):
    return response(200, LOGIN_HTML.encode("utf-8"))


# ---- 登录页获取 ---------------------------------------------------


def test_fetch_login_page_uses_whitelisted_url(fake_transport) -> None:
    transport = fake_transport(get=login_page_ok)
    adapter = make(transport)
    page = adapter.fetch_login_page()
    assert page.is_recognized
    assert transport.get_urls == ["http://192.0.2.10:80/0.htm"]


def test_login_page_timeout_is_network_error(fake_transport) -> None:
    transport = fake_transport(get=lambda url: TimeoutError("timed out"))
    attempt = make(transport).login(ACCOUNT, PASSWORD)
    assert attempt.outcome is AuthOutcome.NETWORK_ERROR
    assert attempt.submitted is False
    assert transport.post_calls == []


def test_login_page_server_error_is_not_success(fake_transport) -> None:
    transport = fake_transport(get=lambda url: response(500, b"boom"))
    attempt = make(transport).login(ACCOUNT, PASSWORD)
    assert attempt.outcome is AuthOutcome.PROTOCOL_MISMATCH
    assert attempt.submitted is False
    assert transport.post_calls == []


def test_login_page_redirect_to_external_refused(fake_transport) -> None:
    transport = fake_transport(
        get=lambda url: response(302, b"", {"Location": "http://evil.example.com/login"})
    )
    attempt = make(transport).login(ACCOUNT, PASSWORD)
    assert attempt.outcome is AuthOutcome.PROTOCOL_MISMATCH
    assert attempt.submitted is False
    assert transport.post_calls == []


# ---- 已登录识别 ---------------------------------------------------


def test_already_online_skips_submission(fake_transport) -> None:
    transport = fake_transport(get=lambda url: response(200, ONLINE_HTML.encode("utf-8")))
    attempt = make(transport).login(ACCOUNT, PASSWORD)
    assert attempt.outcome is AuthOutcome.ALREADY_ONLINE
    assert attempt.submitted is False
    assert transport.post_calls == []


def test_unrecognized_page_does_not_submit(fake_transport) -> None:
    transport = fake_transport(get=lambda url: response(200, b"<html>maintenance</html>"))
    attempt = make(transport).login(ACCOUNT, PASSWORD)
    assert attempt.outcome is AuthOutcome.PROTOCOL_MISMATCH
    assert attempt.submitted is False
    assert transport.post_calls == []
    assert "fingerprint" in attempt.detail


# ---- 认证提交 -----------------------------------------------------


def test_successful_submission(fake_transport) -> None:
    transport = fake_transport(
        get=login_page_ok,
        post=lambda url, data: response(200, "认证成功".encode("utf-8")),
    )
    attempt = make(transport).login(ACCOUNT, PASSWORD)
    assert attempt.outcome is AuthOutcome.SUCCESS
    assert attempt.submitted is True
    assert attempt.http_status == 200
    target, payload = transport.post_calls[0]
    assert target == "http://192.0.2.10:80/0.htm"
    assert payload["DDDDD"] == ACCOUNT
    assert payload["upass"] == PASSWORD
    assert payload["0MKKey"] == "XZLogin"
    assert attempt.detail["mkkey_source"] == "page_hidden_field"


def test_failed_submission(fake_transport) -> None:
    transport = fake_transport(
        get=login_page_ok,
        post=lambda url, data: response(200, "密码错误".encode("utf-8")),
    )
    attempt = make(transport).login(ACCOUNT, PASSWORD)
    assert attempt.outcome is AuthOutcome.INVALID_CREDENTIALS
    assert attempt.submitted is True


def test_http_200_without_marker_is_unknown(fake_transport) -> None:
    transport = fake_transport(
        get=login_page_ok,
        post=lambda url, data: response(200, b"<html><body>welcome</body></html>"),
    )
    attempt = make(transport).login(ACCOUNT, PASSWORD)
    assert attempt.outcome is AuthOutcome.UNKNOWN
    assert attempt.submitted is True
    assert not attempt.is_success


def test_post_redirect_to_external_refused(fake_transport) -> None:
    transport = fake_transport(
        get=login_page_ok,
        post=lambda url, data: response(
            302, b"", {"Location": "http://evil.example.com/collect"}
        ),
    )
    attempt = make(transport).login(ACCOUNT, PASSWORD)
    assert attempt.outcome is AuthOutcome.PROTOCOL_MISMATCH
    assert attempt.submitted is True


def test_post_redirect_within_whitelist_not_followed(fake_transport) -> None:
    """白名单内的 302 只校验、不跟随；响应正文为空时只能是 UNKNOWN。"""
    transport = fake_transport(
        get=login_page_ok,
        post=lambda url, data: response(
            302, b"", {"Location": "http://192.0.2.10:80/ok.htm"}
        ),
    )
    attempt = make(transport).login(ACCOUNT, PASSWORD)
    assert attempt.outcome is AuthOutcome.UNKNOWN
    assert attempt.submitted is True
    assert len(transport.post_calls) == 1
    assert all("/ok.htm" not in url for url in transport.get_urls)


def test_http_error_with_success_token_is_not_success(fake_transport) -> None:
    """4xx/5xx 正文里就算出现“认证成功”，也绝不能报成成功。"""
    for status in (403, 500):
        transport = fake_transport(
            get=login_page_ok,
            post=lambda url, data: response(status, "认证成功".encode("utf-8")),
        )
        attempt = make(transport).login(ACCOUNT, PASSWORD)
        assert attempt.outcome is AuthOutcome.UNKNOWN, status
        assert attempt.submitted is True
        assert attempt.is_success is False
        assert attempt.http_status == status


def test_form_method_get_is_refused(fake_transport) -> None:
    """已知协议是 POST；表单 method=get 属于协议不一致，不提交。"""
    page = LOGIN_HTML.replace('method="post"', 'method="get"')
    transport = fake_transport(get=lambda url: response(200, page.encode("utf-8")))
    attempt = make(transport).login(ACCOUNT, PASSWORD)
    assert attempt.outcome is AuthOutcome.PROTOCOL_MISMATCH
    assert attempt.submitted is False
    assert transport.post_calls == []


@pytest.mark.parametrize(
    "location",
    [
        "http://192.0.2.10:99999/evil.htm",
        "http://192.0.2.10:abc/evil.htm",
        "http://[::1/evil.htm",
    ],
)
def test_malformed_redirect_location_is_refused(fake_transport, location: str) -> None:
    """畸形 Location 必须变成协议错误，不能把 ValueError 抛到界面线程。"""
    transport = fake_transport(
        get=lambda url: response(302, b"", {"Location": location})
    )
    attempt = make(transport).login(ACCOUNT, PASSWORD)
    assert attempt.outcome is AuthOutcome.PROTOCOL_MISMATCH
    assert attempt.submitted is False


def test_malformed_post_redirect_is_refused(fake_transport) -> None:
    transport = fake_transport(
        get=login_page_ok,
        post=lambda url, data: response(
            302, b"", {"Location": "http://192.0.2.10:99999/x"}
        ),
    )
    attempt = make(transport).login(ACCOUNT, PASSWORD)
    assert attempt.outcome is AuthOutcome.PROTOCOL_MISMATCH
    assert attempt.submitted is True


def test_force_reauth_bypasses_online_probe_and_submits(fake_transport) -> None:
    """强制重认证：门户仍显示在线时也要注销并重新提交。"""

    def route(url: str):
        if url.endswith("/F.htm"):
            return response(200, LOGOUT_OK_HTML.encode("utf-8"))
        if url.endswith(":80/"):
            return response(200, ONLINE_HTML.encode("utf-8"))
        return response(200, LOGIN_HTML.encode("utf-8"))

    transport = fake_transport(
        get=route,
        post=lambda url, data: response(200, "认证成功".encode("utf-8")),
    )
    attempt = make(transport).login(ACCOUNT, PASSWORD, force_reauth=True)
    assert attempt.outcome is AuthOutcome.SUCCESS
    assert attempt.submitted is True
    assert len(transport.post_calls) == 1
    assert any(url.endswith("/F.htm") for url in transport.get_urls)


def test_real_portal_shape_already_online_skips_submission(fake_transport) -> None:
    """真实门户在线时 /0.htm 仍是登录表单，必须靠根路径判断，不能再提交。"""

    def route(url: str):
        if url.endswith(":80/"):
            return response(200, ONLINE_HTML.encode("utf-8"))
        return response(200, LOGIN_HTML.encode("utf-8"))

    transport = fake_transport(get=route)
    attempt = make(transport).login(ACCOUNT, PASSWORD)
    assert attempt.outcome is AuthOutcome.ALREADY_ONLINE
    assert attempt.submitted is False
    assert transport.post_calls == []
    assert transport.get_urls == ["http://192.0.2.10:80/"]


def test_form_action_to_external_is_refused(fake_transport) -> None:
    page = LOGIN_HTML.replace('action="0.htm"', 'action="http://evil.example.com/x"')
    transport = fake_transport(get=lambda url: response(200, page.encode("utf-8")))
    attempt = make(transport).login(ACCOUNT, PASSWORD)
    assert attempt.outcome is AuthOutcome.TARGET_REFUSED
    assert attempt.submitted is False
    assert transport.post_calls == []


def test_attempt_does_not_leak_credentials(fake_transport) -> None:
    transport = fake_transport(
        get=login_page_ok,
        post=lambda url, data: response(200, "密码错误".encode("utf-8")),
    )
    attempt = make(transport).login(ACCOUNT, PASSWORD)
    rendered = repr(attempt) + str(attempt.message) + str(attempt.detail)
    assert PASSWORD not in rendered
    assert ACCOUNT not in rendered


def test_empty_credentials_not_submitted(fake_transport) -> None:
    transport = fake_transport(get=login_page_ok)
    attempt = make(transport).login("", "")
    assert attempt.submitted is False
    assert transport.get_urls == []


# ---- 注销 ---------------------------------------------------------


def test_logout_success(fake_transport) -> None:
    transport = fake_transport(get=lambda url: response(200, LOGOUT_OK_HTML.encode("utf-8")))
    attempt = make(transport).logout()
    assert attempt.outcome is AuthOutcome.SUCCESS
    assert transport.get_urls == ["http://192.0.2.10:80/F.htm"]


def test_logout_http_error_with_success_token_is_unknown(fake_transport) -> None:
    transport = fake_transport(
        get=lambda url: response(500, "注销成功".encode("utf-8"))
    )
    attempt = make(transport).logout()
    assert attempt.outcome is AuthOutcome.UNKNOWN
    assert attempt.is_success is False


def test_logout_unknown_is_not_success(fake_transport) -> None:
    transport = fake_transport(get=lambda url: response(200, b"<html>?</html>"))
    attempt = make(transport).logout()
    assert attempt.outcome is AuthOutcome.UNKNOWN
    assert not attempt.is_success


# ---- 门户状态 -----------------------------------------------------


def test_probe_state_login_required(fake_transport) -> None:
    transport = fake_transport(get=login_page_ok)
    assert make(transport).probe_state() is PortalState.LOGIN_REQUIRED


def test_probe_state_online(fake_transport) -> None:
    transport = fake_transport(get=lambda url: response(200, ONLINE_HTML.encode("utf-8")))
    assert make(transport).probe_state() is PortalState.ONLINE


def test_probe_state_unreachable(fake_transport) -> None:
    transport = fake_transport(get=lambda url: ConnectionError("refused"))
    assert make(transport).probe_state() is PortalState.UNREACHABLE


def test_probe_state_unrecognized(fake_transport) -> None:
    transport = fake_transport(get=lambda url: response(200, b"<html>?</html>"))
    assert make(transport).probe_state() is PortalState.UNRECOGNIZED


# ---- 构造期防护 ---------------------------------------------------


@pytest.mark.parametrize("host", ["203.0.113.50", "evil.example.com", ""])
def test_constructor_refuses_other_hosts(host: str, fake_transport) -> None:
    with pytest.raises(CredentialTargetRefused):
        PortalAuthAdapter(host, transport=fake_transport())


def test_constructor_refuses_unknown_login_path(fake_transport) -> None:
    with pytest.raises(CredentialTargetRefused):
        PortalAuthAdapter("192.0.2.10", transport=fake_transport(), login_path="/evil.htm")


def test_a30_path_is_available_when_explicitly_selected(fake_transport) -> None:
    transport = fake_transport(get=login_page_ok)
    adapter = PortalAuthAdapter("192.0.2.10", transport=transport, login_path="/a30.htm")
    adapter.fetch_login_page()
    assert transport.get_urls == ["http://192.0.2.10:80/a30.htm"]
