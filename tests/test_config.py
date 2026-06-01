"""Tests for eagle_watcher/config.py"""

import os
import threading
import yaml
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------

class TestLoadConfig:
    """load_config() — 正常读取 / 文件缺失 / YAML损坏 / 空文件"""

    def test_load_existing(self, mock_data_dir):
        """正常读取已存在的 config.yaml"""
        from eagle_watcher.config import load_config
        cfg = load_config()
        assert cfg["eagle"]["host"] == "http://localhost:41595"
        assert cfg["paths"]["watch_interval"] == 2.0

    def test_load_missing_file(self, mock_data_dir):
        """config.yaml 不存在 → 返回默认配置"""
        from eagle_watcher.config import load_config, CONFIG_PATH
        CONFIG_PATH.unlink(missing_ok=True)
        cfg = load_config()
        assert cfg["eagle"]["host"] == "http://localhost:41595"
        assert cfg["eagle"]["token"] == ""

    def test_load_corrupt_yaml(self, mock_data_dir):
        """config.yaml 内容损坏 → 返回默认配置（不抛异常）"""
        from eagle_watcher.config import load_config, CONFIG_PATH
        CONFIG_PATH.write_text("{ broken: yaml: [\n")
        cfg = load_config()
        assert cfg["eagle"]["host"] == "http://localhost:41595"

    def test_load_empty_file(self, mock_data_dir):
        """config.yaml 为空 → yaml.safe_load 返回 None → 回退默认配置"""
        from eagle_watcher.config import load_config, CONFIG_PATH
        CONFIG_PATH.write_text("")
        cfg = load_config()
        assert cfg["eagle"]["host"] == "http://localhost:41595"
        assert cfg["eagle"]["token"] == ""


# ---------------------------------------------------------------------------
# save_config
# ---------------------------------------------------------------------------

class TestSaveConfig:
    """save_config() — 原子写入 + 内容一致性"""

    def test_save_and_reload(self, mock_data_dir):
        """保存后重新加载，内容一致"""
        from eagle_watcher.config import save_config, load_config
        new_cfg = {
            "eagle": {"host": "http://127.0.0.1:41595", "token": "abc123"},
            "paths": {"downloads": "/tmp", "watch_interval": 5.0},
            "notifications": {"inbox_reminder": False, "import_success": False},
            "ai": {"api_key": "sk-xxx"},
        }
        save_config(new_cfg)
        loaded = load_config()
        assert loaded == new_cfg

    def test_atomic_write_no_temp_left(self, mock_data_dir):
        """原子写入后，data_dir 中不应残留 .tmp 文件"""
        from eagle_watcher.config import save_config, DATA_DIR
        save_config({"eagle": {"host": "http://localhost:41595", "token": ""},
                      "paths": {"downloads": str(Path.home() / "Downloads"), "watch_interval": 2.0},
                      "notifications": {}, "ai": {}})
        tmp_files = [p for p in DATA_DIR.iterdir() if p.suffix == ".tmp"]
        assert len(tmp_files) == 0, f"残留临时文件: {tmp_files}"

    def test_save_default_config_roundtrip(self, mock_data_dir):
        """保存默认格式的配置，回读 yield 合法 YAML"""
        from eagle_watcher.config import save_config, load_config
        cfg = {
            "eagle": {"host": "http://localhost:41595", "token": ""},
            "paths": {"downloads": str(Path.home() / "Downloads"), "watch_interval": 2.0},
            "notifications": {"inbox_reminder": True, "import_success": True},
            "ai": {"api_key": ""},
        }
        save_config(cfg)
        reloaded = load_config()
        assert reloaded == cfg


# ---------------------------------------------------------------------------
# validate_config
# ---------------------------------------------------------------------------

