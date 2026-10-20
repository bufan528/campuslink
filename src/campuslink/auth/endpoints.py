"""校园网认证地址白名单。

这里是唯一允许凭据出站的目标集合。设计要点：

* 默认只包含 RFC 5737 文档示例地址（不会连到任何真实主机）；
* 真实入口由本地文件 ``endpoints_local.py`` 提供（已被 .gitignore 忽略），
  不进入版本库；
* 只接受 http + 端口 80，且 host 必须是字面 IPv4，拒绝主机名
  （DNS 可被外部因素影响）；
* 所有 URL 都由本模块拼装，调用方不能直接传入完整 URL。
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Final

from .errors import CredentialTargetRefused

ALLOWED_SCHEME: Final[str] = "http"
ALLOWED_PORT: Final[int] = 80


@dataclass(frozen=True, slots=True)
class PortalDefinition:
    """一个校园网认证入口的本地定义。"""

    host: str
    label: str
    login_paths: tuple[str, ...]
    logout_path: str
    is_default: bool
    evidence_level: str
    note: str

    @property
    def default_login_path(self) -> str:
        return self.login_paths[0]


_EXAMPLE_PORTALS: Final[dict[str, PortalDefinition]] = {
    "192.0.2.10": PortalDefinition(
        host="192.0.2.10",
        label="示例入口 A（请替换为本地配置）",
        login_paths=("/0.htm", "/a30.htm"),
        logout_path="/F.htm",
        is_default=True,
        evidence_level="example",
        note="RFC 5737 文档地址，仅用于本地开发和测试。",
    ),
    "192.0.2.11": PortalDefinition(
        host="192.0.2.11",
        label="示例入口 B（请替换为本地配置）",
        login_paths=("/0.htm", "/a30.htm"),
        logout_path="/F.htm",
        is_default=False,
        evidence_level="example",
        note="RFC 5737 文档地址，仅用于本地开发和测试。",
    ),
}


def _validate_path(path: str, field: str) -> None:
    if not isinstance(path, str) or not path.startswith("/"):
        raise CredentialTargetRefused(f"本地端口配置的 {field} 必须以 / 开头")
    if path.startswith("//") or "//" in path or "\\" in path:
        raise CredentialTargetRefused(f"本地端口配置的 {field} 含非法路径")
    if any(ch in path for ch in (" ", "\t", "\r", "\n", "@")):
        raise CredentialTargetRefused(f"本地端口配置的 {field} 含非法字符")


def _load_local_portals() -> dict[str, PortalDefinition] | None:
    """可选加载本地私有入口配置；不存在时退回示例地址。

    本地文件示例见 ``endpoints_local.example.py``。它被 .gitignore 忽略，
    因此真实校园网地址不会被提交到版本库。
    """
    try:
        from . import endpoints_local  # type: ignore[attr-defined]
    except ImportError:
        return None
    local = getattr(endpoints_local, "PORTALS", None)
    if not isinstance(local, dict) or not local:
        raise CredentialTargetRefused("endpoints_local.py 必须定义非空的 PORTALS 字典")
    validated: dict[str, PortalDefinition] = {}
    for host, definition in local.items():
        if not isinstance(host, str):
            raise CredentialTargetRefused("本地端口配置的 host 必须是字符串")
        try:
            ipaddress.IPv4Address(host)
        except ValueError:
            raise CredentialTargetRefused("本地端口配置的 host 必须是字面 IPv4") from None
        if not isinstance(definition, PortalDefinition):
            raise CredentialTargetRefused("本地端口配置必须是 PortalDefinition")
        if definition.host != host:
            raise CredentialTargetRefused("PortalDefinition.host 与字典键不一致")
        if not definition.login_paths:
            raise CredentialTargetRefused("PortalDefinition.login_paths 不能为空")
        for path in definition.login_paths:
            _validate_path(path, "login_paths")
        _validate_path(definition.logout_path, "logout_path")
        validated[host] = definition
    return validated


_LOCAL_PORTALS = _load_local_portals()
PORTALS: Final[dict[str, PortalDefinition]] = _LOCAL_PORTALS or dict(_EXAMPLE_PORTALS)
DEFAULT_HOST: Final[str] = next(
    (host for host, portal in PORTALS.items() if portal.is_default),
    sorted(PORTALS)[0],
)
ALLOWED_HOSTS: Final[frozenset[str]] = frozenset(PORTALS)


def _reject(reason: str) -> CredentialTargetRefused:
    return CredentialTargetRefused("拒绝向非白名单认证地址提交凭据：" + reason)


def is_allowed_host(host: str) -> bool:
    """判断 host 是否为允许的认证主机（必须是白名单里的字面 IPv4）。"""
    if host not in ALLOWED_HOSTS:
        return False
    try:
        ipaddress.IPv4Address(host)
    except ValueError:
        return False
    return True


def require_portal(host: str) -> PortalDefinition:
    """取白名单入口，非法则抛 CredentialTargetRefused。"""
    if not is_allowed_host(host):
        raise _reject("目标不是字面 IPv4 白名单主机")
    return PORTALS[host]


def portal_for(host: str) -> PortalDefinition | None:
    return PORTALS.get(host)


def build_url(host: str, path: str) -> str:
    """用白名单主机拼装 URL；path 只允许站点内绝对路径。"""
    require_portal(host)
    _validate_path(path, "path")
    return ALLOWED_SCHEME + "://" + host + ":" + str(ALLOWED_PORT) + path


def require_same_origin(url: str) -> None:
    """校验一个绝对 URL 是否仍在白名单 origin 内（用于重定向校验）。

    注意：URL 来自不可信的响应头/页面（Location、表单 action），
    因此 urlsplit 与 .port 抛出的 ValueError（如非法 IPv6 括号、
    端口 "abc"/99999）必须转成 CredentialTargetRefused，不能让
    原始异常穿透到界面线程。
    """
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url)
        # .port 对 "abc"/99999 等非法端口会抛 ValueError，必须在这里收口。
        port = parts.port or ALLOWED_PORT
        hostname = parts.hostname
    except ValueError as exc:
        raise _reject("URL 无法解析：" + str(exc)) from None
    if parts.scheme != ALLOWED_SCHEME:
        raise _reject("重定向协议不是 http")
    if hostname is None or not is_allowed_host(hostname):
        raise _reject("重定向目标不在白名单主机内")
    if port != ALLOWED_PORT:
        raise _reject("重定向目标端口不是 80")
    if parts.username or parts.password:
        raise _reject("重定向目标包含内嵌凭据")
