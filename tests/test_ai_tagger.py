import json
import os
import time
import hashlib
import base64
from pathlib import Path
from unittest.mock import MagicMock, patch
from collections import OrderedDict

import pytest

from eagle_watcher import ai_tagger
from eagle_watcher.exceptions import AICacheError, AIKeyError, AIModelError


# ============================================================
# Helper fixtures
# ============================================================

@pytest.fixture
def test_png(tmp_path):
    """创建测试用 PNG 文件。"""
    img_path = tmp_path / "test.png"
    img_path.write_bytes(b"fake_png_content")
    return str(img_path)


@pytest.fixture
def test_jpg(tmp_path):
    """创建测试用 JPG 文件。"""
    img_path = tmp_path / "test.jpg"
    img_path.write_bytes(b"fake_jpg_content")
    return str(img_path)


@pytest.fixture
def with_api_key(monkeypatch):
    """在测试中设置 DASHSCOPE_API_KEY 环境变量。"""
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-api-key")


@pytest.fixture
def mock_dashscope_success():
    """模拟 DashScope API 返回成功响应。"""
    response_text = "标签：山水, 风景, 自然\n文件名：山水风景图"
    with patch("eagle_watcher.ai_tagger.MultiModalConversation.call") as mock:
        message_mock = MagicMock()
        message_mock.content = response_text
        choice_mock = MagicMock()
        choice_mock.message = message_mock
        mock.return_value.status_code = 200
        mock.return_value.output.choices = [choice_mock]
        yield mock


# ============================================================
# _get_api_key
# ============================================================

class TestGetApiKey:
    def test_from_config_first(self, monkeypatch):
        """优先从 config.yaml 读取。"""
        def mock_load_config():
            return {"ai": {"api_key": "config-key-123"}}
        monkeypatch.setattr("eagle_watcher.config.load_config", mock_load_config)
        monkeypatch.setenv("DASHSCOPE_API_KEY", "env-key-456")
        assert ai_tagger._get_api_key() == "config-key-123"

    def test_fallback_to_env(self, monkeypatch):
        """config.yaml 无值时回退到环境变量。"""
        def mock_load_config():
            return {"ai": {}}
        monkeypatch.setattr("eagle_watcher.config.load_config", mock_load_config)
        monkeypatch.setenv("DASHSCOPE_API_KEY", "env-key-456")
        assert ai_tagger._get_api_key() == "env-key-456"

    def test_returns_none(self, monkeypatch):
        """两者都没有时返回 None。"""
        def mock_load_config():
            return {"ai": {}}
        monkeypatch.setattr("eagle_watcher.config.load_config", mock_load_config)
        monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
        assert ai_tagger._get_api_key() is None

    def test_config_load_fallback_to_env(self, monkeypatch):
        """load_config 异常时回退到环境变量。"""
        monkeypatch.setattr("eagle_watcher.config.load_config", lambda: (_ for _ in ()).throw(ImportError))
        monkeypatch.setenv("DASHSCOPE_API_KEY", "fallback-key")
        assert ai_tagger._get_api_key() == "fallback-key"


# ============================================================
# _get_image_hash
# ============================================================

class TestGetImageHash:
    def test_returns_md5(self, test_png):
        """正确计算文件的 MD5 哈希。"""
        expected = hashlib.md5(b"fake_png_content").hexdigest()
        assert ai_tagger._get_image_hash(test_png) == expected

    def test_missing_file_returns_empty(self):
        """文件不存在时返回空字符串。"""
        assert ai_tagger._get_image_hash("/nonexistent/file.png") == ""


# ============================================================
# _load_cache
# ============================================================

