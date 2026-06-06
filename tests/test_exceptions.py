"""异常类测试"""

import pytest
from eagle_watcher.exceptions import (
    EagleWatcherError,
    EagleAPIError,
    EagleConnectionError,
    EagleAuthError,
    EagleImportError,
    EagleTimeoutError,
    AIAnalysisError,
    AIModelError,
    AICacheError,
    AITimeoutError,
    AIKeyError,
    FileWatcherError,
    FSEventsError,
    PollingError,
    FileStabilityError,
    ConfigError,
    ConfigLoadError,
    ConfigSaveError,
    ConfigValidationError,
    ValidationError,
    ResourceNotFoundError,
    ConcurrencyError,
    wrap_exception,
    is_retryable_error,
)


class TestEagleWatcherError:
    """基础异常类测试"""

    def test_basic_creation(self):
        """测试基本创建"""
        error = EagleWatcherError("测试错误")
        assert str(error) == "测试错误"
        assert error.message == "测试错误"
        assert error.code == "EagleWatcherError"
        assert error.details == {}
        assert error.original_error is None

    def test_creation_with_code(self):
        """测试带错误代码创建"""
        error = EagleWatcherError("测试错误", code="TEST_ERROR")
        assert error.code == "TEST_ERROR"

    def test_creation_with_details(self):
        """测试带详情创建"""
        details = {"key": "value", "count": 42}
        error = EagleWatcherError("测试错误", details=details)
        assert error.details == details
        assert "key=value" in str(error)
        assert "count=42" in str(error)

    def test_creation_with_original_error(self):
        """测试带原始异常创建"""
        original = ValueError("原始错误")
        error = EagleWatcherError("测试错误", original_error=original)
        assert error.original_error is original

    def test_to_dict(self):
        """测试转换为字典"""
        error = EagleWatcherError(
            "测试错误",
            code="TEST_ERROR",
            details={"key": "value"},
            original_error=ValueError("原始错误"),
        )
        result = error.to_dict()
        assert result["error"] == "EagleWatcherError"
        assert result["message"] == "测试错误"
        assert result["code"] == "TEST_ERROR"
        assert result["details"] == {"key": "value"}
        assert "原始错误" in result["original_error"]

    def test_to_dict_minimal(self):
        """测试最小字典转换"""
        error = EagleWatcherError("测试错误")
        result = error.to_dict()
        assert result["error"] == "EagleWatcherError"
        assert result["message"] == "测试错误"
        assert result["code"] == "EagleWatcherError"
        assert "details" not in result
        assert "original_error" not in result

    def test_str_with_details(self):
        """测试带详情的字符串表示"""
        error = EagleWatcherError("测试错误", details={"path": "/tmp/test"})
        assert "测试错误" in str(error)
        assert "path=/tmp/test" in str(error)

    def test_str_without_details(self):
        """测试不带详情的字符串表示"""
        error = EagleWatcherError("测试错误")
        assert str(error) == "测试错误"


class TestEagleAPIErrors:
    """Eagle API 相关异常测试"""

    def test_eagle_api_error(self):
        """测试 EagleAPIError"""
        error = EagleAPIError("API 错误")
        assert isinstance(error, EagleWatcherError)
        assert error.code == "EagleAPIError"

    def test_eagle_connection_error(self):
        """测试 EagleConnectionError"""
        error = EagleConnectionError(host="http://localhost:41595")
        assert isinstance(error, EagleAPIError)
        assert error.code == "EAGLE_CONNECTION_ERROR"
        assert "无法连接到 Eagle 服务器" in error.message
        assert error.details["host"] == "http://localhost:41595"

    def test_eagle_connection_error_default_message(self):
        """测试 EagleConnectionError 默认消息"""
        error = EagleConnectionError()
        assert "无法连接到 Eagle 服务器" in error.message

    def test_eagle_auth_error(self):
        """测试 EagleAuthError"""
        error = EagleAuthError(token="abcdefghijklmnop")
        assert isinstance(error, EagleAPIError)
        assert error.code == "EAGLE_AUTH_ERROR"
        assert "认证失败" in error.message
        assert error.details["token_prefix"] == "abcdefgh..."

    def test_eagle_auth_error_short_token(self):
        """测试 EagleAuthError 短 Token"""
        error = EagleAuthError(token="short")
        assert error.details["token_prefix"] == "short"

    def test_eagle_import_error(self):
        """测试 EagleImportError"""
        error = EagleImportError(filename="test.jpg", result={"status": "failed"})
        assert isinstance(error, EagleAPIError)
        assert error.code == "EAGLE_IMPORT_ERROR"
        assert "素材导入 Eagle 失败" in error.message
        assert error.details["filename"] == "test.jpg"
        assert error.details["result"] == {"status": "failed"}

    def test_eagle_timeout_error(self):
        """测试 EagleTimeoutError"""
        error = EagleTimeoutError(timeout=30, endpoint="item/list")
        assert isinstance(error, EagleAPIError)
        assert error.code == "EAGLE_TIMEOUT_ERROR"
        assert "超时" in error.message
        assert error.details["timeout"] == 30
        assert error.details["endpoint"] == "item/list"


