"""PySide6 桌面界面与系统托盘。

导入本模块即要求 PySide6 可用；核心层（auth / network / services /
ui.viewmodel）不依赖 Qt，可在无 Qt 环境下完整测试。

线程模型：所有 gateway 调用（探测/登录/注销）都放在后台线程里跑，
结果通过 `_UiBridge.updated` 这个 queued signal 回到 GUI 线程，
因此窗口不会在等待网络时“未响应”。
"""

from __future__ import annotations

import threading
import traceback
from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .viewmodel import AuthViewModel, UiState

STATE_COLORS = {
    "authenticated": "#2e7d32",
    "auth_required": "#ef6c00",
    "signing_in": "#1565c0",
    "checking": "#1565c0",
    "internet_unavailable": "#c62828",
    "auth_failed": "#c62828",
    "disconnected": "#616161",
    "unknown": "#616161",
}

STATE_TITLES = {
    "unknown": "未检测",
    "disconnected": "未连接校园网",
    "checking": "检测中…",
    "auth_required": "需要认证",
    "signing_in": "登录中…",
    "authenticated": "认证成功",
    "internet_unavailable": "互联网不可用",
    "auth_failed": "认证失败",
}

DISCLAIMER = (
    "提示：本工具依据的认证接口可能以明文传输密码，本地凭据保护无法消除这一协议风险。\n"
    "自动恢复联网也会恢复后台应用的数据传输；部分校园网按流量计费，请自行留意费用与流量。"
)


def build_icon(color: str = "#1565c0") -> QIcon:
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor(color))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(4, 4, 24, 24)
    painter.end()
    return QIcon(pixmap)


class _UiBridge(QObject):
    """把后台线程的回调转到 GUI 线程。"""

    updated = Signal(object)