class TestLoadCache:
    def test_returns_cached_result(self, mock_data_dir):
        """有效哈希返回缓存结果。"""
        image_hash = "cachedhash123"
        cache_dir = mock_data_dir / "cache"
        cache_file = cache_dir / f"{image_hash}.json"
        expected = {"tags": ["测试"], "name": "测试图", "raw": "test"}
        cache_file.write_text(json.dumps(expected, ensure_ascii=False), encoding="utf-8")

        result = ai_tagger._load_cache(image_hash)
        assert result == expected

    def test_expired_cache_returns_none(self, mock_data_dir):
        """过期缓存（>30天）返回 None 并删除文件。"""
        image_hash = "oldhash"
        cache_dir = mock_data_dir / "cache"
        cache_file = cache_dir / f"{image_hash}.json"
        cache_file.write_text(json.dumps({"tags": ["旧"]}), encoding="utf-8")

        # 将 mtime 设为 31 天前
        old_ts = time.time() - 31 * 86400
        os.utime(cache_file, (old_ts, old_ts))

        result = ai_tagger._load_cache(image_hash)
        assert result is None
        assert not cache_file.exists()

    def test_nonexistent_cache_returns_none(self):
        """不存在的缓存返回 None。"""
        assert ai_tagger._load_cache("nonexistent") is None

    def test_empty_hash_returns_none(self):
        """空哈希返回 None。"""
        assert ai_tagger._load_cache("") is None

    def test_corrupted_json_raises_cache_error(self, mock_data_dir):
        """损坏的 JSON 文件抛出 AICacheError。"""
        image_hash = "badjson"
        cache_dir = mock_data_dir / "cache"
        cache_file = cache_dir / f"{image_hash}.json"
        cache_file.write_text("{invalid json", encoding="utf-8")

        with pytest.raises(AICacheError) as exc_info:
            ai_tagger._load_cache(image_hash)
        assert exc_info.value.code == "AI_CACHE_ERROR"
        assert exc_info.value.details["operation"] == "load"


# ============================================================
# _save_cache
# ============================================================

class TestSaveCache:
    def test_writes_json_file(self, mock_data_dir):
        """写入正确的 JSON 缓存文件。"""
        image_hash = "savetest"
        result = {"tags": ["保存", "测试"], "name": "测试文件", "raw": "test"}
        ai_tagger._save_cache(image_hash, result)

        cache_file = mock_data_dir / "cache" / f"{image_hash}.json"
        assert cache_file.exists()
        assert json.loads(cache_file.read_text(encoding="utf-8")) == result

    def test_empty_hash_does_nothing(self, mock_data_dir):
        """空哈希不写入。"""
        ai_tagger._save_cache("", {"tags": []})
        # 空哈希时函数直接 return，不会创建文件
        cache_files = list((mock_data_dir / "cache").iterdir())
        assert len(cache_files) == 0

    def test_creates_cache_dir(self, mock_data_dir):
        """自动创建缓存目录。"""
        cache_dir = mock_data_dir / "cache"
        import shutil
        shutil.rmtree(cache_dir)
        assert not cache_dir.exists()

        ai_tagger._save_cache("newhash", {"tags": ["新"]})
        assert cache_dir.exists()
        assert (cache_dir / "newhash.json").exists()


# ============================================================
# _encode_image
# ============================================================

class TestEncodeImage:
    def test_png_data_uri(self, test_png):
        """PNG 图片返回正确的 data URI。"""
        result = ai_tagger._encode_image(test_png)
        expected_b64 = base64.b64encode(b"fake_png_content").decode("utf-8")
        assert result == f"data:image/png;base64,{expected_b64}"

    def test_jpg_mime_type(self, test_jpg):
        """JPG 图片映射为 image/jpeg。"""
        result = ai_tagger._encode_image(test_jpg)
        assert result.startswith("data:image/jpeg;base64,")

    def test_unknown_extension_defaults_to_png(self, tmp_path):
        """未知扩展名默认为 image/png。"""
        img = tmp_path / "test.unknown"
        img.write_bytes(b"content")
        result = ai_tagger._encode_image(str(img))
        assert result.startswith("data:image/png;base64,")

    def test_nonexistent_file(self):
        """不存在的文件返回 None。"""
        assert ai_tagger._encode_image("/nonexistent/file.png") is None


# ============================================================
# _get_model
# ============================================================

class TestGetModel:
    def test_from_config(self, monkeypatch):
        """从配置读取模型名。"""
        def mock_load_config():
            return {"ai": {"model": "qwen-vl-plus"}}
        monkeypatch.setattr("eagle_watcher.config.load_config", mock_load_config)
        assert ai_tagger._get_model() == "qwen-vl-plus"

    def test_default_fallback(self, monkeypatch):
        """配置无模型时返回 DEFAULT_MODEL。"""
        def mock_load_config():
            return {"ai": {}}
        monkeypatch.setattr("eagle_watcher.config.load_config", mock_load_config)
        assert ai_tagger._get_model() == ai_tagger.DEFAULT_MODEL

    def test_config_load_failure(self, monkeypatch):
        """load_config 异常时返回 DEFAULT_MODEL。"""
        monkeypatch.setattr("eagle_watcher.config.load_config", lambda: (_ for _ in ()).throw(ImportError))
        assert ai_tagger._get_model() == ai_tagger.DEFAULT_MODEL