class TestAIAnalysisErrors:
    """AI 分析相关异常测试"""

    def test_ai_analysis_error(self):
        """测试 AIAnalysisError"""
        error = AIAnalysisError("AI 错误")
        assert isinstance(error, EagleWatcherError)
        assert error.code == "AIAnalysisError"

    def test_ai_model_error(self):
        """测试 AIModelError"""
        error = AIModelError(model="qwen-vl-max", status_code=500)
        assert isinstance(error, AIAnalysisError)
        assert error.code == "AI_MODEL_ERROR"
        assert "AI 模型调用失败" in error.message
        assert error.details["model"] == "qwen-vl-max"
        assert error.details["status_code"] == 500

    def test_ai_model_error_with_response(self):
        """测试 AIModelError 带响应"""
        long_response = "x" * 300
        error = AIModelError(response=long_response)
        assert len(error.details["response"]) == 203  # 200 + "..."
        assert error.details["response"].endswith("...")

    def test_ai_cache_error(self):
        """测试 AICacheError"""
        error = AICacheError(cache_path="/tmp/cache", operation="load")
        assert isinstance(error, AIAnalysisError)
        assert error.code == "AI_CACHE_ERROR"
        assert "AI 缓存操作失败" in error.message
        assert error.details["cache_path"] == "/tmp/cache"
        assert error.details["operation"] == "load"

    def test_ai_timeout_error(self):
        """测试 AITimeoutError"""
        error = AITimeoutError(timeout=60, file_path="/tmp/test.jpg")
        assert isinstance(error, AIAnalysisError)
        assert error.code == "AI_TIMEOUT_ERROR"
        assert "AI 分析超时" in error.message
        assert error.details["timeout"] == 60
        assert error.details["file_path"] == "/tmp/test.jpg"

    def test_ai_key_error(self):
        """测试 AIKeyError"""
        error = AIKeyError()
        assert isinstance(error, AIAnalysisError)
        assert error.code == "AI_KEY_ERROR"
        assert "AI API Key 未配置或无效" in error.message


class TestFileWatcherErrors:
    """文件监控相关异常测试"""

    def test_file_watcher_error(self):
        """测试 FileWatcherError"""
        error = FileWatcherError("监控错误")
        assert isinstance(error, EagleWatcherError)
        assert error.code == "FileWatcherError"

    def test_fsevents_error(self):
        """测试 FSEventsError"""
        error = FSEventsError(path="/tmp/test")
        assert isinstance(error, FileWatcherError)
        assert error.code == "FSEVENTS_ERROR"
        assert "FSEvents 监控失败" in error.message
        assert error.details["path"] == "/tmp/test"

    def test_polling_error(self):
        """测试 PollingError"""
        error = PollingError(path="/tmp/test")
        assert isinstance(error, FileWatcherError)
        assert error.code == "POLLING_ERROR"
        assert "文件轮询监控失败" in error.message
        assert error.details["path"] == "/tmp/test"

    def test_file_stability_error(self):
        """测试 FileStabilityError"""
        error = FileStabilityError(file_path="/tmp/test.jpg", timeout=30)
        assert isinstance(error, FileWatcherError)
        assert error.code == "FILE_STABILITY_ERROR"
        assert "文件稳定性检查超时" in error.message
        assert error.details["file_path"] == "/tmp/test.jpg"
        assert error.details["timeout"] == 30


