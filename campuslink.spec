# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（单文件、无控制台窗口）。

认证业务逻辑与 GUI 分离，因此这里只需要保证 campuslink 包和
win32cred 被正确收集；PySide6 由 PyInstaller 自带的 hook 处理。
"""

hiddenimports = ["win32cred", "win32timezone"]

a = Analysis(
    ["scripts/entrypoint.py"],
    pathex=["src"],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "PyInstaller", "tkinter"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="CampusLink",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version="scripts/version_info.txt",
)
