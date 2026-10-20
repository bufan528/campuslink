"""应用配置。

配置文件只保存非敏感设置。密码和账号永远不进这里——账号可选地
保存在 Windows 凭据管理器里（见 credentials 包）。

安全不变量：
* 默认关闭自动重连、默认关闭开机自启，必须用户主动开启。
* portal_host 必须是 auth.endpoints 白名单里的主机；配置文件无法把凭据
  导到别的主机去。
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Final

from ..auth.endpoints import DEFAULT_HOST, portal_for, require_portal
from ..auth.errors import CredentialTargetRefused

DEFAULT_LOGIN_PATH: Final[str] = "/0.htm"
DEFAULT_WIFI_SSIDS: Final[tuple[str, ...]] = ("campus-wlan",)

# 数值边界：给自动重连兜住"每分钟轰炸一次"这种配置。
_BOUNDS: Final[dict[str, tuple[float, float]]] = {
    "reconnect_initial_delay_s": (5.0, 900.0),
    "reconnect_max_delay_s": (30.0, 3600.0),
    "status_poll_interval_s": (15.0, 3600.0),
    "http_timeout_s": (2.0, 60.0),
    "probe_timeout_s": (1.0, 30.0),
    "reconnect_max_attempts": (1, 20),
}


@dataclass(frozen=True, slots=True)
class AppSettings:
    """全部非敏感设置。frozen 保证读出来不会被意外改写。"""

    portal_host: str = DEFAULT_HOST
    login_path: str = DEFAULT_LOGIN_PATH
    auto_reconnect_enabled: bool = False
    autostart_enabled: bool = False
    reconnect_initial_delay_s: float = 15.0
    reconnect_max_delay_s: float = 600.0
    reconnect_max_attempts: int = 8
    status_poll_interval_s: float = 60.0
    http_timeout_s: float = 8.0
    probe_timeout_s: float = 5.0
    wifi_ssid_candidates: tuple[str, ...] = field(default=DEFAULT_WIFI_SSIDS)
    remember_account: bool = True

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["wifi_ssid_candidates"] = list(self.wifi_ssid_candidates)
        return data

    def with_changes(self, **changes: Any) -> AppSettings:
        return replace(self, **changes)


def _coerce_float(raw: Any, key: str, default: float, warnings: list[str]) -> float:
    low, high = _BOUNDS[key]
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        warnings.append(f"配置项 {key} 非法，已回落到默认值 {default}")
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        warnings.append(f"配置项 {key} 非法，已回落到默认值 {default}")
        return default
    if not math.isfinite(value):
        warnings.append(f"配置项 {key} 不是有限数值，已回落到默认值 {default}")
        return default
    if value < low or value > high:
        clamped = min(max(value, low), high)
        warnings.append(f"配置项 {key} 超出允许范围 [{low:g}, {high:g}]，已修正为 {clamped:g}")
        return clamped
    return value


def _coerce_int(raw: Any, key: str, default: int, warnings: list[str]) -> int:
    low, high = _BOUNDS[key]
    if isinstance(raw, bool):
        warnings.append(f"配置项 {key} 非法，已回落到默认值 {default}")
        return default
    if isinstance(raw, float):
        if not math.isfinite(raw) or not raw.is_integer():
            warnings.append(f"配置项 {key} 必须是整数，已回落到默认值 {default}")
            return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        warnings.append(f"配置项 {key} 非法，已回落到默认值 {default}")
        return default
    if value < low or value > high:
        clamped = int(min(max(value, low), high))
        warnings.append(f"配置项 {key} 超出允许范围 [{low:g}, {high:g}]，已修正为 {clamped}")
        return clamped
    return value


def _coerce_bool(raw: Any, key: str, default: bool, warnings: list[str]) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        lowered = raw.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    if isinstance(raw, int) and raw in (0, 1):
        return bool(raw)
    warnings.append(f"配置项 {key} 非法，已回落到默认值 {default}")
    return default


def from_dict(raw: dict[str, Any]) -> tuple[AppSettings, tuple[str, ...]]:
    """把字典解析成 AppSettings，返回 (设置, 警告列表)。

    任何非法值都不会抛异常，而是回落到安全默认值并留下警告——
    配置文件损坏不应让程序无法启动。
    """
    warnings: list[str] = []
    defaults = AppSettings()

    host = raw.get("portal_host", defaults.portal_host)
    if not isinstance(host, str) or portal_for(host) is None:
        warnings.append(
            f"配置项 portal_host 不是白名单内的认证主机，已回落到 {defaults.portal_host}"
        )
        host = defaults.portal_host

    portal = require_portal(host)
    login_path = raw.get("login_path", portal.default_login_path)
    if not isinstance(login_path, str) or login_path not in portal.login_paths:
        warnings.append(
            "配置项 login_path 不在该入口的已知登录路径内，已回落到 "
            + portal.default_login_path
        )
        login_path = portal.default_login_path

    ssids_raw = raw.get("wifi_ssid_candidates", list(DEFAULT_WIFI_SSIDS))
    if isinstance(ssids_raw, (list, tuple)):
        ssids = tuple(str(s).strip() for s in ssids_raw if str(s).strip())
        if any(not isinstance(s, str) for s in ssids_raw):
            warnings.append("配置项 wifi_ssid_candidates 含非字符串项，已转换为字符串")
    else:
        warnings.append("配置项 wifi_ssid_candidates 不是列表，已回落到默认 SSID 列表")
        ssids = DEFAULT_WIFI_SSIDS
    if not ssids:
        warnings.append("配置项 wifi_ssid_candidates 为空，已回落到默认 SSID 列表")
        ssids = DEFAULT_WIFI_SSIDS

    settings = AppSettings(
        portal_host=host,
        login_path=login_path,
        auto_reconnect_enabled=_coerce_bool(
            raw.get("auto_reconnect_enabled", defaults.auto_reconnect_enabled),
            "auto_reconnect_enabled",
            defaults.auto_reconnect_enabled,
            warnings,
        ),
        autostart_enabled=_coerce_bool(
            raw.get("autostart_enabled", defaults.autostart_enabled),
            "autostart_enabled",
            defaults.autostart_enabled,
            warnings,
        ),
        reconnect_initial_delay_s=_coerce_float(
            raw.get("reconnect_initial_delay_s", defaults.reconnect_initial_delay_s),
            "reconnect_initial_delay_s",
            defaults.reconnect_initial_delay_s,
            warnings,
        ),
        reconnect_max_delay_s=_coerce_float(
            raw.get("reconnect_max_delay_s", defaults.reconnect_max_delay_s),
            "reconnect_max_delay_s",
            defaults.reconnect_max_delay_s,
            warnings,
        ),
        reconnect_max_attempts=_coerce_int(
            raw.get("reconnect_max_attempts", defaults.reconnect_max_attempts),
            "reconnect_max_attempts",
            defaults.reconnect_max_attempts,
            warnings,
        ),
        status_poll_interval_s=_coerce_float(
            raw.get("status_poll_interval_s", defaults.status_poll_interval_s),
            "status_poll_interval_s",
            defaults.status_poll_interval_s,
            warnings,
        ),
        http_timeout_s=_coerce_float(
            raw.get("http_timeout_s", defaults.http_timeout_s),
            "http_timeout_s",
            defaults.http_timeout_s,
            warnings,
        ),
        probe_timeout_s=_coerce_float(
            raw.get("probe_timeout_s", defaults.probe_timeout_s),
            "probe_timeout_s",
            defaults.probe_timeout_s,
            warnings,
        ),
        wifi_ssid_candidates=ssids,
        remember_account=_coerce_bool(
            raw.get("remember_account", defaults.remember_account),
            "remember_account",
            defaults.remember_account,
            warnings,
        ),
    )

    if settings.reconnect_max_delay_s < settings.reconnect_initial_delay_s:
        warnings.append("reconnect_max_delay_s 小于初始延迟，已提升为初始延迟")
        settings = settings.with_changes(reconnect_max_delay_s=settings.reconnect_initial_delay_s)

    return settings, tuple(warnings)


def load_settings(path: Path | None) -> tuple[AppSettings, tuple[str, ...]]:
    """读配置。文件不存在则用默认值；损坏则回落到默认值并给出警告。"""
    if path is None:
        return AppSettings(), ()
    path = Path(path)
    if not path.exists():
        return AppSettings(), ()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return AppSettings(), (f"配置文件无法读取，已使用默认设置：{type(exc).__name__}",)
    if not isinstance(raw, dict):
        return AppSettings(), ("配置文件根节点不是对象，已使用默认设置",)
    return from_dict(raw)


def save_settings(path: Path, settings: AppSettings) -> None:
    """原子写配置（先写临时文件再替换），避免半截文件。

    保存前强制走 assert_no_secrets 护栏：即使将来给 AppSettings 加了
    字段，也不能把凭据写进普通配置文件。
    """
    payload_dict = settings.to_dict()
    assert_no_secrets(payload_dict)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(payload_dict, ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


_FORBIDDEN_KEYS: Final[frozenset[str]] = frozenset(
    {
        "pass",
        "pwd",
        "passwd",
        "password",
        "upass",
        "secret",
        "token",
        "apikey",
        "api_key",
        "credential",
        "credentials",
        "cookie",
        "authorization",
        "session",
    }
)


def assert_no_secrets(settings_dict: dict[str, Any]) -> None:
    """护栏：配置字典（含嵌套结构）里不允许出现任何疑似凭据的键。"""

    def scan(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                key_text = str(key).strip().lower()
                if key_text in _FORBIDDEN_KEYS:
                    raise CredentialTargetRefused(
                        "配置中不允许保存敏感字段：" + (path + "." + str(key)).lstrip(".")
                    )
                scan(value, (path + "." + str(key)).lstrip("."))
        elif isinstance(node, (list, tuple)):
            for index, value in enumerate(node):
                scan(value, path + "[" + str(index) + "]")

    scan(settings_dict, "")


__all__ = [
    "AppSettings",
    "DEFAULT_LOGIN_PATH",
    "DEFAULT_WIFI_SSIDS",
    "from_dict",
    "load_settings",
    "save_settings",
    "assert_no_secrets",
]
