"""CLI 只读检测：状态 -> 退出码，且不提交凭据。"""

from __future__ import annotations

from pathlib import Path

import pytest

from campuslink.app import AppContext, build_context, run_check
from campuslink.auth.models import PortalState
from campuslink.config.settings import AppSettings
from campuslink.network.status import NetworkSnapshot


class FakeGateway:
    def __init__(self, snapshot: NetworkSnapshot) -> None:
        self.snapshot = snapshot
        self.calls = 0

    def survey(self) -> NetworkSnapshot:
        self.calls += 1
        return self.snapshot


def snapshot(portal: PortalState, internet: bool, wifi: bool = False) -> NetworkSnapshot:
    return NetworkSnapshot(
        wifi_connected=wifi,
        wifi_ssid="campus-wlan" if wifi else None,
        portal_state=portal,
        internet_available=internet,
        portal_interception=False,
        checked_at=0.0,
        detail="probe",
    )


def context(gateway) -> AppContext:
    return AppContext(
        settings=AppSettings(),
        config_path=Path("config.json"),
        gateway=gateway,  # type: ignore[arg-type]
        warnings=(),
    )


@pytest.mark.parametrize(
    ("snap", "code"),
    [
        (snapshot(PortalState.ONLINE, True), 0),
        (snapshot(PortalState.LOGIN_REQUIRED, False, True), 1),
        (snapshot(PortalState.ONLINE, False, True), 2),
        (snapshot(PortalState.UNREACHABLE, False, False), 3),
        (snapshot(PortalState.UNRECOGNIZED, False, True), 4),
    ],
)
def test_check_exit_codes(snap, code, capsys) -> None:
    gateway = FakeGateway(snap)
    assert run_check(context(gateway)) == code
    assert gateway.calls == 1
    out = capsys.readouterr().out
    assert "只读" in out
    assert snap.portal_state.value in out


def test_check_prints_config_warnings(capsys) -> None:
    ctx = AppContext(
        settings=AppSettings(),
        config_path=Path("config.json"),
        gateway=FakeGateway(snapshot(PortalState.ONLINE, True)),
        warnings=("配置项 portal_host 非法",),
    )
    assert run_check(ctx) == 0
    assert "配置项 portal_host 非法" in capsys.readouterr().out


def test_build_context_does_not_require_credentials(tmp_path, monkeypatch) -> None:
    """无配置文件、无凭据时也必须能装配（默认值 + 空凭据存储）。

    用内存假实现，避免测试去读本机真实 Windows 凭据。
    """

    class EmptyStore:
        def load(self):
            return None

        def save(self, account, password):
            return None

        def delete(self):
            return None

    monkeypatch.setattr("campuslink.app.WindowsCredentialStore", lambda: EmptyStore())
    ctx = build_context(tmp_path / "missing.json")
    assert ctx.settings == AppSettings()
    assert ctx.gateway.load_credentials() is None
