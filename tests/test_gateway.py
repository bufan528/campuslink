"""编排服务：结果判定必须经过独立联网验证。"""

from __future__ import annotations

import pytest
from helpers import LOGIN_HTML, LOGOUT_OK_HTML, ONLINE_HTML, FakeTransport, response

from campuslink.auth.adapter import PortalAuthAdapter
from campuslink.auth.models import AuthOutcome, PortalState
from campuslink.config.settings import AppSettings
from campuslink.network.probe import ProbeResult
from campuslink.network.status import LinkState
from campuslink.network.wifi import WifiStatus
from campuslink.services.gateway import AuthGateway

ACCOUNT = "00000000"
PASSWORD = "P@ssw0rd!"


def ok(flag: bool) -> ProbeResult:
    return ProbeResult(flag, False, "probe")


class FakeProbe:
    def __init__(self, *results: ProbeResult) -> None:
        self._results = list(results)
        self.calls = 0

    def check(self) -> ProbeResult:
        index = min(self.calls, len(self._results) - 1)
        self.calls += 1
        return self._results[index]


class FakeStore:
    def __init__(self, entry=None) -> None:
        self.entry = entry
        self.saved: list[tuple[str, str]] = []
        self.deleted = False

    def save(self, account: str, password: str) -> None:
        self.saved.append((account, password))
        self.entry = (account, password)

    def load(self):
        return self.entry

    def delete(self) -> None:
        self.deleted = True
        self.entry = None


class FakeWifi:
    def __init__(self, ssid: str | None = "campus-wlan") -> None:
        self._status = WifiStatus(True, ssid, "已连接")

    def current_status(self) -> WifiStatus:
        return self._status


def page_router(login_page: bytes = None, logout_ok: bool = True):
    payload = login_page if login_page is not None else LOGIN_HTML.encode("utf-8")

    def handler(url: str):
        if url.endswith("/F.htm"):
            body = LOGOUT_OK_HTML if logout_ok else "<html>?</html>"
            return response(200, body.encode("utf-8"))
        return response(200, payload)

    return handler


def make_gateway(transport, probe_obj, store=None, *, ssid="campus-wlan") -> AuthGateway:
    def factory(host: str, *, timeout: float, login_path: str):
        return PortalAuthAdapter(host, transport=transport, timeout=timeout, login_path=login_path)

    return AuthGateway(
        AppSettings(),
        store if store is not None else FakeStore(),
        adapter_factory=factory,
        probe=probe_obj,
        wifi=FakeWifi(ssid),
    )


def test_already_online_does_not_resubmit() -> None:
    transport = FakeTransport(get=page_router(ONLINE_HTML.encode("utf-8")))
    gateway = make_gateway(transport, FakeProbe(ok(True)))
    report = gateway.sign_in(ACCOUNT, PASSWORD)
    assert report.attempt.outcome is AuthOutcome.ALREADY_ONLINE
    assert report.state is LinkState.AUTHENTICATED
    assert transport.post_calls == []


def test_success_requires_independent_probe() -> None:
    transport = FakeTransport(
        get=page_router(),
        post=lambda url, data: response(200, "认证成功".encode("utf-8")),
    )
    gateway = make_gateway(transport, FakeProbe(ok(False), ok(True)))
    report = gateway.sign_in(ACCOUNT, PASSWORD)
    assert report.attempt.outcome is AuthOutcome.SUCCESS
    assert report.state is LinkState.AUTHENTICATED
    assert report.snapshot.internet_available is True


def test_portal_success_without_internet_is_not_success() -> None:
    transport = FakeTransport(
        get=page_router(),
        post=lambda url, data: response(200, "认证成功".encode("utf-8")),
    )
    gateway = make_gateway(transport, FakeProbe(ok(False), ok(False)))
    report = gateway.sign_in(ACCOUNT, PASSWORD)
    assert report.attempt.outcome is AuthOutcome.SUCCESS
    assert report.state is LinkState.INTERNET_UNAVAILABLE
    assert report.state is not LinkState.AUTHENTICATED


def test_http_200_without_marker_is_not_success() -> None:
    transport = FakeTransport(
        get=page_router(),
        post=lambda url, data: response(200, b"<html>whatever</html>"),
    )
    gateway = make_gateway(transport, FakeProbe(ok(False), ok(False)))
    report = gateway.sign_in(ACCOUNT, PASSWORD)
    assert report.attempt.outcome is AuthOutcome.UNKNOWN
    assert report.state is LinkState.AUTH_FAILED


def test_invalid_credentials_state() -> None:
    transport = FakeTransport(
        get=page_router(),
        post=lambda url, data: response(200, "密码错误".encode("utf-8")),
    )
    gateway = make_gateway(transport, FakeProbe(ok(False), ok(False)))
    report = gateway.sign_in(ACCOUNT, PASSWORD)
    assert report.attempt.outcome is AuthOutcome.INVALID_CREDENTIALS
    assert report.state is LinkState.AUTH_FAILED


def test_unreachable_portal_submits_nothing() -> None:
    transport = FakeTransport(get=lambda url: ConnectionError("unreachable"))
    gateway = make_gateway(transport, FakeProbe(ok(False)))
    report = gateway.sign_in(ACCOUNT, PASSWORD)
    assert report.attempt.outcome is AuthOutcome.NETWORK_ERROR
    assert report.attempt.submitted is False
    assert transport.post_calls == []


