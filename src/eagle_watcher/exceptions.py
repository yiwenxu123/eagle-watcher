"""
Eagle Watcher 异常类层次结构

定义项目特定的异常类，提供更精细的错误处理和更好的调试体验。

异常层次：
- EagleWatcherError (基础异常)
  ├── EagleAPIError (Eagle API 相关)
  │   ├── EagleConnectionError (连接错误)
  │   ├── EagleAuthError (认证错误)
  │   └── EagleImportError (导入错误)
  ├── AIAnalysisError (AI 分析相关)
  │   ├── AIModelError (模型调用错误)
  │   ├── AICacheError (缓存错误)
  │   └── AITimeoutError (超时错误)
  ├── FileWatcherError (文件监控相关)
  │   ├── FSEventsError (FSEvents 错误)
  │   └── PollingError (轮询错误)
  ├── ConfigError (配置相关)
  │   └── ConfigValidationError (配置验证错误)
  └── ValidationError (数据验证错误)
"""

from typing import Optional, Any


class EagleWatcherError(Exception):
    """Eagle Watcher 基础异常类

    所有项目特定异常的基类，提供统一的错误处理接口。

    Attributes:
        message: 错误消息
        code: 错误代码（可选）
        details: 错误详情字典（可选）
        original_error: 原始异常（用于异常链）
    """

    def __init__(
        self,
        message: str,
        code: Optional[str] = None,
        details: Optional[dict] = None,
        original_error: Optional[Exception] = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code or self.__class__.__name__
        self.details = details or {}
        self.original_error = original_error

    def to_dict(self) -> dict:
        """转换为字典格式，便于日志记录和 API 响应"""
        result = {
            "error": self.__class__.__name__,
            "message": self.message,
            "code": self.code,
        }
        if self.details:
            result["details"] = self.details
        if self.original_error:
            result["original_error"] = str(self.original_error)
        return result

    def __str__(self) -> str:
        if self.details:
            details_str = ", ".join(f"{k}={v}" for k, v in self.details.items())
            return f"{self.message} ({details_str})"
        return self.message


# ──────────────────────────────────────────────────────────────
# Eagle API 相关异常
# ──────────────────────────────────────────────────────────────


class EagleAPIError(EagleWatcherError):
    """Eagle API 调用错误基类

    用于 Eagle API 调用过程中发生的错误。
    """
    pass


class EagleConnectionError(EagleAPIError):
    """Eagle 连接错误

    当无法连接到 Eagle 服务器时抛出。
    通常是可重试的错误。
    """

    def __init__(
        self,
        message: str = "无法连接到 Eagle 服务器",
        host: Optional[str] = None,
        original_error: Optional[Exception] = None,
    ):
        details = {}
        if host:
            details["host"] = host
        super().__init__(
            message=message,
            code="EAGLE_CONNECTION_ERROR",
            details=details,
            original_error=original_error,
        )


class EagleAuthError(EagleAPIError):
    """Eagle 认证错误

    当 API Token 无效或过期时抛出。
    通常是不可重试的错误，需要用户干预。
    """

    def __init__(
        self,
        message: str = "Eagle API 认证失败，请检查 Token 配置",
        token: Optional[str] = None,
        original_error: Optional[Exception] = None,
    ):
        details = {}
        if token:
            # 只显示 Token 的前 8 个字符，保护敏感信息
            details["token_prefix"] = token[:8] + "..." if len(token) > 8 else token
        super().__init__(
            message=message,
            code="EAGLE_AUTH_ERROR",
            details=details,
            original_error=original_error,
        )


class EagleImportError(EagleAPIError):
    """Eagle 素材导入错误

    当素材导入到 Eagle 失败时抛出。
    包含导入结果详情。
    """

    def __init__(
        self,
        message: str = "素材导入 Eagle 失败",
        filename: Optional[str] = None,
        result: Optional[dict] = None,
        original_error: Optional[Exception] = None,
    ):
        details = {}
        if filename:
            details["filename"] = filename
        if result:
            details["result"] = result
        super().__init__(
            message=message,
            code="EAGLE_IMPORT_ERROR",
            details=details,
            original_error=original_error,
        )


class EagleTimeoutError(EagleAPIError):
    """Eagle API 超时错误

    当 API 调用超时时抛出。
    通常是可重试的错误。
    """

    def __init__(
        self,
        message: str = "Eagle API 调用超时",
        timeout: Optional[int] = None,
        endpoint: Optional[str] = None,
        original_error: Optional[Exception] = None,
    ):
        details = {}
        if timeout:
            details["timeout"] = timeout
        if endpoint:
            details["endpoint"] = endpoint
        super().__init__(
            message=message,
            code="EAGLE_TIMEOUT_ERROR",
            details=details,
            original_error=original_error,
        )


# ──────────────────────────────────────────────────────────────
# AI 分析相关异常
# ──────────────────────────────────────────────────────────────


class AIAnalysisError(EagleWatcherError):
    """AI 分析错误基类

    用于 AI 视觉分析过程中发生的错误。
    """
    pass


class AIModelError(AIAnalysisError):
    """AI 模型调用错误

    当 AI 模型调用失败时抛出。
    包含模型名称和 API 响应详情。
    """

    def __init__(
        self,
        message: str = "AI 模型调用失败",
        model: Optional[str] = None,
        status_code: Optional[int] = None,
        response: Optional[str] = None,
        original_error: Optional[Exception] = None,
    ):
        details = {}
        if model:
            details["model"] = model
        if status_code:
            details["status_code"] = status_code
        if response:
            # 截断过长的响应
            details["response"] = response[:200] + "..." if len(response) > 200 else response
        super().__init__(
            message=message,
            code="AI_MODEL_ERROR",
            details=details,
            original_error=original_error,
        )


class AICacheError(AIAnalysisError):
    """AI 缓存错误

    当缓存操作失败时抛出。
    包含缓存文件路径和操作类型。
    """

    def __init__(
        self,
        message: str = "AI 缓存操作失败",
        cache_path: Optional[str] = None,
        operation: Optional[str] = None,
        original_error: Optional[Exception] = None,
    ):
        details = {}
        if cache_path:
            details["cache_path"] = cache_path
        if operation:
            details["operation"] = operation
        super().__init__(
            message=message,
            code="AI_CACHE_ERROR",
            details=details,
            original_error=original_error,
        )


class AITimeoutError(AIAnalysisError):
    """AI 分析超时错误

    当 AI 分析超时时抛出。
    包含超时时间和文件信息。
    """

    def __init__(
        self,
        message: str = "AI 分析超时",
        timeout: Optional[int] = None,
        file_path: Optional[str] = None,
        original_error: Optional[Exception] = None,
    ):
        details = {}
        if timeout:
            details["timeout"] = timeout
        if file_path:
            details["file_path"] = file_path
        super().__init__(
            message=message,
            code="AI_TIMEOUT_ERROR",
            details=details,
            original_error=original_error,
        )


class AIKeyError(AIAnalysisError):
    """AI API Key 错误

    当 API Key 未配置或无效时抛出。
    """

    def __init__(
        self,
        message: str = "AI API Key 未配置或无效",
        original_error: Optional[Exception] = None,
    ):
        super().__init__(
            message=message,
            code="AI_KEY_ERROR",
            original_error=original_error,
        )


# ──────────────────────────────────────────────────────────────
# 文件监控相关异常
# ──────────────────────────────────────────────────────────────


class FileWatcherError(EagleWatcherError):
    """文件监控错误基类

    用于文件监控过程中发生的错误。
    """
    pass


class FSEventsError(FileWatcherError):
    """FSEvents 错误

    当 macOS FSEvents 监控失败时抛出。
    通常会导致降级到 inode 轮询。
    """

    def __init__(
        self,
        message: str = "FSEvents 监控失败",
        path: Optional[str] = None,
        original_error: Optional[Exception] = None,
    ):
        details = {}
        if path:
            details["path"] = path
        super().__init__(
            message=message,
            code="FSEVENTS_ERROR",
            details=details,
            original_error=original_error,
        )


class PollingError(FileWatcherError):
    """轮询错误

    当 inode 轮询监控失败时抛出。
    """

    def __init__(
        self,
        message: str = "文件轮询监控失败",
        path: Optional[str] = None,
        original_error: Optional[Exception] = None,
    ):
        details = {}
        if path:
            details["path"] = path
        super().__init__(
            message=message,
            code="POLLING_ERROR",
            details=details,
            original_error=original_error,
        )


class FileStabilityError(FileWatcherError):
    """文件稳定性检查错误

    当文件在指定时间内未达到稳定状态时抛出。
    """

    def __init__(
        self,
        message: str = "文件稳定性检查超时",
        file_path: Optional[str] = None,
        timeout: Optional[int] = None,
        original_error: Optional[Exception] = None,
    ):
        details = {}
        if file_path:
            details["file_path"] = file_path
        if timeout:
            details["timeout"] = timeout
        super().__init__(
            message=message,
            code="FILE_STABILITY_ERROR",
            details=details,
            original_error=original_error,
        )


# ──────────────────────────────────────────────────────────────
# 配置相关异常
# ──────────────────────────────────────────────────────────────


class ConfigError(EagleWatcherError):
    """配置错误基类

    用于配置管理过程中发生的错误。
    """
    pass


class ConfigValidationError(ConfigError):
    """配置验证错误

    当配置文件验证失败时抛出。
    包含验证错误列表。
    """

    def __init__(
        self,
        message: str = "配置验证失败",
        errors: Optional[list] = None,
        warnings: Optional[list] = None,
        original_error: Optional[Exception] = None,
    ):
        details = {}
        if errors:
            details["errors"] = errors
        if warnings:
            details["warnings"] = warnings
        super().__init__(
            message=message,
            code="CONFIG_VALIDATION_ERROR",
            details=details,
            original_error=original_error,
        )


class ConfigLoadError(ConfigError):
    """配置加载错误

    当配置文件加载失败时抛出。
    """

    def __init__(
        self,
        message: str = "配置文件加载失败",
        config_path: Optional[str] = None,
        original_error: Optional[Exception] = None,
    ):
        details = {}
        if config_path:
            details["config_path"] = config_path
        super().__init__(
            message=message,
            code="CONFIG_LOAD_ERROR",
            details=details,
            original_error=original_error,
        )


class ConfigSaveError(ConfigError):
    """配置保存错误

    当配置文件保存失败时抛出。
    """

    def __init__(
        self,
        message: str = "配置文件保存失败",
        config_path: Optional[str] = None,
        original_error: Optional[Exception] = None,
    ):
        details = {}
        if config_path:
            details["config_path"] = config_path
        super().__init__(
            message=message,
            code="CONFIG_SAVE_ERROR",
            details=details,
            original_error=original_error,
        )


# ──────────────────────────────────────────────────────────────
# 通用错误
# ──────────────────────────────────────────────────────────────


class ValidationError(EagleWatcherError):
    """数据验证错误

    当输入数据验证失败时抛出。
    """

    def __init__(
        self,
        message: str = "数据验证失败",
        field: Optional[str] = None,
        value: Optional[Any] = None,
        expected: Optional[str] = None,
        original_error: Optional[Exception] = None,
    ):
        details = {}
        if field:
            details["field"] = field
        if value is not None:
            details["value"] = str(value)
        if expected:
            details["expected"] = expected
        super().__init__(
            message=message,
            code="VALIDATION_ERROR",
            details=details,
            original_error=original_error,
        )


class ResourceNotFoundError(EagleWatcherError):
    """资源未找到错误

    当请求的资源不存在时抛出。
    """

    def __init__(
        self,
        message: str = "资源未找到",
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        original_error: Optional[Exception] = None,
    ):
        details = {}
        if resource_type:
            details["resource_type"] = resource_type
        if resource_id:
            details["resource_id"] = resource_id
        super().__init__(
            message=message,
            code="RESOURCE_NOT_FOUND",
            details=details,
            original_error=original_error,
        )


class ConcurrencyError(EagleWatcherError):
    """并发错误

    当并发操作冲突时抛出。
    """

    def __init__(
        self,
        message: str = "并发操作冲突",
        operation: Optional[str] = None,
        resource: Optional[str] = None,
        original_error: Optional[Exception] = None,
    ):
        details = {}
        if operation:
            details["operation"] = operation
        if resource:
            details["resource"] = resource
        super().__init__(
            message=message,
            code="CONCURRENCY_ERROR",
            details=details,
            original_error=original_error,
        )


# ──────────────────────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────────────────────


def wrap_exception(
    original_error: Exception,
    new_exception_class: type,
    message: Optional[str] = None,
    **kwargs,
) -> EagleWatcherError:
    """将原始异常包装为项目特定异常

    Args:
        original_error: 原始异常
        new_exception_class: 新异常类
        message: 自定义错误消息（可选）
        **kwargs: 传递给新异常的其他参数

    Returns:
        包装后的项目特定异常
    """
    if message is None:
        message = str(original_error)

    return new_exception_class(
        message=message,
        original_error=original_error,
        **kwargs,
    )


def is_retryable_error(error: Exception) -> bool:
    """判断错误是否可重试

    Args:
        error: 异常对象

    Returns:
        True 如果错误可重试，False 否则
    """
    import urllib.error

    # 项目特定的可重试错误
    retryable_errors = (
        EagleConnectionError,
        EagleTimeoutError,
        AITimeoutError,
        FileStabilityError,
    )

    if isinstance(error, retryable_errors):
        return True

    # HTTP 状态码可重试（必须在 URLError 之前检查，因为 HTTPError 是 URLError 的子类）
    if isinstance(error, urllib.error.HTTPError):
        return error.code in {502, 503, 504}

    # 标准库的可重试错误
    standard_retryable = (
        urllib.error.URLError,
        ConnectionError,
        TimeoutError,
        OSError,
    )

    if isinstance(error, standard_retryable):
        return True

    return False
