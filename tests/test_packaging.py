"""打包与交付物检查（任务书测试要求第 12 条）。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "campuslink.spec"
BUILD_SCRIPT = ROOT / "scripts" / "build_exe.ps1"
EXE = ROOT / "dist" / "CampusLink.exe"


def test_required_deliverables_exist() -> None:
    for path in [
        ROOT / "README.md",
        ROOT / "requirements.txt",
        ROOT / "pyproject.toml",
        ROOT / "docs" / "known-limitations.md",
        ROOT / "src" / "campuslink" / "app.py",
        SPEC,
        BUILD_SCRIPT,
    ]:
        assert path.exists(), f"缺少交付物: {path}"


def test_spec_is_valid_python() -> None:
    source = SPEC.read_text(encoding="utf-8")
    compile(source, str(SPEC), "exec")
    assert "console=False" in source
    assert "win32cred" in source


def test_build_script_mentions_pyinstaller_and_output() -> None:
    text = BUILD_SCRIPT.read_text(encoding="utf-8")
    assert "PyInstaller" in text
    assert "CampusLink.exe" in text


def test_package_metadata_pins_dependencies() -> None:
    tomllib = pytest.importorskip("tomllib")
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = data["project"]
    assert project["name"] == "campuslink"
    assert project["version"] == "0.1.0"
    dependencies = " ".join(project["dependencies"])
    for expected in ("requests", "PySide6", "pywin32"):
        assert expected in dependencies, expected
    assert project["requires-python"].startswith(">=3.10")


def test_cli_help_runs() -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [sys.executable, "-m", "campuslink", "--help"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "--check" in (result.stdout or "")


def test_qt_ui_module_imports_when_available() -> None:
    pytest.importorskip("PySide6")
    import campuslink.ui.qt_app as qt_app

    assert hasattr(qt_app, "MainWindow")
    assert hasattr(qt_app, "TrayController")


def test_diagnostics_runs_with_redirected_profile(tmp_path) -> None:
    """诊断模式必须能在不读写用户真实配置/日志的情况下跑通。"""
    env = dict(os.environ)
    env.update(
        {
            "APPDATA": str(tmp_path / "roaming"),
            "LOCALAPPDATA": str(tmp_path / "local"),
            "PYTHONPATH": str(ROOT / "src"),
            "PYTHONIOENCODING": "utf-8",
        }
    )
    result = subprocess.run(
        [sys.executable, "-m", "campuslink", "--diagnostics"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout or ""
    assert "192.0.2.10" in out
    assert "开启（默认）" in out or "关闭（默认）" in out
    assert "203.0.113.50" not in out


@pytest.mark.skipif(not EXE.exists(), reason="EXE 尚未构建（运行 scripts/build_exe.ps1）")
def test_built_exe_exists_and_is_nonempty() -> None:
    assert EXE.is_file()
    assert EXE.stat().st_size > 1_000_000


@pytest.mark.skipif(not EXE.exists(), reason="EXE 尚未构建（运行 scripts/build_exe.ps1）")
def test_built_exe_runs_diagnostics() -> None:
    """真正启动打包产物验证退出码（--diagnostics 不做任何网络访问）。"""
    result = subprocess.run(
        [str(EXE), "--diagnostics"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    assert result.returncode == 0, result.stderr

