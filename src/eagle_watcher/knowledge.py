"""知识库管理：读写 knowledge.yaml + 匹配逻辑"""

import re
import threading
import tempfile
import os
import fcntl
import logging
import yaml
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

_LOG = logging.getLogger("knowledge")
_knowledge_lock = threading.RLock()

DATA_DIR = Path.home() / ".eagle-watcher"
KNOWLEDGE_PATH = DATA_DIR / "knowledge.yaml"
LOCK_PATH = DATA_DIR / "knowledge.lock"

DEFAULT_CONFIDENCE_NEW = 0.7
CONFIDENCE_PER_MATCH = 0.15
MAX_CONFIDENCE = 0.98

# 文件锁超时时间（秒）
_FILE_LOCK_TIMEOUT = 5


def _acquire_file_lock(lock_file, timeout=_FILE_LOCK_TIMEOUT):
    """获取文件锁，超时后抛出异常"""
    import time
    start_time = time.time()
    while True:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except (IOError, OSError):
            if time.time() - start_time >= timeout:
                raise TimeoutError(f"获取文件锁超时 ({timeout}s)")
            time.sleep(0.01)


def _load() -> dict:
    """加载知识库数据（需要外部持有 _knowledge_lock）"""
    if not KNOWLEDGE_PATH.exists():
        return {"keywords_mapping": {}, "sources": {}}
    try:
        with open(KNOWLEDGE_PATH) as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError, UnicodeDecodeError) as e:
        _LOG.warning("知识库文件损坏，已重置：%s", e)
        return {"keywords_mapping": {}, "sources": {}}
    return data or {"keywords_mapping": {}, "sources": {}}


def _save(data: dict):
    """保存知识库数据（需要外部持有 _knowledge_lock）"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(DATA_DIR), suffix=".yaml.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, sort_keys=False)
        os.replace(tmp_path, str(KNOWLEDGE_PATH))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _atomic_operation(operation_name: str):
    """装饰器：确保整个读-改-改操作的原子性

    使用文件锁保护整个操作周期，防止并发修改导致数据丢失。
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            try:
                with open(LOCK_PATH, "w") as lock_file:
                    _acquire_file_lock(lock_file)
                    try:
                        return func(*args, **kwargs)
                    finally:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except TimeoutError:
                _LOG.error("知识库操作超时: %s", operation_name)
                raise
            except Exception as e:
                _LOG.error("知识库操作失败: %s - %s", operation_name, e)
                raise
        return wrapper
    return decorator


# ────────── 匹配 ──────────


@_atomic_operation("match_by_filename")
def match_by_filename(filename: str) -> Optional[dict]:
    # O(n) 线性扫描所有关键词。当前规模（<1000 条）性能可接受。
    # 当知识库超过 5000 条时，应考虑构建倒排索引：token → [keyword]
    data = _load()
    mapping = data.get("keywords_mapping", {})

    stem = Path(filename).stem.lower()
    # 按常见分隔符拆分：空格、_、-、·、.
    words = set(re.split(r"[\s_\-.·]+", stem))
    words = {w for w in words if len(w) >= 2 and not w.isdigit()}

    best = None
    best_conf = 0

    # 优先匹配完整词（避免部分匹配）
    for keyword, info in mapping.items():
        theme = info.get("theme")
        if not theme:
            continue

        keyword_lower = keyword.lower()
        conf = info.get("confidence", DEFAULT_CONFIDENCE_NEW)

        # 完整词匹配（最高优先级）
        if keyword_lower in words:
            if conf > best_conf:
                best_conf = conf
                best = {
                    "theme": theme,
                    "tags": info.get("tags", []),
                    "confidence": conf,
                    "keyword": keyword,
                    "match_type": "exact_word",
                }
        # 使用正则表达式进行完整词边界匹配（仅对 ASCII 关键词有效；CJK 字符在 Unicode 模式下皆为 \w，
        # 导致 \b 在中文字符与 ASCI 标点间误匹配，故使用 re.ASCII 限制 \b 只对 ASCII 关键词生效）
        elif re.search(rf'\b{re.escape(keyword_lower)}\b', stem, re.ASCII):
            if conf > best_conf:
                best_conf = conf
                best = {
                    "theme": theme,
                    "tags": info.get("tags", []),
                    "confidence": conf,
                    "keyword": keyword,
                    "match_type": "word_boundary",
                }

    # 如果没有完整词匹配，才考虑部分匹配（降低置信度）
    if not best:
        for keyword, info in mapping.items():
            theme = info.get("theme")
            if not theme:
                continue

            keyword_lower = keyword.lower()
            if keyword_lower in stem:
                conf = info.get("confidence", DEFAULT_CONFIDENCE_NEW) * 0.8  # 降低置信度
                if conf > best_conf:
                    best_conf = conf
                    best = {
                        "theme": theme,
                        "tags": info.get("tags", []),
                        "confidence": conf,
                        "keyword": keyword,
                        "match_type": "partial",
                    }

    return best