class TestValidateConfig:
    """validate_config() — 各错误分支"""

    def test_valid_config(self, mock_data_dir):
        """完整有效的配置 → 返回空列表"""
        from eagle_watcher.config import validate_config
        cfg = {
            "eagle": {"host": "http://localhost:41595", "token": "some_token"},
            "paths": {"downloads": str(Path.home() / "Downloads"), "watch_interval": 2.0},
        }
        errors, warnings = validate_config(cfg)
        assert errors == []

    def test_missing_eagle_key(self, mock_data_dir):
        """缺少 eagle 键（cfg.get 回退 {}）→ host + token 错误"""
        from eagle_watcher.config import validate_config
        errors, warnings = validate_config({"paths": {"downloads": str(Path.home() / "Downloads")}})
        assert "缺少 eagle.host 配置" in errors
        assert any("缺少 eagle.token" in e for e in warnings)

    def test_missing_host(self, mock_data_dir):
        """eagle 下缺少 host"""
        from eagle_watcher.config import validate_config
        cfg = {"eagle": {"token": "x"}, "paths": {"downloads": str(Path.home() / "Downloads")}}
        errors, warnings = validate_config(cfg)
        assert "缺少 eagle.host 配置" in errors

    def test_empty_host(self, mock_data_dir):
        """host 为空字符串"""
        from eagle_watcher.config import validate_config
        cfg = {"eagle": {"host": "", "token": "x"},
               "paths": {"downloads": str(Path.home() / "Downloads")}}
        errors, warnings = validate_config(cfg)
        assert "缺少 eagle.host 配置" in errors

    def test_missing_token(self, mock_data_dir):
        """eagle 下缺少 token"""
        from eagle_watcher.config import validate_config
        cfg = {"eagle": {"host": "http://localhost:41595"},
               "paths": {"downloads": str(Path.home() / "Downloads")}}
        errors, warnings = validate_config(cfg)
        assert any("缺少 eagle.token" in e for e in warnings)

    def test_empty_token(self, mock_data_dir):
        """token 为空字符串"""
        from eagle_watcher.config import validate_config
        cfg = {"eagle": {"host": "http://localhost:41595", "token": ""},
               "paths": {"downloads": str(Path.home() / "Downloads")}}
        errors, warnings = validate_config(cfg)
        assert any("缺少 eagle.token" in e for e in warnings)

    def test_missing_paths(self, mock_data_dir):
        """缺少 paths 键 → downloads + interval 都回退默认，仅报 downloads 缺失"""
        from eagle_watcher.config import validate_config
        cfg = {"eagle": {"host": "http://localhost:41595", "token": "x"}}
        errors, warnings = validate_config(cfg)
        assert "缺少 paths.downloads 配置" in errors

    def test_nonexistent_downloads_dir(self, mock_data_dir):
        """downloads 指向不存在的目录"""
        from eagle_watcher.config import validate_config
        cfg = {"eagle": {"host": "http://localhost:41595", "token": "x"},
               "paths": {"downloads": "/tmp/nonexistent-dir-xyz-999"}}
        errors, warnings = validate_config(cfg)
        assert any("下载目录不存在" in e for e in errors)

    def test_interval_is_string(self, mock_data_dir):
        """watch_interval 为字符串 → 无效"""
        from eagle_watcher.config import validate_config
        cfg = {"eagle": {"host": "http://localhost:41595", "token": "x"},
               "paths": {"downloads": str(Path.home() / "Downloads"), "watch_interval": "abc"}}
        errors, warnings = validate_config(cfg)
        assert any("监控间隔配置无效" in e for e in errors)

    def test_interval_is_zero(self, mock_data_dir):
        """watch_interval 为 0 → <=0 → 无效"""
        from eagle_watcher.config import validate_config
        cfg = {"eagle": {"host": "http://localhost:41595", "token": "x"},
               "paths": {"downloads": str(Path.home() / "Downloads"), "watch_interval": 0}}
        errors, warnings = validate_config(cfg)
        assert any("监控间隔配置无效" in e for e in errors)

    def test_interval_is_negative(self, mock_data_dir):
        """watch_interval 为负数 → 无效"""
        from eagle_watcher.config import validate_config
        cfg = {"eagle": {"host": "http://localhost:41595", "token": "x"},
               "paths": {"downloads": str(Path.home() / "Downloads"), "watch_interval": -1}}
        errors, warnings = validate_config(cfg)
        assert any("监控间隔配置无效" in e for e in errors)

    def test_extra_dirs_valid_list(self, mock_data_dir):
        """extra_watch_dirs 为有效列表 → 无警告"""
        from eagle_watcher.config import validate_config
        cfg = {"eagle": {"host": "http://localhost:41595", "token": "x"},
               "paths": {"downloads": str(Path.home() / "Downloads"),
                         "extra_watch_dirs": ["/tmp", "/Users"],
                         "watch_interval": 2.0}}
        errors, warnings = validate_config(cfg)
        assert not any("extra_watch_dirs" in w for w in warnings)

    def test_extra_dirs_not_list(self, mock_data_dir):
        """extra_watch_dirs 不是列表 → 警告"""
        from eagle_watcher.config import validate_config
        cfg = {"eagle": {"host": "http://localhost:41595", "token": "x"},
               "paths": {"downloads": str(Path.home() / "Downloads"),
                         "extra_watch_dirs": "not-a-list",
                         "watch_interval": 2.0}}
        errors, warnings = validate_config(cfg)
        assert any("extra_watch_dirs 应为列表" in w for w in warnings)


