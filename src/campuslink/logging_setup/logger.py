"""日志装配：滚动文件 + 脱敏格式化器。

只记录状态变化、错误类型与时间；不记录请求正文、Cookie 或凭据。
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path
from typing import Final

from .redact import DEFAULT_REDACTOR, Redactor

LOGGER_NAME: Final[str] = "campuslink"
_HANDLER_TAG: Final[str] = "_campuslink_handler"
_DEFAULT_FORMAT: Final[str] = "%(asctime)s %(levelname)-7s %(name)s :: %(message)s"
_MAX_BYTES: Final[int] = 1_000_000
_BACKUPS: Final[int] = 5


class RedactingFormatter(logging.Formatter):
    """在已格式化的最终文本上脱敏，因此 exc_info 内容也会被处理。"""

    def __init__(
        self,
        fmt: str = _DEFAULT_FORMAT,
        redactor: Redactor = DEFAULT_REDACTOR,
    ) -> None:
        super().__init__(fmt=fmt, datefmt="%Y-%m-%d %H:%M:%S")
        self._redactor = redactor

    @property
    def redactor(self) -> Redactor:
        return self._redactor

    def format(self, record: logging.LogRecord) -> str:
        return self._redactor.redact_text(super().format(record))


def configure_logging(
    log_file: Path,
    *,
    level: int = logging.INFO,
    redactor: Redactor = DEFAULT_REDACTOR,
    echo_stdout: bool = False,
) -> logging.Logger:
    """装配应用日志器。重复调用会替换旧处理器，不会叠加。"""
    log_file = Path(log_file)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False

    for handler in list(logger.handlers):
        if getattr(handler, _HANDLER_TAG, False):
            logger.removeHandler(handler)
            handler.close()

    formatter = RedactingFormatter(redactor=redactor)

    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUPS,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    setattr(file_handler, _HANDLER_TAG, True)
    logger.addHandler(file_handler)

    if echo_stdout:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        setattr(stream, _HANDLER_TAG, True)
        logger.addHandler(stream)

    return logger


def get_logger(name: str = "") -> logging.Logger:
    """取子日志器；未显式装配时退化为静默，不会抛错。"""
    suffix = "." + name if name else ""
    return logging.getLogger(LOGGER_NAME + suffix)
