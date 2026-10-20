"""认证地址白名单（任务书测试要求第 7 条）。"""

from __future__ import annotations

import pytest

from campuslink.auth import endpoints
from campuslink.auth.errors import CredentialTargetRefused


def test_default_host_is_xuancheng_portal() -> None:
    assert endpoints.DEFAULT_HOST == "192.0.2.10"


@pytest.mark.parametrize(
    "host",
    ["192.0.2.10", "192.0.2.11"],
)
def test_whitelisted_hosts_allowed(host: str) -> None:
    assert endpoints.is_allowed_host(host)


@pytest.mark.parametrize(
    "host",
    [
        "203.0.113.50",   # 非白名单示例地址
        "203.0.113.51",   # 非白名单示例地址
        "203.0.113.61",   # 非白名单示例地址
        "192.0.2.12",
        "portal.example.edu",  # 主机名可能受 DNS 影响，不作提交目标
        "192.0.2.10.evil.com",
        "",
        "localhost",
    ],
)
def test_non_whitelisted_hosts_rejected(host: str) -> None:
    assert not endpoints.is_allowed_host(host)


def test_build_url_uses_whitelisted_origin() -> None:
    assert endpoints.build_url("192.0.2.10", "/0.htm") == "http://192.0.2.10:80/0.htm"
    assert endpoints.build_url("192.0.2.11", "/a30.htm") == "http://192.0.2.11:80/a30.htm"


def test_build_url_refuses_other_host() -> None:
    with pytest.raises(CredentialTargetRefused):
        endpoints.build_url("203.0.113.50", "/drcom/login")


@pytest.mark.parametrize(
    "path",
    [
        "http://evil.example.com/0.htm",
        "//evil.example.com/0.htm",
        "0.htm",
        "/0.htm HTTP/1.1\r\nHost: evil",
        "/0@evil.example.com",
    ],
)
def test_build_url_refuses_injection_attempts(path: str) -> None:
    with pytest.raises(CredentialTargetRefused):
        endpoints.build_url("192.0.2.10", path)


@pytest.mark.parametrize(
    "url",
    [
        "http://192.0.2.10:80/0.htm",
        "http://192.0.2.11:80/F.htm",
    ],
)
def test_same_origin_accepts_whitelisted(url: str) -> None:
    endpoints.require_same_origin(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://192.0.2.10/0.htm",
        "http://evil.example.com/",
        "http://192.0.2.10:8080/0.htm",
        "http://user:pw@192.0.2.10/0.htm",
        # 以下来自不可信响应头/页面，urlsplit 本身会抛 ValueError：
        "http://192.0.2.10:99999/0.htm",
        "http://192.0.2.10:abc/0.htm",
        "http://[::1/0.htm",
    ],
)
def test_same_origin_rejects_others(url: str) -> None:
    with pytest.raises(CredentialTargetRefused):
        endpoints.require_same_origin(url)


def test_portal_definitions_document_evidence_level() -> None:
    for host, portal in endpoints.PORTALS.items():
        assert portal.host == host
        assert portal.login_paths
        assert portal.evidence_level
