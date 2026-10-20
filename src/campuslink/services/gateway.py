"""认证编排服务：不依赖 GUI，可被界面、托盘和测试直接调用。

安全不变量（逐条对应任务书要求）：
* 提交凭据前必须拿到"可识别"的登录页，否则不提交；
* 认证主机不可达时绝不重复提交；
* 只有独立连通性探测通过，才把状态标为"认证成功"；
* 任何返回给界面的文本都不含密码、账号完整值或 Cookie。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Protocol

from ..auth.adapter import PortalAuthAdapter
from ..auth.errors import CampusLinkError, CredentialStoreError, CredentialsMissing
from ..auth.models import AuthAttempt, AuthOutcome, PortalState
from ..config.settings import AppSettings
from ..logging_setup.logger import get_logger
from ..logging_setup.redact import DEFAULT_REDACTOR, Redactor, mask_account
from ..network.probe import ConnectivityProbe
from ..network.status import (
    LinkState,
    NetworkSnapshot,
    derive_link_state,
    is_campus_wifi,
)
from ..network.wifi import WifiInspector

_log = get_logger("services.gateway")


class CredentialStoreLike(Protocol):
    def save(self, account: str, password: str) -> None: ...

    def load(self) -> tuple[str, str] | None: ...

    def delete(self) -> None: ...


class AdapterFactory(Protocol):
    def __call__(self, host: str, *, timeout: float, login_path: str) -> PortalAuthAdapter: ...


@dataclass(frozen=True, slots=True)
class SignInReport:
    """一次登录操作的结果，界面只看这个。"""

    attempt: AuthAttempt
    snapshot: NetworkSnapshot
    state: LinkState
    notice: str = ""

    @property
    def message(self) -> str:
        return self.attempt.message


def _default_adapter_factory(
    host: str, *, timeout: float, login_path: str
) -> PortalAuthAdapter:
    return PortalAuthAdapter(host, timeout=timeout, login_path=login_path)


class AuthGateway:
    """认证网关：编排探测、提交、校验。"""

    def __init__(
        self,
        settings: AppSettings,
        credential_store: CredentialStoreLike,
        *,
        adapter_factory: AdapterFactory | None = None,
        probe: ConnectivityProbe | None = None,
        wifi: WifiInspector | None = None,
        redactor: Redactor = DEFAULT_REDACTOR,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._settings = settings
        self._store = credential_store
        self._adapter_factory = adapter_factory or _default_adapter_factory
        self._probe = probe or ConnectivityProbe(timeout=settings.probe_timeout_s)
        self._wifi = wifi or WifiInspector()
        self._redactor = redactor
        self._clock = clock
        # 自动重连线程与界面线程会同时调用同一个 gateway；串行化每次认证/探测，
        # 避免“点登录的同时后台又提交一次”导致重复认证。
        self._lock = threading.RLock()

    @property
    def settings(self) -> AppSettings:
        return self._settings

    def update_settings(self, settings: AppSettings) -> None:
        self._settings = settings

    def _make_adapter(self) -> PortalAuthAdapter:
        return self._adapter_factory(
            self._settings.portal_host,
            timeout=self._settings.http_timeout_s,
            login_path=self._settings.login_path,
        )

    # ---- 状态检测 -------------------------------------------------

    def survey(self) -> NetworkSnapshot:
        """只读探测：不提交任何凭据。"""
        with self._lock:
            wifi_status = self._wifi.current_status()
            connected = wifi_status.matches(self._settings.wifi_ssid_candidates)
            adapter = self._make_adapter()
            try:
                portal_state = adapter.probe_state()
            finally:
                adapter.close()
            probe_result = self._probe.check()
            return NetworkSnapshot(
                wifi_connected=connected,
                wifi_ssid=wifi_status.ssid,
                portal_state=portal_state,
                internet_available=probe_result.internet_available,
                portal_interception=probe_result.portal_interception,
                checked_at=self._clock(),
                detail=probe_result.detail,
            )

    def current_state(self) -> tuple[LinkState, NetworkSnapshot]:
        snapshot = self.survey()
        return derive_link_state(snapshot), snapshot

    # ---- 登录 -----------------------------------------------------

    def sign_in(
        self,
        account: str | None = None,
        password: str | None = None,
        *,
        save_credentials: bool = False,
    ) -> SignInReport:
        with self._lock:
            return self._sign_in_locked(account, password, save_credentials=save_credentials)

    def _sign_in_locked(
        self,
        account: str | None,
        password: str | None,
        *,
        save_credentials: bool,
    ) -> SignInReport:
        account, password = self._resolve_credentials(account, password)
        if not account or not password:
            attempt = AuthAttempt(
                outcome=AuthOutcome.UNKNOWN,
                message="未提供账号或密码，未发起认证。",
                submitted=False,
            )
            return SignInReport(attempt, self.survey(), LinkState.AUTH_FAILED)

        self._redactor.add(account)
        self._redactor.add(password)

        # 先看是否本来就通；通了就不去打扰认证服务器。
        probe_result = self._probe.check()
        adapter = self._make_adapter()
        try:
            if probe_result.internet_available:
                portal_state = adapter.probe_state()
                if portal_state is PortalState.ONLINE:
                    notice = self._save_credentials(account, password, save_credentials)
                    attempt = AuthAttempt(
                        outcome=AuthOutcome.ALREADY_ONLINE,
                        message="当前已在线，未重复提交认证。",
                        submitted=False,
                    )
                    snapshot = self._snapshot_from(adapter, probe_result)
                    return SignInReport(
                        attempt, snapshot, LinkState.AUTHENTICATED, notice=notice
                    )

            attempt = adapter.login(account, password)
            notice = ""
            if attempt.is_success:
                notice = self._save_credentials(account, password, save_credentials)

            # 独立的联网验证：绝不因为 POST 返回 200 就宣称成功。
            verify = self._probe.check()
            snapshot = self._snapshot_from(adapter, verify)
            state = self._state_after_attempt(attempt, snapshot, verify.internet_available)
            _log.info(
                "登录结束 outcome=%s state=%s submitted=%s",
                attempt.outcome.value,
                state.value,
                attempt.submitted,
            )
            return SignInReport(attempt, snapshot, state, notice=notice)
        except CampusLinkError as exc:
            attempt = AuthAttempt(
                outcome=AuthOutcome.NETWORK_ERROR,
                message=str(exc),
                submitted=False,
            )
            return SignInReport(attempt, self.survey(), LinkState.AUTH_FAILED)
        finally:
            adapter.close()

    def _save_credentials(
        self, account: str, password: str, save_credentials: bool
    ) -> str:
        """按用户要求保存凭据；失败时返回给界面看的说明（不含凭据）。"""
        if not save_credentials:
            return ""
        try:
            self._store.save(account, password)
        except CredentialStoreError as exc:
            _log.warning("保存凭据失败：%s", exc)
            return "凭据未保存：" + str(exc)
        except Exception as exc:  # 未知实现/系统错误，也不能把密码带出去
            _log.warning("保存凭据失败：%s", type(exc).__name__)
            return "凭据未保存（系统错误 " + type(exc).__name__ + "）"
        _log.info("凭据已保存到系统凭据管理器 account=%s", mask_account(account))
        return ""

    def _snapshot_from(self, adapter: PortalAuthAdapter, probe_result) -> NetworkSnapshot:
        wifi_status = self._wifi.current_status()
        try:
            portal_state = adapter.probe_state()
        except CampusLinkError:
            portal_state = PortalState.UNRECOGNIZED
        return NetworkSnapshot(
            wifi_connected=wifi_status.matches(self._settings.wifi_ssid_candidates),
            wifi_ssid=wifi_status.ssid,
            portal_state=portal_state,
            internet_available=probe_result.internet_available,
            portal_interception=probe_result.portal_interception,
            checked_at=self._clock(),
            detail=probe_result.detail,
        )

    @staticmethod
    def _state_after_attempt(
        attempt: AuthAttempt, snapshot: NetworkSnapshot, internet_available: bool
    ) -> LinkState:
        # 认证层已经明确拒绝/失败时，不能因为“互联网碰巧可用”就显示成功：
        # 那可能是多网卡、缓存或探测假阳性，会把“密码错误”盖成“认证成功”。
        if attempt.outcome in {
            AuthOutcome.INVALID_CREDENTIALS,
            AuthOutcome.REJECTED,
            AuthOutcome.PROTOCOL_MISMATCH,
            AuthOutcome.TARGET_REFUSED,
        }:
            return LinkState.AUTH_FAILED
        if attempt.outcome is AuthOutcome.NETWORK_ERROR:
            return (
                LinkState.DISCONNECTED
                if not snapshot.wifi_connected
                else LinkState.INTERNET_UNAVAILABLE
            )
        if internet_available:
            return LinkState.AUTHENTICATED
        if attempt.outcome in {AuthOutcome.SUCCESS, AuthOutcome.ALREADY_ONLINE}:
            # 门户说成功，但外网不通 —— 不能报成功。
            return LinkState.INTERNET_UNAVAILABLE
        return LinkState.AUTH_FAILED

    def _resolve_credentials(
        self, account: str | None, password: str | None
    ) -> tuple[str | None, str | None]:
        if account and password:
            return account, password
        if account or password:
            # 只给了一半就按“不完整的手动输入”处理，不与已保存的另一个值拼接，
            # 避免把 A 的账号和 B 的密码配成一对。
            return account, password
        try:
            stored = self._store.load()
        except CredentialStoreError as exc:
            _log.warning("读取系统凭据失败：%s", exc)
            return None, None
        if stored is None:
            return None, None
        return stored

    # ---- 注销 -----------------------------------------------------

    def sign_out(self) -> SignInReport:
        with self._lock:
            adapter = self._make_adapter()
            try:
                attempt = adapter.logout()
            finally:
                adapter.close()
            snapshot = self.survey()
            # 只有命中注销成功标记才敢说已注销；其余一律按实际探测结果展示。
            if attempt.outcome is AuthOutcome.SUCCESS:
                state = LinkState.AUTH_REQUIRED
            else:
                state = derive_link_state(snapshot)
            _log.info("注销结束 outcome=%s", attempt.outcome.value)
            return SignInReport(attempt, snapshot, state)

    # ---- 凭据管理 -------------------------------------------------

    def save_credentials(self, account: str, password: str) -> None:
        if not account or not password:
            raise CredentialsMissing("账号或密码为空，未保存。")
        with self._lock:
            self._store.save(account, password)

    def load_credentials(self) -> tuple[str, str] | None:
        with self._lock:
            return self._store.load()

    def forget_credentials(self) -> None:
        self._store.delete()