# ---------------------------------------------------------------------------
# _default_config
# ---------------------------------------------------------------------------

class TestDefaultConfig:

    def test_extra_dirs_in_defaults(self):
        """默认配置包含空的 extra_watch_dirs 列表"""
        from eagle_watcher.config import _default_config
        cfg = _default_config()
        assert "extra_watch_dirs" in cfg["paths"]
        assert cfg["paths"]["extra_watch_dirs"] == []


# ---------------------------------------------------------------------------
# load_themes
# ---------------------------------------------------------------------------

class TestLoadThemes:
    """load_themes() — 正常 / 文件缺失 / 损坏 / 旧格式迁移"""

    def test_load_normal(self, mock_data_dir):
        """正常读取 themes.yaml"""
        from eagle_watcher.config import load_themes
        data = load_themes()
        assert "categories" in data
        assert "projects" in data
        assert "武安侯" in data["projects"]
        assert data["projects"]["武安侯"]["category"] == "历史"

    def test_load_missing_file(self, mock_data_dir):
        """themes.yaml 不存在 → 返回空模板"""
        from eagle_watcher.config import load_themes, THEMES_PATH
        THEMES_PATH.unlink(missing_ok=True)
        data = load_themes()
        assert data == {"categories": {}, "projects": {}}

    def test_load_corrupt_yaml(self, mock_data_dir):
        """themes.yaml 损坏 → 返回空模板（不抛异常）"""
        from eagle_watcher.config import load_themes, THEMES_PATH
        THEMES_PATH.write_text("{broken: yaml: [\n")
        data = load_themes()
        assert data == {"categories": {}, "projects": {}}

    def test_load_empty_file(self, mock_data_dir):
        """themes.yaml 为空 → yaml.safe_load 返回 None → 回退空模板"""
        from eagle_watcher.config import load_themes, THEMES_PATH
        THEMES_PATH.write_text("")
        data = load_themes()
        assert data == {"categories": {}, "projects": {}}

    def test_old_format_migration(self, mock_data_dir):
        """旧格式（只有 themes 键）→ 自动迁移为 categories / projects, 且持久化到文件"""
        from eagle_watcher.config import load_themes, THEMES_PATH
        old_data = {
            "themes": {
                "武安侯": {"category": "历史", "default_tags": ["战国", "武将"], "created_at": "2025-01-01"},
                "秦始皇": {"category": "历史", "default_tags": ["秦朝", "文物"], "created_at": "2025-01-02"},
            }
        }
        THEMES_PATH.write_text(yaml.dump(old_data, allow_unicode=True))
        data = load_themes()
        # 内存中已迁移
        assert "themes" not in data
        assert "categories" in data
        assert "projects" in data
        assert data["projects"]["武安侯"]["category"] == "历史"
        assert data["projects"]["秦始皇"]["default_tags"] == ["秦朝", "文物"]
        # 文件也已被持久化
        with open(THEMES_PATH) as f:
            on_disk = yaml.safe_load(f)
        assert "themes" not in on_disk
        assert "categories" in on_disk
        assert "projects" in on_disk


