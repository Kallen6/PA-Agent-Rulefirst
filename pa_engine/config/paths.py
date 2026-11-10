"""路径常量（与 v1 保持一致的组织方式：全部基于项目根）。"""
from __future__ import annotations

from pathlib import Path

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent.parent

MD_DIR: Path = PROJECT_ROOT / "md"
CONFIG_DIR: Path = PROJECT_ROOT / "config"
CONFIG_JSON_PATH: Path = CONFIG_DIR / "settings.json"
SETTINGS_JSON_PATH: Path = CONFIG_JSON_PATH  # 语义化别名（对齐 v1）
RECORDS_DIR: Path = PROJECT_ROOT / "records"
LOGS_DIR: Path = PROJECT_ROOT / "logs"

# v1 项目默认位置（可用 config/settings.json 的 v1.path 覆盖）
V1_ROOT_DEFAULT: Path = PROJECT_ROOT.parent / "PA_Agent"