def test_missing_credentials() -> None:
    transport = FakeTransport(get=page_router())
    gateway = make_gateway(transport, FakeProbe(ok(False)))
    report = gateway.sign_in()
    assert report.attempt.submitted is False
    assert report.state is LinkState.AUTH_FAILED
    assert "未提供" in report.message


def test_credentials_loaded_from_store() -> None:
    transport = FakeTransport(
        get=page_router(),
        post=lambda url, data: response(200, "认证成功".encode("utf-8")),
    )
    store = FakeStore((ACCOUNT, PASSWORD))
    gateway = make_gateway(transport, FakeProbe(ok(False), ok(True)), store)
    gateway.sign_in()
    assert transport.post_calls[0][1]["DDDDD"] == ACCOUNT


def test_credentials_saved_only_on_request_and_success() -> None:
    transport = FakeTransport(
        get=page_router(),
        post=lambda url, data: response(200, "认证成功".encode("utf-8")),
    )
    store = FakeStore()
    gateway = make_gateway(transport, FakeProbe(ok(False), ok(True)), store)
    gateway.sign_in(ACCOUNT, PASSWORD, save_credentials=True)
    assert store.saved == [(ACCOUNT, PASSWORD)]

    failing = FakeTransport(
        get=page_router(),
        post=lambda url, data: response(200, "密码错误".encode("utf-8")),
    )
    store2 = FakeStore()
    gateway2 = make_gateway(failing, FakeProbe(ok(False), ok(False)), store2)
    gateway2.sign_in(ACCOUNT, PASSWORD, save_credentials=True)
    assert store2.saved == []


def test_invalid_credentials_wins_over_internet_available() -> None:
    """密码错误 + 探测到互联网（多网卡/假阳性）也不能报“认证成功”。"""
    transport = FakeTransport(
        get=page_router(),
        post=lambda url, data: response(200, "密码错误".encode("utf-8")),
    )
    gateway = make_gateway(transport, FakeProbe(ok(False), ok(True)))
    report = gateway.sign_in(ACCOUNT, PASSWORD)
    assert report.attempt.outcome is AuthOutcome.INVALID_CREDENTIALS
    assert report.state is LinkState.AUTH_FAILED


def test_already_online_saves_credentials_when_asked() -> None:
    transport = FakeTransport(get=page_router(ONLINE_HTML.encode("utf-8")))
    store = FakeStore()
    gateway = make_gateway(transport, FakeProbe(ok(True)), store)
    report = gateway.sign_in(ACCOUNT, PASSWORD, save_credentials=True)
    assert report.attempt.outcome is AuthOutcome.ALREADY_ONLINE
    assert store.saved == [(ACCOUNT, PASSWORD)]
    assert report.notice == ""


class FailingStore(FakeStore):
    def save(self, account: str, password: str) -> None:
        from campuslink.auth.errors import CredentialStoreError

        raise CredentialStoreError("模拟凭据存储不可用")


def test_credential_save_failure_is_surfaced() -> None:
    transport = FakeTransport(
        get=page_router(),
        post=lambda url, data: response(200, "认证成功".encode("utf-8")),
    )
    gateway = make_gateway(transport, FakeProbe(ok(False), ok(True)), FailingStore())
    report = gateway.sign_in(ACCOUNT, PASSWORD, save_credentials=True)
    assert report.attempt.outcome is AuthOutcome.SUCCESS
    assert "未保存" in report.notice


def test_partial_manual_credentials_are_not_mixed_with_store() -> None:
    transport = FakeTransport(get=page_router())
    store = FakeStore(("stored-account", "stored-password"))
    gateway = make_gateway(transport, FakeProbe(ok(False)), store)
    report = gateway.sign_in(ACCOUNT)  # 只给账号，不给密码
    assert report.attempt.submitted is False
    assert "未提供" in report.message


def test_report_never_contains_credentials() -> None:
    transport = FakeTransport(
        get=page_router(),
        post=lambda url, data: response(200, "密码错误".encode("utf-8")),
    )
    gateway = make_gateway(transport, FakeProbe(ok(False), ok(False)))
    report = gateway.sign_in(ACCOUNT, PASSWORD)
    rendered = repr(report) + report.message + report.snapshot.describe()
    assert PASSWORD not in rendered
    assert ACCOUNT not in rendered


def test_survey_separates_three_facts() -> None:
    transport = FakeTransport(get=page_router())
    gateway = make_gateway(transport, FakeProbe(ok(False)))
    snapshot = gateway.survey()
    assert snapshot.wifi_connected is True
    assert snapshot.portal_state is PortalState.LOGIN_REQUIRED
    assert snapshot.internet_available is False


def test_sign_out_requires_success_marker() -> None:
    transport = FakeTransport(get=page_router(logout_ok=True))
    gateway = make_gateway(transport, FakeProbe(ok(False)))
    report = gateway.sign_out()
    assert report.attempt.outcome is AuthOutcome.SUCCESS
    assert report.state is LinkState.AUTH_REQUIRED


def test_sign_out_unknown_not_reported_as_success() -> None:
    transport = FakeTransport(get=page_router(logout_ok=False))
    gateway = make_gateway(transport, FakeProbe(ok(False)))
    report = gateway.sign_out()
    assert report.attempt.outcome is AuthOutcome.UNKNOWN
    assert report.attempt.is_success is False


def test_forget_credentials() -> None:
    store = FakeStore((ACCOUNT, PASSWORD))
    gateway = make_gateway(FakeTransport(get=page_router()), FakeProbe(ok(False)), store)
    gateway.forget_credentials()
    assert store.deleted is True
