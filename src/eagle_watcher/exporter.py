"""导出工作区：将 Eagle 素材 copy 到本地目录，方便剪映等工具直接导入"""

import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

_LOG = logging.getLogger("exporter")

# 默认导出目录大小上限（10GB）
DEFAULT_MAX_EXPORT_SIZE = 10 * 1024 * 1024 * 1024  # 10GB in bytes

# 最小可用空间要求（1GB）
MIN_FREE_SPACE = 1 * 1024 * 1024 * 1024  # 1GB in bytes


def _get_export_dir_size(export_dir: Path) -> int:
    """计算导出目录的总大小（字节）"""
    total_size = 0
    try:
        for entry in export_dir.rglob("*"):
            if entry.is_file():
                try:
                    total_size += entry.stat().st_size
                except OSError:
                    pass
    except OSError as e:
        _LOG.warning("计算导出目录大小失败: %s", e)
    return total_size


def _get_oldest_files(export_dir: Path, limit: int = 100) -> list[tuple[float, Path]]:
    """获取导出目录中最旧的文件列表（按修改时间排序）"""
    files = []
    try:
        for entry in export_dir.rglob("*"):
            if entry.is_file():
                try:
                    mtime = entry.stat().st_mtime
                    files.append((mtime, entry))
                except OSError:
                    pass
    except OSError as e:
        _LOG.warning("扫描导出目录失败: %s", e)

    # 按修改时间排序（最旧的在前）
    files.sort(key=lambda x: x[0])
    return files[:limit]


def _cleanup_export_dir(export_dir: Path, max_size: int) -> int:
    """清理导出目录，确保不超过最大大小限制

    Args:
        export_dir: 导出目录路径
        max_size: 最大大小限制（字节）

    Returns:
        清理的文件数量
    """
    current_size = _get_export_dir_size(export_dir)
    if current_size <= max_size:
        return 0

    cleared = 0
    target_size = int(max_size * 0.8)  # 清理到 80%，避免频繁清理

    # 获取最旧的文件
    oldest_files = _get_oldest_files(export_dir, limit=500)

    for mtime, file_path in oldest_files:
        if current_size <= target_size:
            break

        try:
            file_size = file_path.stat().st_size
            file_path.unlink()
            current_size -= file_size
            cleared += 1
            _LOG.debug("清理旧文件: %s (%.2f MB)", file_path.name, file_size / 1024 / 1024)
        except OSError as e:
            _LOG.warning("清理文件失败 %s: %s", file_path, e)

    if cleared:
        _LOG.info("导出目录清理: 删除 %d 个文件，释放 %.2f MB",
                  cleared, (_get_export_dir_size(export_dir) - current_size) / 1024 / 1024)

    return cleared


def _check_disk_space(path: Path, min_free: int = MIN_FREE_SPACE) -> bool:
    """检查磁盘可用空间是否足够

    Args:
        path: 要检查的路径
        min_free: 最小可用空间要求（字节）

    Returns:
        True 表示空间足够，False 表示空间不足
    """
    try:
        stat = shutil.disk_usage(path)
        return stat.free >= min_free
    except OSError as e:
        _LOG.warning("检查磁盘空间失败: %s", e)
        return False