# ────────── 学习 ──────────


@_atomic_operation("record_match")
def record_match(filename: str, keyword: str, theme: str, tags: list[str]):
    with _knowledge_lock:
        data = _load()
        mapping = data.setdefault("keywords_mapping", {})

        if keyword in mapping:
            entry = mapping[keyword]
            entry["theme"] = theme
            entry["match_count"] = entry.get("match_count", 0) + 1
            entry["confidence"] = min(
                DEFAULT_CONFIDENCE_NEW + (entry["match_count"] - 1) * CONFIDENCE_PER_MATCH,
                MAX_CONFIDENCE,
            )
            entry["source"] = "user_confirmed"
            existing_tags = set(entry.get("tags", []))
            existing_tags.update(tags)
            entry["tags"] = list(existing_tags)
        else:
            mapping[keyword] = {
                "theme": theme,
                "tags": tags,
                "confidence": DEFAULT_CONFIDENCE_NEW,
                "first_seen": datetime.now().strftime('%Y-%m-%d'),
                "match_count": 1,
                "source": "user_confirmed",
            }

        _save(data)


@_atomic_operation("record_miss")
def record_miss(filename: str, theme: str = "__inbox__"):
    """记录未匹配的文件（仅统计，不再自动学习关键词避免噪音污染）"""
    if theme == "__inbox__":
        return

    with _knowledge_lock:
        stem = Path(filename).stem
        words = {w for w in re.split(r"[\s_\-.·]+", stem) if len(w) >= 3 and not w.isdigit()}
        if not words:
            return

        data = _load()
        misses = data.setdefault("misses", [])
        misses.append({
            "filename": filename,
            "theme": theme,
            "time": datetime.now().isoformat(),
        })
        # 只保留最近 200 条
        if len(misses) > 200:
            data["misses"] = misses[-200:]
        _save(data)


# ────────── 来源匹配 ──────────


@_atomic_operation("match_by_source")
def match_by_source(source_url: str) -> Optional[dict]:
    data = _load()
    sources = data.get("sources", {})
    for source_pattern, info in sources.items():
        if source_pattern in source_url:
            return {
                "theme": info.get("theme"),
                "tags": info.get("tags", []),
                "source": source_pattern,
            }
    return None


# ────────── 清理与统计 ──────────


@_atomic_operation("cleanup_stale_entries")
def cleanup_stale_entries(max_age_days: int = 90, min_confidence: float = 0.3) -> dict:
    """清理知识库中的过期和低置信度条目。

    Args:
        max_age_days: 超过此天数未更新的条目将被清理（如果置信度低于 0.5）
        min_confidence: 低于此置信度的条目将被清理

    Returns:
        清理统计: {"keywords_removed": int, "sources_removed": int}
    """
    cutoff = datetime.now() - timedelta(days=max_age_days)

    stats = {"keywords_removed": 0, "sources_removed": 0}

    with _knowledge_lock:
        data = _load()
        mapping = data.get("keywords_mapping", {})

        # 清理低置信度或过期的关键词条目
        to_delete = []
        for keyword, info in mapping.items():
            conf = info.get("confidence", 0)
            # 删除低置信度条目
            if conf < min_confidence:
                to_delete.append(keyword)
                continue
            # 删除陈旧的低置信度条目（老旧 + 置信度不足）
            first_seen = info.get("first_seen", "")
            match_count = info.get("match_count", 0)
            if first_seen and match_count <= 1:
                try:
                    seen_date = datetime.fromisoformat(first_seen)
                    if seen_date < cutoff:
                        to_delete.append(keyword)
                except (ValueError, TypeError):
                    pass

        if to_delete:
            _LOG.info("清理知识库: 删除 %d 个关键词条目", len(to_delete))
            for keyword in to_delete:
                del mapping[keyword]
            stats["keywords_removed"] = len(to_delete)

        _save(data)

    return stats


