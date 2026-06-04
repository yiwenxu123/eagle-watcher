"""配置管理：读 config.yaml + 管理分类/项目 + 读 themes.yaml"""

import logging
import os
import tempfile
import threading
import yaml
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from eagle_watcher.services.state_manager import get_state_manager

_LOG = logging.getLogger("config")
_themes_lock = threading.Lock()
_config_lock = threading.Lock()

DATA_DIR = Path.home() / ".eagle-watcher"
CONFIG_PATH = DATA_DIR / "config.yaml"
THEMES_PATH = DATA_DIR / "themes.yaml"
KNOWLEDGE_PATH = DATA_DIR / "knowledge.yaml"


def ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    with _config_lock:
        ensure_data_dir()
        if not CONFIG_PATH.exists():
            return _default_config()
        try:
            with open(CONFIG_PATH) as f:
                return yaml.safe_load(f) or _default_config()
        except Exception:
            _LOG.warning("config.yaml 读取失败，使用默认配置")
            return _default_config()


def _default_config() -> dict:
    return {
        "eagle": {
            "host": "http://localhost:41595",
            "token": "",
        },
        "paths": {
            "downloads": str(Path.home() / "Downloads"),
            "extra_watch_dirs": [],
            "watch_interval": 2.0,
        },
        "delete_after_import": "keep",
        "import_filters": {
            "extensions": [],  # 空列表=不过滤；填入如 [".jpg", ".png"] 则仅处理匹配的文件
            "skip_extensions": [".tmp", ".part", ".download", ".crdownload"],  # 始终跳过临时/下载中文件
        },
        "notifications": {
            "inbox_reminder": True,
            "import_success": True,
        },
        "ai": {
            "api_key": "",
        },
        "server": {
            "api_key": "",
        },
        "export": {
            "enabled": False,
            "dir": "",
            "auto": True,
            "structure": "theme",
            "themes": [],
            "max_size_bytes": 10 * 1024 * 1024 * 1024,  # 10GB
        },
    }