# ---------------------------------------------------------------------------
# save_themes
# ---------------------------------------------------------------------------

class TestSaveThemes:
    """save_themes() — 原子写入 + 内容一致性"""

    def test_save_and_reload(self, mock_data_dir):
        """保存 themes 后重新加载，内容一致"""
        from eagle_watcher.config import save_themes, load_themes
        data = {
            "categories": {"历史": {"eagle_folder": "历史", "created_at": "2025-01-01"}},
            "projects": {"武安侯": {"category": "历史", "default_tags": ["战国"], "created_at": "2025-01-01"}},
        }
        save_themes(data)
        loaded = load_themes()
        assert loaded == data

    def test_save_no_temp_left(self, mock_data_dir):
        """保存后无 .tmp 残留"""
        from eagle_watcher.config import save_themes, DATA_DIR
        save_themes({"categories": {}, "projects": {}})
        tmp_files = [p for p in DATA_DIR.iterdir() if p.suffix == ".tmp"]
        assert len(tmp_files) == 0


# ---------------------------------------------------------------------------
# 分类管理 CRUD
# ---------------------------------------------------------------------------

class TestCategories:
    """get_categories / create_category / delete_category / get_category_names / get_category_info"""

    def test_get_categories_empty_by_default(self, mock_data_dir):
        """空 themes → get_categories 返回 {}"""
        from eagle_watcher.config import get_categories, THEMES_PATH
        THEMES_PATH.write_text(yaml.dump({"categories": {}, "projects": {}}))
        assert get_categories() == {}

    def test_create_category(self, mock_data_dir):
        """create_category 后 get_categories 能读到"""
        from eagle_watcher.config import create_category, get_categories
        create_category("历史", eagle_folder="历史资料")
        cats = get_categories()
        assert "历史" in cats
        assert cats["历史"]["eagle_folder"] == "历史资料"

    def test_create_category_default_folder(self, mock_data_dir):
        """不传 eagle_folder → 使用 name 作为默认值"""
        from eagle_watcher.config import create_category, get_categories
        create_category("设计参考")
        assert get_categories()["设计参考"]["eagle_folder"] == "设计参考"

    def test_create_category_with_folder_id(self, mock_data_dir):
        """传入 folder_id → 记录到 entry"""
        from eagle_watcher.config import create_category, get_categories
        create_category("设计", folder_id="abc-123")
        assert get_categories()["设计"]["folder_id"] == "abc-123"

    def test_delete_category(self, mock_data_dir):
        """删除已存在的分类"""
        from eagle_watcher.config import create_category, delete_category, get_categories
        create_category("历史")
        delete_category("历史")
        assert "历史" not in get_categories()

    def test_delete_nonexistent_category(self, mock_data_dir):
        """删除不存在的分类 → 不抛异常"""
        from eagle_watcher.config import delete_category, get_categories
        delete_category("不存在的分类")
        assert get_categories() == {}

    def test_get_category_names(self, mock_data_dir):
        """get_category_names 返回名称列表"""
        from eagle_watcher.config import create_category, get_category_names
        create_category("历史")
        create_category("设计")
        names = get_category_names()
        assert "历史" in names
        assert "设计" in names

    def test_get_category_info(self, mock_data_dir):
        """get_category_info 返回详情或 None"""
        from eagle_watcher.config import create_category, get_category_info
        create_category("历史", eagle_folder="历史资料", folder_id="f-001")
        info = get_category_info("历史")
        assert info is not None
        assert info["eagle_folder"] == "历史资料"
        assert info["folder_id"] == "f-001"

    def test_get_category_info_missing(self, mock_data_dir):
        """不存在的分类 → None"""
        from eagle_watcher.config import get_category_info
        assert get_category_info("不存在的分类") is None