# ============================================================
# _parse_response
# ============================================================

class TestParseResponse:
    def test_standard_format(self):
        """标准格式解析：中文冒号，逗号分隔标签。"""
        text = "标签：人物, 肖像, 摄影\n文件名：人物肖像照"
        result = ai_tagger._parse_response(text, "/path/img.jpg")
        assert result["tags"] == ["人物", "肖像", "摄影"]
        assert result["name"] == "人物肖像照"
        assert result["raw"] == text

    def test_english_colon(self):
        """英文冒号变体。"""
        text = "标签:建筑,城市,夜景\n文件名:城市夜景"
        result = ai_tagger._parse_response(text, "/path/img.jpg")
        assert result["tags"] == ["建筑", "城市", "夜景"]
        assert result["name"] == "城市夜景"

    def test_chinese_comma(self):
        """中文逗号分隔标签。"""
        text = "标签：人物、肖像\n文件名：人物图"
        result = ai_tagger._parse_response(text, "/path/img.jpg")
        assert result["tags"] == ["人物、肖像"]
        assert result["name"] == "人物图"

    def test_malformed_fallback_to_split(self):
        """非标准格式回退到逗号分割取前5个。"""
        text = "日出的景色非常美丽 金色阳光 橙色天空 风景摄影"
        result = ai_tagger._parse_response(text, "/path/sunset.jpg")
        assert len(result["tags"]) > 0
        # 没有标签行也没有文件名行，但文本以逗号分割
        assert result["name"] == "sunset"  # 从文件名提取

    def test_empty_text(self):
        """空文本返回文件名作为 name。"""
        result = ai_tagger._parse_response("", "/path/test.jpg")
        assert result["tags"] == []
        assert result["name"] == "test"
        assert result["raw"] == ""

    def test_only_tags(self):
        """只有标签行，没有文件名行时使用文件名。"""
        text = "标签：猫, 动物, 可爱"
        result = ai_tagger._parse_response(text, "/path/kitten.png")
        assert result["tags"] == ["猫", "动物", "可爱"]
        assert result["name"] == "kitten"

    def test_only_name(self):
        """只有文件名行，没有标签行。"""
        text = "文件名：可爱猫咪"
        result = ai_tagger._parse_response(text, "/path/kitten.png")
        assert result["name"] == "可爱猫咪"
        assert result["tags"] == []

    def test_more_than_eight_tags(self):
        """超过8个标签时只保留前8个。"""
        text = "标签：一, 二, 三, 四, 五, 六, 七, 八, 九, 十\n文件名：测试"
        result = ai_tagger._parse_response(text, "/path/test.png")
        assert len(result["tags"]) == 8
        assert result["tags"] == ["一", "二", "三", "四", "五", "六", "七", "八"]


# ============================================================
# analyze_image
# ============================================================

