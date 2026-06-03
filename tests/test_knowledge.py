"""Tests for knowledge.py — knowledge base management module.

Covers: match_by_filename (3 levels, priority, Chinese boundary), record_match
(confidence calc, tags merge, new/existing), record_miss (inbox skip, word filter),
match_by_source (exact/partial/no match), cleanup_stale_entries, get_knowledge_stats,
and thread safety.
"""

import threading
from datetime import datetime, timedelta

import pytest
import yaml

from typing import Optional

from eagle_watcher.knowledge import (
    match_by_filename,
    match_by_source,
    record_match,
    record_miss,
    cleanup_stale_entries,
    get_knowledge_stats,
    DEFAULT_CONFIDENCE_NEW,
    CONFIDENCE_PER_MATCH,
    MAX_CONFIDENCE,
    KNOWLEDGE_PATH,
    _load,
    _save,
)


# ════════════════════════════════════════════════════════════
#  helpers
# ════════════════════════════════════════════════════════════


def _clear_knowledge():
    """Wipe the knowledge file so each test starts clean."""
    _save({"keywords_mapping": {}, "sources": {}})


def _seed_knowledge():
    """Populate with two well-known keywords used across many tests."""
    _clear_knowledge()
    record_match("白起.jpg", "白起", "武安侯", ["战国", "武将"])
    record_match("兵马俑.jpg", "兵马俑", "秦始皇", ["秦朝", "文物"])


def _direct_create_entry(keyword: str, theme: str, confidence: float,
                         match_count: int = 1, first_seen: Optional[str] = None,
                         tags: Optional[list[str]] = None):
    """Directly write an entry into knowledge.yaml (bypass record_match for
    edge-case confidence / date manipulation)."""
    data = _load()
    entry = {
        "theme": theme,
        "tags": tags or [],
        "confidence": confidence,
        "match_count": match_count,
        "source": "test",
    }
    if first_seen is not None:
        entry["first_seen"] = first_seen
    data.setdefault("keywords_mapping", {})[keyword] = entry
    _save(data)


# ════════════════════════════════════════════════════════════
#  match_by_filename
# ════════════════════════════════════════════════════════════