class MainWindow(QWidget):
    def __init__(
        self,
        viewmodel: AuthViewModel,
        *,
        on_quit: Callable[[], None] | None = None,
        on_login_finished: Callable[[UiState], None] | None = None,
        close_to_tray: bool = True,
    ) -> None:
        super().__init__()
        self._vm = viewmodel
        self._on_quit = on_quit
        self._on_login_finished = on_login_finished
        self._close_to_tray = close_to_tray
        self._bridge = _UiBridge()
        self._bridge.updated.connect(self._apply_state)
        self._action_in_flight = False
        self._clear_password_on_success = False

        self.setWindowTitle("CampusLink · 校园网自动连接助手")
        self.setMinimumWidth(520)
        self._build_ui()
        self._vm.subscribe(self._on_vm_change)
        self._apply_state(self._vm.state)
        self._prefill_account()
        QTimer.singleShot(250, self._do_refresh)

    # ---- 布局 -----------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        self.status_label = QLabel("尚未检测")
        font = self.status_label.font()
        font.setPointSize(font.pointSize() + 3)
        font.setBold(True)
        self.status_label.setFont(font)
        root.addWidget(self.status_label)

        self.detail_label = QLabel("—")
        self.detail_label.setWordWrap(True)
        self.detail_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self.detail_label)

        cred_box = QGroupBox("账号")
        form = QFormLayout(cred_box)
        self.account_edit = QLineEdit()
        self.account_edit.setPlaceholderText("账号")
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.password_edit.setPlaceholderText("校园网密码")
        self.remember_check = QCheckBox("保存到 Windows 凭据管理器（默认不保存）")
        form.addRow("账号", self.account_edit)
        form.addRow("密码", self.password_edit)
        form.addRow("", self.remember_check)
        root.addWidget(cred_box)

        buttons = QHBoxLayout()
        self.login_button = QPushButton("一键登录")
        self.login_button.clicked.connect(self._do_login)
        self.refresh_button = QPushButton("检测状态")
        self.refresh_button.clicked.connect(self._do_refresh)
        self.logout_button = QPushButton("注销")
        self.logout_button.clicked.connect(self._do_logout)
        self.forget_button = QPushButton("清除已保存凭据")
        self.forget_button.clicked.connect(self._do_forget)
        for button in (
            self.login_button,
            self.refresh_button,
            self.logout_button,
            self.forget_button,
        ):
            buttons.addWidget(button)
        root.addLayout(buttons)

        opts = QGroupBox("可选功能（默认全部关闭）")
        opts_layout = QVBoxLayout(opts)
        self.autoreconnect_check = QCheckBox("掉线时自动重连（带退避与失败上限，可随时暂停）")
        self.autoreconnect_check.toggled.connect(self._on_autoreconnect_toggled)
        self.autostart_check = QCheckBox("开机自启（写入当前用户 Run 项，可随时关闭）")
        self.autostart_check.toggled.connect(self._on_autostart_toggled)
        opts_layout.addWidget(self.autoreconnect_check)
        opts_layout.addWidget(self.autostart_check)
        warn = QLabel(
            "自动恢复互联网连接可能同时恢复后台应用的数据传输；"
            "部分校园网按流量计费，请留意流量与费用。"
        )
        warn.setWordWrap(True)
        warn.setStyleSheet("color: #b26a00;")
        opts_layout.addWidget(warn)
        root.addWidget(opts)

        self.disclaimer_label = QLabel(DISCLAIMER)
        self.disclaimer_label.setWordWrap(True)
        self.disclaimer_label.setStyleSheet("color: #777;")
        root.addWidget(self.disclaimer_label)

    # ---- 状态渲染 -------------------------------------------------

    def _on_vm_change(self, state: UiState) -> None:
        # 可能在后台线程被调用；交给 queued signal 回 GUI 线程。
        self._bridge.updated.emit(state)

    def _apply_state(self, state: UiState) -> None:
        color = STATE_COLORS.get(state.state.value, "#616161")
        self.status_label.setText(STATE_TITLES.get(state.state.value, state.state.value))
        self.status_label.setStyleSheet("color: " + color + ";")
        self.detail_label.setText(state.message or "—")
        busy = bool(state.busy)
        if not busy:
            self._action_in_flight = False
            if self._clear_password_on_success:
                self._clear_password_on_success = False
                if state.state.value == "authenticated":
                    self.password_edit.clear()
        enabled = not busy
        self.login_button.setEnabled(enabled)
        self.refresh_button.setEnabled(enabled)
        self.logout_button.setEnabled(enabled)
        self.forget_button.setEnabled(enabled)
        if self.autoreconnect_check.isChecked() != state.auto_reconnect_enabled:
            self.autoreconnect_check.blockSignals(True)
            self.autoreconnect_check.setChecked(state.auto_reconnect_enabled)
            self.autoreconnect_check.blockSignals(False)
        if self.autostart_check.isChecked() != state.autostart_enabled:
            self.autostart_check.blockSignals(True)
            self.autostart_check.setChecked(state.autostart_enabled)
            self.autostart_check.blockSignals(False)

    # ---- 后台动作 -------------------------------------------------

    def _run_async(self, action: Callable[[], object]) -> None:
        if self._action_in_flight:
            return
        self._action_in_flight = True
        for button in (
            self.login_button,
            self.refresh_button,
            self.logout_button,
            self.forget_button,
        ):
            button.setEnabled(False)

        def runner() -> None:
            try:
                action()
            except Exception as exc:  # 最后一道兜底，绝不让按钮永久卡死
                traceback.print_exc()
                try:
                    self._vm.report_error("界面操作失败：" + type(exc).__name__)
                except Exception:
                    pass

        threading.Thread(
            target=runner, name="campuslink-ui-action", daemon=True
        ).start()

    # ---- 动作 -----------------------------------------------------

    def _prefill_account(self) -> None:
        account = self._vm.load_saved_account()
        if account:
            self.account_edit.setText(account)
            self.detail_label.setText("已读取保存的账号：" + account[:2] + "***")

    def _do_refresh(self) -> None:
        self._run_async(self._vm.refresh)

    def _do_login(self) -> None:
        account = self.account_edit.text().strip()
        password = self.password_edit.text()
        remember = self.remember_check.isChecked()
        if not account or not password:
            # 输入不完整：同步给出提示，不需要起线程。
            self._vm.sign_in(account, password, remember=remember)
            return
        self._clear_password_on_success = True

        def action() -> None:
            state = self._vm.sign_in(account, password, remember=remember)
            if self._on_login_finished is not None:
                try:
                    self._on_login_finished(state)
                except Exception:
                    traceback.print_exc()

        self._run_async(action)

    def _do_logout(self) -> None:
        answer = QMessageBox.question(
            self,
            "确认注销",
            "注销会中断当前校园网连接，后台应用将无法继续联网。确定注销吗？",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._run_async(self._vm.sign_out)

    def _do_forget(self) -> None:
        try:
            self._vm.forget_credentials()
        finally:
            self.password_edit.clear()

    def _on_autoreconnect_toggled(self, checked: bool) -> None:
        self._vm.set_auto_reconnect(checked)

    def _on_autostart_toggled(self, checked: bool) -> None:
        if checked:
            answer = QMessageBox.question(
                self,
                "确认开机自启",
                "这会在当前用户下写入一条开机启动项，只有你本人登录 Windows 后生效。确定开启吗？",
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.autostart_check.blockSignals(True)
                self.autostart_check.setChecked(False)
                self.autostart_check.blockSignals(False)
                return
        self._vm.set_autostart(checked)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._close_to_tray:
            event.ignore()
            self.hide()
            return
        event.accept()


class TrayController:
    """系统托盘：最小化、查看状态、手动登录、暂停自动重连、退出。"""

    def __init__(
        self,
        app: QApplication,
        window: MainWindow,
        viewmodel: AuthViewModel,
        *,
        reconnect=None,
        on_quit: Callable[[], None] | None = None,
    ) -> None:
        self._app = app
        self._window = window
        self._vm = viewmodel
        self._reconnect = reconnect
        self._on_quit = on_quit
        self._bridge = _UiBridge()
        self._bridge.updated.connect(self._apply_state)

        self.tray = QSystemTrayIcon(build_icon(), app)
        self.tray.setToolTip("CampusLink")
        menu = QMenu()

        show_action = QAction("显示主窗口", menu)
        show_action.triggered.connect(self._show_window)
        menu.addAction(show_action)

        self.status_action = QAction("状态：未检测", menu)
        self.status_action.setEnabled(False)
        menu.addAction(self.status_action)

        login_action = QAction("手动登录", menu)
        login_action.triggered.connect(self._manual_login)
        menu.addAction(login_action)

        self.pause_action = QAction("暂停自动重连", menu)
        self.pause_action.setCheckable(True)
        self.pause_action.triggered.connect(self._toggle_pause)
        menu.addAction(self.pause_action)

        menu.addSeparator()
        quit_action = QAction("退出", menu)
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_activated)
        self.tray.show()

        self._vm.subscribe(self._on_vm_change)
        self._apply_state(self._vm.state)

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._show_window()

    def _show_window(self) -> None:
        self._window.show()
        self._window.raise_()
        self._window.activateWindow()

    def _on_vm_change(self, state: UiState) -> None:
        self._bridge.updated.emit(state)

    def _apply_state(self, state: UiState) -> None:
        self.status_action.setText("状态：" + (state.message or state.state.value))
        color = STATE_COLORS.get(state.state.value, "#1565c0")
        self.tray.setIcon(build_icon(color))
        title = STATE_TITLES.get(state.state.value, state.state.value)
        self.tray.setToolTip("CampusLink · " + title)
        paused = False
        if self._reconnect is not None:
            try:
                paused = self._reconnect.supervisor.controller.paused
            except Exception:
                paused = False
        self.pause_action.setEnabled(bool(state.auto_reconnect_enabled))
        self.pause_action.blockSignals(True)
        self.pause_action.setChecked(paused)
        self.pause_action.blockSignals(False)
        self.pause_action.setText("恢复自动重连" if paused else "暂停自动重连")

    def _manual_login(self) -> None:
        self._show_window()
        if self._window.account_edit.text().strip() and self._window.password_edit.text():
            self._window._do_login()  # noqa: SLF001 - 托盘与窗口共用同一动作
        else:
            # 密码框已被清空时不能直接“登录”，否则只会得到一条“请输入账号和密码”。
            self._window.password_edit.setFocus()

    def _toggle_pause(self, checked: bool) -> None:
        if self._reconnect is None:
            return
        if checked:
            self._reconnect.pause()
        else:
            self._reconnect.resume()
        self._apply_state(self._vm.state)

    def _quit(self) -> None:
        if self._on_quit is not None:
            self._on_quit()
        self.tray.hide()
        self._app.quit()
