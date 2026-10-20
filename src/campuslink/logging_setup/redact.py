"""日志脱敏。

目标：密码、账号完整值、Cookie、凭据请求正文都不能进入日志。
做法是双重保险：
1. 调用方登记"秘密值"（密码、账号），出现即替换；
2. 无论是否登记，表单参数名 (DDDDD/upass/0MKKey) 与 Cookie/Authorization
   头一律按字段名遮蔽，防止漏登记。
"""

from __future__ import annotations

import re
import threading
from typing import Final, Iterable

MASK: Final[str] = "***"

_PARAM_RE: Final[re.Pattern[str]] = re.compile(
    r"""(?i)\b(DDDDD|upass|0MKKey|password|passwd)\b("?\s*[=:]\s*"?)([^"&\s"'<>]+)"""
)
_QUOTED_JSON_RE: Final[re.Pattern[str]] = re.compile(
    r"""(?i)("(?:DDDDD|upass|0MKKey|password|passwd)"\s*:\s*")[^"]*(")"""
)
_HEADER_RE: Final[re.Pattern[str]] = re.compile(
    r"""(?i)\b(cookie|set-cookie|authorization|proxy-authorization)\b\s*[:=]\s*[^\r\n]+"""
)
_URL_CRED_RE: Final[re.Pattern[str]] = re.compile(
    r"""(?i)([?&](?:DDDDD|upass|0MKKey|password|passwd)=)([^&\s"'<>]+)"""
)


class Redactor:
    """把敏感串替换为遮蔽符的纯函数式工具（线程安全：日志可能来自多个线程）。"""

    __slots__ = ("_secrets", "_lock")

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        self._secrets: set[str] = set()
        self._lock = threading.Lock()
        for secret in secrets:
            self.add(secret)

    def add(self, secret: str | None) -> None:
        """登记一个需要遮蔽的秘密值。过短的值不登记以免误伤。"""
        if secret and len(secret) >= 3:
            with self._lock:
                self._secrets.add(secret)

    def clear(self) -> None:
        with self._lock:
            self._secrets.clear()

    @property
    def secret_count(self) -> int:
        with self._lock:
            return len(self._secrets)

    def redact_text(self, text: str) -> str:
        if not text:
            return text
        with self._lock:
            secrets = sorted(self._secrets, key=len, reverse=True)
        out = text
        for secret in secrets:
            if secret in out:
                out = out.replace(secret, MASK)
        out = _PARAM_RE.sub(lambda m: m.group(1) + m.group(2) + MASK, out)
        out = _QUOTED_JSON_RE.sub(lambda m: m.group(1) + MASK + m.group(2), out)
        out = _URL_CRED_RE.sub(lambda m: m.group(1) + MASK, out)
        out = _HEADER_RE.sub(lambda m: m.group(1) + ": " + MASK, out)
        return out


def mask_account(account: str | None) -> str:
    """把账号变成可安全写日志的形态（保留前 2 位便于人工核对）。"""
    if not account:
        return "(未设置)"
    if len(account) <= 2:
        return MASK
    return account[:2] + MASK


DEFAULT_REDACTOR: Final[Redactor] = Redactor()