# ---------------------------------------------------------------------------
# 项目管理 CRUD
# ---------------------------------------------------------------------------

class TestProjects:
    """get_projects / create_project / delete_project / get_project_names / get_project_info"""

    def test_get_projects_existing(self, mock_data_dir):
        """读取初始 themes.yaml 中的项目"""
        from eagle_watcher.config import get_projects
        projs = get_projects()
        assert "武安侯" in projs
        assert projs["武安侯"]["category"] == "历史"

    def test_create_project(self, mock_data_dir):
        """create_project 后 get_projects 能读到"""
        from eagle_watcher.config import create_project, get_projects
        create_project("汉朝", "历史", tags=["汉代", "文物"])
        projs = get_projects()
        assert "汉朝" in projs
        assert projs["汉朝"]["category"] == "历史"
        assert projs["汉朝"]["default_tags"] == ["汉代", "文物"]

    def test_create_project_default_tags(self, mock_data_dir):
        """不传 tags → 默认空列表"""
        from eagle_watcher.config import create_project, get_projects
        create_project("汉朝", "历史")
        assert get_projects()["汉朝"]["default_tags"] == []

    def test_delete_project(self, mock_data_dir):
        """删除已存在的项目"""
        from eagle_watcher.config import create_project, delete_project, get_projects
        create_project("汉朝", "历史")
        delete_project("汉朝")
        assert "汉朝" not in get_projects()

    def test_delete_nonexistent_project(self, mock_data_dir):
        """删除不存在的项目 → 不抛异常"""
        from eagle_watcher.config import delete_project
        delete_project("不存在的项目")  # should not raise

    def test_delete_project_clears_current(self, mock_data_dir):
        """删除当前正在使用的项目 → get_current_project 返回 None"""
        from eagle_watcher.config import (create_project, delete_project,
                                          get_current_project, set_current_project)
        from eagle_watcher.services.state_manager import get_state_manager
        get_state_manager().set_current_project(None)  # reset

        create_project("汉朝", "历史")
        set_current_project("汉朝")
        assert get_current_project() == "汉朝"
        delete_project("汉朝")
        assert get_current_project() is None

    def test_get_project_names(self, mock_data_dir):
        """get_project_names 返回名称列表"""
        from eagle_watcher.config import create_project, get_project_names
        create_project("项目A", "分类1")
        create_project("项目B", "分类2")
        names = get_project_names()
        assert "项目A" in names
        assert "项目B" in names

    def test_get_project_info_with_category_info(self, mock_data_dir):
        """get_project_info 返回信息包含 eagle_folder（来自关联分类）"""
        from eagle_watcher.config import (create_category, create_project, get_project_info)
        create_category("历史", eagle_folder="历史资料")
        create_project("秦朝", "历史")
        info = get_project_info("秦朝")
        assert info is not None
        assert info["category"] == "历史"
        assert info["eagle_folder"] == "历史资料"

    def test_get_project_info_missing(self, mock_data_dir):
        """不存在的项目 → None"""
        from eagle_watcher.config import get_project_info
        assert get_project_info("不存在的项目") is None

    def test_get_project_info_missing_category(self, mock_data_dir):
        """项目关联的分类不存在 → eagle_folder 使用分类名本身"""
        from eagle_watcher.config import create_project, get_project_info
        create_project("秦朝", "不存在的分类")
        info = get_project_info("秦朝")
        assert info is not None
        assert info["eagle_folder"] == "不存在的分类"


# ---------------------------------------------------------------------------
# 当前项目
# ---------------------------------------------------------------------------

