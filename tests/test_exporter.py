"""Tests for exporter.py — 导出工作区模块"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from eagle_watcher.exporter import export_file, get_export_status, clear_export_dir, export_by_theme


# ════════════════════════════════════════════════════════════
#  export_file
# ════════════════════════════════════════════════════════════


class TestExportFile:
    """文件导出核心逻辑"""

    def _make_cfg(self, tmp_path, enabled=True, structure="theme", themes=None):
        export_dir = tmp_path / "export"
        export_dir.mkdir(exist_ok=True)
        cfg = {
            "export": {
                "enabled": enabled,
                "dir": str(export_dir),
                "auto": True,
                "structure": structure,
            }
        }
        if themes is not None:
            cfg["export"]["themes"] = themes
        return cfg

    def _make_source(self, tmp_path, name="test.jpg", content=b"fake image"):
        src = tmp_path / name
        src.write_bytes(content)
        return str(src)

    def test_export_basic(self, tmp_path):
        """正常 copy 文件到导出目录"""
        cfg = self._make_cfg(tmp_path)
        src = self._make_source(tmp_path)
        result = export_file(src, "武安侯", "test.jpg", cfg)
        assert result is not None
        assert os.path.exists(result)
        assert "武安侯" in result
        assert Path(result).read_bytes() == b"fake image"

    def test_export_disabled(self, tmp_path):
        """enabled=false 时不导出"""
        cfg = self._make_cfg(tmp_path, enabled=False)
        src = self._make_source(tmp_path)
        result = export_file(src, "武安侯", "test.jpg", cfg)
        assert result is None

    def test_export_empty_dir(self, tmp_path):
        """dir 为空时不导出"""
        cfg = {"export": {"enabled": True, "dir": "", "auto": True, "structure": "theme"}}
        src = self._make_source(tmp_path)
        result = export_file(src, "武安侯", "test.jpg", cfg)
        assert result is None

    def test_export_skip_duplicate(self, tmp_path):
        """同名同大小文件跳过"""
        cfg = self._make_cfg(tmp_path)
        src = self._make_source(tmp_path)
        r1 = export_file(src, "武安侯", "test.jpg", cfg)
        r2 = export_file(src, "武安侯", "test.jpg", cfg)
        assert r1 == r2  # 返回同一路径，不重复 copy

    def test_export_theme_structure(self, tmp_path):
        """structure=theme 时按主题建子目录"""
        cfg = self._make_cfg(tmp_path, structure="theme")
        src = self._make_source(tmp_path)
        result = export_file(src, "秦始皇", "img.png", cfg)
        assert "秦始皇" in result

    def test_export_flat_structure(self, tmp_path):
        """structure=flat 时直接放在导出根目录"""
        cfg = self._make_cfg(tmp_path, structure="flat")
        src = self._make_source(tmp_path)
        result = export_file(src, "秦始皇", "img.png", cfg)
        parent = Path(result).parent
        assert parent == Path(cfg["export"]["dir"])

    def test_export_date_structure(self, tmp_path):
        """structure=date 时按日期建子目录"""
        cfg = self._make_cfg(tmp_path, structure="date")
        src = self._make_source(tmp_path)
        result = export_file(src, "秦始皇", "img.png", cfg)
        # 目录名应包含日期格式
        parent_name = Path(result).parent.name
        assert len(parent_name) == 10  # YYYY-MM-DD

    def test_export_no_theme_defaults_to_uncategorized(self, tmp_path):
        """theme 为空时放到"未分类"目录"""
        cfg = self._make_cfg(tmp_path)
        src = self._make_source(tmp_path)
        result = export_file(src, "", "test.jpg", cfg)
        assert "未分类" in result

    def test_export_preserves_mtime(self, tmp_path):
        """copy2 保留文件修改时间"""
        cfg = self._make_cfg(tmp_path)
        src = self._make_source(tmp_path)
        src_mtime = os.path.getmtime(src)
        result = export_file(src, "T", "test.jpg", cfg)
        dst_mtime = os.path.getmtime(result)
        assert abs(src_mtime - dst_mtime) < 1

    def test_export_theme_filter_match(self, tmp_path):
        """theme 在过滤列表中时正常导出"""
        cfg = self._make_cfg(tmp_path, themes=["武安侯", "秦始皇"])
        src = self._make_source(tmp_path)
        result = export_file(src, "武安侯", "test.jpg", cfg)
        assert result is not None
        assert "武安侯" in result

    def test_export_theme_filter_no_match(self, tmp_path):
        """theme 不在过滤列表中时跳过"""
        cfg = self._make_cfg(tmp_path, themes=["武安侯"])
        src = self._make_source(tmp_path)
        result = export_file(src, "海报参考", "test.jpg", cfg)
        assert result is None

    def test_export_theme_filter_empty_list(self, tmp_path):
        """空过滤列表 = 全量导出"""
        cfg = self._make_cfg(tmp_path, themes=[])
        src = self._make_source(tmp_path)
        result = export_file(src, "任意主题", "test.jpg", cfg)
        assert result is not None


# ════════════════════════════════════════════════════════════
#  get_export_status
# ════════════════════════════════════════════════════════════


class TestGetExportStatus:
    """导出工作区统计"""

    def test_empty_status(self, tmp_path):
        export_dir = tmp_path / "export"
        export_dir.mkdir()
        cfg = {"export": {"enabled": True, "dir": str(export_dir), "auto": True, "structure": "theme"}}
        status = get_export_status(cfg)
        assert status["file_count"] == 0
        assert status["size_bytes"] == 0
        assert status["themes"] == {}

    def test_counts_files(self, tmp_path):
        export_dir = tmp_path / "export"
        (export_dir / "武安侯").mkdir(parents=True)
        (export_dir / "武安侯" / "a.jpg").write_bytes(b"aaa")
        (export_dir / "武安侯" / "b.png").write_bytes(b"bbbb")
        cfg = {"export": {"enabled": True, "dir": str(export_dir), "auto": True, "structure": "theme"}}
        status = get_export_status(cfg)
        assert status["file_count"] == 2
        assert status["size_bytes"] == 7
        assert status["themes"]["武安侯"] == 2

    def test_disabled_config(self, tmp_path):
        cfg = {"export": {"enabled": False, "dir": str(tmp_path), "auto": True, "structure": "theme"}}
        status = get_export_status(cfg)
        assert status["enabled"] is False

    def test_nonexistent_dir(self, tmp_path):
        cfg = {"export": {"enabled": True, "dir": str(tmp_path / "nope"), "auto": True, "structure": "theme"}}
        status = get_export_status(cfg)
        assert status["file_count"] == 0


# ════════════════════════════════════════════════════════════
#  clear_export_dir
# ════════════════════════════════════════════════════════════


class TestClearExportDir:
    """清空导出工作区"""

    def test_clear_removes_all(self, tmp_path):
        export_dir = tmp_path / "export"
        (export_dir / "sub1").mkdir(parents=True)
        (export_dir / "sub2").mkdir(parents=True)
        (export_dir / "sub1" / "a.jpg").write_bytes(b"a")
        (export_dir / "sub2" / "b.png").write_bytes(b"b")
        cfg = {"export": {"enabled": True, "dir": str(export_dir)}}
        result = clear_export_dir(cfg)
        assert result["cleared"] == 2
        assert list(export_dir.iterdir()) == []

    def test_clear_empty_dir(self, tmp_path):
        export_dir = tmp_path / "export"
        export_dir.mkdir()
        cfg = {"export": {"enabled": True, "dir": str(export_dir)}}
        result = clear_export_dir(cfg)
        assert result["cleared"] == 0

    def test_clear_no_dir_configured(self, tmp_path):
        cfg = {"export": {"enabled": True, "dir": ""}}
        result = clear_export_dir(cfg)
        assert result["cleared"] == 0
        assert "error" in result


# ════════════════════════════════════════════════════════════
#  export_by_theme
# ════════════════════════════════════════════════════════════


class TestExportByTheme:
    """按主题批量导出"""

    def _make_cfg(self, tmp_path):
        export_dir = tmp_path / "export"
        export_dir.mkdir(exist_ok=True)
        return {
            "export": {
                "enabled": True,
                "dir": str(export_dir),
                "auto": True,
                "structure": "theme",
            }
        }

    def _mock_eagle_api(self, items, file_paths=None):
        """创建 mock EagleAPI。file_paths: {item_id: path} 映射"""
        api = MagicMock()
        api.get_or_create_folder.return_value = "folder-123"
        api.list_items.return_value = items
        if file_paths:
            api.get_item_file_path.side_effect = lambda item_id, name, ext: file_paths.get(item_id)
        else:
            api.get_item_file_path.return_value = None
        return api

    @patch("eagle_watcher.config.get_project_info")
    def test_export_basic(self, mock_proj, tmp_path):
        """正常批量导出"""
        mock_proj.return_value = {"eagle_folder": "历史", "category": "历史"}
        cfg = self._make_cfg(tmp_path)
        # 创建源文件
        src = tmp_path / "source"
        src.mkdir()
        (src / "img.jpg").write_bytes(b"fake")
        api = self._mock_eagle_api(
            [{"id": "ITEM0001", "name": "img", "ext": "jpg"}],
            file_paths={"ITEM0001": str(src / "img.jpg")},
        )
        result = export_by_theme("武安侯", api, cfg)
        assert result["exported"] == 1
        assert result["skipped"] == 0
        assert result["error"] is None

    @patch("eagle_watcher.config.get_project_info")
    def test_export_skip_existing(self, mock_proj, tmp_path):
        """同名同大小文件跳过"""
        mock_proj.return_value = {"eagle_folder": "历史", "category": "历史"}
        cfg = self._make_cfg(tmp_path)
        src = tmp_path / "source"
        src.mkdir()
        (src / "img.jpg").write_bytes(b"fake")
        api = self._mock_eagle_api(
            [{"id": "ITEM0001", "name": "img", "ext": "jpg"}],
            file_paths={"ITEM0001": str(src / "img.jpg")},
        )
        export_by_theme("武安侯", api, cfg)
        result = export_by_theme("武安侯", api, cfg)
        assert result["exported"] == 0
        assert result["skipped"] == 1

    @patch("eagle_watcher.config.get_project_info")
    def test_export_no_dir(self, mock_proj, tmp_path):
        """未设置导出目录"""
        mock_proj.return_value = {"eagle_folder": "历史"}
        cfg = {"export": {"enabled": True, "dir": ""}}
        api = self._mock_eagle_api([])
        result = export_by_theme("武安侯", api, cfg)
        assert result["error"] == "未设置导出目录"

    @patch("eagle_watcher.config.get_project_info")
    def test_export_unknown_theme(self, mock_proj, tmp_path):
        """不存在的主题"""
        mock_proj.return_value = None
        cfg = self._make_cfg(tmp_path)
        api = self._mock_eagle_api([])
        result = export_by_theme("不存在", api, cfg)
        assert "不存在" in result["error"]

    @patch("eagle_watcher.config.get_project_info")
    def test_export_empty_folder(self, mock_proj, tmp_path):
        """空文件夹"""
        mock_proj.return_value = {"eagle_folder": "历史", "category": "历史"}
        cfg = self._make_cfg(tmp_path)
        api = self._mock_eagle_api([])
        result = export_by_theme("武安侯", api, cfg)
        assert result["exported"] == 0
        assert result["skipped"] == 0
        assert result["error"] is None

    @patch("eagle_watcher.config.get_project_info")
    def test_export_file_not_found(self, mock_proj, tmp_path):
        """素材文件在本地不存在时跳过"""
        mock_proj.return_value = {"eagle_folder": "历史", "category": "历史"}
        cfg = self._make_cfg(tmp_path)
        api = self._mock_eagle_api(
            [{"id": "ITEM0001", "name": "img", "ext": "jpg"}],
            file_paths={},  # get_item_file_path 返回 None
        )
        result = export_by_theme("武安侯", api, cfg)
        assert result["exported"] == 0
        assert result["skipped"] == 1