def export_file(source_path: str, theme: str, filename: str, cfg: dict) -> Optional[str]:
    """将文件 copy 到导出工作区。返回目标路径或 None（未启用/跳过）。

    Args:
        source_path: 源文件绝对路径
        theme: 主题名（用于子目录）
        filename: 目标文件名
        cfg: 完整 config dict

    Returns:
        成功导出返回目标路径，未启用或跳过返回 None
    """
    export_cfg = cfg.get("export", {})
    if not export_cfg.get("enabled"):
        return None

    # 主题过滤：非空列表时仅导出匹配主题
    themes_filter = export_cfg.get("themes", [])
    if themes_filter and theme not in themes_filter:
        return None

    dir_str = export_cfg.get("dir", "").strip()
    if not dir_str:
        return None

    export_dir = Path(dir_str).expanduser()

    # 检查磁盘空间
    if not _check_disk_space(export_dir):
        _LOG.warning("磁盘空间不足，跳过导出: %s", filename)
        return None

    # 检查导出目录大小限制
    max_size = export_cfg.get("max_size_bytes", DEFAULT_MAX_EXPORT_SIZE)
    if max_size > 0:
        current_size = _get_export_dir_size(export_dir)
        if current_size >= max_size:
            _LOG.info("导出目录已达上限 (%.2f GB)，开始清理...",
                      current_size / 1024 / 1024 / 1024)
            _cleanup_export_dir(export_dir, max_size)

    structure = export_cfg.get("structure", "theme")

    if structure == "theme":
        target_dir = export_dir / (theme or "未分类")
    elif structure == "date":
        target_dir = export_dir / datetime.now().strftime("%Y-%m-%d")
    else:
        target_dir = export_dir

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        _LOG.error("创建导出目录失败 %s: %s", target_dir, e)
        return None

    target = target_dir / filename

    # 幂等：跳过已存在的同名同大小文件
    if target.exists():
        try:
            src_size = os.path.getsize(source_path)
            dst_size = os.path.getsize(target)
            if abs(src_size - dst_size) < 64:
                _LOG.debug("跳过已导出: %s", filename)
                return str(target)
        except OSError:
            pass

    try:
        shutil.copy2(source_path, target)
        _LOG.info("已导出: %s → %s", filename, target)
        return str(target)
    except OSError as e:
        _LOG.error("导出失败 %s: %s", filename, e)
        return None


def get_export_status(cfg: dict) -> dict:
    """统计导出工作区：文件数、占用空间、主题分布。"""
    export_cfg = cfg.get("export", {})
    dir_str = export_cfg.get("dir", "").strip()

    result = {
        "enabled": export_cfg.get("enabled", False),
        "dir": dir_str,
        "auto": export_cfg.get("auto", True),
        "structure": export_cfg.get("structure", "theme"),
        "themes_filter": export_cfg.get("themes", []),
        "file_count": 0,
        "size_bytes": 0,
        "themes": {},
    }

    if not dir_str:
        return result

    export_dir = Path(dir_str).expanduser()
    if not export_dir.is_dir():
        return result

    total_size = 0
    file_count = 0
    themes: dict[str, int] = {}

    try:
        for entry in export_dir.rglob("*"):
            if entry.is_file():
                file_count += 1
                try:
                    total_size += entry.stat().st_size
                except OSError:
                    pass
                # 统计主题（第一级子目录名）
                try:
                    rel = entry.relative_to(export_dir)
                    if rel.parts:
                        theme_name = rel.parts[0]
                        themes[theme_name] = themes.get(theme_name, 0) + 1
                except ValueError:
                    pass
    except OSError as e:
        _LOG.warning("扫描导出目录失败: %s", e)

    result["file_count"] = file_count
    result["size_bytes"] = total_size
    result["themes"] = themes
    return result


def clear_export_dir(cfg: dict) -> dict:
    """清空导出工作区。返回清理统计。"""
    export_cfg = cfg.get("export", {})
    dir_str = export_cfg.get("dir", "").strip()
    if not dir_str:
        return {"cleared": 0, "error": "未设置导出目录"}

    export_dir = Path(dir_str).expanduser()
    if not export_dir.is_dir():
        return {"cleared": 0}

    cleared = 0
    try:
        for entry in export_dir.iterdir():
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
            cleared += 1
    except OSError as e:
        _LOG.error("清空导出目录失败: %s", e)
        return {"cleared": cleared, "error": str(e)}

    return {"cleared": cleared}


