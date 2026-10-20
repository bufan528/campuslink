"""自动重连循环：只在需要认证时提交，暂停可恢复，达上限停手。"""

from __future__ import annotations

import threading

from campuslink.auth.models import AuthAttempt, AuthOutcome, PortalState
from campuslink.network.reconnect import BackoffPolicy
from campuslink.network.status import LinkState, NetworkSnapshot
from campuslink.services.gateway import SignInReport
from campuslink.services.reconnect_loop import ReconnectSupervisor, ReconnectWorker

SNAPSHOT = NetworkSnapshot(
    wifi_connected=True,
    wifi_ssid="campus-wlan",
    portal_state=PortalState.LOGIN_REQUIRED,
    internet_available=False,
    portal_interception=False,
    checked_at=0.0,
)


def make_report(outcome: AuthOutcome, state: LinkState) -> SignInReport:
    return SignInReport(
        attempt=AuthAttempt(outcome=outcome, message="m", submitted=True),
        snapshot=SNAPSHOT,
        state=state,
    )


class FakeGateway:
    def __init__(self, state: LinkState, report: SignInReport | None = None) -> None:
        self.state = state
        self.report = report or make_report(AuthOutcome.SUCCESS, LinkState.AUTHENTICATED)
        self.survey_calls = 0
        self.sign_in_calls = 0

    def current_state(self):
        self.survey_calls += 1
        return (self.state, SNAPSHOT)

    def sign_in(self, account=None, password=None, *, save_credentials: bool = False):
        self.sign_in_calls += 1
        return self.report


def policy() -> BackoffPolicy:
    return BackoffPolicy(initial_s=5, max_s=20, factor=2, jitter_ratio=0.0, max_attempts=3)


def test_online_state_does_not_submit() -> None:
    gateway = FakeGateway(LinkState.AUTHENTICATED)
    outcome = ReconnectSupervisor(gateway, policy()).tick()
    assert outcome.attempted is False
    assert gateway.sign_in_calls == 0
    assert outcome.next_delay_s is not None


def test_auth_required_triggers_login() -> None:
    gateway = FakeGateway(LinkState.AUTH_REQUIRED)
    outcome = ReconnectSupervisor(gateway, policy()).tick()
    assert outcome.attempted is True
    assert gateway.sign_in_calls == 1
    assert outcome.state is LinkState.AUTHENTICATED


def test_states_that_must_not_submit_credentials() -> None:
    for state in (
        LinkState.DISCONNECTED,
        LinkState.INTERNET_UNAVAILABLE,
        LinkState.UNKNOWN,
    ):
        gateway = FakeGateway(state)
        outcome = ReconnectSupervisor(gateway, policy()).tick()
        assert outcome.attempted is False, state
        assert gateway.sign_in_calls == 0, state
        assert "未提交凭据" in outcome.message


def test_failures_back_off_and_then_give_up() -> None:
    gateway = FakeGateway(
        LinkState.AUTH_REQUIRED,
        make_report(AuthOutcome.INVALID_CREDENTIALS, LinkState.AUTH_FAILED),
    )
    supervisor = ReconnectSupervisor(gateway, policy())
    delays = []
    for _ in range(4):
        outcome = supervisor.tick()
        delays.append(outcome.next_delay_s)
    assert delays[0] == 5
    assert delays[1] == 10
    assert delays[2] is None  # max_attempts=3 -> 第三次失败后停手
    assert supervisor.controller.exhausted is True
    assert gateway.sign_in_calls == 3


def test_pause_idles_without_exiting() -> None:
    gateway = FakeGateway(LinkState.AUTH_REQUIRED)
    supervisor = ReconnectSupervisor(gateway, policy())
    supervisor.pause()
    outcome = supervisor.tick()
    assert outcome.attempted is False
    assert outcome.next_delay_s is not None, "暂停必须给出等待间隔，否则后台线程会退出"
    assert gateway.sign_in_calls == 0
    supervisor.resume()
    assert supervisor.tick().attempted is True


def test_stopped_returns_no_delay() -> None:
    gateway = FakeGateway(LinkState.AUTH_REQUIRED)
    supervisor = ReconnectSupervisor(gateway, policy())
    supervisor.stop()
    assert supervisor.tick().next_delay_s is None


def test_worker_starts_and_stops_cleanly() -> None:
    gateway = FakeGateway(LinkState.AUTHENTICATED)
    supervisor = ReconnectSupervisor(gateway, policy())
    worker = ReconnectWorker(supervisor, stop_event=threading.Event())
    worker.start()
    worker.stop(timeout=5.0)
    assert gateway.survey_calls >= 1


def test_missing_credentials_do_not_consume_retry_budget() -> None:
    gateway = FakeGateway(
        LinkState.AUTH_REQUIRED,
        make_report(AuthOutcome.UNKNOWN, LinkState.AUTH_FAILED),
    )
    gateway.report = SignInReport(
        attempt=AuthAttempt(
            outcome=AuthOutcome.UNKNOWN,
            message="未提供账号或密码，未发起认证。",
            submitted=False,
        ),
        snapshot=SNAPSHOT,
        state=LinkState.AUTH_FAILED,
    )
    supervisor = ReconnectSupervisor(gateway, policy())
    outcome = supervisor.tick()
    assert outcome.attempted is False
    assert outcome.next_delay_s is not None
    assert supervisor.controller.failures == 0
    assert "未提交凭据" in outcome.message


def test_poll_interval_comes_from_gateway_settings() -> None:
    from types import SimpleNamespace

    gateway = FakeGateway(LinkState.AUTHENTICATED)
    gateway.settings = SimpleNamespace(status_poll_interval_s=42.0)
    supervisor = ReconnectSupervisor(gateway, policy())
    assert supervisor.tick().next_delay_s == 42.0


def test_pause_during_survey_prevents_submission() -> None:
    gateway = FakeGateway(LinkState.AUTH_REQUIRED)
    supervisor = ReconnectSupervisor(gateway, policy())

    def pause_and_return():
        supervisor.pause()
        return (LinkState.AUTH_REQUIRED, SNAPSHOT)

    gateway.current_state = pause_and_return  # type: ignore[assignment]
    outcome = supervisor.tick()
    assert outcome.attempted is False
    assert gateway.sign_in_calls == 0
    assert supervisor.controller.paused is True


def test_exhausted_can_be_reset_and_retried() -> None:
    gateway = FakeGateway(
        LinkState.AUTH_REQUIRED,
        make_report(AuthOutcome.INVALID_CREDENTIALS, LinkState.AUTH_FAILED),
    )
    supervisor = ReconnectSupervisor(gateway, policy())
    for _ in range(3):
        supervisor.tick()
    assert supervisor.controller.exhausted is True
    supervisor.reset()
    assert supervisor.tick().attempted is True


def test_on_state_callback_receives_outcomes() -> None:
    seen = []
    gateway = FakeGateway(LinkState.AUTHENTICATED)
    ReconnectSupervisor(gateway, policy(), on_state=seen.append).tick()
    assert len(seen) == 1