class TestMatchByFilename:
    """Three match levels, no-match, priority order, Chinese-ASCII boundary."""

    @pytest.mark.parametrize("filename,exp_theme,exp_type", [
        # exact_word – keyword is a standalone token after split
        ("白起_剧照.jpg",    "武安侯", "exact_word"),
        ("兵马俑_展览.jpg",  "秦始皇", "exact_word"),
# word_boundary – keyword bounded by non-\w chars (e.g. `!`), but only for ASCII keywords
        # 中文关键词不再触发 \b（详见 B5: re.ASCII 修复），降为 partial 匹配
        ("白起!剧照.png",     "武安侯", "partial"),
        ("兵马俑!展览.png",   "秦始皇", "partial"),
        # partial – keyword is a substring but no \w boundary (adjacent CJK)
        ("白起剧照.png",     "武安侯", "partial"),
        ("兵马俑展览馆.jpg", "秦始皇", "partial"),
    ])
    def test_match_levels(self, filename, exp_theme, exp_type, mock_data_dir):
        _seed_knowledge()
        result = match_by_filename(filename)
        assert result is not None, f"{filename} should match {exp_theme}"
        assert result["theme"] == exp_theme
        assert result["match_type"] == exp_type

    @pytest.mark.parametrize("filename", [
        "完全无关的文件.jpg",
        "random_name.png",
        "a.jpg",                  # too short
        "123456.jpg",             # digits, stripped
    ])
    def test_no_match(self, filename, mock_data_dir):
        _seed_knowledge()
        assert match_by_filename(filename) is None

    # ── priority ──

    def test_exact_word_over_partial(self, mock_data_dir):
        _seed_knowledge()
        r = match_by_filename("白起_剧照.png")
        assert r is not None and r["match_type"] == "exact_word"

    def test_word_boundary_over_partial(self, mock_data_dir):
        _seed_knowledge()
        # ASCII 关键词仍触发 \b；中文关键词因 B5 re.ASCII 修复不再触发
        record_match("warrior.jpg", "warrior", "战士", ["战斗"])
        r = match_by_filename("warrior!king.png")
        assert r is not None and r["match_type"] == "word_boundary"

    def test_higher_confidence_wins_in_first_pass(self, mock_data_dir):
        """When both exact_word matches exist for different keywords,
        the entry with higher stored confidence wins."""
        _seed_knowledge()
        # record_match again for 兵马俑 → confidence jumps to 0.85
        record_match("兵马俑_v2.jpg", "兵马俑", "秦始皇", ["秦朝"])
        r = match_by_filename("兵马俑_白起.jpg")
        assert r is not None
        assert r["theme"] == "秦始皇"          # 0.85 > 0.70
        assert r["confidence"] == pytest.approx(0.85)

    # ── ASCII boundary ──

    def test_ascii_word_boundary(self, mock_data_dir):
        r"""ASCII \b works as expected between \w and non-\w chars.
        Use `!` which is NOT in the split pattern [\s_\-.·]+ so the token stays
        intact, then \b triggers because `!` is non-\w."""
        _seed_knowledge()
        record_match("warrior.jpg", "warrior", "战士", ["战斗"])
        r = match_by_filename("warrior!king.jpg")
        assert r is not None
        assert r["match_type"] == "word_boundary"
        assert r["theme"] == "战士"

    # ── Chinese boundary (AKA known bug B5) ──

    def test_chinese_no_word_boundary_false_positive(self, mock_data_dir):
        r"""CJK adjacent chars are both \w in Python re, so \b does NOT match
        between them.  '白起' in '白起剧照' must be *partial*, not word_boundary."""
        _seed_knowledge()
        r = match_by_filename("白起剧照.png")
        assert r is not None
        assert r["match_type"] == "partial", \
            "Expected partial match (B5: CJK chars are \\w so \\b is invisible)"

    def test_partial_confidence_is_reduced(self, mock_data_dir):
        """Partial match returns confidence * 0.8."""
        _seed_knowledge()
        r = match_by_filename("白起剧照.png")
        assert r is not None
        assert r["match_type"] == "partial"
        assert r["confidence"] == pytest.approx(DEFAULT_CONFIDENCE_NEW * 0.8)

    def test_case_insensitivity(self, mock_data_dir):
        _seed_knowledge()
        record_match("poster.jpg", "poster", "设计", ["海报"])
        r = match_by_filename("Best_Poster_Ever.jpg")
        assert r is not None
        assert r["match_type"] == "exact_word"
        assert r["theme"] == "设计"

    def test_short_words_and_digits_filtered(self, mock_data_dir):
        """Words shorter than 2 chars or pure digits are excluded from split."""
        _seed_knowledge()
        record_match("a.jpg", "a", "单字母", [])
        record_match("123.jpg", "123", "数字", [])
        _clear_knowledge()  # these shouldn't be matched
        _seed_knowledge()
        assert match_by_filename("a.jpg") is None
        assert match_by_filename("123.jpg") is None

    def test_missing_keyword_entry(self, mock_data_dir):
        """Verify no-op when theme is missing/empty (skip keyword in loop)."""
        _clear_knowledge()
        data = _load()
        data["keywords_mapping"]["orphan"] = {
            "tags": [], "confidence": 0.9, "source": "test",
            # no "theme" key
        }
        _save(data)
        assert match_by_filename("orphan_file.jpg") is None


# ════════════════════════════════════════════════════════════
#  record_match
# ════════════════════════════════════════════════════════════


