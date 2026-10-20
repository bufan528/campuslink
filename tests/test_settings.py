"""配置层：默认关闭、白名单校验、不落凭据。"""

from __future__ import annotations

import json

import pytest

from campuslink.auth.errors import CredentialTargetRefused
from campuslink.config.settings import (
    AppSettings,
    assert_no_secrets,
    from_dict,
    load_settings,
    save_settings,
)


def test_defaults_are_safe() -> None:
    settings = AppSettings()
    assert settings.portal_host == "192.0.2.10"
    assert settings.login_path == "/0.htm"
    assert settings.auto_reconnect_enabled is False
    assert settings.autostart_enabled is False
    assert settings.wifi_ssid_candidates == ("campus-wlan",)


def test_missing_file_uses_defaults(tmp_path) -> None:
    settings, warnings = load_settings(tmp_path / "nope.json")
    assert settings == AppSettings()
    assert warnings == ()


def test_roundtrip(tmp_path) -> None:
    path = tmp_path / "config.json"
    original = AppSettings(
        auto_reconnect_enabled=True,
        autostart_enabled=True,
        reconnect_initial_delay_s=30.0,
        portal_host="192.0.2.11",
    )
    save_settings(path, original)
    loaded, warnings = load_settings(path)
    assert loaded == original
    assert warnings == ()


def test_corrupted_file_falls_back(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    settings, warnings = load_settings(path)
    assert settings == AppSettings()
    assert warnings and "无法读取" in warnings[0]


def test_non_whitelisted_host_is_rejected() -> None:
    settings, warnings = from_dict({"portal_host": "203.0.113.50"})
    assert settings.portal_host == "192.0.2.10"
    assert any("portal_host" in w for w in warnings)


def test_unknown_login_path_is_rejected() -> None:
    settings, warnings = from_dict({"login_path": "/evil.htm"})
    assert settings.login_path == "/0.htm"
    assert any("login_path" in w for w in warnings)


def test_alternate_path_accepted_for_its_portal() -> None:
    settings, warnings = from_dict({"login_path": "/a30.htm"})
    assert settings.login_path == "/a30.htm"
    assert warnings == ()


def test_a30_path_accepted_for_alternate_portal() -> None:
    settings, _warnings = from_dict({"portal_host": "192.0.2.11", "login_path": "/a30.htm"})
    assert settings.portal_host == "192.0.2.11"
    assert settings.login_path == "/a30.htm"


def test_numeric_values_are_clamped() -> None:
    settings, warnings = from_dict(
        {
            "reconnect_initial_delay_s": 0.1,
            "reconnect_max_attempts": 999,
            "status_poll_interval_s": 1e9,
        }
    )
    assert settings.reconnect_initial_delay_s == 5.0
    assert settings.reconnect_max_attempts == 20
    assert settings.status_poll_interval_s == 3600.0
    assert len(warnings) >= 3


def test_booleans_accept_strings() -> None:
    settings, _ = from_dict({"auto_reconnect_enabled": "yes", "autostart_enabled": "off"})
    assert settings.auto_reconnect_enabled is True
    assert settings.autostart_enabled is False


def test_weird_boolean_falls_back_to_default() -> None:
    settings, warnings = from_dict({"auto_reconnect_enabled": "maybe"})
    assert settings.auto_reconnect_enabled is False
    assert any("auto_reconnect_enabled" in w for w in warnings)


def test_max_delay_not_below_initial() -> None:
    settings, warnings = from_dict(
        {"reconnect_initial_delay_s": 300.0, "reconnect_max_delay_s": 60.0}
    )
    assert settings.reconnect_max_delay_s >= settings.reconnect_initial_delay_s
    assert any("reconnect_max_delay_s" in w for w in warnings)


def test_saved_config_contains_no_credentials(tmp_path) -> None:
    path = tmp_path / "config.json"
    save_settings(path, AppSettings(auto_reconnect_enabled=True))
    raw = json.loads(path.read_text(encoding="utf-8"))
    text = path.read_text(encoding="utf-8")
    for key in raw:
        assert key.lower() not in {"password", "upass", "pass", "secret", "token", "cookie"}
    assert "upass" not in text
    assert "password" not in text.lower()


def test_nan_and_infinity_are_rejected() -> None:
    settings, warnings = from_dict(
        {
            "reconnect_initial_delay_s": float("nan"),
            "probe_timeout_s": float("inf"),
        }
    )
    assert settings.reconnect_initial_delay_s == 15.0
    assert settings.probe_timeout_s == 5.0
    assert sum("有限数值" in w for w in warnings) == 2


def test_bool_is_not_accepted_as_number() -> None:
    settings, warnings = from_dict({"reconnect_max_attempts": True})
    assert settings.reconnect_max_attempts == 8
    assert any("reconnect_max_attempts" in w for w in warnings)


def test_fractional_max_attempts_falls_back() -> None:
    settings, warnings = from_dict({"reconnect_max_attempts": 3.9})
    assert settings.reconnect_max_attempts == 8
    assert any("必须是整数" in w for w in warnings)


def test_ssid_candidates_wrong_type_warns() -> None:
    settings, warnings = from_dict({"wifi_ssid_candidates": "campus-wlan"})
    assert settings.wifi_ssid_candidates == ("campus-wlan",)
    assert any("不是列表" in w for w in warnings)


def test_assert_no_secrets_scans_nested_structures() -> None:
    with pytest.raises(CredentialTargetRefused):
        assert_no_secrets({"smtp": {"password": "x"}})
    with pytest.raises(CredentialTargetRefused):
        assert_no_secrets({"hosts": [{"pwd": "x"}]})
    assert_no_secrets({"portal_host": "192.0.2.10", "nested": {"ok": True}})


@pytest.mark.parametrize("key", ["password", "upass", "secret", "cookie", "TOKEN", "pwd"])
def test_assert_no_secrets_guard(key: str) -> None:
    with pytest.raises(CredentialTargetRefused):
        assert_no_secrets({key: "x"})


def test_assert_no_secrets_allows_normal_keys() -> None:
    assert_no_secrets({"portal_host": "192.0.2.10", "remember_account": True})
