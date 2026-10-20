"""日志脱敏：密码/账号/Cookie 不得进入日志。"""

from __future__ import annotations

import logging

import pytest

from campuslink.logging_setup.logger import LOGGER_NAME, configure_logging
from campuslink.logging_setup.redact import Redactor, mask_account

ACCOUNT = "00000000"
PASSWORD = "P@ssw0rd!x"


def _drain(logger: logging.Logger) -> None:
    for handler in logger.handlers:
        handler.flush()


@pytest.fixture()
def log_file(tmp_path):
    path = tmp_path / "campuslink.log"
    logger = configure_logging(path, redactor=Redactor([ACCOUNT, PASSWORD]))
    try:
        yield path, logger
    finally:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()


def test_registered_secret_is_masked() -> None:
    redactor = Redactor([PASSWORD])
    assert PASSWORD not in redactor.redact_text("login " + PASSWORD)


def test_form_parameters_masked_without_registration() -> None:
    redactor = Redactor()
    text = redactor.redact_text("DDDDD=00000000&upass=abcd&0MKKey=xyz")
    assert "00000000" not in text
    assert "abcd" not in text
    assert "DDDDD=***" in text


def test_cookie_header_masked() -> None:
    redactor = Redactor()
    text = redactor.redact_text("Cookie: JSESSIONID=deadbeef; path=/")
    assert "deadbeef" not in text


def test_url_credentials_masked() -> None:
    redactor = Redactor()
    text = redactor.redact_text("http://192.0.2.10/0.htm?DDDDD=00000000&upass=abcd")
    assert "00000000" not in text
    assert "abcd" not in text


def test_quoted_json_form_parameters_masked_without_registration() -> None:
    redactor = Redactor()
    text = redactor.redact_text('{"DDDDD": "00000000", "upass": "abcd1234"}')
    assert "00000000" not in text
    assert "abcd1234" not in text
    assert '"upass": "***"' in text


def test_mask_account_keeps_only_prefix() -> None:
    assert mask_account(ACCOUNT) == "00***"
    assert mask_account(None) == "(未设置)"
    assert mask_account("12") == "***"


def test_log_file_has_no_secrets(log_file) -> None:
    path, logger = log_file
    logger.info("登录 account=%s password=%s", ACCOUNT, PASSWORD)
    logger.info("DDDDD=%s&upass=%s", ACCOUNT, PASSWORD)
    logger.info("Cookie: JSESSIONID=deadbeef")
    logger.warning("异常：%s", PASSWORD)
    _drain(logger)
    text = path.read_text(encoding="utf-8")
    assert PASSWORD not in text
    assert ACCOUNT not in text
    assert "deadbeef" not in text
    assert "***" in text


def test_exception_traceback_is_redacted(log_file) -> None:
    path, logger = log_file
    try:
        raise RuntimeError("failed with " + PASSWORD)
    except RuntimeError:
        logger.exception("提交失败 account=%s", ACCOUNT)
    _drain(logger)
    text = path.read_text(encoding="utf-8")
    assert PASSWORD not in text
    assert ACCOUNT not in text


def test_logger_name_is_stable() -> None:
    assert LOGGER_NAME == "campuslink"
