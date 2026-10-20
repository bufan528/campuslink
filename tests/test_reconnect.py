"""退避、停止与暂停（任务书测试要求第 9 条）。"""

from __future__ import annotations

import pytest

from campuslink.network.reconnect import BackoffController, BackoffPolicy, ReconnectState


def test_policy_validation() -> None:
    with pytest.raises(ValueError):
        BackoffPolicy(initial_s=0)
    with pytest.raises(ValueError):
        BackoffPolicy(initial_s=100, max_s=10)
    with pytest.raises(ValueError):
        BackoffPolicy(factor=0.5)
    with pytest.raises(ValueError):
        BackoffPolicy(jitter_ratio=1.0)
    with pytest.raises(ValueError):
        BackoffPolicy(max_attempts=0)


def test_delay_grows_and_is_capped() -> None:
    controller = BackoffController(BackoffPolicy(initial_s=10, max_s=100, factor=2), rand=lambda: 0.0)
    delays = []
    for _ in range(6):
        controller.record_failure()
        delays.append(controller.next_delay())
    # 第一次重试等 initial_s，之后翻倍，并在 max_s 处封顶。
    assert delays == [10, 20, 40, 80, 100, 100]


def test_delay_never_exceeds_max_with_jitter() -> None:
    policy = BackoffPolicy(initial_s=10, max_s=50, factor=3, jitter_ratio=0.2)
    controller = BackoffController(policy, rand=lambda: 1.0)
    seen: list[float] = []
    for _ in range(8):
        controller.record_failure()
        delay = controller.next_delay()
        if delay is None:
            break
        assert delay <= policy.max_s
        assert delay >= policy.initial_s * 0.5
        seen.append(delay)
    assert len(seen) >= 3
    # 抖动是在基线上“只向下”，此前先算 delay 再记失败会让本用例永远只测首轮。
    assert seen[0] < seen[-1]


def test_exhaustion_stops_retrying() -> None:
    policy = BackoffPolicy(initial_s=5, max_s=60, max_attempts=3)
    controller = BackoffController(policy, rand=lambda: 0.0)
    assert controller.record_failure() is True
    assert controller.record_failure() is True
    assert controller.record_failure() is False
    assert controller.exhausted is True
    assert controller.state == ReconnectState.EXHAUSTED
    assert controller.next_delay() is None


def test_success_resets_counter() -> None:
    controller = BackoffController(BackoffPolicy(max_attempts=2), rand=lambda: 0.0)
    controller.record_failure()
    controller.record_success()
    assert controller.failures == 0
    assert controller.state == ReconnectState.IDLE


def test_pause_and_resume() -> None:
    controller = BackoffController(rand=lambda: 0.0)
    controller.pause()
    assert controller.paused is True
    assert controller.next_delay() is None
    controller.resume()
    assert controller.paused is False
    assert controller.next_delay() is not None


def test_stop_prevents_retry() -> None:
    controller = BackoffController(rand=lambda: 0.0)
    controller.stop()
    assert controller.next_delay() is None


def test_record_does_not_override_pause_or_stop() -> None:
    controller = BackoffController(rand=lambda: 0.0)
    controller.pause()
    controller.record_failure()
    controller.record_success()
    assert controller.paused is True
    controller.stop()
    controller.record_failure()
    assert controller.state == ReconnectState.STOPPED


def test_reset_recovers_from_exhaustion() -> None:
    controller = BackoffController(
        BackoffPolicy(initial_s=5, max_s=60, max_attempts=2), rand=lambda: 0.0
    )
    controller.record_failure()
    controller.record_failure()
    assert controller.exhausted is True
    controller.reset()
    assert controller.exhausted is False
    assert controller.next_delay() is not None


def test_jitter_is_only_downward() -> None:
    policy = BackoffPolicy(initial_s=20, max_s=20, factor=1, jitter_ratio=0.5)
    hi = BackoffController(policy, rand=lambda: 0.0).next_delay()
    lo = BackoffController(policy, rand=lambda: 1.0).next_delay()
    assert hi == 20
    assert lo is not None and lo < hi
