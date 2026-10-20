"""开机自启：默认关闭，且只在用户开启时写注册表。"""

from __future__ import annotations

import pytest

from campuslink.autostart.registry import (
    VALUE_NAME,
    AutostartManager,
    InMemoryAutostartBackend,
    build_launch_command,
)


def test_default_off_and_enable_disable() -> None:
    backend = InMemoryAutostartBackend()
    manager = AutostartManager(backend)
    assert manager.enabled is False
    manager.enable()
    assert manager.enabled is True
    assert VALUE_NAME in backend.values
    manager.disable()
    assert manager.enabled is False


def test_set_enabled_roundtrip() -> None:
    backend = InMemoryAutostartBackend()
    manager = AutostartManager(backend)
    manager.set_enabled(True)
    assert backend.values.get(VALUE_NAME)
    manager.set_enabled(False)
    assert backend.values.get(VALUE_NAME) is None


def test_disable_when_absent_is_safe() -> None:
    AutostartManager(InMemoryAutostartBackend()).disable()


def test_launch_command_contains_minimized_flag() -> None:
    command = build_launch_command("C:\\Apps\\CampusLink.exe")
    assert "--minimized" in command
    assert "CampusLink.exe" in command


def test_source_mode_launch_command_injects_src_path() -> None:
    """源码运行不能只写 `-m campuslink`：开机时没有 PYTHONPATH 会导入失败。"""
    command = build_launch_command()
    assert "-m campuslink" not in command
    assert "sys.path.insert" in command
    assert "campuslink" in command
    assert "--minimized" in command


def test_frozen_launch_command_uses_exe(monkeypatch) -> None:
    monkeypatch.setattr("sys.frozen", True, raising=False)
    command = build_launch_command("C:\\Program Files\\CampusLink\\CampusLink.exe")
    assert command == '"C:\\Program Files\\CampusLink\\CampusLink.exe" --minimized'


def test_empty_registry_value_is_not_enabled() -> None:
    backend = InMemoryAutostartBackend()
    backend.values[VALUE_NAME] = "   "
    assert AutostartManager(backend).enabled is False


def test_manager_never_touches_real_registry_by_default() -> None:
    """默认构造会用注册表后端，但测试里一律注入内存后端。"""
    manager = AutostartManager(InMemoryAutostartBackend())
    manager.enable()
    assert manager.enabled is True


@pytest.mark.parametrize("enabled", [True, False])
def test_engine_requires_explicit_toggle(enabled: bool) -> None:
    backend = InMemoryAutostartBackend()
    manager = AutostartManager(backend)
    if enabled:
        manager.set_enabled(True)
    assert manager.enabled is enabled