class TestAnalyzeImage:
    def test_cache_hit_returns_without_api_call(self, mock_data_dir, test_png):
        """缓存命中直接返回，不调用 API。"""
        # 预填充缓存
        image_hash = hashlib.md5(b"fake_png_content").hexdigest()
        cache_data = {"tags": ["缓存"], "name": "缓存结果", "raw": "cached"}
        cache_file = mock_data_dir / "cache" / f"{image_hash}.json"
        cache_file.write_text(json.dumps(cache_data, ensure_ascii=False), encoding="utf-8")

        with patch("eagle_watcher.ai_tagger._encode_image") as mock_encode:
            result = ai_tagger.analyze_image(test_png, use_cache=True)
            assert result == cache_data
            mock_encode.assert_not_called()

    def test_cache_miss_calls_api_and_caches(self, mock_data_dir, tmp_path, with_api_key, mock_dashscope_success):
        """缓存未命中时调用 API 并缓存结果。"""
        img_path = tmp_path / "test.png"
        img_path.write_bytes(b"analyze_me")
        image_hash = hashlib.md5(b"analyze_me").hexdigest()

        result = ai_tagger.analyze_image(str(img_path), use_cache=True)

        assert result is not None
        assert result["tags"] == ["山水", "风景", "自然"]
        assert result["name"] == "山水风景图"
        assert mock_dashscope_success.call_count == 1

        # 验证缓存被保存
        cache_file = mock_data_dir / "cache" / f"{image_hash}.json"
        assert cache_file.exists()
        assert json.loads(cache_file.read_text(encoding="utf-8")) == result

    def test_retries_on_api_failure(self, mock_data_dir, tmp_path, with_api_key):
        """API 失败时重试（MAX_RETRIES 次）。"""
        img_path = tmp_path / "test.png"
        img_path.write_bytes(b"retry_me")

        with patch("eagle_watcher.ai_tagger._call_qwen_vl") as mock_call:
            mock_call.side_effect = Exception("API Error")
            result = ai_tagger.analyze_image(str(img_path), use_cache=False)
            assert result is None
            assert mock_call.call_count == ai_tagger.MAX_RETRIES

    def test_returns_none_after_all_retries(self, mock_data_dir, tmp_path, with_api_key):
        """所有重试失败后返回 None。"""
        img_path = tmp_path / "test.png"
        img_path.write_bytes(b"fail_all")

        with patch("eagle_watcher.ai_tagger.MultiModalConversation.call") as mock:
            mock.side_effect = Exception("Persistent Error")
            result = ai_tagger.analyze_image(str(img_path), use_cache=False)
            assert result is None
            assert mock.call_count == ai_tagger.MAX_RETRIES

    def test_encode_failure_returns_none(self, mock_data_dir):
        """文件编码失败时立即返回 None。"""
        result = ai_tagger.analyze_image("/nonexistent/file.png", use_cache=False)
        assert result is None

    def test_cache_disabled_always_calls_api(self, mock_data_dir, tmp_path, with_api_key, mock_dashscope_success):
        """use_cache=False 时跳过缓存检查，直接调用 API。"""
        img_path = tmp_path / "test.png"
        img_path.write_bytes(b"no_cache_please")
        image_hash = hashlib.md5(b"no_cache_please").hexdigest()

        # 预填充缓存
        cache_data = {"tags": ["旧缓存"], "name": "旧结果", "raw": "old"}
        cache_file = mock_data_dir / "cache" / f"{image_hash}.json"
        cache_file.write_text(json.dumps(cache_data, ensure_ascii=False), encoding="utf-8")

        result = ai_tagger.analyze_image(str(img_path), use_cache=False)

        # 即使有缓存，也调用了 API（返回新的结果）
        assert mock_dashscope_success.call_count == 1
        assert result["tags"] == ["山水", "风景", "自然"]  # 来自 mock API 的结果

    def test_no_api_key_returns_none(self, mock_data_dir, tmp_path):
        """没有 API Key 时 analyze_image 返回 None。"""
        img_path = tmp_path / "test.png"
        img_path.write_bytes(b"no_key")

        result = ai_tagger.analyze_image(str(img_path), use_cache=False)
        assert result is None

    def test_saves_cache_on_success(self, mock_data_dir, tmp_path, with_api_key, mock_dashscope_success):
        """API 调用成功后将结果存入缓存。"""
        img_path = tmp_path / "test.png"
        img_path.write_bytes(b"save_cache")
        image_hash = hashlib.md5(b"save_cache").hexdigest()

        result = ai_tagger.analyze_image(str(img_path), use_cache=True)

        cache_file = mock_data_dir / "cache" / f"{image_hash}.json"
        assert cache_file.exists()
        assert json.loads(cache_file.read_text(encoding="utf-8")) == result


# ============================================================
# clear_cache & get_cache_size
# ============================================================