class TestConfigErrors:
    """配置相关异常测试"""

    def test_config_error(self):
        """测试 ConfigError"""
        error = ConfigError("配置错误")
        assert isinstance(error, EagleWatcherError)
        assert error.code == "ConfigError"

    def test_config_load_error(self):
        """测试 ConfigLoadError"""
        error = ConfigLoadError(config_path="/tmp/config.yaml")
        assert isinstance(error, ConfigError)
        assert error.code == "CONFIG_LOAD_ERROR"
        assert "配置文件加载失败" in error.message
        assert error.details["config_path"] == "/tmp/config.yaml"

    def test_config_save_error(self):
        """测试 ConfigSaveError"""
        error = ConfigSaveError(config_path="/tmp/config.yaml")
        assert isinstance(error, ConfigError)
        assert error.code == "CONFIG_SAVE_ERROR"
        assert "配置文件保存失败" in error.message
        assert error.details["config_path"] == "/tmp/config.yaml"

    def test_config_validation_error(self):
        """测试 ConfigValidationError"""
        errors = ["缺少 eagle.host", "路径不存在"]
        warnings = ["缺少 token"]
        error = ConfigValidationError(errors=errors, warnings=warnings)
        assert isinstance(error, ConfigError)
        assert error.code == "CONFIG_VALIDATION_ERROR"
        assert "配置验证失败" in error.message
        assert error.details["errors"] == errors
        assert error.details["warnings"] == warnings


class TestOtherErrors:
    """其他异常测试"""

    def test_validation_error(self):
        """测试 ValidationError"""
        error = ValidationError(
            field="email",
            value="invalid",
            expected="有效的邮箱地址",
        )
        assert isinstance(error, EagleWatcherError)
        assert error.code == "VALIDATION_ERROR"
        assert "数据验证失败" in error.message
        assert error.details["field"] == "email"
        assert error.details["value"] == "invalid"
        assert error.details["expected"] == "有效的邮箱地址"

    def test_resource_not_found_error(self):
        """测试 ResourceNotFoundError"""
        error = ResourceNotFoundError(
            resource_type="文件",
            resource_id="/tmp/test.jpg",
        )
        assert isinstance(error, EagleWatcherError)
        assert error.code == "RESOURCE_NOT_FOUND"
        assert "资源未找到" in error.message
        assert error.details["resource_type"] == "文件"
        assert error.details["resource_id"] == "/tmp/test.jpg"

    def test_concurrency_error(self):
        """测试 ConcurrencyError"""
        error = ConcurrencyError(
            operation="写入",
            resource="state.json",
        )
        assert isinstance(error, EagleWatcherError)
        assert error.code == "CONCURRENCY_ERROR"
        assert "并发操作冲突" in error.message
        assert error.details["operation"] == "写入"
        assert error.details["resource"] == "state.json"


class TestWrapException:
    """wrap_exception 函数测试"""

    def test_wrap_basic(self):
        """测试基本包装"""
        original = ValueError("原始错误")
        wrapped = wrap_exception(original, EagleAPIError)
        assert isinstance(wrapped, EagleAPIError)
        assert wrapped.original_error is original
        assert "原始错误" in wrapped.message

    def test_wrap_with_custom_message(self):
        """测试自定义消息包装"""
        original = ValueError("原始错误")
        wrapped = wrap_exception(original, EagleAPIError, message="自定义消息")
        assert wrapped.message == "自定义消息"
        assert wrapped.original_error is original

    def test_wrap_with_kwargs(self):
        """测试带额外参数包装"""
        original = ValueError("原始错误")
        wrapped = wrap_exception(
            original,
            EagleConnectionError,
            host="http://localhost:41595",
        )
        assert isinstance(wrapped, EagleConnectionError)
        assert wrapped.details["host"] == "http://localhost:41595"


