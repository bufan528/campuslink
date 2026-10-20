"""凭据存储：Windows 凭据管理器。

硬性约束：
* 只用系统凭据存储（CredWrite/CredRead），没有"退化成明文文件"的路径。
* 任何错误信息都不包含密码或账号完整值。
* pywin32 缺失时明确失败，而不是悄悄写文件。
"""

from __future__ import annotations

from typing import Final, Protocol, runtime_checkable

from ..auth.errors import CredentialStoreError, CredentialsMissing

CRED_TYPE_GENERIC: Final[int] = 1
CRED_PERSIST_LOCAL_MACHINE: Final[int] = 2
DEFAULT_TARGET: Final[str] = "CampusLink/portal"
ERROR_NOT_FOUND: Final[int] = 1168


@runtime_checkable
class CredentialStore(Protocol):
    """凭据存储接口，便于测试替换。"""

    def save(self, account: str, password: str) -> None: ...

    def load(self) -> tuple[str, str] | None: ...

    def delete(self) -> None: ...


def _win32cred():
    try:
        import win32cred  # type: ignore[import-not-found]
    except Exception as exc:  # pragma: no cover - 仅在缺少 pywin32 时触发
        raise CredentialStoreError(
            "系统凭据存储不可用（缺少 pywin32）。本项目不会改用明文文件保存密码。"
        ) from exc
    return win32cred


def _error_code(exc: BaseException) -> int | None:
    code = getattr(exc, "winerror", None)
    if isinstance(code, int):
        return code
    return None


class WindowsCredentialStore:
    """把账号密码存进 Windows 凭据管理器（Generic 凭据）。"""

    def __init__(self, target: str = DEFAULT_TARGET) -> None:
        self._target = target

    @property
    def target(self) -> str:
        return self._target

    def save(self, account: str, password: str) -> None:
        if not account or not password:
            raise CredentialsMissing("账号或密码为空，未保存凭据。")
        win32cred = _win32cred()
        blob = {
            "Type": CRED_TYPE_GENERIC,
            "TargetName": self._target,
            "UserName": account,
            "CredentialBlob": password,
            "Persist": CRED_PERSIST_LOCAL_MACHINE,
            "Comment": "CampusLink 校园网凭据",
        }
        try:
            win32cred.CredWrite(blob, 0)
        except Exception as exc:
            raise CredentialStoreError(
                f"写入系统凭据管理器失败（错误码 {_error_code(exc)}）。"
            ) from None

    def load(self) -> tuple[str, str] | None:
        win32cred = _win32cred()
        try:
            cred = win32cred.CredRead(self._target, CRED_TYPE_GENERIC, 0)
        except Exception as exc:
            if _error_code(exc) == ERROR_NOT_FOUND:
                return None
            raise CredentialStoreError(
                f"读取系统凭据管理器失败（错误码 {_error_code(exc)}）。"
            ) from None
        if not isinstance(cred, dict):
            raise CredentialStoreError("系统凭据管理器返回了非预期结构。")
        account = cred.get("UserName") or ""
        blob = cred.get("CredentialBlob") or b""
        if isinstance(blob, (bytes, bytearray)):
            try:
                password = bytes(blob).decode("utf-16-le")
            except UnicodeDecodeError:
                raise CredentialStoreError("系统凭据内容无法解码。") from None
        else:
            password = str(blob)
        if not account or not password:
            return None
        return str(account), password.rstrip("\x00")

    def delete(self) -> None:
        win32cred = _win32cred()
        try:
            win32cred.CredDelete(self._target, CRED_TYPE_GENERIC, 0)
        except Exception as exc:
            if _error_code(exc) == ERROR_NOT_FOUND:
                return
            raise CredentialStoreError(
                f"删除系统凭据失败（错误码 {_error_code(exc)}）。"
            ) from None


class InMemoryCredentialStore:
    """测试用内存实现；也可用作"本次运行不落盘"的选择。"""

    def __init__(self) -> None:
        self._entry: tuple[str, str] | None = None

    def save(self, account: str, password: str) -> None:
        if not account or not password:
            raise CredentialsMissing("账号或密码为空，未保存凭据。")
        self._entry = (account, password)

    def load(self) -> tuple[str, str] | None:
        return self._entry

    def delete(self) -> None:
        self._entry = None


class NullCredentialStore:
    """用户选择"不保存凭据"时使用：拒绝一切写入。"""

    def save(self, account: str, password: str) -> None:
        raise CredentialStoreError("用户未授权保存凭据到系统凭据管理器。")

    def load(self) -> tuple[str, str] | None:
        return None

    def delete(self) -> None:
        return