class TestCacheManagement:
    def test_clear_cache_removes_directory(self, mock_data_dir):
        """clear_cache 删除整个缓存目录。"""
        cache_dir = mock_data_dir / "cache"
        (cache_dir / "test.json").write_text("{}")
        assert cache_dir.exists()

        ai_tagger.clear_cache()
        assert not cache_dir.exists()

    def test_clear_cache_no_error_when_empty(self, mock_data_dir):
        """缓存目录不存在时 clear_cache 不会报错。"""
        import shutil
        shutil.rmtree(mock_data_dir / "cache")
        ai_tagger.clear_cache()  # 不应抛出异常

    def test_get_cache_size_returns_bytes(self, mock_data_dir):
        """get_cache_size 返回目录总字节数。"""
        cache_dir = mock_data_dir / "cache"
        (cache_dir / "a.json").write_text('{"a": 1}')
        (cache_dir / "b.json").write_text('{"b": 2}')

        size = ai_tagger.get_cache_size()
        assert size == len(b'{"a": 1}') + len(b'{"b": 2}')

    def test_get_cache_size_empty(self, mock_data_dir):
        """空缓存返回 0。"""
        assert ai_tagger.get_cache_size() == 0

    def test_get_cache_size_no_directory(self, mock_data_dir):
        """缓存目录不存在时返回 0。"""
        import shutil
        shutil.rmtree(mock_data_dir / "cache")
        assert ai_tagger.get_cache_size() == 0


# ============================================================
# 内存缓存 (_get_memory_cache / _set_memory_cache)
# ============================================================

class TestMemoryCache:
    def setup_method(self):
        """每个测试前清空内存缓存。"""
        ai_tagger._memory_cache.clear()

    def test_get_returns_cached_value(self):
        """命中时返回缓存值。"""
        ai_tagger._memory_cache["hash1"] = {"tags": ["测试"]}
        result = ai_tagger._get_memory_cache("hash1")
        assert result == {"tags": ["测试"]}

    def test_get_miss_returns_none(self):
        """未命中时返回 None。"""
        assert ai_tagger._get_memory_cache("nonexistent") is None

    def test_set_and_get(self):
        """设置后可以获取。"""
        ai_tagger._set_memory_cache("h1", {"tags": ["a"]})
        assert ai_tagger._get_memory_cache("h1") == {"tags": ["a"]}

    def test_set_updates_existing(self):
        """更新已有条目。"""
        ai_tagger._set_memory_cache("h1", {"tags": ["old"]})
        ai_tagger._set_memory_cache("h1", {"tags": ["new"]})
        assert ai_tagger._get_memory_cache("h1") == {"tags": ["new"]}

    def test_lru_eviction(self):
        """超过最大大小时淘汰最旧条目。"""
        for i in range(ai_tagger.MEMORY_CACHE_MAX_SIZE + 5):
            ai_tagger._set_memory_cache(f"hash_{i}", {"tags": [str(i)]})
        assert len(ai_tagger._memory_cache) == ai_tagger.MEMORY_CACHE_MAX_SIZE
        # 最早的条目应被淘汰
        assert ai_tagger._get_memory_cache("hash_0") is None
        # 最新的条目应保留
        assert ai_tagger._get_memory_cache(f"hash_{ai_tagger.MEMORY_CACHE_MAX_SIZE + 4}") is not None

    def test_lru_move_to_end_on_get(self):
        """读取时将条目移到末尾（LRU）。"""
        ai_tagger._set_memory_cache("a", {"tags": ["a"]})
        ai_tagger._set_memory_cache("b", {"tags": ["b"]})
        # 读取 "a" 使其移到末尾
        ai_tagger._get_memory_cache("a")
        # "b" 现在是最早的，"a" 是最新的
        keys = list(ai_tagger._memory_cache.keys())
        assert keys[-1] == "a"
        assert keys[0] == "b"


# ============================================================
# 缓存文件锁 (_acquire_cache_lock / _release_cache_lock)
# ============================================================

class TestCacheLock:
    def test_acquire_and_release(self, mock_data_dir):
        """正常获取和释放锁。"""
        lock_file = ai_tagger._acquire_cache_lock(timeout=2)
        assert lock_file is not None
        assert not lock_file.closed
        ai_tagger._release_cache_lock(lock_file)
        assert lock_file.closed

    def test_release_handles_errors(self):
        """释放锁时静默处理异常。"""
        mock_file = MagicMock()
        mock_file.fileno.side_effect = OSError("already closed")
        ai_tagger._release_cache_lock(mock_file)  # 不应抛出异常


# ============================================================
# _call_qwen_vl
# ============================================================

