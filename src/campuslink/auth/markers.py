"""认证响应与页面身份判定。

判定分两类：
* 页面身份：门户页面自带的标记（用于判断登录页/已登录页）；
* 登录结果：页面正文中的显式成功/失败提示。不同门户可能不同，属于可替换集合。

两条铁律不变：
1. 命中显式标记才判定；全不命中判 UNKNOWN，绝不以 HTTP 200 判成功；
2. 标记是按示例门户整理的**可替换假设**，请按你实际门户校准。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .models import AuthOutcome

# ---- 页面身份标记（示例门户）----------------------------------------
PAGE_LOGOUT_FORM_MARKER: Final[str] = "Dr.COMWebLoginID_0.htm"   # 登录页
PAGE_STATUS_MARKER: Final[str] = "Dr.COMWebLoginID_1.htm"        # 已登录状态页


@dataclass(frozen=True, slots=True)
class Marker:
    token: str
    outcome: AuthOutcome
    evidence: str


# 顺序敏感：靠前的规则先匹配。**所有条目均为待验证假设，请按实际门户校准。**
LOGIN_MARKERS: Final[tuple[Marker, ...]] = (
    Marker("\u5bc6\u7801\u9519\u8bef", AuthOutcome.INVALID_CREDENTIALS, "待验证：密码错误提示"),
    Marker("\u7528\u6237\u540d\u6216\u5bc6\u7801", AuthOutcome.INVALID_CREDENTIALS, "待验证：账号或密码错误"),
    Marker("\u8d26\u53f7\u6216\u5bc6\u7801", AuthOutcome.INVALID_CREDENTIALS, "待验证：账号或密码错误（变体）"),
    Marker("loginErrMsg", AuthOutcome.INVALID_CREDENTIALS, "待验证：常见错误元素名"),
    Marker("\u7528\u6237\u4e0d\u5b58\u5728", AuthOutcome.INVALID_CREDENTIALS, "待验证：账号不存在"),
    Marker("\u5df2\u6b20\u8d39", AuthOutcome.REJECTED, "待验证：欠费停网"),
    Marker("\u4f59\u989d\u4e0d\u8db3", AuthOutcome.REJECTED, "待验证：余额不足"),
    Marker("\u5df2\u505c\u673a", AuthOutcome.REJECTED, "待验证：账号停机"),
    Marker("\u4e0d\u5141\u8bb8\u767b\u5f55", AuthOutcome.REJECTED, "待验证：被策略拒绝"),
    Marker("\u8ba4\u8bc1\u6210\u529f", AuthOutcome.SUCCESS, "待验证：认证成功提示"),
    Marker("\u767b\u5f55\u6210\u529f", AuthOutcome.SUCCESS, "待验证：登录成功提示"),
    Marker("\u8ba4\u8bc1\u901a\u8fc7", AuthOutcome.SUCCESS, "待验证：认证通过提示"),
    Marker("Dr.COMUserWeb", AuthOutcome.SUCCESS, "待验证：成功页面标题示例"),
)

# 已登录状态页特征（示例门户）。
ALREADY_ONLINE_MARKERS: Final[tuple[str, ...]] = (
    PAGE_STATUS_MARKER,
    "\u6ce8\u9500",
    "Logout",
    "logout",
    "\u5df2\u5728\u7ebf",
)

# 注意：注销后回到登录页虽然"看起来"是成功，但这属于推断而非证据，
# 因此不写进成功标记——宁可报"未确认"，也不谎报注销成功（会白扣流量费）。
LOGOUT_SUCCESS_MARKERS: Final[tuple[str, ...]] = (
    "\u6ce8\u9500\u6210\u529f",
    "\u9000\u51fa\u6210\u529f",
)

LOGOUT_FAILURE_MARKERS: Final[tuple[str, ...]] = (
    "\u6ce8\u9500\u5931\u8d25",
    "\u672a\u767b\u5f55",
    "\u4e0d\u5728\u7ebf",
)


def is_login_page(text: str) -> bool:
    """页面自报为登录页（已验证标记）。"""
    return PAGE_LOGOUT_FORM_MARKER in text


def is_status_page(text: str) -> bool:
    """页面自报为已登录状态页（已验证标记）。"""
    return PAGE_STATUS_MARKER in text


def classify_login_body(text: str) -> tuple[AuthOutcome, str]:
    """按标记表判定登录响应正文。返回 (结果, 依据说明)。

    同一正文若同时命中“成功”和“失败/拒绝”标记（例如脚本里同时存在
    成功提示与错误提示的字符串），不猜哪一个是真的，一律判 UNKNOWN。
    宁可显示“认证结果未确认”，也不把失败报成成功或反过来。
    """
    if not text:
        return AuthOutcome.UNKNOWN, "响应正文为空，无法判定"
    lowered = text.lower()
    hits = [marker for marker in LOGIN_MARKERS if marker.token.lower() in lowered]
    if hits:
        outcomes = {marker.outcome for marker in hits}
        if len(outcomes) > 1:
            tokens = "、".join(marker.token for marker in hits)
            return (
                AuthOutcome.UNKNOWN,
                "同一响应同时命中多个互斥标记（" + tokens + "），认证结果未确认",
            )
        first = hits[0]
        return first.outcome, "命中标记 " + first.token + "（" + first.evidence + "）"
    if is_status_page(text):
        return (
            AuthOutcome.UNKNOWN,
            "响应是已登录状态页（Dr.COMWebLoginID_1.htm），但缺少明确的成功提示",
        )
    return AuthOutcome.UNKNOWN, "未命中任何已知标记，认证结果未确认"


def looks_like_online_page(text: str, has_password_field: bool) -> bool:
    """已登录判定：没有密码框，且自报为状态页或出现注销入口。"""
    if has_password_field:
        return False
    if is_status_page(text):
        return True
    lowered = text.lower()
    return any(token.lower() in lowered for token in ALREADY_ONLINE_MARKERS)


def classify_logout_body(text: str) -> tuple[bool | None, str]:
    """注销判定：True 成功 / False 失败 / None 未确认。"""
    lowered = text.lower()
    for token in LOGOUT_FAILURE_MARKERS:
        if token.lower() in lowered:
            return False, "命中注销失败标记 " + token
    for token in LOGOUT_SUCCESS_MARKERS:
        if token.lower() in lowered:
            return True, "命中注销成功标记 " + token
    return None, "未命中注销标记，注销结果未确认"
