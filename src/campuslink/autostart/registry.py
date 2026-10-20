"""开机自启开关。

默认关闭，且**只有用户明确点开开关**才会写注册表。
写的是 HKCU 下当前用户的 Run 项，不需要管理员权限，不碰计划任务、
不装服务、不装证书。

所有注册表访问都走可注入的后端，因此本项目自带的测试不会真的改注册表。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Final, Protocol

RUN_KEY: Final[str] = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME: Final[str] = "CampusLink"


class AutostartBackend(Protocol):
    def read(self, name: str) -> str | None: ...

    def write(self, name: str, command: str) -> None: ...

    def delete(self, name: str) -> None: ...


class RegistryAutostartBackend:
    """真实实现：HKCU 的 Run 项。"""

    def __init__(self, key_path: str = RUN_KEY) -> None:
        self._key_path = key_path

    def _key(self, access: int, *, create: bool = False):
        import winreg

        if create:
            return winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, self._key_path, 0, access)
        return winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._key_path, 0, access)

    def read(self, name: str) -> str | None:
        import winreg

        try:
            with self._key(winreg.KEY_READ) as key:
                value, _kind = winreg.QueryValueEx(key, name)
                return str(value)
        except FileNotFoundError:
            return None
        except OSError:
            return None

    def write(self, name: str, command: str) -> None:
        import winreg

        with self._key(winreg.KEY_SET_VALUE, create=True) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, command)

    def delete(self, name: str) -> None:
        import winreg

        try:
            with self._key(winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, name)
        except FileNotFoundError:
            return


class InMemoryAutostartBackend:
    """测试用后端：只记在内存里。"""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def read(self, name: str) -> str | None:
        return self.values.get(name)

    def write(self, name: str, command: str) -> None:
        self.values[name] = command

    def delete(self, name: str) -> None:
        self.values.pop(name, None)


def build_launch_command(executable: str | None = None) -> str:
    """构造自启命令行：打包后直接用 exe，源码运行则把 src 目录写进命令。

    源码模式下只在当前 shell 设置 PYTHONPATH 对开机自启无效，所以这里把
    `src` 的绝对路径直接注入 `-c` 代码里；否则登录时必然 "No module named campuslink"。
    """
    if getattr(sys, "frozen", False):
        target = executable or sys.executable
        return '"' + target + '" --minimized'
    interpreter = executable or sys.executable
    src_dir = Path(__file__).resolve().parents[2]
    code = (
        "import sys;sys.path.insert(0,"
        + repr(str(src_dir))
        + ");from campuslink.app import main;"
        + "raise SystemExit(main(['--minimized']))"
    )
    return '"' + interpreter + '" -c "' + code + '"'


class AutostartManager:
    def __init__(
        self,
        backend: AutostartBackend | None = None,
        *,
        name: str = VALUE_NAME,
        executable: str | None = None,
    ) -> None:
        self._backend = backend or RegistryAutostartBackend()
        self._name = name
        self._executable = executable

    @property
    def enabled(self) -> bool:
        value = self._backend.read(self._name)
        return bool(value and value.strip())

    def enable(self) -> None:
        self._backend.write(self._name, build_launch_command(self._executable))

    def disable(self) -> None:
        self._backend.delete(self._name)

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            self.enable()
        else:
            self.disable()