@_atomic_operation("maybe_cleanup")
def maybe_cleanup(threshold: int = 500) -> dict:
    """如果知识库条目数超过阈值，自动执行清理。

    Args:
        threshold: 触发清理的条目数阈值（默认 500）

    Returns:
        清理统计，如未触发清理则返回空统计
    """
    data = _load()
    if len(data.get("keywords_mapping", {})) > threshold:
        return cleanup_stale_entries()
    return {"keywords_removed": 0, "sources_removed": 0}


@_atomic_operation("get_knowledge_stats")
def get_knowledge_stats() -> dict:
    """获取知识库统计信息。"""
    data = _load()
    mapping = data.get("keywords_mapping", {})
    sources = data.get("sources", {})

    # 按主题分组统计
    theme_stats = {}
    total_confidence = 0
    for keyword, info in mapping.items():
        theme = info.get("theme", "未归类")
        if theme not in theme_stats:
            theme_stats[theme] = {"count": 0, "keywords": []}
        theme_stats[theme]["count"] += 1
        theme_stats[theme]["keywords"].append(keyword)
        total_confidence += info.get("confidence", 0)

    avg_confidence = total_confidence / len(mapping) if mapping else 0

    # 置信度分布
    high_conf = sum(1 for v in mapping.values() if v.get("confidence", 0) >= 0.8)
    mid_conf = sum(1 for v in mapping.values() if 0.5 <= v.get("confidence", 0) < 0.8)
    low_conf = sum(1 for v in mapping.values() if v.get("confidence", 0) < 0.5)

    return {
        "total_keywords": len(mapping),
        "total_sources": len(sources),
        "themes": len(theme_stats),
        "theme_details": theme_stats,
        "avg_confidence": round(avg_confidence, 2),
        "confidence_distribution": {
            "high_80_100": high_conf,
            "medium_50_80": mid_conf,
            "low_0_50": low_conf,
        },
    }


# ────────── CRUD ──────────


@_atomic_operation("list_keywords")
def list_keywords(search: str = "", theme: str = "", sort: str = "confidence",
                  page: int = 1, per_page: int = 20) -> dict:
    """分页列出关键词，支持搜索、按主题筛选、排序。

    Returns:
        {items: [...], total: int, page: int, per_page: int}
    """
    data = _load()
    mapping = data.get("keywords_mapping", {})

    items = []
    for keyword, info in mapping.items():
        # 主题筛选
        if theme and info.get("theme") != theme:
            continue
        # 搜索筛选
        if search and search.lower() not in keyword.lower():
            continue
        items.append({
            "keyword": keyword,
            "theme": info.get("theme", ""),
            "tags": info.get("tags", []),
            "confidence": info.get("confidence", DEFAULT_CONFIDENCE_NEW),
            "match_count": info.get("match_count", 0),
            "first_seen": info.get("first_seen", ""),
        })

    # 排序
    sort_keys = {
        "confidence": lambda x: x["confidence"],
        "match_count": lambda x: x["match_count"],
        "keyword": lambda x: x["keyword"].lower(),
        "first_seen": lambda x: x["first_seen"],
    }
    key_fn = sort_keys.get(sort, sort_keys["confidence"])
    items.sort(key=key_fn, reverse=(sort != "keyword"))

    total = len(items)
    start = (page - 1) * per_page
    return {
        "items": items[start:start + per_page],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@_atomic_operation("update_keyword")
def update_keyword(keyword: str, theme: str = None, tags: list[str] = None) -> bool:
    """修改已有关键词的主题和/或标签。返回是否成功。"""
    with _knowledge_lock:
        data = _load()
        mapping = data.get("keywords_mapping", {})
        if keyword not in mapping:
            return False
        entry = mapping[keyword]
        if theme is not None:
            entry["theme"] = theme
        if tags is not None:
            entry["tags"] = tags
        _save(data)
        return True


@_atomic_operation("delete_keyword")
def delete_keyword(keyword: str) -> bool:
    """删除指定关键词。返回是否成功。"""
    with _knowledge_lock:
        data = _load()
        mapping = data.get("keywords_mapping", {})
        if keyword not in mapping:
            return False
        del mapping[keyword]
        _save(data)
        return True
