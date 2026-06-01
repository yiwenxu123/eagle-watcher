"""
文件名解析 + 主题匹配决策
核心逻辑：设了主题 -> 强制归入；无主题 -> 知识库匹配 -> 通用箱
"""

import logging
import re
from pathlib import Path

from eagle_watcher.config import get_current_project, get_project_info
from eagle_watcher.knowledge import match_by_filename, match_by_source, record_miss

_LOG = logging.getLogger("analyzer")

# 支持的图片扩展名
IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".ico",
    ".tiff", ".tif", ".psd", ".ai", ".eps", ".raw", ".heic", ".heif",
}


def is_vague_name(filename: str) -> bool:
    """判断文件名是否模糊（纯数字/乱码/无意义），需要 AI 视觉识别。"""
    stem = Path(filename).stem

    # 纯数字（至少5位）
    if re.match(r"^\d{5,}$", stem):
        return True

    # 哈希值（32位十六进制）
    if re.match(r"^[a-f0-9]{32}$", stem, re.I):
        return True

    # 无意义长字符串（纯字母20+，或纯字母数字30+）
    if re.match(r"^[a-zA-Z]{20,}$", stem):
        return True
    if re.match(r"^[a-zA-Z0-9]{30,}$", stem):
        return True

    # 短随机串（5-15位，包含数字和字母，但不是常见单词）
    if re.match(r"^[a-zA-Z0-9]{5,15}$", stem) and re.search(r"\d", stem):
        # 检查是否包含连续3个以上元音或辅音（可能是无意义串）
        if not re.search(r"[aeiou]{3}|[bcdfghjklmnpqrstvwxyz]{4}", stem, re.I):
            # 检查是否是常见单词
            common_words = {"image", "photo", "pic", "img", "file", "doc", "test"}
            if stem.lower() not in common_words:
                return True

    return False


def is_image_file(filename: str) -> bool:
    """判断文件是否是图片文件"""
    ext = Path(filename).suffix.lower()
    return ext in IMAGE_EXTENSIONS


def extract_keywords(filename: str) -> list[str]:
    stem = Path(filename).stem
    words = [w for w in re.split(r"[\s_\-.·]+", stem) if len(w) >= 2 and not w.isdigit()]
    return words


def decide(filename: str, source_url: str = "") -> dict:
    """
    主题决策入口。

    返回 dict:
      - action: "import" | "inbox" | "ai_analyze"
      - theme: 主题名 或 None
      - tags: 建议标签列表
      - folder: Eagle 文件夹名
    """
    # 检查文件扩展名，非图片文件不进行 AI 分析
    is_image = is_image_file(filename)

    # 第一步：用户手动设了项目？
    current = get_current_project()
    if current:
        project_info = get_project_info(current)
        if project_info:
            default_tags = list(project_info.get("default_tags", []))
            keywords = extract_keywords(filename)
            if current not in default_tags:
                default_tags = [current] + default_tags
            return {
                "action": "import",
                "theme": current,
                "tags": default_tags + keywords,
                "folder": project_info.get("eagle_folder", current),
            }
        _LOG.warning("当前项目 '%s' 不存在，回退到自动匹配", current)

    # 第二步：按来源网站匹配？
    if source_url:
        source_match = match_by_source(source_url)
        if source_match and source_match.get("theme"):
            return {
                "action": "import",
                "theme": source_match["theme"],
                "tags": source_match["tags"],
                "folder": source_match["theme"],
            }

    # 第三步：按知识库匹配文件名
    kb_match = match_by_filename(filename)
    if kb_match:
        return {
            "action": "import",
            "theme": kb_match["theme"],
            "tags": kb_match["tags"],
            "folder": kb_match["theme"],
            "confidence": kb_match["confidence"],
        }

    # 第四步：文件名太模糊，需要 AI 看（仅限图片文件）
    if is_vague_name(filename) and is_image:
        return {
            "action": "ai_analyze",
            "theme": None,
            "tags": ["待分类"],
            "folder": None,
        }

    # 第五步：都不匹配 -> 进通用箱
    record_miss(filename)
    return {
        "action": "inbox",
        "theme": None,
        "tags": ["待分类"],
        "folder": None,
    }
