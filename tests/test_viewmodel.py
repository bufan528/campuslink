"""界面状态与后台认证结果一致（任务书测试要求第 11 条）。"""

from __future__ import annotations

import pytest

from campuslink.auth.models import AuthAttempt, AuthOutcome, PortalState
from campuslink.config.settings import AppSettings
from campuslink.network.status import LinkState, NetworkSnapshot
from campuslink.services.gateway import SignInReport
from campuslink.ui.viewmodel import AuthViewModel

SNAPSHOT = NetworkSnapshot(
    wifi_connected=True,
    wifi_ssid="campus-wlan",
    portal_state=PortalState.LOGIN_REQUIRED,
    internet_available=False,
    portal_interception=False,
    checked_at=0.0,
)


def report(outcome: AuthOutcome, state: LinkState, message: str = "msg") -> SignInReport:
    return SignInReport(
        attempt=AuthAttempt(outcome=outcome, message=message, submitted=True),
        snapshot=SNAPSHOT,
        state=state,
    )


class FakeGateway:
    def __init__(self, sign_in_report=None, sign_out_report=None, state=LinkState.AUTH_REQUIRED,
                 stored=None) -> None:
        self._sign_in = sign_in_report or report(AuthOutcome.SUCCESS, LinkState.AUTHENTICATED)
        self._sign_out = sign_out_report or report(AuthOutcome.SUCCESS, LinkState.AUTH_REQUIRED)
        self._state = state
        self._stored = stored
        self.calls: list[tuple] = []
        self.forgot = False

    def current_state(self):
        return (self._state, SNAPSHOT)

    def sign_in(self, account=None, password=None, *, save_credentials: bool = False):
        self.calls.append(("sign_in", account, password, save_credentials))
        return self._sign_in

    def sign_out(self):
        self.calls.append(("sign_out",))
        return self._sign_out

    def save_credentials(self, account, password):
        self._stored = (account, password)

    def forget_credentials(self):
        self.forgot = True
        self._stored = None

    def load_credentials(self):
        return self._stored


class FakeAutostart:
    def __init__(self) -> None:
        self.enabled = False
        self.calls: list[bool] = []

    def set_enabled(self, enabled: bool) -> None:
        self.calls.append(enabled)
        self.enabled = enabled


def make(gateway, settings=None, autostart=None, persist=None):
    return AuthViewModel(
        gateway,
        settings or AppSettings(),
        autostart=autostart,
        persist=persist,
    )


def test_default_state_matches_settings() -> None:
    vm = make(FakeGateway())
    assert vm.state.state is LinkState.UNKNOWN
    assert vm.state.auto_reconnect_enabled is False
    assert vm.state.autostart_enabled is False


def test_empty_credentials_do_not_call_backend() -> None:
    gateway = FakeGateway()
    vm = make(gateway)
    state = vm.sign_in("", "")
    assert state.state is LinkState.AUTH_FAILED
    assert gateway.calls == []


@pytest.mark.parametrize(
    "backend_state",
    [
        LinkState.AUTHENTICATED,
        LinkState.INTERNET_UNAVAILABLE,
        LinkState.AUTH_FAILED,
        LinkState.AUTH_REQUIRED,
        LinkState.DISCONNECTED,
    ],
)
def test_ui_state_equals_backend_result(backend_state: LinkState) -> None:
    gateway = FakeGateway(sign_in_report=report(AuthOutcome.SUCCESS, backend_state))
    vm = make(gateway)
    state = vm.sign_in("00000000", "pw")
    assert state.state is backend_state


def test_success_message_overridden_when_internet_unavailable() -> None:
    gateway = FakeGateway(
        sign_in_report=report(AuthOutcome.SUCCESS, LinkState.INTERNET_UNAVAILABLE)
    )
    vm = make(gateway)
    state = vm.sign_in("00000000", "pw")
    assert "外网仍不可用" in state.message


def test_unknown_outcome_message_marks_unconfirmed() -> None:
    gateway = FakeGateway(sign_in_report=report(AuthOutcome.UNKNOWN, LinkState.AUTH_FAILED))
    vm = make(gateway)
    state = vm.sign_in("00000000", "pw")
    assert "认证结果未确认" in state.message


def test_remember_flag_forwarded() -> None:
    gateway = FakeGateway()
    vm = make(gateway)
    vm.sign_in("00000000", "pw", remember=True)
    assert gateway.calls[0][3] is True


def test_busy_flag_cleared_after_login() -> None:
    vm = make(FakeGateway())
    state = vm.sign_in("00000000", "pw")
    assert state.busy is False


def test_account_hint_is_masked() -> None:
    vm = make(FakeGateway())
    state = vm.sign_in("00000000", "pw")
    assert state.account_hint == "00***"