def export_by_theme(theme: str, eagle_api, cfg: dict) -> dict:
    """批量导出某主题下所有 Eagle 素材到导出工作区。

    通过 Eagle API 的 thumbnail 端点推导每个素材的本地文件路径，
    避免依赖本地库目录扫描（API 返回的 name 与本地 metadata 可能不一致）。

    Args:
        theme: 主题名（对应 themes.yaml 中的 project 名）
        eagle_api: EagleAPI 实例
        cfg: 完整 config dict

    Returns:
        {"exported": int, "skipped": int, "error": str | None}
    """
    from eagle_watcher.config import get_project_info

    export_cfg = cfg.get("export", {})
    dir_str = export_cfg.get("dir", "").strip()
    if not dir_str:
        _LOG.warning("批量导出失败: 未设置导出目录")
        return {"exported": 0, "skipped": 0, "error": "未设置导出目录"}

    export_dir = Path(dir_str).expanduser()
    structure = export_cfg.get("structure", "theme")

    # 解析主题 → 分类 → Eagle 文件夹
    project_info = get_project_info(theme)
    _LOG.debug("批量导出「%s」: project_info=%s", theme, project_info)
    if not project_info:
        return {"exported": 0, "skipped": 0, "error": f"主题「{theme}」不存在"}

    eagle_folder = project_info.get("eagle_folder", "")
    _LOG.debug("批量导出「%s」: eagle_folder=%s", theme, eagle_folder)
    if not eagle_folder:
        return {"exported": 0, "skipped": 0, "error": f"主题「{theme}」未关联 Eagle 文件夹"}

    # 获取文件夹 ID
    folder_id = eagle_api.get_or_create_folder(eagle_folder)
    _LOG.debug("批量导出「%s」: folder_id=%s", theme, folder_id)
    if not folder_id:
        return {"exported": 0, "skipped": 0, "error": f"无法获取 Eagle 文件夹「{eagle_folder}」"}

    # 列出文件夹中所有素材（支持分页，防止大数量主题导出不完整）
    try:
        all_items = []
        batch_size = 100
        offset = 0
        while True:
            batch = eagle_api.list_items(folders=folder_id, limit=batch_size, offset=offset)
            if not batch:
                break
            all_items.extend(batch)
            if len(batch) < batch_size:
                break
            offset += batch_size
        items = all_items
    except Exception as e:
        _LOG.warning("批量导出「%s」: 获取素材列表失败: %s", theme, e)
        return {"exported": 0, "skipped": 0, "error": f"获取素材列表失败: {e}"}

    _LOG.info("批量导出「%s」: 找到 %d 个素材，开始导出", theme, len(items))
    if not items:
        return {"exported": 0, "skipped": 0, "error": None}

    # 确定目标目录
    if structure == "theme":
        target_dir = export_dir / (theme or "未分类")
    elif structure == "date":
        target_dir = export_dir / datetime.now().strftime("%Y-%m-%d")
    else:
        target_dir = export_dir

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return {"exported": 0, "skipped": 0, "error": f"创建目录失败: {e}"}

    exported = 0
    skipped = 0
    not_found = 0

    for item in items:
        item_id = item.get("id", "")
        name = item.get("name", "")
        ext = item.get("ext", "")
        filename = f"{name}.{ext}" if ext else name
        if not filename or not item_id:
            skipped += 1
            continue

        # 通过 Eagle API thumbnail 端点推导实际文件路径
        file_path = eagle_api.get_item_file_path(item_id, name, ext)
        if not file_path:
            _LOG.debug("批量导出「%s」: 素材 %s (%s) 文件未找到", theme, name, item_id)
            not_found += 1
            skipped += 1
            continue

        target = target_dir / filename

        # 幂等：跳过同名同大小
        if target.exists():
            try:
                src_size = os.path.getsize(file_path)
                dst_size = os.path.getsize(target)
                if abs(src_size - dst_size) < 64:
                    skipped += 1
                    continue
            except OSError:
                pass

        try:
            shutil.copy2(file_path, target)
            exported += 1
        except OSError as e:
            _LOG.warning("批量导出失败 %s: %s", filename, e)
            skipped += 1

    if not_found:
        _LOG.warning("批量导出「%s」: %d 个素材文件未找到", theme, not_found)
    _LOG.info("批量导出「%s」: 导出 %d，跳过 %d", theme, exported, skipped)
    return {"exported": exported, "skipped": skipped, "error": None}
