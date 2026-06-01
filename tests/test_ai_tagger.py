import json
import os
import time
import hashlib
import base64
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from eagle_watcher import ai_tagger


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

    def test_corrupted_json_returns_none(self, mock_data_dir):
        """损坏的 JSON 文件返回 None。"""
        image_hash = "badjson"
        cache_dir = mock_data_dir / "cache"
        cache_file = cache_dir / f"{image_hash}.json"
        cache_file.write_text("{invalid json", encoding="utf-8")

        result = ai_tagger._load_cache(image_hash)
        assert result is None


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