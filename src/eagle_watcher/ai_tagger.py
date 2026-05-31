"""
AI 视觉分析模块（DashScope Qwen-VL）

用途：仅当文件名模糊（纯数字、乱码等）时调用，
AI 识别图片内容后输出标签 + 更合理的文件名。
支持重试机制和缓存。
"""

import os
import json
import time
import hashlib
import base64
import logging
from pathlib import Path
from typing import Optional

from dashscope import MultiModalConversation

MODEL = "qwen-vl-max"
MAX_RETRIES = 3
RETRY_DELAY = 2  # 秒

_LOG = logging.getLogger("ai_tagger")

# 缓存目录
CACHE_DIR = Path.home() / ".eagle-watcher" / "cache"


def _get_api_key() -> Optional[str]:
    return os.environ.get("DASHSCOPE_API_KEY")


def _get_image_hash(file_path: str) -> str:
    """计算图片文件的 MD5 哈希值，用于缓存"""
    try:
        with open(file_path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
    except Exception:
        return ""


def _load_cache(image_hash: str) -> Optional[dict]:
    """从缓存加载分析结果"""
    if not image_hash:
        return None

    cache_file = CACHE_DIR / f"{image_hash}.json"
    if not cache_file.exists():
        return None

    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_cache(image_hash: str, result: dict):
    """保存分析结果到缓存"""
    if not image_hash:
        return

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{image_hash}.json"

    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    except Exception as e:
        _LOG.warning(f"保存缓存失败：{e}")


def _encode_image(file_path: str) -> Optional[str]:
    try:
        with open(file_path, "rb") as f:
            data = f.read()
        ext = Path(file_path).suffix.lower()
        mime_map = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".bmp": "image/bmp",
        }
        mime = mime_map.get(ext, "image/png")
        return f"data:{mime};base64,{base64.b64encode(data).decode('utf-8')}"
    except Exception:
        return None


def _call_qwen_vl(img_data: str) -> Optional[str]:
    """调用 Qwen-VL API，返回原始响应文本"""
    api_key = _get_api_key()
    if not api_key:
        return None

    prompt = (
        "分析这张图片，输出：\n"
        "1. 3-5 个中文标签描述图片内容（逗号分隔）\n"
        "2. 一个简短的中文文件名建议（5-15 字，不含扩展名，不含标点）\n\n"
        "格式：\n"
        "标签：xxx, xxx, xxx\n"
        "文件名：xxx"
    )

    resp = MultiModalConversation.call(
        model=MODEL,
        messages=[
            {
                "role": "user",
                "content": [
                    {"text": prompt},
                    {"image": img_data},
                ],
            }
        ],
        max_tokens=200,
        api_key=api_key,
    )

    if resp.status_code != 200:
        raise Exception(f"API 返回异常：{resp.status_code}")

    content = resp.output.choices[0].message.content
    if isinstance(content, list):
        return "".join(c.get("text", "") for c in content if "text" in c)
    return str(content)


def analyze_image(file_path: str, use_cache: bool = True) -> Optional[dict]:
    """分析图片内容，返回标签和新文件名建议。

    支持重试机制和缓存。

    返回 dict:
      - tags: list[str] — 建议标签（3-5 个）
      - name: str — 建议的文件名（不含扩展名）
      - raw: str — AI 原始输出

    失败返回 None。
    """
    # 检查缓存
    image_hash = _get_image_hash(file_path)
    if use_cache:
        cached = _load_cache(image_hash)
        if cached:
            _LOG.info(f"使用缓存结果：{Path(file_path).name}")
            return cached

    img_data = _encode_image(file_path)
    if not img_data:
        return None

    # 重试机制
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            _LOG.info(f"AI 分析尝试 {attempt + 1}/{MAX_RETRIES}：{Path(file_path).name}")
            text = _call_qwen_vl(img_data)
            if text:
                result = _parse_response(text, file_path)
                # 保存到缓存
                if result and use_cache:
                    _save_cache(image_hash, result)
                return result
        except Exception as e:
            last_error = e
            _LOG.warning(f"AI 分析失败（尝试 {attempt + 1}）：{e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))  # 指数退避

    _LOG.error(f"AI 分析最终失败：{last_error}")
    return None


def _parse_response(text: str, file_path: str) -> Optional[dict]:
    tags = []
    name = None

    for line in text.strip().split("\n"):
        line = line.strip()
        if line.startswith("标签") or line.startswith("标签"):
            tag_str = line.split("：", 1)[-1].split(":", 1)[-1].strip()
            tags = [t.strip() for t in tag_str.replace("，", ",").split(",") if t.strip()]
        elif line.startswith("文件名"):
            name = line.split("：", 1)[-1].split(":", 1)[-1].strip()

    if not tags and not name:
        words = [w.strip() for w in text.replace("，", ",").split(",") if w.strip()]
        if words:
            tags = words[:5]

    if not name:
        name = Path(file_path).stem

    return {
        "tags": tags[:8],
        "name": name,
        "raw": text,
    }


def clear_cache():
    """清除所有缓存"""
    import shutil
    if CACHE_DIR.exists():
        shutil.rmtree(CACHE_DIR)
        _LOG.info("缓存已清除")


def get_cache_size() -> int:
    """获取缓存大小（字节）"""
    if not CACHE_DIR.exists():
        return 0

    total = 0
    for f in CACHE_DIR.rglob("*"):
        if f.is_file():
            total += f.stat().st_size
    return total
