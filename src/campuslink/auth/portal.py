"""登录页面解析。

只做一件事：把 HTML 解析成"表单指纹 + 字段值"，供适配器在提交前核对。

实现依据是标准 HTML 表单语义，不依赖任何特定单位的页面副本：

1. 页面编码从响应头/页面声明中读取（支持 GBK/GB18030 等常见中文编码）；
2. 登录表单取页面里的**第一个** form，action 为空表示提交到同一 URL；
3. 提交按钮的 name 按页面实际值收集（不臆造固定值）；
4. 页面里的第二个 form 通常用于注销，只解析第一个；
5. 页面身份标记来自门户自身，用于在提交前做一致性校验。

避免引入第三方依赖，用标准库 html.parser。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Final

from .models import LoginPageFingerprint

ACCOUNT_FIELD: Final[str] = "DDDDD"
PASSWORD_FIELD: Final[str] = "upass"
MKKEY_FIELD: Final[str] = "0MKKey"
# 历史资料曾把 0MKKey 描述为固定值；这里仅保留一个示例常量用于回归测试，
# 真实请求永远按页面实际控件构造，绝不使用这个常量作为提交值。
LEGACY_DOCUMENTED_MKKEY_VALUE: Final[str] = "legacy-example"


@dataclass(slots=True)
class _FormScan(HTMLParser):
    """提取页面第一个表单的结构与控件。"""

    form_action: str | None = None
    form_method: str | None = None
    controls: list[tuple[str, str, str]] = field(default_factory=list)
    _form_index: int = 0
    _collecting: bool = False

    def __post_init__(self) -> None:
        """dataclass 生成的 __init__ 不会调用 HTMLParser.__init__，必须手动补。"""
        HTMLParser.__init__(self, convert_charrefs=True)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """只解析页面里第一个表单，防止把注销表单/表单外字段混进来。"""
        name = tag.lower()
        attr = {k.lower(): (v if v is not None else "") for k, v in attrs}
        if name == "form":
            self._form_index += 1
            if self._form_index == 1:
                self.form_action = attr.get("action", "")
                self.form_method = attr.get("method", "get")
                self._collecting = True
            else:
                # 已进入第二个表单，后续控件一律不要。
                self._collecting = False
            return
        if name == "input" and self._collecting:
            self.controls.append(
                (
                    attr.get("name", ""),
                    attr.get("type", "text").lower(),
                    attr.get("value", ""),
                )
            )

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "form" and self._form_index == 1:
            # 第一个表单已结束：在 </form> 之后出现的 input 不属于登录表单。
            self._collecting = False

    def handle_data(self, data: str) -> None:
        return


@dataclass(frozen=True, slots=True)
class ParsedLoginPage:
    fingerprint: LoginPageFingerprint
    hidden_values: dict[str, str]
    submit_values: dict[str, str]
    raw_text: str

    @property
    def is_recognized(self) -> bool:
        return self.fingerprint.is_recognized


def parse_login_page(html: str, encoding: str = "unknown") -> ParsedLoginPage:
    scanner = _FormScan()
    try:
        scanner.feed(html)
        scanner.close()
    except Exception:
        pass

    names: set[str] = set()
    hidden: dict[str, str] = {}
    submit: dict[str, str] = {}
    for name, kind, value in scanner.controls:
        if not name:
            continue
        names.add(name)
        if kind == "hidden":
            hidden[name] = value
        elif kind == "submit":
            submit[name] = value

    fingerprint = LoginPageFingerprint(
        action=scanner.form_action if scanner.form_action else None,
        method=scanner.form_method or None,
        field_names=frozenset(names),
        has_account_field=ACCOUNT_FIELD in names,
        has_password_field=PASSWORD_FIELD in names,
        encoding=encoding,
    )
    return ParsedLoginPage(
        fingerprint=fingerprint,
        hidden_values=hidden,
        submit_values=submit,
        raw_text=html,
    )


def build_login_payload(
    account: str,
    password: str,
    page: ParsedLoginPage | None = None,
) -> tuple[dict[str, str], str]:
    """按浏览器的实际提交行为构造表单。

    浏览器的规则是：提交该表单里所有"成功的"控件——文本框、密码框、
    隐藏域，外加被点击的那个提交按钮。这里照做，不做任何猜测。

    返回 (payload, mkkey_source)：
    mkkey_source 记录 0MKKey 的实际来源，便于事后核对协议变化。
    """
    payload: dict[str, str] = {
        ACCOUNT_FIELD: account,
        PASSWORD_FIELD: password,
    }
    if page is None:
        return payload, "omitted"

    for name, value in page.hidden_values.items():
        if name and name not in payload:
            payload[name] = value
    for name, value in page.submit_values.items():
        if name and name not in payload:
            payload[name] = value

    if MKKEY_FIELD in page.hidden_values:
        source = "page_hidden_field"
    elif MKKEY_FIELD in page.submit_values:
        source = "page_submit_button"
    else:
        source = "omitted"
    return payload, source
