"""登录页解析与表单构造。"""

from __future__ import annotations

from campuslink.auth import portal
from campuslink.auth.portal import build_login_payload, parse_login_page

from helpers import LOGIN_HTML, REAL_LOGIN_HTML


def test_parses_known_login_form() -> None:
    page = parse_login_page(LOGIN_HTML, "utf-8")
    fingerprint = page.fingerprint
    assert fingerprint.has_account_field
    assert fingerprint.has_password_field
    assert fingerprint.action == "0.htm"
    assert fingerprint.method == "post"
    assert fingerprint.encoding == "utf-8"
    assert page.hidden_values["0MKKey"] == "XZLogin"
    assert "0.htm" in fingerprint.describe()


def test_only_first_form_is_used() -> None:
    """第二个表单里的密码框不应让页面被误判成可登录页面。"""
    html = (
        "<form action='0.htm' method='post'>"
        "<input name='DDDDD' type='text'>"
        "</form>"
        "<form action='other.htm' method='post'>"
        "<input name='upass' type='password'>"
        "</form>"
    )
    page = parse_login_page(html)
    assert page.fingerprint.has_account_field
    assert not page.fingerprint.has_password_field
    assert not page.is_recognized


def test_unrecognized_page_is_rejected() -> None:
    page = parse_login_page("<html><body>维护中</body></html>")
    assert not page.is_recognized


def test_payload_prefers_page_hidden_value() -> None:
    page = parse_login_page(LOGIN_HTML)
    payload, source = build_login_payload("00000000", "pw", page)
    assert payload["DDDDD"] == "00000000"
    assert payload["upass"] == "pw"
    assert payload["0MKKey"] == "XZLogin"
    assert source == "page_hidden_field"


def test_payload_omits_mkkey_when_page_has_none() -> None:
    """页面上没有 0MKKey 时就不发这个字段，绝不臆造一个值。"""
    html = (
        "<form action='0.htm' method='post'>"
        "<input name='DDDDD' type='text'>"
        "<input name='upass' type='password'>"
        "</form>"
    )
    page = parse_login_page(html)
    payload, source = build_login_payload("00000000", "pw", page)
    assert "0MKKey" not in payload
    assert source == "omitted"


def test_payload_without_page() -> None:
    payload, source = build_login_payload("a", "b", None)
    assert set(payload) == {"DDDDD", "upass"}
    assert source == "omitted"


def test_legacy_documented_value_is_retained_but_never_sent() -> None:
    """历史资料里的固定值只作为反例保留，不得进入真实请求。"""
    assert portal.LEGACY_DOCUMENTED_MKKEY_VALUE == "legacy-example"
    payload, _source = build_login_payload("a", "b", None)
    assert portal.LEGACY_DOCUMENTED_MKKEY_VALUE not in payload.values()


# ---- 示例页面结构回归 ------------------------------------------------


def test_real_page_shape_is_parsed() -> None:
    page = parse_login_page(REAL_LOGIN_HTML, "gbk")
    fingerprint = page.fingerprint
    assert fingerprint.has_account_field
    assert fingerprint.has_password_field
    assert fingerprint.action is None  # action="" -> 提交到同一 URL
    assert fingerprint.method == "post"
    assert page.hidden_values == {"v6ip": ""}
    assert page.submit_values == {"0MKKey": "登 录　Login"}


def test_real_page_payload_mirrors_browser_submission() -> None:
    """提交按钮的值按页面实际值发送，不使用历史资料里的固定值。"""
    page = parse_login_page(REAL_LOGIN_HTML, "gbk")
    payload, source = build_login_payload("00000000", "pw", page)
    assert payload["DDDDD"] == "00000000"
    assert payload["upass"] == "pw"
    assert payload["v6ip"] == ""
    assert payload["0MKKey"] == "登 录　Login"
    assert source == "page_submit_button"


def test_input_after_first_form_end_is_ignored() -> None:
    """第一个 </form> 之后的 input 不属于登录表单，不能凑出“可识别”指纹。"""
    html = (
        "<form action='0.htm' method='post'>"
        "<input name='DDDDD' type='text'>"
        "</form>"
        "<input name='upass' type='password'>"
    )
    page = parse_login_page(html)
    assert page.fingerprint.has_account_field
    assert not page.fingerprint.has_password_field
    assert not page.is_recognized


def test_real_page_second_form_fields_are_not_collected() -> None:
    """第二个表单是注销表单（0MKKey=123456、R1/R2），不得混进登录请求。"""
    page = parse_login_page(REAL_LOGIN_HTML, "gbk")
    payload, _source = build_login_payload("00000000", "pw", page)
    assert "R1" not in payload
    assert "R2" not in payload
    assert payload["0MKKey"] != "123456"
