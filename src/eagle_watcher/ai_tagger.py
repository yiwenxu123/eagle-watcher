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
import threading
import fcntl
from pathlib import Path
from typing import Optional
from collections import OrderedDict

from dashscope import MultiModalConversation

DEFAULT_MODEL = "qwen-vl-max"
MAX_RETRIES = 3
RETRY_DELAY = 2  # 秒

_LOG = logging.getLogger("ai_tagger")

# 缓存目录
CACHE_DIR = Path.home() / ".eagle-watcher" / "cache"

# 内存缓存配置
MEMORY_CACHE_MAX_SIZE = 100

# 内存缓存（LRU）
_memory_cache: OrderedDict[str, dict] = OrderedDict()
_memory_cache_lock = threading.Lock()


def _get_memory_cache(image_hash: str) -> Optional[dict]:
    """从内存缓存获取结果"""
    with _memory_cache_lock:
        if image_hash in _memory_cache:
            # 移动到末尾（LRU）
            _memory_cache.move_to_end(image_hash)
            return _memory_cache[image_hash]
    return None


def _set_memory_cache(image_hash: str, result: dict):
    """设置内存缓存"""
    with _memory_cache_lock:
        if image_hash in _memory_cache:
            # 更新现有条目
            _memory_cache[image_hash] = result
            _memory_cache.move_to_end(image_hash)
        else:
            # 添加新条目
            _memory_cache[image_hash] = result
            # 如果超过最大大小，删除最旧的条目
            while len(_memory_cache) > MEMORY_CACHE_MAX_SIZE:
                _memory_cache.popitem(last=False)


def _acquire_cache_lock(timeout: float = 5):
    """获取缓存文件锁"""
    # 确保缓存目录存在
    cache_dir = CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)

    # 使用缓存目录下的锁文件
    lock_path = cache_dir / ".cache.lock"
    lock_file = open(lock_path, "w")
    start_time = time.time()
    while True:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return lock_file
        except (IOError, OSError):
            if time.time() - start_time >= timeout:
                lock_file.close()
                raise TimeoutError(f"获取缓存锁超时 ({timeout}s)")
            time.sleep(0.01)


def _release_cache_lock(lock_file):
    """释放缓存文件锁"""
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()
    except Exception:
        pass


def _get_api_key() -> Optional[str]:
    # 优先从配置文件读取，其次是环境变量
    try:
        from eagle_watcher.config import load_config
        cfg = load_config()
        key = cfg.get("ai", {}).get("api_key", "")
        if key:
            _LOG.debug("AI API Key 从 config.yaml 读取")
            return key
    except (ImportError, KeyError, OSError) as e:
        _LOG.debug("config.yaml 读取失败，回退到环境变量: %s", e)
        pass
    key = os.environ.get("DASHSCOPE_API_KEY")
    if key:
        _LOG.debug("AI API Key 从环境变量读取")
    return key


def _get_image_hash(file_path: str) -> str:
    """计算图片文件的 MD5 哈希值，用于缓存"""
    try:
        with open(file_path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
    except (OSError, PermissionError, FileNotFoundError) as e:
        _LOG.warning("图片哈希计算失败 %s: %s", file_path, e)
        return ""


CACHE_MAX_AGE_DAYS = 30


def _load_cache(image_hash: str) -> Optional[dict]:
    """从缓存加载分析结果（超过 30 天自动过期）

    优先从内存缓存读取，失败则从文件缓存读取
    """
    if not image_hash:
        return None

    # 优先从内存缓存读取
    cached = _get_memory_cache(image_hash)
    if cached:
        _LOG.debug("从内存缓存读取: %s", image_hash[:8])
        return cached

    # 从文件缓存读取
    cache_file = CACHE_DIR / f"{image_hash}.json"
    if not cache_file.exists():
        return None

    # 检查缓存是否过期
    try:
        mtime = cache_file.stat().st_mtime
        age_days = (time.time() - mtime) / 86400
        if age_days > CACHE_MAX_AGE_DAYS:
            cache_file.unlink(missing_ok=True)
            return None
    except OSError:
        pass

    try:
        lock_file = _acquire_cache_lock()
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                result = json.load(f)
                # 加载到内存缓存
                _set_memory_cache(image_hash, result)
                return result
        finally:
            _release_cache_lock(lock_file)
    except TimeoutError:
        _LOG.warning("读取缓存超时: %s", image_hash[:8])
        return None
    except (json.JSONDecodeError, OSError) as e:
        _LOG.warning("AI 缓存读取失败: %s", e)
        return None


def _save_cache(image_hash: str, result: dict):
    """保存分析结果到缓存（同时更新内存缓存和文件缓存）"""
    if not image_hash:
        return

    # 更新内存缓存
    _set_memory_cache(image_hash, result)

    # 更新文件缓存
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{image_hash}.json"

    try:
        lock_file = _acquire_cache_lock()
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
        finally:
            _release_cache_lock(lock_file)
    except TimeoutError:
        _LOG.warning("保存缓存超时: %s", image_hash[:8])
    except Exception as e:
        _LOG.warning("保存缓存失败：%s", e)


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
    except (OSError, FileNotFoundError, PermissionError) as e:
        _LOG.warning("图片编码失败 %s: %s", file_path, e)
        return None


def _get_model() -> str:
    try:
        from eagle_watcher.config import load_config
        cfg = load_config()
        model = cfg.get("ai", {}).get("model", "")
        return model or DEFAULT_MODEL
    except (ImportError, KeyError, OSError) as e:
        _LOG.debug("读取模型配置失败，使用默认值 %s: %s", DEFAULT_MODEL, e)
        return DEFAULT_MODEL


def _call_qwen_vl(img_data: str) -> Optional[str]:
    api_key = _get_api_key()
    if not api_key:
        return None

    model = _get_model()

    prompt = (
        "分析这张图片，输出：\n"
        "1. 3-5 个中文标签描述图片内容（逗号分隔）\n"
        "2. 一个简短的中文文件名建议（5-15 字，不含扩展名，不含标点）\n\n"
        "格式：\n"
        "标签：xxx, xxx, xxx\n"
        "文件名：xxx"
    )

    resp = MultiModalConversation.call(
        model=model,
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
        timeout=60,
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
            _LOG.info("使用缓存结果：%s", Path(file_path).name)
            return cached

    img_data = _encode_image(file_path)
    if not img_data:
        return None

    # 重试机制
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            _LOG.info("AI 分析尝试 %d/%d：%s", attempt + 1, MAX_RETRIES, Path(file_path).name)
            text = _call_qwen_vl(img_data)
            if text:
                result = _parse_response(text, file_path)
                # 保存到缓存
                if result and use_cache:
                    _save_cache(image_hash, result)
                return result
        except Exception as e:
            last_error = e
            _LOG.warning("AI 分析失败（尝试 %d）：%s", attempt + 1, e)
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))  # 指数退避

    _LOG.error("AI 分析最终失败：%s", last_error)
    return None


def _parse_response(text: str, file_path: str) -> Optional[dict]:
    tags = []
    name = None

    for line in text.strip().split("\n"):
        line = line.strip()
        if line.startswith("标签"):
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
