"""自动重连循环。

规则（对应任务书第四节末与第六节）：
* 只在"确实需要认证"时提交凭据；
* 未连校园网 / 认证主机不可达 / 页面不符预期时**只等待，不提交**；
* 退避 + 失败上限，失败到上限就停手等用户手动操作；
* 可随时暂停；
* 暂停/停止后，正在路上的那一步不得再提交或覆盖状态。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Protocol

from ..auth.models import AuthOutcome, PortalState
from ..logging_setup.logger import get_logger
from ..network.reconnect import BackoffController, BackoffPolicy
from ..network.status import LinkState

_log = get_logger("services.reconnect")


class GatewayLike(Protocol):
    def current_state(self): ...

    def sign_in(
        self,
        account=None,
        password=None,
        *,
        save_credentials: bool = False,
        force_reauth: bool = False,
    ): ...


@dataclass(frozen=True, slots=True)
class TickOutcome:
    state: LinkState
    attempted: bool
    next_delay_s: float | None
    message: str = ""


def policy_from_settings(settings) -> BackoffPolicy:
    """把用户配置的退避参数真正接到状态机上（此前一直被忽略）。"""
    return BackoffPolicy(
        initial_s=float(settings.reconnect_initial_delay_s),
        max_s=float(settings.reconnect_max_delay_s),
        max_attempts=int(settings.reconnect_max_attempts),
    )


class ReconnectSupervisor:
    """单步驱动的重连决策器；不自己开线程，便于测试。"""

    def __init__(
        self,
        gateway: GatewayLike,
        policy: BackoffPolicy | None = None,
        *,
        on_state: Callable[[TickOutcome], None] | None = None,
        poll_interval_s: float | None = None,
    ) -> None:
        self._gateway = gateway
        self._controller = BackoffController(policy)
        self._on_state = on_state
        self._last: TickOutcome | None = None
        self._poll_interval_s = poll_interval_s
        self._last_health: tuple | None = None

    @property
    def controller(self) -> BackoffController:
        return self._controller

    @property
    def last(self) -> TickOutcome | None:
        return self._last

    def _poll_delay(self) -> float:
        """在线/不满足提交条件时的轮询间隔；优先用用户配置。"""
        if self._poll_interval_s is not None:
            return float(self._poll_interval_s)
        settings = getattr(self._gateway, "settings", None)
        configured = getattr(settings, "status_poll_interval_s", None)
        if configured is not None:
            try:
                return max(1.0, float(configured))
            except (TypeError, ValueError):
                pass
        return float(self._controller.policy.initial_s)

    def pause(self) -> None:
        self._controller.pause()

    def resume(self) -> None:
        self._controller.resume()

    def stop(self) -> None:
        self._controller.stop()

    def reset(self) -> None:
        self._controller.reset()

    def _stopped_or_paused(self) -> bool:
        return self._controller.paused or self._controller.state == "stopped"

    def _note_health(self, state: LinkState, snapshot) -> None:
        """只在状态变化时写日志，方便事后定位“为什么没自动登录”。"""
        signature = (
            state.value,
            getattr(snapshot, "portal_state", None),
            getattr(snapshot, "internet_available", None),
            getattr(snapshot, "wifi_connected", None),
        )
        if signature == self._last_health:
            return
        self._last_health = signature
        _log.info(
            "自动重连检测 state=%s portal=%s internet=%s wifi=%s detail=%s",
            state.value,
            getattr(getattr(snapshot, "portal_state", None), "value", "-"),
            getattr(snapshot, "internet_available", "-"),
            getattr(snapshot, "wifi_connected", "-"),
            getattr(snapshot, "detail", ""),
        )

    def _finish_with_report(self, report) -> TickOutcome:
        if report.state is LinkState.AUTHENTICATED:
            self._controller.record_success()
            return self._emit(
                TickOutcome(
                    report.state,
                    report.attempt.submitted,
                    self._poll_delay(),
                    report.message,
                )
            )
        if not report.attempt.submitted and report.attempt.outcome is AuthOutcome.UNKNOWN:
            # 多半是没保存凭据（或凭据为空）。这不是“重试失败”，
            # 不能消耗退避预算，否则用户永远等不到手动登录的机会。
            return self._emit(
                TickOutcome(
                    report.state,
                    False,
                    self._poll_delay(),
                    "未提交凭据：" + report.message + "（请手动登录或检查保存的凭据）",
                )
            )
        still_allowed = self._controller.record_failure()
        delay = self._controller.next_delay() if still_allowed else None
        _log.info(
            "自动重连失败 attempt=%s exhausted=%s",
            self._controller.failures,
            self._controller.exhausted,
        )
        return self._emit(
            TickOutcome(
                report.state,
                report.attempt.submitted,
                delay,
                "重连未成功：" + report.message,
            )
        )

    def _emit(self, outcome: TickOutcome) -> TickOutcome:
        self._last = outcome
        if self._on_state is not None:
            try:
                self._on_state(outcome)
            except Exception:
                _log.warning("自动重连状态回调异常", exc_info=True)
        return outcome

    def tick(self) -> TickOutcome:
        if self._controller.paused:
            # 暂停只是空转：必须返回一个等待间隔，否则后台线程会直接退出，
            # 用户点"恢复"时就再也醒不过来了。
            return self._emit(
                TickOutcome(
                    LinkState.UNKNOWN,
                    False,
                    self._poll_delay(),
                    "自动重连已暂停",
                )
            )
        if self._controller.state == "stopped":
            return self._emit(TickOutcome(LinkState.UNKNOWN, False, None, "自动重连已停止"))
        if self._controller.exhausted:
            return self._emit(
                TickOutcome(LinkState.UNKNOWN, False, None, "已达失败次数上限，等待手动操作")
            )

        state, snapshot = self._gateway.current_state()
        self._note_health(state, snapshot)
        if self._stopped_or_paused():
            return self._emit(
                TickOutcome(LinkState.UNKNOWN, False, self._poll_delay(), "自动重连已暂停/停止")
            )

        if state is LinkState.AUTHENTICATED:
            self._controller.record_success()
            return self._emit(
                TickOutcome(state, False, self._poll_delay(), "在线，无需重连")
            )

        if state is LinkState.AUTH_REQUIRED:
            if self._stopped_or_paused():
                return self._emit(
                    TickOutcome(LinkState.UNKNOWN, False, self._poll_delay(), "自动重连已暂停/停止")
                )
            report = self._gateway.sign_in()
            # sign_in 期间用户可能点了暂停/退出；此时不得写回状态或再提交。
            if self._stopped_or_paused():
                return self._emit(
                    TickOutcome(LinkState.UNKNOWN, False, self._poll_delay(), "自动重连已暂停/停止")
                )
            return self._finish_with_report(report)

        if (
            state is LinkState.INTERNET_UNAVAILABLE
            and getattr(snapshot, "portal_state", None) is PortalState.ONLINE
        ):
            # 典型隔夜场景：门户仍把旧会话当作“在线”，但实际外网已断。
            # 仅等待永远不会恢复，必须强制注销后重新提交一次认证。
            if self._stopped_or_paused():
                return self._emit(
                    TickOutcome(LinkState.UNKNOWN, False, self._poll_delay(), "自动重连已暂停/停止")
                )
            _log.info("门户在线但外网不可用，尝试强制重新认证")
            report = self._gateway.sign_in(force_reauth=True)
            if self._stopped_or_paused():
                return self._emit(
                    TickOutcome(LinkState.UNKNOWN, False, self._poll_delay(), "自动重连已暂停/停止")
                )
            return self._finish_with_report(report)

        # 未连接 / 主机不可达 / 页面不符预期 / 结果未确认：不提交凭据，只等下一轮。
        return self._emit(
            TickOutcome(
                state,
                False,
                self._poll_delay(),
                "当前不满足提交条件（" + state.value + "），未提交凭据",
            )
        )


class ReconnectWorker:
    """把 supervisor 放到后台线程里跑；暂停时只是空转等待。"""

    def __init__(
        self,
        supervisor: ReconnectSupervisor,
        *,
        stop_event: threading.Event | None = None,
    ) -> None:
        self._supervisor = supervisor
        self._stop = stop_event or threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            # 线程还活着（可能正卡在一次网络调用里）：清掉停止标记让它继续，
            # 不要再起第二个线程。
            self._stop.clear()
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="campuslink-reconnect", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        # join 超时说明还有一次网络调用在路上。不要假装线程已结束，
        # 否则再次 start() 会启动第二个线程。
        self._thread = thread if thread is not None and thread.is_alive() else None
        self._supervisor.stop()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                outcome = self._supervisor.tick()
            except Exception:
                _log.warning("自动重连单步异常", exc_info=True)
                outcome = None
            delay = outcome.next_delay_s if outcome is not None else 60.0
            if delay is None:
                if self._supervisor.controller.exhausted:
                    # 达到失败上限：停手，但保持线程存活；用户手动登录成功
                    # 或重新开关自动重连后 reset()，下一轮即可恢复。
                    if self._stop.wait(15.0):
                        return
                    continue
                return
            if self._stop.wait(max(delay, 1.0)):
                return


class AutoReconnectController:
    """把 supervisor/worker 的生命周期接给界面开关。

    没有它的时候，GUI 里的“自动重连”复选框只是写配置：开启要重启才生效，
    关闭也停不掉已经在跑的后台线程。
    """

    def __init__(
        self,
        supervisor: ReconnectSupervisor,
        worker: ReconnectWorker,
        *,
        on_change: Callable[[bool], None] | None = None,
    ) -> None:
        self._supervisor = supervisor
        self._worker = worker
        self._on_change = on_change
        self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def supervisor(self) -> ReconnectSupervisor:
        return self._supervisor

    @property
    def worker(self) -> ReconnectWorker:
        return self._worker

    def _notify(self) -> None:
        if self._on_change is not None:
            try:
                self._on_change(self._enabled)
            except Exception:
                _log.warning("自动重连开关回调异常", exc_info=True)

    def set_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled:
            self._supervisor.reset()
            self._worker.start()
        else:
            # 只停线程，不 reset：保持 STOPPED，让正在路上的那一步
            # 在返回后看到“已停止”而不是继续记录结果/提交。
            self._worker.stop()
        self._enabled = enabled
        _log.info("自动重连开关=%s", enabled)
        self._notify()

    def reset(self) -> None:
        """手动登录成功后调用：清零失败计数并确保后台线程在跑。"""
        self._supervisor.reset()
        if self._enabled:
            self._worker.start()

    def on_manual_login(self, state: LinkState) -> None:
        if self._enabled and state is LinkState.AUTHENTICATED:
            self.reset()

    def pause(self) -> None:
        self._supervisor.pause()

    def resume(self) -> None:
        self._supervisor.resume()

    def shutdown(self) -> None:
        self._worker.stop()
        self._supervisor.stop()