class TestRecordMatch:
    """New keyword, existing keyword update, confidence ramp, tag merge."""

    def test_new_keyword(self, mock_data_dir):
        _clear_knowledge()
        record_match("白起.jpg", "白起", "武安侯", ["战国", "武将"])
        data = _load()
        entry = data["keywords_mapping"]["白起"]
        assert entry["theme"] == "武安侯"
        assert entry["match_count"] == 1
        assert entry["confidence"] == DEFAULT_CONFIDENCE_NEW  # 0.7
        assert entry["tags"] == ["战国", "武将"]
        assert "first_seen" in entry

    def test_existing_keyword_increments_count(self, mock_data_dir):
        _clear_knowledge()
        record_match("白起.jpg", "白起", "武安侯", ["战国"])
        record_match("白起_v2.jpg", "白起", "武安侯", ["武将"])
        data = _load()
        entry = data["keywords_mapping"]["白起"]
        assert entry["match_count"] == 2

    def test_existing_keyword_updates_confidence(self, mock_data_dir):
        """Check the 0.7 → 0.85 → 0.98 → 0.98 ramp."""
        _clear_knowledge()
        for i in range(4):
            record_match(f"白起_{i}.jpg", "白起", "武安侯", [])
        data = _load()
        entry = data["keywords_mapping"]["白起"]
        # 1st: 0.7 | 2nd: 0.7 + 0.15 = 0.85 | 3rd: 0.7 + 0.30 = 1.0 → 0.98 | 4th: stays 0.98
        assert entry["match_count"] == 4
        assert entry["confidence"] == pytest.approx(MAX_CONFIDENCE)  # 0.98

    def test_confidence_steps(self, mock_data_dir):
        """Verify every step of the confidence calculation."""
        _clear_knowledge()
        record_match("k.jpg", "k", "T", [])       # 1st – new
        assert _load()["keywords_mapping"]["k"]["confidence"] == pytest.approx(0.70)

        record_match("k2.jpg", "k", "T", [])      # 2nd – 0.70 + 0.15 = 0.85
        assert _load()["keywords_mapping"]["k"]["confidence"] == pytest.approx(0.85)

        record_match("k3.jpg", "k", "T", [])      # 3rd – 0.70 + 0.30 = 1.00 → cap at 0.98
        assert _load()["keywords_mapping"]["k"]["confidence"] == pytest.approx(0.98)

        record_match("k4.jpg", "k", "T", [])      # 4th – stays 0.98
        assert _load()["keywords_mapping"]["k"]["confidence"] == pytest.approx(0.98)

    def test_existing_keyword_merges_tags(self, mock_data_dir):
        _clear_knowledge()
        record_match("白起.jpg", "白起", "武安侯", ["战国", "武将"])
        record_match("白起_v2.jpg", "白起", "武安侯", ["武将", "秦国"])
        data = _load()
        tags = set(data["keywords_mapping"]["白起"]["tags"])
        assert tags == {"战国", "武将", "秦国"}

    def test_existing_keyword_updates_theme(self, mock_data_dir):
        """If theme changes on subsequent match, it is updated."""
        _clear_knowledge()
        record_match("白起.jpg", "白起", "武安侯", [])
        record_match("白起.jpg", "白起", "秦国名将", [])
        assert _load()["keywords_mapping"]["白起"]["theme"] == "秦国名将"

    def test_source_field_set(self, mock_data_dir):
        _clear_knowledge()
        record_match("x.jpg", "x", "T", [])
        assert _load()["keywords_mapping"]["x"]["source"] == "user_confirmed"


# ════════════════════════════════════════════════════════════
#  record_miss
# ════════════════════════════════════════════════════════════


class TestRecordMiss:
    """Regular miss, __inbox__ skip, word-length filter."""

    def test_regular_miss_appended(self, mock_data_dir):
        _clear_knowledge()
        record_miss("unknown_document.pdf", "设计参考")
        data = _load()
        misses = data.get("misses", [])
        assert len(misses) == 1
        assert misses[0]["filename"] == "unknown_document.pdf"
        assert misses[0]["theme"] == "设计参考"
        assert "time" in misses[0]

    def test_inbox_theme_skipped(self, mock_data_dir):
        _clear_knowledge()
        record_miss("random_file.pdf", "__inbox__")
        data = _load()
        assert "misses" not in data or len(data["misses"]) == 0

    def test_short_words_filtered(self, mock_data_dir):
        """Words < 3 chars in the stem should cause the miss to be skipped."""
        _clear_knowledge()
        record_miss("ab.pdf", "设计")          # only word is "ab" (len=2)
        data = _load()
        assert "misses" not in data or len(data["misses"]) == 0

    def test_digit_only_stem_skipped(self, mock_data_dir):
        """Pure digit stems produce no words → miss skipped."""
        _clear_knowledge()
        record_miss("12345.pdf", "设计")
        data = _load()
        assert "misses" not in data or len(data["misses"]) == 0

    def test_misses_capped_at_200(self, mock_data_dir):
        """Only the latest 200 misses are kept."""
        _clear_knowledge()
        for i in range(250):
            record_miss(f"file_{i}.pdf", "设计")
        data = _load()
        assert len(data["misses"]) == 200
        # newest file should be file_249
        assert data["misses"][-1]["filename"] == "file_249.pdf"


# ════════════════════════════════════════════════════════════
#  match_by_source
# ════════════════════════════════════════════════════════════


