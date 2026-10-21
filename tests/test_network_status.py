"""三态区分：连着 Wi-Fi / 校园网已认证 / 互联网可用。"""

from __future__ import annotations

import pytest

from campuslink.auth.models import PortalState
from campuslink.network.probe import ConnectivityProbe
from campuslink.network.status import (
    LinkState,
    NetworkSnapshot,
    derive_link_state,
    is_campus_wifi,
)
from campuslink.network.wifi import WifiInspector, WifiStatus, parse_netsh_output

NETSH_CONNECTED = """
接口名称: WLAN
    状态                  : 已连接
    SSID                  : campus-wlan
    BSSID                 : aa:bb:cc:dd:ee:ff
    信号                  : 90%
"""

NETSH_DISCONNECTED = """
接口名称: WLAN
    状态                  : 已断开
    SSID                  :
    BSSID                 : 
"""


def snapshot(portal=PortalState.LOGIN_REQUIRED, internet=False, wifi=False, ssid=None):
    return NetworkSnapshot(
        wifi_connected=wifi,
        wifi_ssid=ssid,
        portal_state=portal,
        internet_available=internet,
        portal_interception=False,
        checked_at=0.0,
    )


def test_netsh_parsing_connected() -> None:
    status = parse_netsh_output(NETSH_CONNECTED)
    assert status.readable is True
    assert status.ssid == "campus-wlan"
    assert status.matches(["campus-wlan"])
    assert not status.matches(["Campus-WiFi"])


def test_netsh_parsing_multiple_interfaces_matches_any() -> None:
    """第二块网卡/热点不应把校园网 SSID 挤掉。"""
    text = """
    接口名称: WLAN
        状态                  : 已连接
        SSID                  : campus-wlan
    接口名称: 本地连接* 2
        状态                  : 已连接
        SSID                  : PhoneHotspot
    """
    status = parse_netsh_output(text)
    assert status.matches(["campus-wlan"])
    assert status.ssid == "campus-wlan"
    assert "PhoneHotspot" in status.ssids


def test_netsh_parsing_does_not_confuse_bssid() -> None:
    status = parse_netsh_output(NETSH_DISCONNECTED)
    assert status.ssid is None
    assert not status.matches(["campus-wlan"])


def test_netsh_parsing_garbage() -> None:
    status = parse_netsh_output("command not found")
    assert status.readable is False
    assert status.ssid is None


def test_wifi_inspector_handles_runner_error() -> None:
    def boom():
        raise OSError("netsh missing")

    assert WifiInspector(runner=boom).current_status().ssid is None


def test_is_campus_wifi() -> None:
    assert is_campus_wifi("campus-wlan", ["campus-wlan"])
    assert is_campus_wifi("CAMPUS-WLAN", ["campus-wlan"])
    assert not is_campus_wifi("Neighbor-WiFi", ["campus-wlan"])
    assert not is_campus_wifi(None, ["campus-wlan"])


@pytest.mark.parametrize(
    ("portal", "internet", "wifi", "expected"),
    [
        (PortalState.LOGIN_REQUIRED, False, True, LinkState.AUTH_REQUIRED),
        (PortalState.ONLINE, True, True, LinkState.AUTHENTICATED),
        (PortalState.ONLINE, False, True, LinkState.INTERNET_UNAVAILABLE),
        (PortalState.UNREACHABLE, False, False, LinkState.DISCONNECTED),
        (PortalState.UNREACHABLE, False, True, LinkState.INTERNET_UNAVAILABLE),
        (PortalState.UNREACHABLE, True, True, LinkState.AUTHENTICATED),
        (PortalState.UNRECOGNIZED, False, True, LinkState.UNKNOWN),
        (PortalState.LOGIN_REQUIRED, True, True, LinkState.AUTH_REQUIRED),
    ],
)
def test_derive_link_state(portal, internet, wifi, expected) -> None:
    assert derive_link_state(snapshot(portal, internet, wifi)) is expected


def test_wifi_connected_is_not_internet_available() -> None:
    """连着 Wi-Fi 但没网，必须是"需要认证"，不能显示成功。"""
    state = derive_link_state(snapshot(PortalState.LOGIN_REQUIRED, False, True))
    assert state is LinkState.AUTH_REQUIRED
    assert state is not LinkState.AUTHENTICATED


class _Resp:
    def __init__(self, status_code: int, content: bytes = b"") -> None:
        self.status_code = status_code
        self.content = content


PROBES = (
    ("http://connect.rom.miui.com/generate_204", 204, None),
    ("http://www.msftconnecttest.com/connecttest.txt", 200, b"Microsoft Connect Test"),
)


def test_probe_success() -> None:
    probe = ConnectivityProbe(PROBES, timeout=1.0, http_get=lambda url, **kw: _Resp(204))
    result = probe.check()
    assert result.internet_available is True


def test_probe_interception_detected() -> None:
    probe = ConnectivityProbe(PROBES, timeout=1.0, http_get=lambda url, **kw: _Resp(302))
    result = probe.check()
    assert result.internet_available is False
    assert result.portal_interception is True


def test_probe_body_mismatch_means_not_available() -> None:
    probe = ConnectivityProbe(
        PROBES[1:], timeout=1.0, http_get=lambda url, **kw: _Resp(200, b"<html>portal</html>")
    )
    result = probe.check()
    assert result.internet_available is False
    assert result.portal_interception is True


def test_probe_all_failures() -> None:
    def boom(url, **kwargs):
        raise TimeoutError("nope")

    probe = ConnectivityProbe(PROBES, timeout=1.0, http_get=boom)
    result = probe.check()
    assert result.internet_available is False
    assert result.portal_interception is False
    assert "探测失败" in result.detail


def test_probe_falls_through_to_second_endpoint() -> None:
    calls: list[str] = []

    def get(url, **kwargs):
        calls.append(url)
        if "miui" in url:
            raise TimeoutError("blocked")
        return _Resp(200, b"Microsoft Connect Test")

    probe = ConnectivityProbe(PROBES, timeout=1.0, http_get=get)
    assert probe.check().internet_available is True
    assert len(calls) == 2


def test_http_204_without_https_is_not_internet() -> None:
    """门户常放行 generate_204；没有 HTTPS 强证据时不能报"互联网可用"。"""
    probes = (
        ("https://secure.example/", 200, None),
        ("http://portal-allowed.example/generate_204", 204, None),
    )

    def get(url, **kwargs):
        if url.startswith("https://"):
            raise TimeoutError("tls blocked")
        return _Resp(204)

    probe = ConnectivityProbe(probes, timeout=1.0, http_get=get)
    result = probe.check()
    assert result.internet_available is False
    assert result.portal_interception is True
    assert "HTTPS" in result.detail


def test_https_probe_success_is_strong_evidence() -> None:
    probes = (("https://secure.example/", 200, None),)

    def get(url, **kwargs):
        return _Resp(200, b"<html>ok</html>")

    result = ConnectivityProbe(probes, timeout=1.0, http_get=get).check()
    assert result.internet_available is True
    assert result.detail.startswith("HTTPS 探测通过")
