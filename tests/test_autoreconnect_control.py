"""自动重连控制器：GUI 开关必须真的启停后台线程。"""

from __future__ import annotations

from campuslink.network.status import LinkState
from campuslink.services.reconnect_loop import AutoReconnectController


class FakeSupervisor:
    def __init__(self) -> None:
        self.resets = 0
        self.pauses = 0
        self.resumes = 0
        self.stops = 0

    def reset(self) -> None:
        self.resets += 1

    def pause(self) -> None:
        self.pauses += 1

    def resume(self) -> None:
        self.resumes += 1

    def stop(self) -> None:
        self.stops += 1


class FakeWorker:
    def __init__(self) -> None:
        self.starts = 0
        self.stops = 0
        self.running = False

    def start(self) -> None:
        self.starts += 1
        self.running = True

    def stop(self, timeout: float = 2.0) -> None:
        self.stops += 1
        self.running = False


def make():
    supervisor = FakeSupervisor()
    worker = FakeWorker()
    return AutoReconnectController(supervisor, worker), supervisor, worker


def test_enable_starts_worker_and_resets() -> None:
    controller, supervisor, worker = make()
    controller.set_enabled(True)
    assert controller.enabled is True
    assert supervisor.resets == 1
    assert worker.starts == 1
    assert worker.running is True


def test_disable_stops_worker() -> None:
    controller, _supervisor, worker = make()
    controller.set_enabled(True)
    controller.set_enabled(False)
    assert controller.enabled is False
    assert worker.stops == 1
    assert worker.running is False


def test_manual_login_success_resets_after_exhaustion() -> None:
    controller, supervisor, worker = make()
    controller.set_enabled(True)
    resets_before = supervisor.resets
    controller.on_manual_login(LinkState.AUTHENTICATED)
    assert supervisor.resets == resets_before + 1
    assert worker.starts >= 1


def test_manual_login_failure_does_not_reset() -> None:
    controller, supervisor, _worker = make()
    controller.set_enabled(True)
    resets_before = supervisor.resets
    controller.on_manual_login(LinkState.AUTH_FAILED)
    assert supervisor.resets == resets_before


def test_pause_resume_delegate() -> None:
    controller, supervisor, _worker = make()
    controller.pause()
    controller.resume()
    assert supervisor.pauses == 1
    assert supervisor.resumes == 1


def test_shutdown_stops_everything() -> None:
    controller, supervisor, worker = make()
    controller.set_enabled(True)
    controller.shutdown()
    assert worker.stops >= 1
    assert supervisor.stops == 1


def test_enable_callback_notified() -> None:
    seen: list[bool] = []
    supervisor = FakeSupervisor()
    worker = FakeWorker()
    controller = AutoReconnectController(supervisor, worker, on_change=seen.append)
    controller.set_enabled(True)
    controller.set_enabled(False)
    assert seen == [True, False]
