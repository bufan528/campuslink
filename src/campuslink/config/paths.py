"""应用数据目录解析。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

APP_DIR_NAME: Final[str] = "CampusLink"


def _roaming_root() -> Path:
    base = os.environ.get("APPDATA")
    return Path(base) if base else Path.home() / "AppData" / "Roaming"


def _local_root() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) if base else Path.home() / "AppData" / "Local"


def config_dir() -> Path:
    return _roaming_root() / APP_DIR_NAME


def default_config_file() -> Path:
    return config_dir() / "config.json"


def log_dir() -> Path:
    return _local_root() / APP_DIR_NAME / "logs"


def default_log_file() -> Path:
    return log_dir() / "campuslink.log"