class TestCurrentProject:
    """get_current_project / set_current_project — 委托给 state_manager"""

    def test_default_is_none(self, mock_data_dir):
        """初始状态下 current_project 为 None"""
        from eagle_watcher.config import get_current_project
        from eagle_watcher.services.state_manager import get_state_manager
        get_state_manager().set_current_project(None)
        assert get_current_project() is None

    def test_set_and_get(self, mock_data_dir):
        """set_current_project 后再 get 返回相同值"""
        from eagle_watcher.config import get_current_project, set_current_project
        from eagle_watcher.services.state_manager import get_state_manager
        get_state_manager().set_current_project(None)

        set_current_project("武安侯")
        assert get_current_project() == "武安侯"

    def test_set_none(self, mock_data_dir):
        """设为 None 清除当前项目"""
        from eagle_watcher.config import get_current_project, set_current_project
        from eagle_watcher.services.state_manager import get_state_manager
        get_state_manager().set_current_project(None)

        set_current_project("武安侯")
        set_current_project(None)
        assert get_current_project() is None

    def test_is_persisted_to_state_file(self, mock_data_dir):
        """set_current_project 持久化到 state.json"""
        from eagle_watcher.config import set_current_project
        from eagle_watcher.services.state_manager import get_state_manager, STATE_PATH
        get_state_manager().set_current_project(None)

        set_current_project("秦始皇")
        with open(STATE_PATH) as f:
            import json
            state = json.load(f)
        assert state["current_project"] == "秦始皇"

    def test_legacy_current_theme_fallback(self, mock_data_dir):
        """若 current_project 为 None，但 current_theme 有值 → 回退到 current_theme"""
        from eagle_watcher.config import get_current_project
        from eagle_watcher.services.state_manager import get_state_manager, STATE_PATH
        import json
        # 直接写 state.json，模拟旧版遗留数据
        state = {"current_project": None, "current_theme": "武安侯", "set_at": None}
        with open(STATE_PATH, "w") as f:
            json.dump(state, f)
        # 重新实例化 StateManager：需要重置单例
        # 单例一旦创建，就不会重新读取文件，因此这里手动写文件后重新获取
        # 但 get_state_manager 返回已有实例，不会重读。我们通过内部访问重建。
        import eagle_watcher.services.state_manager as sm
        sm._instance = None  # 强制重建
        assert get_current_project() == "武安侯"
        sm._instance = None  # 清理，留给后续测试干净状态


# ---------------------------------------------------------------------------
# 线程安全 — 并发写入
# ---------------------------------------------------------------------------

class TestThreadSafety:
    """save_categories + save_projects 并发调用不丢数据"""

    def test_concurrent_save_categories_and_projects(self, mock_data_dir):
        """并发 create_category + create_project，锁保证两类数据不互相覆盖"""
        from eagle_watcher.config import create_category, create_project, get_categories, get_projects

        results = []

        def _create_category():
            try:
                create_category("cat_0", eagle_folder="folder_0")
                results.append(("cat", None))
            except Exception as e:
                results.append(("cat", e))

        def _create_project():
            try:
                create_project("proj_0", "测试分类", tags=["tag_0"])
                results.append(("proj", None))
            except Exception as e:
                results.append(("proj", e))

        t1 = threading.Thread(target=_create_category)
        t2 = threading.Thread(target=_create_project)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        errors = [err for _, err in results if err is not None]
        assert not errors, f"并发操作产生异常: {errors}"

        cats = get_categories()
        projs = get_projects()
        assert "cat_0" in cats
        assert "proj_0" in projs

        # 验证 themes.yaml 不是损坏的 YAML
        from eagle_watcher.config import THEMES_PATH
        with open(THEMES_PATH) as f:
            raw = f.read()
        assert raw.strip(), "themes.yaml 不应为空"
        parsed = yaml.safe_load(raw)
        assert parsed is not None
        assert "categories" in parsed
        assert "projects" in parsed