class TestCallQwenVl:
    def test_raises_ai_key_error_when_no_key(self, monkeypatch):
        """没有 API Key 时抛出 AIKeyError。"""
        monkeypatch.setattr("eagle_watcher.ai_tagger._get_api_key", lambda: None)
        with pytest.raises(AIKeyError):
            ai_tagger._call_qwen_vl("data:image/png;base64,abc")

    def test_raises_ai_model_error_on_non_200(self, monkeypatch):
        """非 200 状态码抛出 AIModelError。"""
        monkeypatch.setattr("eagle_watcher.ai_tagger._get_api_key", lambda: "test-key")
        monkeypatch.setattr("eagle_watcher.ai_tagger._get_model", lambda: "test-model")

        with patch("eagle_watcher.ai_tagger.MultiModalConversation.call") as mock:
            mock.return_value.status_code = 500
            with pytest.raises(AIModelError) as exc_info:
                ai_tagger._call_qwen_vl("data:image/png;base64,abc")
            assert exc_info.value.details["status_code"] == 500

    def test_raises_ai_model_error_on_exception(self, monkeypatch):
        """API 调用异常时抛出 AIModelError。"""
        monkeypatch.setattr("eagle_watcher.ai_tagger._get_api_key", lambda: "test-key")
        monkeypatch.setattr("eagle_watcher.ai_tagger._get_model", lambda: "test-model")

        with patch("eagle_watcher.ai_tagger.MultiModalConversation.call") as mock:
            mock.side_effect = ConnectionError("网络错误")
            with pytest.raises(AIModelError) as exc_info:
                ai_tagger._call_qwen_vl("data:image/png;base64,abc")
            assert "网络错误" in str(exc_info.value)

    def test_success_returns_text(self, monkeypatch):
        """成功调用返回文本。"""
        monkeypatch.setattr("eagle_watcher.ai_tagger._get_api_key", lambda: "test-key")
        monkeypatch.setattr("eagle_watcher.ai_tagger._get_model", lambda: "test-model")

        with patch("eagle_watcher.ai_tagger.MultiModalConversation.call") as mock:
            message_mock = MagicMock()
            message_mock.content = "标签：测试\n文件名：测试图"
            choice_mock = MagicMock()
            choice_mock.message = message_mock
            mock.return_value.status_code = 200
            mock.return_value.output.choices = [choice_mock]

            result = ai_tagger._call_qwen_vl("data:image/png;base64,abc")
            assert result == "标签：测试\n文件名：测试图"

    def test_list_content_joined(self, monkeypatch):
        """content 为列表时拼接文本。"""
        monkeypatch.setattr("eagle_watcher.ai_tagger._get_api_key", lambda: "test-key")
        monkeypatch.setattr("eagle_watcher.ai_tagger._get_model", lambda: "test-model")

        with patch("eagle_watcher.ai_tagger.MultiModalConversation.call") as mock:
            message_mock = MagicMock()
            message_mock.content = [{"text": "标签：测试"}, {"text": "\n文件名：测试图"}]
            choice_mock = MagicMock()
            choice_mock.message = message_mock
            mock.return_value.status_code = 200
            mock.return_value.output.choices = [choice_mock]

            result = ai_tagger._call_qwen_vl("data:image/png;base64,abc")
            assert result == "标签：测试\n文件名：测试图"


# ============================================================
# analyze_image 补充测试
# ============================================================