class TestMatchBySource:
    """Exact substring match, no match."""

    def setup_sources(self):
        data = _load()
        data["sources"] = {
            "pinterest.com": {"theme": "设计灵感", "tags": ["pin"]},
            "unsplash.com": {"theme": "摄影", "tags": []},
        }
        _save(data)

    def test_exact_url_match(self, mock_data_dir):
        self.setup_sources()
        r = match_by_source("https://www.pinterest.com/pin/123/")
        assert r is not None
        assert r["theme"] == "设计灵感"
        assert r["tags"] == ["pin"]

    def test_substring_url_match(self, mock_data_dir):
        """Pattern appears anywhere in the URL (not just host)."""
        self.setup_sources()
        r = match_by_source("https://images.unsplash.com/photo-abc")
        assert r is not None
        assert r["theme"] == "摄影"

    def test_no_source_match(self, mock_data_dir):
        self.setup_sources()
        assert match_by_source("https://example.com/image.jpg") is None

    def test_empty_sources(self, mock_data_dir):
        _clear_knowledge()
        assert match_by_source("https://example.com") is None


# ════════════════════════════════════════════════════════════
#  cleanup_stale_entries
# ════════════════════════════════════════════════════════════


class TestCleanupStaleEntries:
    """Removes low-confidence entries and old entries with low match_count."""

    def test_removes_low_confidence(self, mock_data_dir):
        _clear_knowledge()
        _direct_create_entry("bad_kw", "T", confidence=0.2)
        _direct_create_entry("good_kw", "T", confidence=0.9)
        stats = cleanup_stale_entries(min_confidence=0.3)
        assert stats["keywords_removed"] == 1
        assert "bad_kw" not in _load()["keywords_mapping"]
        assert "good_kw" in _load()["keywords_mapping"]

    def test_keeps_above_threshold(self, mock_data_dir):
        _clear_knowledge()
        _direct_create_entry("ok_kw", "T", confidence=0.5)
        stats = cleanup_stale_entries(min_confidence=0.3)
        assert stats["keywords_removed"] == 0
        assert "ok_kw" in _load()["keywords_mapping"]

    def test_removes_old_single_match_entries(self, mock_data_dir):
        """Entries with match_count <= 1 and first_seen older than max_age_days
        are removed."""
        _clear_knowledge()
        old_date = (datetime.now() - timedelta(days=100)).strftime("%Y-%m-%d")
        _direct_create_entry("old_kw", "T", confidence=0.7,
                             match_count=1, first_seen=old_date)
        stats = cleanup_stale_entries(max_age_days=90)
        assert stats["keywords_removed"] == 1
        assert "old_kw" not in _load()["keywords_mapping"]

    def test_keeps_recent_single_match_entries(self, mock_data_dir):
        """Single-match entries within the age window should be kept."""
        _clear_knowledge()
        recent = datetime.now().strftime("%Y-%m-%d")
        _direct_create_entry("recent_kw", "T", confidence=0.7,
                             match_count=1, first_seen=recent)
        stats = cleanup_stale_entries(max_age_days=90)
        assert stats["keywords_removed"] == 0

    def test_keeps_old_but_high_match_count(self, mock_data_dir):
        """Old entries with match_count > 1 are kept regardless of age."""
        _clear_knowledge()
        old_date = (datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d")
        _direct_create_entry("popular_old_kw", "T", confidence=0.7,
                             match_count=5, first_seen=old_date)
        stats = cleanup_stale_entries(max_age_days=90)
        assert stats["keywords_removed"] == 0

    def test_no_entries(self, mock_data_dir):
        _clear_knowledge()
        stats = cleanup_stale_entries()
        assert stats == {"keywords_removed": 0, "sources_removed": 0}

    def test_malformed_first_seen_skipped(self, mock_data_dir):
        """If first_seen is not parseable, the entry is skipped during cleanup."""
        _clear_knowledge()
        _direct_create_entry("broken_date", "T", confidence=0.7,
                             match_count=1, first_seen="not-a-date")
        stats = cleanup_stale_entries(max_age_days=1)
        assert stats["keywords_removed"] == 0


# ════════════════════════════════════════════════════════════
#  get_knowledge_stats
# ════════════════════════════════════════════════════════════


class TestKnowledgeStats:
    """Counts, theme distribution, confidence distribution."""

    def test_empty_stats(self, mock_data_dir):
        _clear_knowledge()
        stats = get_knowledge_stats()
        assert stats["total_keywords"] == 0
        assert stats["total_sources"] == 0
        assert stats["themes"] == 0
        assert stats["avg_confidence"] == 0
        assert stats["confidence_distribution"] == {"high_80_100": 0, "medium_50_80": 0, "low_0_50": 0}

    def test_counts_and_theme_distribution(self, mock_data_dir):
        _clear_knowledge()
        record_match("白起.jpg", "白起", "武安侯", [])
        record_match("廉颇.jpg", "廉颇", "武安侯", [])
        record_match("兵马俑.jpg", "兵马俑", "秦始皇", [])
        stats = get_knowledge_stats()
        assert stats["total_keywords"] == 3
        assert stats["total_sources"] == 0
        assert stats["themes"] == 2
        assert "武安侯" in stats["theme_details"]
        assert "秦始皇" in stats["theme_details"]
        assert stats["theme_details"]["武安侯"]["count"] == 2
        assert stats["theme_details"]["秦始皇"]["count"] == 1

    def test_confidence_distribution(self, mock_data_dir):
        _clear_knowledge()
        # high: conf >= 0.8
        _direct_create_entry("high1", "T", confidence=0.95)
        _direct_create_entry("high2", "T", confidence=0.80)
        # medium: 0.5 <= conf < 0.8
        _direct_create_entry("med1", "T", confidence=0.70)
        # low: conf < 0.5
        _direct_create_entry("low1", "T", confidence=0.30)
        _direct_create_entry("low2", "T", confidence=0.10)

        stats = get_knowledge_stats()
        dist = stats["confidence_distribution"]
        assert dist["high_80_100"] == 2
        assert dist["medium_50_80"] == 1
        assert dist["low_0_50"] == 2

    def test_avg_confidence(self, mock_data_dir):
        _clear_knowledge()
        _direct_create_entry("a", "T", confidence=0.5)
        _direct_create_entry("b", "T", confidence=0.9)
        stats = get_knowledge_stats()
        assert stats["avg_confidence"] == pytest.approx(0.70)

    def test_theme_details_keywords_list(self, mock_data_dir):
        _clear_knowledge()
        record_match("白起.jpg", "白起", "武安侯", [])
        record_match("廉颇.jpg", "廉颇", "武安侯", [])
        stats = get_knowledge_stats()
        kw_set = set(stats["theme_details"]["武安侯"]["keywords"])
        assert kw_set == {"白起", "廉颇"}


# ════════════════════════════════════════════════════════════
#  thread safety
# ════════════════════════════════════════════════════════════


class TestThreadSafety:
    """Concurrent record_match calls must not corrupt state."""

    def test_concurrent_record_match(self, mock_data_dir):
        _clear_knowledge()

        n_threads = 20
        barrier = threading.Barrier(n_threads)

        def _worker(keyword: str):
            barrier.wait()                          # synchronised start
            record_match(f"{keyword}.jpg", keyword, "同主题", ["tag"])

        threads = [
            threading.Thread(target=_worker, args=(f"kw_{i}",))
            for i in range(n_threads)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        data = _load()
        mapping = data["keywords_mapping"]
        assert len(mapping) == n_threads
        for i in range(n_threads):
            kw = f"kw_{i}"
            assert kw in mapping
            assert mapping[kw]["match_count"] == 1
            assert mapping[kw]["confidence"] == pytest.approx(DEFAULT_CONFIDENCE_NEW)

    def test_concurrent_same_keyword(self, mock_data_dir):
        """Multiple threads writing the same keyword must not lose updates."""
        _clear_knowledge()

        # Pre-create the keyword
        record_match("first.jpg", "same_kw", "主题", [])

        n_threads = 10
        barrier = threading.Barrier(n_threads)

        def _worker():
            barrier.wait()
            record_match("dup.jpg", "same_kw", "主题", [])

        threads = [threading.Thread(target=_worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        data = _load()
        entry = data["keywords_mapping"]["same_kw"]
        # 1 pre-created + 10 concurrent = 11 total
        assert entry["match_count"] == 11
        expected_conf = min(
            DEFAULT_CONFIDENCE_NEW + (11 - 1) * CONFIDENCE_PER_MATCH,
            MAX_CONFIDENCE,
        )
        assert entry["confidence"] == pytest.approx(expected_conf)


# ════════════════════════════════════════════════════════════
#  异常处理
# ════════════════════════════════════════════════════════════


class TestLoadCorruptYaml:
    """_load() 应优雅处理损坏的 YAML 文件"""

    def test_corrupt_yaml_returns_empty(self, mock_data_dir):
        """损坏的 YAML → 返回空知识库，不抛异常"""
        KNOWLEDGE_PATH.write_text("{ broken: yaml: [\n")
        data = _load()
        assert data == {"keywords_mapping": {}, "sources": {}}

    def test_binary_garbage_returns_empty(self, mock_data_dir):
        """二进制垃圾数据 → 返回空知识库"""
        KNOWLEDGE_PATH.write_bytes(b"\x00\x01\x02\x03\xff\xfe")
        data = _load()
        assert data == {"keywords_mapping": {}, "sources": {}}
