"""配置管理：读 config.yaml + 管理主题 + 读 themes.yaml"""

import os
import yaml
from pathlib import Path
from typing import Optional

from services.state_manager import get_state_manager

DATA_DIR = Path.home() / ".eagle-watcher"
CONFIG_PATH = DATA_DIR / "config.yaml"
THEMES_PATH = DATA_DIR / "themes.yaml"
KNOWLEDGE_PATH = DATA_DIR / "knowledge.yaml"


def ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    ensure_data_dir()
    if not CONFIG_PATH.exists():
        return _default_config()
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f) or _default_config()


def _default_config() -> dict:
    return {
        "eagle": {
            "host": "http://localhost:41595",
            "token": "",
        },
        "paths": {
            "downloads": str(Path.home() / "Downloads"),
            "watch_interval": 2.0,
        },
        "notifications": {
            "inbox_reminder": True,
            "import_success": False,
        },
    }


def save_config(cfg: dict):
    ensure_data_dir()
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(cfg, f, allow_unicode=True)


def get_current_theme() -> Optional[str]:
    return get_state_manager().get_current_theme()


def set_current_theme(theme: Optional[str]):
    get_state_manager().set_current_theme(theme)


# ────────── themes.yaml（主题列表）──────────

def load_themes() -> dict:
    ensure_data_dir()
    if not THEMES_PATH.exists():
        return {"themes": {}}
    with open(THEMES_PATH) as f:
        return yaml.safe_load(f) or {"themes": {}}


def save_themes(data: dict):
    ensure_data_dir()
    with open(THEMES_PATH, "w") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False)


def get_theme_names() -> list[str]:
    return list(load_themes().get("themes", {}).keys())


def validate_config(cfg: dict) -> list[str]:
    """验证配置文件，返回错误列表"""
    errors = []

    # 验证 Eagle 配置
    eagle_cfg = cfg.get("eagle", {})
    if not eagle_cfg.get("host"):
        errors.append("缺少 eagle.host 配置")

    # 验证路径配置
    paths_cfg = cfg.get("paths", {})
    if not paths_cfg.get("downloads"):
        errors.append("缺少 paths.downloads 配置")
    else:
        downloads = paths_cfg["downloads"]
        if not os.path.isdir(downloads):
            errors.append(f"下载目录不存在：{downloads}")

    # 验证监控间隔
    interval = paths_cfg.get("watch_interval", 2.0)
    if not isinstance(interval, (int, float)) or interval <= 0:
        errors.append(f"监控间隔配置无效：{interval}")

    return errors
