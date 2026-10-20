"""响应标记判定：绝不把 HTTP 200 当成功。"""

from __future__ import annotations

import pytest

from campuslink.auth import markers
from campuslink.auth.models import AuthOutcome

from helpers import LOGOUT_OK_HTML, ONLINE_HTML, REAL_LOGIN_HTML, REAL_STATUS_HTML


@pytest.mark.parametrize(
    "text",
    ["密码错误，请重试", "用户名或密码有误", "loginErrMsg: fail", "该账号用户不存在"],
)
def test_invalid_credentials_markers(text: str) -> None:
    outcome, _reason = markers.classify_login_body(text)
    assert outcome is AuthOutcome.INVALID_CREDENTIALS


@pytest.mark.parametrize("text", ["认证成功", "登录成功", "认证通过", "Dr.COMUserWeb"])
def test_success_markers(text: str) -> None:
    outcome, reason = markers.classify_login_body(text)
    assert outcome is AuthOutcome.SUCCESS
    assert reason


@pytest.mark.parametrize("text", ["已欠费", "余额不足", "账号已停机"])
def test_rejected_markers(text: str) -> None:
    outcome, _reason = markers.classify_login_body(text)
    assert outcome is AuthOutcome.REJECTED


@pytest.mark.parametrize("text", ["", "<html><body>hello</body></html>", "OK"])
def test_unknown_is_not_success(text: str) -> None:
    outcome, reason = markers.classify_login_body(text)
    assert outcome is AuthOutcome.UNKNOWN
    assert "未确认" in reason or "无法判定" in reason


def test_online_page_detection() -> None:
    assert markers.looks_like_online_page(ONLINE_HTML, has_password_field=False)
    assert not markers.looks_like_online_page(ONLINE_HTML, has_password_field=True)


def test_page_identity_markers_are_recognised() -> None:
    """页面身份标记，比中文关键词可靠。"""
    assert markers.is_login_page(REAL_LOGIN_HTML)
    assert not markers.is_status_page(REAL_LOGIN_HTML)
    assert markers.is_status_page(REAL_STATUS_HTML)
    assert not markers.is_login_page(REAL_STATUS_HTML)


def test_real_login_page_is_not_treated_as_online() -> None:
    """登录页始终带表单，即使已登录也照样渲染，所以不能当状态页。"""
    assert not markers.looks_like_online_page(REAL_LOGIN_HTML, has_password_field=True)
    assert not markers.looks_like_online_page(REAL_LOGIN_HTML, has_password_field=False)


def test_real_status_page_is_online() -> None:
    assert markers.looks_like_online_page(REAL_STATUS_HTML, has_password_field=False)
    assert not markers.looks_like_online_page(REAL_STATUS_HTML, has_password_field=True)


def test_status_page_without_explicit_success_marker_is_unknown() -> None:
    outcome, reason = markers.classify_login_body(REAL_STATUS_HTML)
    assert outcome is AuthOutcome.UNKNOWN
    assert "状态页" in reason


def test_conflicting_markers_are_unknown() -> None:
    """正文同时含成功与失败标记时不能猜，必须报未确认。"""
    outcome, reason = markers.classify_login_body(
        "认证成功 <script>var msg='密码错误';</script>"
    )
    assert outcome is AuthOutcome.UNKNOWN
    assert "互斥" in reason
    outcome, _ = markers.classify_login_body("登录成功，但密码错误提示来自脚本")
    assert outcome is AuthOutcome.UNKNOWN


def test_logout_classification() -> None:
    assert markers.classify_logout_body(LOGOUT_OK_HTML) == (
        True,
        "命中注销成功标记 注销成功",
    )
    ok, _ = markers.classify_logout_body("未登录，无需注销")
    assert ok is False
    ok, _ = markers.classify_logout_body("<html>???</html>")
    assert ok is None
