"""知识库管理：读写 knowledge.yaml + 匹配逻辑"""

import re
import yaml
from pathlib import Path
from typing import Optional

DATA_DIR = Path.home() / ".eagle-watcher"
KNOWLEDGE_PATH = DATA_DIR / "knowledge.yaml"

DEFAULT_CONFIDENCE_NEW = 0.7
CONFIDENCE_PER_MATCH = 0.15
MAX_CONFIDENCE = 0.98


def _load() -> dict:
    if not KNOWLEDGE_PATH.exists():
        return {"keywords_mapping": {}, "sources": {}}
    with open(KNOWLEDGE_PATH) as f:
        return yaml.safe_load(f) or {"keywords_mapping": {}, "sources": {}}


def _save(data: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(KNOWLEDGE_PATH, "w") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False)


# ────────── 匹配 ──────────


def match_by_filename(filename: str) -> Optional[dict]:
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
        # 使用正则表达式进行完整词边界匹配
        elif re.search(rf'\b{re.escape(keyword_lower)}\b', stem):
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


def record_match(filename: str, keyword: str, theme: str, tags: list[str]):
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
            "first_seen": __import__("datetime").datetime.now().isoformat()[:10],
            "match_count": 1,
            "source": "user_confirmed",
        }

    _save(data)


def record_miss(filename: str, theme: str = "__inbox__"):
    if theme == "__inbox__":
        return

    stem = Path(filename).stem
    words = {w for w in re.split(r"[\s_\-.·]+", stem) if len(w) >= 2 and not w.isdigit()}
    if not words:
        return

    keyword = max(words, key=len)
    record_match(filename, keyword, theme, [keyword])


# ────────── 来源匹配 ──────────


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