def test_auto_reconnect_toggle_persists() -> None:
    saved: list[AppSettings] = []
    vm = make(FakeGateway(), persist=saved.append)
    state = vm.set_auto_reconnect(True)
    assert state.auto_reconnect_enabled is True
    assert saved and saved[-1].auto_reconnect_enabled is True
    assert vm.settings.auto_reconnect_enabled is True


def test_autostart_toggle_calls_backend() -> None:
    autostart = FakeAutostart()
    saved: list[AppSettings] = []
    vm = make(FakeGateway(), autostart=autostart, persist=saved.append)
    state = vm.set_autostart(True)
    assert autostart.calls == [True]
    assert state.autostart_enabled is True
    assert saved[-1].autostart_enabled is True
    vm.set_autostart(False)
    assert autostart.calls == [True, False]


def test_refresh_sets_checking_then_final() -> None:
    vm = make(FakeGateway(state=LinkState.AUTH_REQUIRED))
    seen: list[LinkState] = []
    vm.subscribe(lambda state: seen.append(state.state))
    vm.refresh()
    assert LinkState.CHECKING in seen
    assert vm.state.state is LinkState.AUTH_REQUIRED


def test_forget_credentials_clears_hint() -> None:
    gateway = FakeGateway(stored=("00000000", "pw"))
    vm = make(gateway)
    state = vm.forget_credentials()
    assert gateway.forgot is True
    assert state.account_hint == ""


def test_load_saved_account() -> None:
    vm = make(FakeGateway(stored=("00000000", "pw")))
    assert vm.load_saved_account() == "00000000"
    assert make(FakeGateway()).load_saved_account() == ""


def test_sign_out_reports_backend_state() -> None:
    gateway = FakeGateway(sign_out_report=report(AuthOutcome.SUCCESS, LinkState.AUTH_REQUIRED))
    vm = make(gateway)
    state = vm.sign_out()
    assert state.state is LinkState.AUTH_REQUIRED
    assert "注销" in state.message


def test_backend_exception_clears_busy_and_reports() -> None:
    class BoomGateway(FakeGateway):
        def current_state(self):
            raise RuntimeError("boom")

    vm = make(BoomGateway())
    state = vm.refresh()
    assert state.busy is False
    assert state.state is LinkState.UNKNOWN
    assert "失败" in state.message


def test_sign_in_exception_clears_busy() -> None:
    class BoomGateway(FakeGateway):
        def sign_in(self, *args, **kwargs):
            raise RuntimeError("boom")

    vm = make(BoomGateway())
    state = vm.sign_in("00000000", "pw")
    assert state.busy is False
    assert state.state is LinkState.AUTH_FAILED
    assert "登录失败" in state.message


def test_autostart_failure_rolls_back_state() -> None:
    class DeniedAutostart:
        def set_enabled(self, enabled):
            raise PermissionError("denied")

    saved: list[AppSettings] = []
    vm = make(FakeGateway(), autostart=DeniedAutostart(), persist=saved.append)
    state = vm.set_autostart(True)
    assert state.autostart_enabled is False
    assert "失败" in state.message
    assert saved == []


def test_auto_reconnect_change_callback_called() -> None:
    calls: list[bool] = []
    vm = AuthViewModel(
        FakeGateway(),
        AppSettings(),
        persist=lambda s: None,
        on_auto_reconnect_change=calls.append,
    )
    vm.set_auto_reconnect(True)
    vm.set_auto_reconnect(False)
    assert calls == [True, False]


def test_save_failure_notice_is_shown() -> None:
    report_obj = SignInReport(
        attempt=AuthAttempt(outcome=AuthOutcome.SUCCESS, message="认证成功", submitted=True),
        snapshot=SNAPSHOT,
        state=LinkState.AUTHENTICATED,
        notice="凭据未保存：模拟失败",
    )
    vm = make(FakeGateway(sign_in_report=report_obj))
    state = vm.sign_in("00000000", "pw", remember=True)
    assert "凭据未保存" in state.message


def test_apply_auto_reconnect_updates_message() -> None:
    from campuslink.services.reconnect_loop import TickOutcome

    vm = make(FakeGateway())
    state = vm.apply_auto_reconnect(
        TickOutcome(LinkState.AUTH_REQUIRED, False, 60.0, "未提交凭据")
    )
    assert state.state is LinkState.AUTH_REQUIRED
    assert "自动重连" in state.message


def test_listener_errors_do_not_break_updates() -> None:
    vm = make(FakeGateway())

    def bad(_state):
        raise RuntimeError("listener boom")

    vm.subscribe(bad)
    state = vm.refresh()
    assert state.busy is False
