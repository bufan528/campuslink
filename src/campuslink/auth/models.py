"""认证层数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final


class AuthOutcome(StrEnum):
    """一次认证交互的判定结果。

    注意 UNKNOWN 与 SUCCESS 是两件事：没有命中任何已知标记时判 UNKNOWN，
    绝不以"HTTP 200 且没有报错"当作成功。
    """

    ALREADY_ONLINE = "already_online"
    SUCCESS = "success"
    INVALID_CREDENTIALS = "invalid_credentials"
    REJECTED = "rejected"
    PROTOCOL_MISMATCH = "protocol_mismatch"
    NETWORK_ERROR = "network_error"
    TARGET_REFUSED = "target_refused"
    UNKNOWN = "unknown"


SUCCESS_OUTCOMES: Final[frozenset[AuthOutcome]] = frozenset(
    {AuthOutcome.SUCCESS, AuthOutcome.ALREADY_ONLINE}
)


class PortalState(StrEnum):
    """门户页面的客观状态（不含凭据、不表示互联网可用）。"""

    LOGIN_REQUIRED = "login_required"
    ONLINE = "online"
    UNRECOGNIZED = "unrecognized"
    UNREACHABLE = "unreachable"


@dataclass(frozen=True, slots=True)
class LoginPageFingerprint:
    """登录页指纹，用于在提交前做一致性校验。"""

    action: str | None
    method: str | None
    field_names: frozenset[str]
    has_account_field: bool
    has_password_field: bool
    encoding: str

    @property
    def is_recognized(self) -> bool:
        return self.has_account_field and self.has_password_field

    def describe(self) -> str:
        names = ",".join(sorted(self.field_names)) or "(none)"
        return (
            f"action={self.action!r} method={self.method!r} "
            f"fields=[{names}] encoding={self.encoding}"
        )


@dataclass(frozen=True, slots=True)
class AuthAttempt:
    """一次认证尝试的结果。detail 中不得包含任何凭据或 Cookie。"""

    outcome: AuthOutcome
    message: str
    http_status: int | None = None
    submitted: bool = False
    detail: dict[str, str] = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        return self.outcome in SUCCESS_OUTCOMES


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """与具体 HTTP 客户端解耦的响应视图。"""

    status: int
    url: str
    headers: dict[str, str]
    body: bytes

    def header(self, name: str) -> str | None:
        lowered = name.lower()
        for key, value in self.headers.items():
            if key.lower() == lowered:
                return value
        return None

    @property
    def location(self) -> str | None:
        return self.header("Location")

    def decode(self) -> tuple[str, str]:
        """返回 (文本, 实际使用的编码)。GBK 兜底以适配可能的中文页面。"""
        ctype = self.header("Content-Type") or ""
        charset = ""
        for part in ctype.split(";"):
            part = part.strip()
            if part.lower().startswith("charset="):
                charset = part.split("=", 1)[1].strip().strip('"\'').lower()
                break
        candidates: list[str] = []
        if charset:
            candidates.append(charset)
        candidates.extend(["utf-8", "gb18030"])
        for enc in candidates:
            try:
                return self.body.decode(enc), enc
            except (UnicodeDecodeError, LookupError):
                continue
        return self.body.decode("utf-8", errors="replace"), "utf-8(replace)"
