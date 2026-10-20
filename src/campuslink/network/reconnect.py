"""自动重连的退避控制。

刻意做成纯状态机（不发请求、不开线程），便于完整测试：
* 指数退避 + 上限 + 抖动，绝不每隔几秒无限轰炸认证服务器；
* 连续失败达到上限就停手，等用户手动再点；
* 可随时暂停 / 恢复 / 停止。
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class BackoffPolicy:
    """退避参数。默认值面向"避免频繁请求认证服务器"。"""

    initial_s: float = 15.0
    max_s: float = 600.0
    factor: float = 2.0
    jitter_ratio: float = 0.2
    max_attempts: int = 8

    def __post_init__(self) -> None:
        if self.initial_s <= 0:
            raise ValueError("initial_s 必须大于 0")
        if self.max_s < self.initial_s:
            raise ValueError("max_s 不得小于 initial_s")
        if self.factor < 1.0:
            raise ValueError("factor 不得小于 1")
        if not 0.0 <= self.jitter_ratio < 1.0:
            raise ValueError("jitter_ratio 必须在 [0, 1) 内")
        if self.max_attempts < 1:
            raise ValueError("max_attempts 至少为 1")


class ReconnectState:
    """重连控制器的公开状态。"""

    IDLE: Final[str] = "idle"
    WAITING: Final[str] = "waiting"
    PAUSED: Final[str] = "paused"
    EXHAUSTED: Final[str] = "exhausted"
    STOPPED: Final[str] = "stopped"


class BackoffController:
    def __init__(
        self,
        policy: BackoffPolicy | None = None,
        *,
        rand: Callable[[], float] = random.random,
    ) -> None:
        self._policy = policy or BackoffPolicy()
        self._rand = rand
        self._failures = 0
        self._state = ReconnectState.IDLE

    @property
    def policy(self) -> BackoffPolicy:
        return self._policy

    @property
    def failures(self) -> int:
        return self._failures

    @property
    def state(self) -> str:
        return self._state

    @property
    def exhausted(self) -> bool:
        return self._state == ReconnectState.EXHAUSTED

    @property
    def paused(self) -> bool:
        return self._state == ReconnectState.PAUSED

    def reset(self) -> None:
        self._failures = 0
        self._state = ReconnectState.IDLE

    def record_success(self) -> None:
        self._failures = 0
        # 暂停/停止是用户显式状态，不能被一次探测结果覆盖。
        if self._state not in {ReconnectState.PAUSED, ReconnectState.STOPPED}:
            self._state = ReconnectState.IDLE

    def record_failure(self) -> bool:
        """记一次失败；返回是否还有重试机会。暂停/停止期间不计数。"""
        if self._state in {ReconnectState.PAUSED, ReconnectState.STOPPED}:
            return True
        self._failures += 1
        if self._failures >= self._policy.max_attempts:
            self._state = ReconnectState.EXHAUSTED
            return False
        self._state = ReconnectState.WAITING
        return True

    def pause(self) -> None:
        if self._state != ReconnectState.EXHAUSTED:
            self._state = ReconnectState.PAUSED

    def resume(self) -> None:
        if self._state == ReconnectState.PAUSED:
            self._state = ReconnectState.WAITING

    def stop(self) -> None:
        self._state = ReconnectState.STOPPED

    def next_delay(self) -> float | None:
        """返回本次重试前应等待的秒数；None 表示不该再重试。"""
        if self._state in {ReconnectState.PAUSED, ReconnectState.STOPPED, ReconnectState.EXHAUSTED}:
            return None
        if self._failures >= self._policy.max_attempts:
            return None
        # 第一次重试就等 initial_s，之后按 factor 翻倍。
        base = self._policy.initial_s * (
            self._policy.factor ** max(self._failures - 1, 0)
        )
        capped = min(base, self._policy.max_s)
        jitter = capped * self._policy.jitter_ratio
        # 只向下抖动，保证不会超过上限
        value = capped - jitter * self._rand()
        return max(self._policy.initial_s * 0.5, min(value, self._policy.max_s))
