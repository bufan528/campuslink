"""GUI 端到端（offscreen）：网络动作不得阻塞 GUI 线程，异常不得卡死按钮。"""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from campuslink.auth.models import AuthAttempt, AuthOutcome, PortalState  # noqa: E402
from campuslink.config.settings import AppSettings  # noqa: E402
from campuslink.network.status import LinkState, NetworkSnapshot  # noqa: E402
from campuslink.services.gateway import SignInReport  # noqa: E402
from campuslink.ui.qt_app import MainWindow  # noqa: E402
from campuslink.ui.viewmodel import AuthViewModel  # noqa: E402

SNAPSHOT = NetworkSnapshot(
    wifi_connected=True,
    wifi_ssid="campus-wlan",
    portal_state=PortalState.LOGIN_REQUIRED,
    internet_available=False,
    portal_interception=False,
    checked_at=0.0,
)


class FakeGateway:
    def __init__(self, *, delay: float = 0.0, fail_refresh: bool = False) -> None:
        self.delay = delay
        self.fail_refresh = fail_refresh
        self.sign_in_result = SignInReport(
            attempt=AuthAttempt(
                outcome=AuthOutcome.SUCCESS, message="认证成功", submitted=True
            ),
            snapshot=SNAPSHOT,
            state=LinkState.AUTHENTICATED,
        )
        self.stored: tuple[str, str] | None = None
        self.settings = AppSettings()

    def _wait(self) -> None:
        if self.delay:
            time.sleep(self.delay)

    def current_state(self):
        self._wait()
        if self.fail_refresh:
            raise RuntimeError("boom")
        return (LinkState.AUTH_REQUIRED, SNAPSHOT)

    def sign_in(self, account=None, password=None, *, save_credentials: bool = False):
        self._wait()
        return self.sign_in_result

    def sign_out(self):
        self._wait()
        return SignInReport(
            attempt=AuthAttempt(outcome=AuthOutcome.SUCCESS, message="注销成功", submitted=True),
            snapshot=SNAPSHOT,
            state=LinkState.AUTH_REQUIRED,
        )

    def save_credentials(self, account, password):
        self.stored = (account, password)

    def forget_credentials(self):
        self.stored = None

    def load_credentials(self):
        return self.stored

    def update_settings(self, settings):
        self.settings = settings


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


def wait_for(qt_app, predicate, timeout: float = 4.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    qt_app.processEvents()
    return predicate()


def settle_startup(qt_app, window, seconds: float = 0.3) -> None:
    """让 MainWindow 的 250ms 启动检测跑完，避免与测试动作互相覆盖状态。"""
    time.sleep(seconds)
    wait_for(qt_app, lambda: not window._action_in_flight, timeout=3.0)


def make_window(gateway):
    vm = AuthViewModel(gateway, AppSettings(), persist=lambda s: None)
    window = MainWindow(vm, close_to_tray=False)
    return vm, window


def test_login_does_not_block_gui_thread(qt_app) -> None:
    gateway = FakeGateway(delay=0.4)
    _vm, window = make_window(gateway)
    settle_startup(qt_app, window)
    window.account_edit.setText("00000000")
    window.password_edit.setText("pw")
    started = time.monotonic()
    window._do_login()
    returned_after = time.monotonic() - started
    assert returned_after < 0.25, "登录动作必须在后台线程执行"
    assert wait_for(qt_app, lambda: window.status_label.text() == "认证成功")
    assert window.password_edit.text() == "", "成功后才清空密码"
    assert window.login_button.isEnabled() is True
    window.deleteLater()
    qt_app.processEvents()


def test_failed_login_keeps_password_and_reenables_buttons(qt_app) -> None:
    gateway = FakeGateway()
    gateway.sign_in_result = SignInReport(
        attempt=AuthAttempt(
            outcome=AuthOutcome.INVALID_CREDENTIALS, message="密码错误", submitted=True
        ),
        snapshot=SNAPSHOT,
        state=LinkState.AUTH_FAILED,
    )
    _vm, window = make_window(gateway)
    settle_startup(qt_app, window)
    window.account_edit.setText("00000000")
    window.password_edit.setText("wrong-password")
    window._do_login()
    assert wait_for(qt_app, lambda: window.status_label.text() == "认证失败")
    assert window.password_edit.text() == "wrong-password", "失败时保留密码以便重试"
    assert window.login_button.isEnabled() is True
    assert window.refresh_button.isEnabled() is True
    window.deleteLater()
    qt_app.processEvents()


def test_backend_exception_does_not_leave_ui_busy(qt_app) -> None:
    gateway = FakeGateway(fail_refresh=True)
    _vm, window = make_window(gateway)
    settle_startup(qt_app, window)
    window._do_refresh()
    assert wait_for(qt_app, lambda: window.status_label.text() != "检测中…")
    assert window.login_button.isEnabled() is True
    assert window.refresh_button.isEnabled() is True
    assert "失败" in window.detail_label.text()
    window.deleteLater()
    qt_app.processEvents()

