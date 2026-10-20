"""视图模型：Qt 无关的状态机。

把"后台认证结果"映射成"界面状态"，因此任务书要求第 11 条
（GUI 状态与后台认证结果一致）可以完全用 pytest 验证，不需要装 Qt。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Protocol

from ..auth.models import AuthAttempt, AuthOutcome
from ..config.settings import AppSettings
from ..logging_setup.logger import get_logger
from ..logging_setup.redact import mask_account
from ..network.status import STATE_LABELS, LinkState, NetworkSnapshot

_log = get_logger("ui.viewmodel")


class GatewayPort(Protocol):
    def current_state(self): ...

    def sign_in(self, account=None, password=None, *, save_credentials: bool = False): ...

    def sign_out(self): ...

    def save_credentials(self, account: str, password: str) -> None: ...

    def forget_credentials(self) -> None: ...

    def load_credentials(self): ...

    def update_settings(self, settings: AppSettings) -> None: ...


@dataclass(frozen=True, slots=True)
class UiState:
    state: LinkState = LinkState.UNKNOWN
    message: str = "尚未检测"
    account_hint: str = ""
    snapshot: NetworkSnapshot | None = None
    auto_reconnect_enabled: bool = False
    autostart_enabled: bool = False
    busy: bool = False


def _label(state: LinkState) -> str:
    return STATE_LABELS.get(state, state.value)


class AuthViewModel:
    """所有界面动作都走这里，保证界面与业务一致。"""

    def __init__(
        self,
        gateway: GatewayPort,
        settings: AppSettings,
        *,
        autostart=None,
        persist: Callable[[AppSettings], None] | None = None,
        on_change: Callable[[UiState], None] | None = None,
        on_auto_reconnect_change: Callable[[bool], None] | None = None,
    ) -> None:
        self._gateway = gateway
        self._settings = settings
        self._autostart = autostart
        self._persist = persist
        self._on_change = on_change
        self._on_auto_reconnect_change = on_auto_reconnect_change
        self._listeners: list[Callable[[UiState], None]] = []
        self._state = UiState(
            auto_reconnect_enabled=settings.auto_reconnect_enabled,
            autostart_enabled=settings.autostart_enabled,
        )
        if on_change is not None:
            self._listeners.append(on_change)

    @property
    def settings(self) -> AppSettings:
        return self._settings

    @property
    def state(self) -> UiState:
        return self._state

    @property
    def label(self) -> str:
        return _label(self._state.state)

    def subscribe(self, listener: Callable[[UiState], None]) -> None:
        self._listeners.append(listener)

    def _emit(self, state: UiState) -> UiState:
        self._state = state
        for listener in list(self._listeners):
            try:
                listener(state)
            except Exception:
                _log.warning("界面回调异常", exc_info=True)
        return state

    def _patch(self, **changes) -> UiState:
        return self._emit(replace(self._state, **changes))

    def _persist_settings(self, settings: AppSettings) -> None:
        self._settings = settings
        update = getattr(self._gateway, "update_settings", None)
        if callable(update):
            try:
                update(settings)
            except Exception:
                _log.warning("同步设置到认证服务失败", exc_info=True)
        if self._persist is not None:
            try:
                self._persist(settings)
            except Exception:
                _log.warning("保存设置失败", exc_info=True)

    @staticmethod
    def _error_detail(exc: BaseException) -> str:
        from ..auth.errors import CampusLinkError

        if isinstance(exc, CampusLinkError):
            return str(exc)
        return type(exc).__name__

    def _failure_state(
        self, action: str, exc: BaseException, *, state: LinkState | None = None
    ) -> UiState:
        _log.warning("%s失败：%s", action, type(exc).__name__)
        changes: dict = {
            "message": action + "失败：" + self._error_detail(exc),
            "busy": False,
        }
        if state is not None:
            changes["state"] = state
        return self._patch(**changes)

    # ---- 动作 -----------------------------------------------------

    def refresh(self) -> UiState:
        self._patch(state=LinkState.CHECKING, message="正在检测网络状态…", busy=True)
        try:
            state, snapshot = self._gateway.current_state()
        except Exception as exc:
            return self._failure_state("检测", exc, state=LinkState.UNKNOWN)
        message = _label(state)
        if snapshot is not None:
            message = message + "（" + snapshot.describe() + "）"
        return self._patch(state=state, snapshot=snapshot, message=message, busy=False)

    def sign_in(self, account: str, password: str, *, remember: bool = False) -> UiState:
        if not account or not password:
            return self._patch(
                state=LinkState.AUTH_FAILED,
                message="请输入账号和密码。",
                busy=False,
            )
        self._patch(state=LinkState.SIGNING_IN, message="正在登录…", busy=True)
        try:
            report = self._gateway.sign_in(account, password, save_credentials=remember)
        except Exception as exc:
            return self._failure_state("登录", exc, state=LinkState.AUTH_FAILED)
        attempt: AuthAttempt = report.attempt
        message = report.message
        notice = getattr(report, "notice", "")
        if notice:
            message = message + "（" + notice + "）"
        if attempt.outcome is AuthOutcome.SUCCESS and report.state is LinkState.INTERNET_UNAVAILABLE:
            message = "门户报告认证成功，但外网仍不可用；请确认是否需要补充认证。"
        if attempt.outcome is AuthOutcome.UNKNOWN:
            message = "认证结果未确认：" + report.message
            if notice:
                message = message + "（" + notice + "）"
        _log.info(
            "界面登录完成 outcome=%s state=%s account=%s",
            attempt.outcome.value,
            report.state.value,
            mask_account(account),
        )
        return self._patch(
            state=report.state,
            message=message,
            snapshot=report.snapshot,
            account_hint=mask_account(account),
            busy=False,
        )

    def sign_out(self) -> UiState:
        self._patch(state=LinkState.SIGNING_IN, message="正在注销…", busy=True)
        try:
            report = self._gateway.sign_out()
        except Exception as exc:
            return self._failure_state("注销", exc)
        return self._patch(
            state=report.state,
            message="注销：" + report.message,
            snapshot=report.snapshot,
            busy=False,
        )

    def set_auto_reconnect(self, enabled: bool) -> UiState:
        enabled = bool(enabled)
        if self._on_auto_reconnect_change is not None:
            try:
                self._on_auto_reconnect_change(enabled)
            except Exception as exc:
                _log.warning("自动重连开关失败", exc_info=True)
                return self._patch(
                    message="自动重连开关失败：" + self._error_detail(exc),
                    auto_reconnect_enabled=self._state.auto_reconnect_enabled,
                )
        settings = self._settings.with_changes(auto_reconnect_enabled=enabled)
        self._persist_settings(settings)
        _log.info("自动重连开关=%s", enabled)
        return self._patch(auto_reconnect_enabled=enabled)

    def set_autostart(self, enabled: bool) -> UiState:
        enabled = bool(enabled)
        try:
            if self._autostart is not None:
                self._autostart.set_enabled(enabled)
        except Exception as exc:
            _log.warning("设置开机自启失败", exc_info=True)
            # 保持原有状态，界面会把复选框恢复成旧值。
            return self._patch(
                message="设置开机自启失败：" + self._error_detail(exc),
                autostart_enabled=self._state.autostart_enabled,
            )
        settings = self._settings.with_changes(autostart_enabled=enabled)
        self._persist_settings(settings)
        _log.info("开机自启开关=%s", enabled)
        return self._patch(autostart_enabled=enabled)

    def forget_credentials(self) -> UiState:
        try:
            self._gateway.forget_credentials()
        except Exception as exc:
            return self._failure_state("清除凭据", exc)
        _log.info("已清除保存的凭据")
        return self._patch(account_hint="", message="已清除本机保存的凭据。")

    def load_saved_account(self) -> str:
        try:
            stored = self._gateway.load_credentials()
        except Exception:
            return ""
        if not stored:
            return ""
        return stored[0]

    def apply_auto_reconnect(self, outcome) -> UiState:
        """后台自动重连的状态回调（由 GUI 线程转投进来）。"""
        if self._state.busy:
            return self._state
        message = "自动重连：" + str(getattr(outcome, "message", "") or "")
        if getattr(outcome, "state", LinkState.UNKNOWN) is LinkState.UNKNOWN:
            return self._patch(message=message)
        return self._patch(state=outcome.state, message=message, busy=False)

    def report_error(self, message: str) -> UiState:
        """界面线程的最后一道兜底：任何未预期异常都要解除 busy 状态。"""
        _log.warning("界面操作异常：%s", message)
        return self._patch(state=LinkState.UNKNOWN, message=message, busy=False)