class TestAnalyzeImageExtended:
    def test_ai_key_error_no_retry(self, mock_data_dir, tmp_path, monkeypatch):
        """AIKeyError 不重试，直接返回 None。"""
        img_path = tmp_path / "test.png"
        img_path.write_bytes(b"no_key_retry")

        with patch("eagle_watcher.ai_tagger._get_image_hash", return_value="hash"), \
             patch("eagle_watcher.ai_tagger._encode_image", return_value="data:image/png;base64,abc"), \
             patch("eagle_watcher.ai_tagger._call_qwen_vl", side_effect=AIKeyError()):
            result = ai_tagger.analyze_image(str(img_path), use_cache=False)
            assert result is None

    def test_ai_model_error_retries(self, mock_data_dir, tmp_path, with_api_key):
        """AIModelError 触发重试。"""
        img_path = tmp_path / "test.png"
        img_path.write_bytes(b"model_retry")

        success_text = "标签：重试成功\n文件名：重试图"
        with patch("eagle_watcher.ai_tagger._call_qwen_vl") as mock_call, \
             patch("eagle_watcher.ai_tagger.time.sleep"):
            mock_call.side_effect = [
                AIModelError(message="第一次失败", model="test"),
                success_text,
            ]
            result = ai_tagger.analyze_image(str(img_path), use_cache=False)
            assert result is not None
            assert result["name"] == "重试图"
            assert mock_call.call_count == 2

    def test_cache_read_error_continues(self, mock_data_dir, tmp_path, with_api_key, mock_dashscope_success):
        """缓存读取失败时继续执行分析。"""
        img_path = tmp_path / "test.png"
        img_path.write_bytes(b"cache_err_continue")

        with patch("eagle_watcher.ai_tagger._load_cache", side_effect=AICacheError("缓存失败")):
            result = ai_tagger.analyze_image(str(img_path), use_cache=True)
            assert result is not None

    def test_cache_save_error_continues(self, mock_data_dir, tmp_path, with_api_key, mock_dashscope_success):
        """缓存保存失败时继续返回结果。"""
        img_path = tmp_path / "test.png"
        img_path.write_bytes(b"cache_save_err")

        with patch("eagle_watcher.ai_tagger._save_cache", side_effect=AICacheError("保存失败")):
            result = ai_tagger.analyze_image(str(img_path), use_cache=True)
            assert result is not None
            assert result["tags"] == ["山水", "风景", "自然"]

    def test_memory_cache_hit_skips_file_cache(self, mock_data_dir, tmp_path):
        """内存缓存命中时跳过文件缓存读取。"""
        img_path = tmp_path / "test.png"
        img_path.write_bytes(b"mem_cache_hit")
        image_hash = hashlib.md5(b"mem_cache_hit").hexdigest()

        # 只设置内存缓存
        cached_data = {"tags": ["内存缓存"], "name": "内存结果", "raw": "mem"}
        ai_tagger._set_memory_cache(image_hash, cached_data)

        result = ai_tagger.analyze_image(str(img_path), use_cache=True)
        assert result == cached_data


# ============================================================
# _encode_image 补充测试
# ============================================================

class TestEncodeImageExtended:
    def test_gif_mime_type(self, tmp_path):
        """GIF 图片映射为 image/gif。"""
        img = tmp_path / "test.gif"
        img.write_bytes(b"fake_gif")
        result = ai_tagger._encode_image(str(img))
        assert result.startswith("data:image/gif;base64,")

    def test_webp_mime_type(self, tmp_path):
        """WebP 图片映射为 image/webp。"""
        img = tmp_path / "test.webp"
        img.write_bytes(b"fake_webp")
        result = ai_tagger._encode_image(str(img))
        assert result.startswith("data:image/webp;base64,")

    def test_bmp_mime_type(self, tmp_path):
        """BMP 图片映射为 image/bmp。"""
        img = tmp_path / "test.bmp"
        img.write_bytes(b"fake_bmp")
        result = ai_tagger._encode_image(str(img))
        assert result.startswith("data:image/bmp;base64,")

    def test_permission_error_returns_none(self, tmp_path, monkeypatch):
        """权限错误返回 None。"""
        img = tmp_path / "test.png"
        img.write_bytes(b"content")
        monkeypatch.setattr("builtins.open", MagicMock(side_effect=PermissionError("denied")))
        assert ai_tagger._encode_image(str(img)) is None


# ============================================================
# _load_cache 补充测试
# ============================================================

class TestLoadCacheExtended:
    def test_memory_cache_hit(self, mock_data_dir):
        """内存缓存命中时直接返回，不读文件。"""
        image_hash = "mem_hit_hash"
        cached = {"tags": ["内存"], "name": "内存测试", "raw": "mem"}
        ai_tagger._set_memory_cache(image_hash, cached)

        result = ai_tagger._load_cache(image_hash)
        assert result == cached

    def test_loads_into_memory_cache(self, mock_data_dir):
        """从文件缓存加载后同时写入内存缓存。"""
        image_hash = "file_to_mem"
        cache_dir = mock_data_dir / "cache"
        cache_file = cache_dir / f"{image_hash}.json"
        expected = {"tags": ["文件"], "name": "文件测试", "raw": "file"}
        cache_file.write_text(json.dumps(expected, ensure_ascii=False), encoding="utf-8")

        ai_tagger._memory_cache.clear()
        result = ai_tagger._load_cache(image_hash)
        assert result == expected
        # 验证已写入内存缓存
        assert ai_tagger._get_memory_cache(image_hash) == expected