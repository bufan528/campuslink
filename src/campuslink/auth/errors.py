"""认证层异常。

所有异常的 str() 都不包含密码、账号完整值、Cookie 或请求正文，
调用方可以安全地把消息写进日志或显示在界面上。
"""

from __future__ import annotations


class CampusLinkError(Exception):
    """本项目的异常基类。"""


class ConfigError(CampusLinkError):
    """配置文件缺失、损坏或取值非法。"""


class CredentialStoreError(CampusLinkError):
    """系统凭据存储读写失败。"""


class CredentialsMissing(CampusLinkError):
    """未提供账号或密码，无法发起认证。"""


class CredentialTargetRefused(CampusLinkError):
    """目标地址不在校园网认证白名单内，拒绝提交凭据。"""


class PortalUnreachable(CampusLinkError):
    """认证主机连接失败或超时。"""


class RedirectRefused(CampusLinkError):
    """认证主机试图把请求重定向到非白名单地址，已终止。"""


class PortalProtocolMismatch(CampusLinkError):
    """返回的页面与已知协议不一致，未提交凭据。"""

    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail


class UnexpectedResponse(CampusLinkError):
    """响应无法归类（含认证结果不可判定）。"""
