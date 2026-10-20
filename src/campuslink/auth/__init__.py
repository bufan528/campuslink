"""认证层：白名单、页面解析、提交与结果判定。"""

from __future__ import annotations

from .adapter import PortalAuthAdapter, RequestsTransport, Transport
from .endpoints import DEFAULT_HOST, PORTALS, build_url, is_allowed_host, require_portal
from .models import AuthAttempt, AuthOutcome, HttpResponse, LoginPageFingerprint, PortalState

__all__ = [
    "AuthAttempt",
    "AuthOutcome",
    "DEFAULT_HOST",
    "HttpResponse",
    "LoginPageFingerprint",
    "PORTALS",
    "PortalAuthAdapter",
    "PortalState",
    "RequestsTransport",
    "Transport",
    "build_url",
    "is_allowed_host",
    "require_portal",
]