class TestIsRetryableError:
    """is_retryable_error 函数测试"""

    def test_retryable_project_errors(self):
        """测试项目特定的可重试错误"""
        assert is_retryable_error(EagleConnectionError()) is True
        assert is_retryable_error(EagleTimeoutError()) is True
        assert is_retryable_error(AITimeoutError()) is True
        assert is_retryable_error(FileStabilityError()) is True

    def test_non_retryable_project_errors(self):
        """测试项目特定的不可重试错误"""
        assert is_retryable_error(EagleAuthError()) is False
        assert is_retryable_error(AIKeyError()) is False
        assert is_retryable_error(ConfigError("配置错误")) is False
        assert is_retryable_error(ValidationError("验证错误")) is False

    def test_retryable_standard_errors(self):
        """测试标准库的可重试错误"""
        import urllib.error
        assert is_retryable_error(ConnectionError()) is True
        assert is_retryable_error(TimeoutError()) is True
        assert is_retryable_error(OSError()) is True
        assert is_retryable_error(urllib.error.URLError("test")) is True

    def test_retryable_http_errors(self):
        """测试 HTTP 状态码可重试错误"""
        import urllib.error
        assert is_retryable_error(urllib.error.HTTPError(
            url="http://test",
            code=502,
            msg="Bad Gateway",
            hdrs={},
            fp=None,
        )) is True
        assert is_retryable_error(urllib.error.HTTPError(
            url="http://test",
            code=503,
            msg="Service Unavailable",
            hdrs={},
            fp=None,
        )) is True
        assert is_retryable_error(urllib.error.HTTPError(
            url="http://test",
            code=504,
            msg="Gateway Timeout",
            hdrs={},
            fp=None,
        )) is True

    def test_non_retryable_http_errors(self):
        """测试 HTTP 状态码不可重试错误"""
        import urllib.error
        assert is_retryable_error(urllib.error.HTTPError(
            url="http://test",
            code=400,
            msg="Bad Request",
            hdrs={},
            fp=None,
        )) is False
        assert is_retryable_error(urllib.error.HTTPError(
            url="http://test",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=None,
        )) is False
        assert is_retryable_error(urllib.error.HTTPError(
            url="http://test",
            code=404,
            msg="Not Found",
            hdrs={},
            fp=None,
        )) is False

    def test_non_retryable_other_errors(self):
        """测试其他不可重试错误"""
        assert is_retryable_error(ValueError()) is False
        assert is_retryable_error(TypeError()) is False
        assert is_retryable_error(RuntimeError()) is False


class TestExceptionHierarchy:
    """异常层次结构测试"""

    def test_eagle_api_hierarchy(self):
        """测试 Eagle API 异常层次"""
        assert issubclass(EagleAPIError, EagleWatcherError)
        assert issubclass(EagleConnectionError, EagleAPIError)
        assert issubclass(EagleAuthError, EagleAPIError)
        assert issubclass(EagleImportError, EagleAPIError)
        assert issubclass(EagleTimeoutError, EagleAPIError)

    def test_ai_analysis_hierarchy(self):
        """测试 AI 分析异常层次"""
        assert issubclass(AIAnalysisError, EagleWatcherError)
        assert issubclass(AIModelError, AIAnalysisError)
        assert issubclass(AICacheError, AIAnalysisError)
        assert issubclass(AITimeoutError, AIAnalysisError)
        assert issubclass(AIKeyError, AIAnalysisError)

    def test_file_watcher_hierarchy(self):
        """测试文件监控异常层次"""
        assert issubclass(FileWatcherError, EagleWatcherError)
        assert issubclass(FSEventsError, FileWatcherError)
        assert issubclass(PollingError, FileWatcherError)
        assert issubclass(FileStabilityError, FileWatcherError)

    def test_config_hierarchy(self):
        """测试配置异常层次"""
        assert issubclass(ConfigError, EagleWatcherError)
        assert issubclass(ConfigLoadError, ConfigError)
        assert issubclass(ConfigSaveError, ConfigError)
        assert issubclass(ConfigValidationError, ConfigError)

    def test_catch_hierarchy(self):
        """测试异常捕获层次"""
        # EagleConnectionError 应该被 EagleAPIError 捕获
        with pytest.raises(EagleAPIError):
            raise EagleConnectionError()

        # EagleConnectionError 应该被 EagleWatcherError 捕获
        with pytest.raises(EagleWatcherError):
            raise EagleConnectionError()

        # AIModelError 应该被 AIAnalysisError 捕获
        with pytest.raises(AIAnalysisError):
            raise AIModelError()

        # AIModelError 应该被 EagleWatcherError 捕获
        with pytest.raises(EagleWatcherError):
            raise AIModelError()