def save_config(cfg: dict):
    with _config_lock:
        ensure_data_dir()
        fd, tmp_path = tempfile.mkstemp(dir=str(DATA_DIR), suffix=".yaml.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                yaml.dump(cfg, f, allow_unicode=True)
            os.replace(tmp_path, str(CONFIG_PATH))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


# ────────── themes.yaml（主题列表）──────────

def load_themes() -> dict:
    ensure_data_dir()
    if not THEMES_PATH.exists():
        return {"categories": {}, "projects": {}}
    try:
        with open(THEMES_PATH) as f:
            data = yaml.safe_load(f) or {"categories": {}, "projects": {}}
    except Exception:
        _LOG.warning("themes.yaml 读取失败，使用默认配置")
        return {"categories": {}, "projects": {}}

    # 自动迁移旧格式（只有 themes 键，没有 categories/projects）
    if "themes" in data and "categories" not in data and "projects" not in data:
        _LOG.info("检测到旧版 themes.yaml 格式，自动迁移...")
        old_themes = data.pop("themes", {})
        new_projects = {}
        for name, info in old_themes.items():
            new_projects[name] = {
                "category": info.get("category", "未分类"),
                "default_tags": info.get("default_tags", []),
                "created_at": info.get("created_at", ""),
            }
        data["categories"] = {}
        data["projects"] = new_projects
        save_themes(data)
        _LOG.info("迁移完成: %d 个主题 → 项目", len(new_projects))

    return data


def save_themes(data: dict):
    ensure_data_dir()
    fd, tmp_path = tempfile.mkstemp(dir=str(DATA_DIR), suffix=".yaml.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, sort_keys=False)
        os.replace(tmp_path, str(THEMES_PATH))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def validate_config(cfg: dict) -> tuple[list[str], list[str]]:
    """验证配置文件，返回 (errors, warnings) 元组"""
    errors: list[str] = []
    warnings: list[str] = []

    # 验证 Eagle 配置
    eagle_cfg = cfg.get("eagle", {})
    host = eagle_cfg.get("host", "")
    if not host:
        errors.append("缺少 eagle.host 配置")
    else:
        parsed = urlparse(host)
        if not parsed.scheme:
            errors.append(f"eagle.host 缺少协议前缀（应为 http:// 或 https://）：{host}")
        elif parsed.scheme not in ("http", "https"):
            errors.append(f"eagle.host 协议不支持：{parsed.scheme}")
        elif not parsed.hostname:
            errors.append(f"eagle.host 格式无效：{host}")
    if not eagle_cfg.get("token"):
        warnings.append("缺少 eagle.token 配置，请在 Eagle 设置 → 开发者选项中获取")

    # 验证 AI 配置
    ai_cfg = cfg.get("ai", {})
    if not ai_cfg.get("api_key"):
        _LOG.warning("ai.api_key 未配置，AI 视觉分析不可用")

    # 验证 Server 配置（仅在配置存在时检查）
    server_cfg = cfg.get("server", {})
    if server_cfg and not server_cfg.get("api_key"):
        _LOG.warning("server.api_key 未配置，HTTP API 无认证保护（仅 localhost 安全）")

    # 验证路径配置
    paths_cfg = cfg.get("paths", {})
    if not paths_cfg.get("downloads"):
        errors.append("缺少 paths.downloads 配置")
    else:
        downloads = paths_cfg["downloads"]
        if not os.path.isdir(downloads):
            errors.append(f"下载目录不存在：{downloads}")

    # 验证额外监控目录
    extra_dirs = paths_cfg.get("extra_watch_dirs", [])
    if not isinstance(extra_dirs, list):
        warnings.append("paths.extra_watch_dirs 应为列表格式，将被忽略")
    else:
        for d in extra_dirs:
            if not isinstance(d, str) or not d.strip():
                warnings.append(f"paths.extra_watch_dirs 包含无效路径：{d}")

    # 验证监控间隔
    interval = paths_cfg.get("watch_interval", 2.0)
    if not isinstance(interval, (int, float)) or interval <= 0:
        errors.append(f"监控间隔配置无效：{interval}")

    return errors, warnings


# ────────── 分类管理（categories = Eagle 文件夹）──────────

def get_categories() -> dict:
    data = load_themes()
    return data.get("categories", {})


def get_category_names() -> list[str]:
    return list(get_categories().keys())


def get_category_info(name: str) -> Optional[dict]:
    return get_categories().get(name)


def save_categories(data: dict):
    with _themes_lock:
        themes = load_themes()
        themes["categories"] = data
        save_themes(themes)


def create_category(name: str, eagle_folder: Optional[str] = None,
                    folder_id: Optional[str] = None):
    from datetime import datetime
    data = get_categories()
    entry = {
        "eagle_folder": eagle_folder or name,
        "created_at": datetime.now().strftime("%Y-%m-%d"),
    }
    if folder_id:
        entry["folder_id"] = folder_id
    data[name] = entry
    save_categories(data)


def delete_category(name: str):
    data = get_categories()
    data.pop(name, None)
    save_categories(data)


# ────────── 项目管理（projects = Eagle 标签）──────────

def get_projects() -> dict:
    data = load_themes()
    return data.get("projects", {})


def get_project_names() -> list[str]:
    return list(get_projects().keys())


def get_project_info(name: str) -> Optional[dict]:
    projects = get_projects()
    project = projects.get(name)
    if not project:
        return None
    result = dict(project)
    category = project.get("category", "")
    cat_info = get_category_info(category)
    result["eagle_folder"] = cat_info.get("eagle_folder", category) if cat_info else category
    return result


def save_projects(data: dict):
    with _themes_lock:
        themes = load_themes()
        themes["projects"] = data
        save_themes(themes)


def create_project(name: str, category: str, tags: Optional[list[str]] = None):
    from datetime import datetime
    data = get_projects()
    data[name] = {
        "category": category,
        "default_tags": tags or [],
        "created_at": datetime.now().strftime("%Y-%m-%d"),
    }
    save_projects(data)


def delete_project(name: str):
    data = get_projects()
    data.pop(name, None)
    save_projects(data)
    if get_current_project() == name:
        set_current_project(None)


# ────────── 当前项目（替代旧当前主题）──────────

def get_current_project() -> Optional[str]:
    return get_state_manager().get_current_project()


def set_current_project(name: Optional[str]):
    get_state_manager().set_current_project(name)
