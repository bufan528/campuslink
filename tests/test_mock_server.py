"""真实 HTTP 路径测试：Cookie 会话、编码、超时、重定向。"""

from __future__ import annotations

import pytest
from helpers import LOGIN_HTML

from campuslink.auth.adapter import PortalAuthAdapter, RequestsTransport
from campuslink.auth.models import AuthOutcome

ACCOUNT = "00000000"
PASSWORD = "P@ssw0rd!"


def make(session, timeout: float = 2.0) -> PortalAuthAdapter:
    return PortalAuthAdapter(
        "192.0.2.10", transport=RequestsTransport(session), timeout=timeout
    )


def test_login_over_real_http(portal, wired_session) -> None:
    attempt = make(wired_session).login(ACCOUNT, PASSWORD)
    assert attempt.outcome is AuthOutcome.SUCCESS
    assert attempt.submitted is True
    assert portal.post_bodies, "应当真的收到一次表单 POST"
    body = portal.post_bodies[0]
    assert body["DDDDD"] == [ACCOUNT]
    assert body["upass"] == [PASSWORD]
    assert "0MKKey" in body


def test_cookie_session_is_reused(portal, wired_session) -> None:
    portal.behaviour.set_cookie_on_get = "JSESSIONID=abc123; Path=/"
    portal.behaviour.require_cookie = True
    attempt = make(wired_session).login(ACCOUNT, PASSWORD)
    assert attempt.outcome is AuthOutcome.SUCCESS
    assert any("JSESSIONID=abc123" in cookie for cookie in portal.cookies_seen)


def test_missing_cookie_yields_403_and_not_success(portal, wired_session) -> None:
    portal.behaviour.require_cookie = True
    attempt = make(wired_session).login(ACCOUNT, PASSWORD)
    assert attempt.http_status == 403
    assert not attempt.is_success


def test_timeout_is_network_error(portal, wired_session) -> None:
    portal.behaviour.get_delay_s = 0.6
    attempt = make(wired_session, timeout=0.05).login(ACCOUNT, PASSWORD)
    assert attempt.outcome is AuthOutcome.NETWORK_ERROR
    assert attempt.submitted is False
    assert portal.post_bodies == []


def test_gbk_page_is_decoded(portal, wired_session) -> None:
    portal.behaviour.login_charset = "gbk"
    portal.behaviour.login_page = LOGIN_HTML.encode("gbk")
    page = make(wired_session).fetch_login_page()
    assert page.is_recognized
    assert page.fingerprint.encoding in {"gbk", "gb18030"}


def test_unexpected_html_blocks_submission(portal, wired_session) -> None:
    portal.behaviour.login_page = "<html><body>系统维护中</body></html>".encode("utf-8")
    attempt = make(wired_session).login(ACCOUNT, PASSWORD)
    assert attempt.outcome is AuthOutcome.PROTOCOL_MISMATCH
    assert portal.post_bodies == []


def test_redirect_to_external_portal_refused(portal, wired_session) -> None:
    portal.behaviour.login_status = 302
    portal.behaviour.login_location = "http://evil.example.com/login"
    attempt = make(wired_session).login(ACCOUNT, PASSWORD)
    assert attempt.outcome is AuthOutcome.PROTOCOL_MISMATCH
    assert portal.post_bodies == []


def test_logout_over_real_http(portal, wired_session) -> None:
    attempt = make(wired_session).logout()
    assert attempt.outcome is AuthOutcome.SUCCESS
    assert ("GET", "/F.htm") in portal.request_paths


def test_already_online_page_detected(portal, wired_session) -> None:
    from helpers import ONLINE_HTML

    portal.behaviour.login_page = ONLINE_HTML.encode("utf-8")
    attempt = make(wired_session).login(ACCOUNT, PASSWORD)
    assert attempt.outcome is AuthOutcome.ALREADY_ONLINE
    assert portal.post_bodies == []
