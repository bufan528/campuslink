"""程序入口与装配。

用法：
    python -m campuslink                 图形界面
    python -m campuslink --check         只读检测当前网络状态（不提交凭据）
    python -m campuslink --diagnostics   打印配置/日志路径与当前设置
    python -m campuslink --minimized     启动后直接进托盘
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from .auth.endpoints import PORTALS
from .config.paths import default_config_file, default_log_file, log_dir
from .config.settings import AppSettings, load_settings, save_settings
from .credentials.store import (
    NullCredentialStore,
    WindowsCredentialStore,
)
from .logging_setup.logger import configure_logging, get_logger
from .logging_setup.redact import DEFAULT_REDACTOR
from .network.status import STATE_LABELS, LinkState
from .services.gateway import AuthGateway

_log = get_logger("app")

# --check 的退出码：0 已认证；1 需要认证；2 校园网可达但外网不可用；
# 3 未连接/门户不可达；4 无法判定。便于脚本化巡检。
_CHECK_EXIT_CODES: dict[LinkState, int] = {
    LinkState.AUTHENTICATED: 0,
    LinkState.AUTH_REQUIRED: 1,
    LinkState.INTERNET_UNAVAILABLE: 2,
    LinkState.AUTH_FAILED: 2,
    LinkState.DISCONNECTED: 3,
    LinkState.UNKNOWN: 4,
    LinkState.CHECKING: 4,
    LinkState.SIGNING_IN: 4,
}


@dataclass(slots=True)
class AppContext:
    settings: AppSettings
    config_path: Path
    gateway: AuthGateway
    warnings: tuple[str, ...]


def _safe_streams() -> None:
    """打包成无控制台 EXE 后 sys.stdout/sys.stderr 是 None，print 会抛异常。

    这里给它们接上 devnull，让命令行分支在窗口模式下也只是静默而非崩溃。
    """
    import os

    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            try:
                setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
            except Exception:
                pass
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def build_context(config_path: Path | None = None) -> AppContext:
    config_path = config_path or default_config_file()
    settings, warnings = load_settings(config_path)
    for warning in warnings:
        _log.warning("配置提示：%s", warning)

    try:
        store = WindowsCredentialStore()
        # 触发一次读取，确认凭据后端真的可用（不存在凭据是正常情况）。
        store.load()
    except Exception as exc:
        _log.warning("系统凭据存储不可用，本次运行不保存凭据：%s", exc)
        store = NullCredentialStore()

    gateway = AuthGateway(settings, store, redactor=DEFAULT_REDACTOR)
    return AppContext(
        settings=settings,
        config_path=config_path,
        gateway=gateway,
        warnings=warnings,
    )


def print_diagnostics(context: AppContext) -> None:
    settings = context.settings
    actual_autostart: bool | None
    try:
        from .autostart.registry import AutostartManager

        actual_autostart = AutostartManager().enabled
    except Exception:
        actual_autostart = None
    print("CampusLink 诊断信息")
    print("  配置路径:", context.config_path)
    print("  日志目录:", log_dir())
    print("  当前认证入口:", settings.portal_host, settings.login_path)
    print("  白名单入口:")
    for host, portal in PORTALS.items():
        print("   -", host, portal.label, "配置来源", portal.evidence_level)
    print("  自动重连:", "开启" if settings.auto_reconnect_enabled else "关闭（默认）")
    autostart_text = "开启" if actual_autostart else "关闭（默认）"
    if actual_autostart is None:
        autostart_text = "未知（读取注册表失败）"
    elif actual_autostart != settings.autostart_enabled:
        autostart_text += "（配置=开启）" if settings.autostart_enabled else "（配置=关闭）"
    print("  开机自启:", autostart_text)
    print("  重连轮询间隔:", "%.0fs" % settings.status_poll_interval_s)
    print("  重连退避: 初始 %.0fs 上限 %.0fs 最多 %d 次" % (
        settings.reconnect_initial_delay_s,
        settings.reconnect_max_delay_s,
        settings.reconnect_max_attempts,
    ))
    if context.warnings:
        print("  配置警告:")
        for warning in context.warnings:
            print("   -", warning)


def run_check(context: AppContext) -> int:
    """只读检测：不提交任何凭据，不修改任何设置。

    退出码见 _CHECK_EXIT_CODES，方便脚本/验收清单自动判断。
    """
    from .network.status import derive_link_state

    for warning in context.warnings:
        print("配置警告：", warning)
    snapshot = context.gateway.survey()
    state = derive_link_state(snapshot)
    print("网络检测结果（只读，未提交凭据）")
    print("  是否连着目标校园网 Wi-Fi:", "是" if snapshot.wifi_connected else "否",
          "（SSID:", snapshot.wifi_ssid or "无", "）")
    print("  门户状态:", snapshot.portal_state.value)
    print("  互联网可用:", "是" if snapshot.internet_available else "否")
    print("  探测细节:", snapshot.detail or "-")
    print("  推导状态:", STATE_LABELS.get(state, state.value), "(" + state.value + ")")
    if snapshot.portal_state.value in {"unrecognized", "unreachable"}:
        print("  提示: 门户页面与已知协议不一致或不可达，本工具不会提交凭据。")
    return _CHECK_EXIT_CODES.get(state, 4)


def run_gui(context: AppContext, *, minimized: bool = False) -> int:
    try:
        from PySide6.QtWidgets import QApplication, QSystemTrayIcon
    except Exception as exc:
        print("无法加载 PySide6，图形界面不可用：", exc, file=sys.stderr)
        print("可改用 python -m campuslink --check 做只读检测。", file=sys.stderr)
        return 2

    from .autostart.registry import AutostartManager
    from .services.reconnect_loop import (
        AutoReconnectController,
        ReconnectSupervisor,
        ReconnectWorker,
        policy_from_settings,
    )
    from .ui.qt_app import MainWindow, TrayController
    from .ui.viewmodel import AuthViewModel
    from PySide6.QtCore import QObject, Signal

    class _ReconnectBridge(QObject):
        """把后台重连线程的结果用 queued signal 送到 GUI 线程。"""

        updated = Signal(object)

    app = QApplication.instance() or QApplication(sys.argv)
    tray_available = QSystemTrayIcon.isSystemTrayAvailable()
    app.setQuitOnLastWindowClosed(not tray_available)

    autostart = AutostartManager()
    # 以注册表实际状态为准，避免配置与系统不一致时误显示。
    try:
        actual_autostart = autostart.enabled
        if actual_autostart != context.settings.autostart_enabled:
            context.settings = context.settings.with_changes(autostart_enabled=actual_autostart)
    except Exception:
        _log.warning("读取开机自启状态失败", exc_info=True)

    def persist(settings: AppSettings) -> None:
        save_settings(context.config_path, settings)

    reconnect_bridge = _ReconnectBridge()
    supervisor = ReconnectSupervisor(
        context.gateway,
        policy_from_settings(context.settings),
        on_state=reconnect_bridge.updated.emit,
    )
    worker = ReconnectWorker(supervisor)
    reconnect = AutoReconnectController(supervisor, worker)

    viewmodel = AuthViewModel(
        context.gateway,
        context.settings,
        autostart=autostart,
        persist=persist,
        on_auto_reconnect_change=reconnect.set_enabled,
    )
    reconnect_bridge.updated.connect(viewmodel.apply_auto_reconnect)

    window = MainWindow(
        viewmodel,
        on_login_finished=reconnect.on_manual_login,
        close_to_tray=tray_available,
    )

    def quit_all() -> None:
        reconnect.shutdown()
        _log.info("用户退出程序")

    tray = None
    if tray_available:
        tray = TrayController(
            app,
            window,
            viewmodel,
            reconnect=reconnect,
            on_quit=quit_all,
        )
    else:
        _log.warning("系统托盘不可用：关闭窗口即退出程序")
    app.aboutToQuit.connect(quit_all)

    if context.settings.auto_reconnect_enabled:
        reconnect.set_enabled(True)
        _log.info("自动重连已按用户设置启动")

    if tray_available:
        if not minimized:
            window.show()
        tray.tray.showMessage(
            "CampusLink",
            "本工具的自动重连与开机自启默认关闭，需你主动开启。",
        )
    else:
        window.show()
    return app.exec()


def main(argv: list[str] | None = None) -> int:
    _safe_streams()
    parser = argparse.ArgumentParser(
        prog="campuslink",
        description="校园网自动连接助手（本地认证客户端）",
    )
    parser.add_argument("--check", action="store_true", help="只读检测网络状态，不提交凭据")
    parser.add_argument("--diagnostics", action="store_true", help="打印配置与日志路径")
    parser.add_argument("--minimized", action="store_true", help="启动后直接进托盘")
    parser.add_argument("--config", type=Path, default=None, help="指定配置文件路径")
    parser.add_argument("--log-level", default="INFO", help="日志级别")
    args = parser.parse_args(argv)

    level = getattr(logging, str(args.log_level).upper(), logging.INFO)
    try:
        configure_logging(default_log_file(), level=level)
    except Exception:
        # 日志目录不可写 / 磁盘满时不能让程序直接起不来。
        fallback = logging.getLogger("campuslink")
        fallback.setLevel(level)
        fallback.addHandler(logging.NullHandler())
    _log.info("启动 CampusLink，参数=%s", vars(args))

    context = build_context(args.config)

    if args.diagnostics:
        print_diagnostics(context)
        return 0
    if args.check:
        return run_check(context)
    return run_gui(context, minimized=args.minimized)


if __name__ == "__main__":
    raise SystemExit(main())
