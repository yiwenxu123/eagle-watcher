"""
Eagle HTTP API 封装
参考：http://localhost:41595/ (Eagle 内建 API 文档)
"""

import json
import logging
import os
import time
import urllib.parse
import urllib.request
import urllib.error
from typing import Optional, Any
from functools import wraps

from eagle_watcher.exceptions import (
    EagleAPIError,
    EagleConnectionError,
    EagleAuthError,
    EagleTimeoutError,
    is_retryable_error,
    wrap_exception,
)

_LOG = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30
FILE_TIMEOUT = 60
URL_TIMEOUT = 120
PING_TIMEOUT = 5

# 重试配置
MAX_RETRIES = 3
INITIAL_RETRY_DELAY = 1  # 秒
MAX_RETRY_DELAY = 10  # 秒

# 可重试的 HTTP 状态码
RETRYABLE_STATUS_CODES = {502, 503, 504}

# 可重试的异常类型
RETRYABLE_EXCEPTIONS = (
    urllib.error.URLError,
    ConnectionError,
    TimeoutError,
    OSError,
)


def _is_retryable_error(error: Exception) -> bool:
    """判断错误是否可重试"""
    # 使用项目特定的可重试错误判断
    return is_retryable_error(error)


def _calculate_retry_delay(attempt: int) -> float:
    """计算重试延迟（指数退避）"""
    delay = INITIAL_RETRY_DELAY * (2 ** attempt)
    return min(delay, MAX_RETRY_DELAY)


def retry_on_failure(max_retries: int = MAX_RETRIES):
    """重试装饰器：自动重试可恢复的错误

    Args:
        max_retries: 最大重试次数（默认 3）
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_error = e
                    if attempt == max_retries:
                        # 最后一次尝试失败，抛出异常
                        raise
                    if not _is_retryable_error(e):
                        # 不可重试的错误，立即抛出
                        raise
                    # 计算重试延迟
                    delay = _calculate_retry_delay(attempt)
                    _LOG.warning(
                        "API 调用失败，%d 秒后重试 (尝试 %d/%d): %s",
                        delay, attempt + 1, max_retries, e
                    )
                    time.sleep(delay)
            # 不应该到达这里，但作为安全措施
            raise last_error
        return wrapper
    return decorator


def resolve_token(cfg: dict) -> str:
    """按优先级解析 Eagle API Token：config → keychain → env"""
    token = cfg.get("eagle", {}).get("token", "")
    if token:
        return token
    try:
        from eagle_watcher.keychain import get_token
        token = get_token()
        if token:
            return token
    except (ImportError, OSError) as e:
        _LOG.debug("Keychain 访问失败，跳过: %s", e)
    token = os.environ.get("EAGLE_TOKEN", "")
    return token


# httpx 0.28+ 与 Eagle 的 HTTP 服务器不兼容（始终返回 502），
# 故使用标准库 urllib 替代


class EagleAPI:

    def __init__(self, base_url: str = "http://localhost:41595", token: str = ""):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._opener = urllib.request.build_opener()

    def _url(self, path: str) -> str:
        return f"{self.base_url}/api/{path.lstrip('/')}"

    @retry_on_failure()
    def _get(self, path: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
        url = self._url(path)
        if self.token:
            sep = "&" if "?" in path else "?"
            url += f"{sep}token={self.token}"
        with self._opener.open(urllib.request.Request(url), timeout=timeout) as resp:
            return json.loads(resp.read().decode())

    @retry_on_failure()
    def _post(self, path: str, body: Optional[dict] = None, timeout: int = DEFAULT_TIMEOUT) -> dict:
        url = self._url(path)
        if self.token:
            sep = "&" if "?" in path else "?"
            url += f"{sep}token={self.token}"
        data = json.dumps(body or {}).encode()
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self._opener.open(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())

    # ────────────── 素材操作 ──────────────

    def add_from_path(
        self,
        file_path: str,
        *,
        name: Optional[str] = None,
        tags: Optional[list[str]] = None,
        folder_id: Optional[str] = None,
        annotation: Optional[str] = None,
    ) -> dict:
        body: dict = {"path": file_path}
        if name is not None:
            body["name"] = name
        if tags:
            body["tags"] = tags
        if folder_id:
            body["folderId"] = folder_id
        if annotation:
            body["annotation"] = annotation
        return self._post("item/addFromPath", body, timeout=FILE_TIMEOUT)

    def add_from_url(
        self,
        url: str,
        *,
        name: Optional[str] = None,
        tags: Optional[list[str]] = None,
        folder_id: Optional[str] = None,
    ) -> dict:
        body: dict = {"url": url}
        if name is not None:
            body["name"] = name
        if tags:
            body["tags"] = tags
        if folder_id:
            body["folderId"] = folder_id
        return self._post("item/addFromURL", body, timeout=URL_TIMEOUT)

    def update_item(self, item_id: str, *, tags: Optional[list[str]] = None,
                    annotation: Optional[str] = None,
                    star: Optional[int] = None) -> dict:
        body: dict = {"id": item_id}
        if tags is not None:
            body["tags"] = tags
        if annotation is not None:
            body["annotation"] = annotation
        if star is not None:
            body["star"] = star
        return self._post("item/update", body)

    # ────────────── 素材查询 ──────────────

    def list_items(self, folders: Optional[str] = None,
                   tags: Optional[str] = None,
                   order_by: Optional[str] = None,
                   limit: Optional[int] = None,
                   offset: Optional[int] = None) -> list[dict]:
        params = {}
        if folders:
            params["folders"] = folders
        if tags:
            params["tags"] = tags
        if order_by:
            params["orderBy"] = order_by
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        path = "item/list"
        if params:
            path += "?" + urllib.parse.urlencode(params)
        data = self._get(path)
        return data.get("data", [])

    def get_item(self, item_id: str) -> Optional[dict[str, Any]]:
        """按 ID 查询单个素材，返回素材字典或 None。"""
        path = f"item/list?id={item_id}"
        data = self._get(path)
        items = data.get("data", [])
        return items[0] if items else None

    def get_item_file_path(self, item_id: str, name: str, ext: str) -> Optional[str]:
        """获取素材的本地文件绝对路径。

        通过 thumbnail 端点推导文件目录，再拼接原文件名。
        """
        try:
            data = self._get(f"item/thumbnail?id={item_id}")
            thumb_path = data.get("data", "")
            if not thumb_path:
                _LOG.debug("get_item_file_path: thumbnail 返回空 data, item_id=%s", item_id)
                return None
            # thumbnail 路径形如 .../ITEM_ID.info/NAME_thumbnail.png
            # 原文件在同一目录：NAME.EXT
            from urllib.parse import unquote
            thumb_path = unquote(thumb_path)
            parent = os.path.dirname(thumb_path)
            file_path = os.path.join(parent, f"{name}.{ext}")
            if not os.path.isfile(file_path):
                _LOG.debug("get_item_file_path: 文件不存在 %s (thumbnail=%s)", file_path, thumb_path)
                return None
            return file_path
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            _LOG.warning("get_item_file_path 网络/IO 异常 item_id=%s: %s", item_id, e)
            return None
        except (KeyError, TypeError, ValueError) as e:
            _LOG.debug("get_item_file_path 数据解析异常 item_id=%s: %s", item_id, e)
            return None
        except EagleAPIError as e:
            _LOG.warning("get_item_file_path Eagle API 异常 item_id=%s: %s", item_id, e)
            return None

    # ────────────── 文件夹操作 ──────────────

    def list_folders(self) -> list[dict]:
        data = self._get("folder/list")
        return data.get("data", [])

    def create_folder(self, folder_name: str) -> dict:
        return self._post("folder/create", {"folderName": folder_name})

    def delete_folder(self, folder_id: str) -> bool:
        try:
            result = self._post("folder/delete", {"folderId": folder_id})
            return result.get("status") == "success"
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            _LOG.warning("删除 Eagle 文件夹失败 %s: %s", folder_id, e)
            return False
        except EagleAPIError as e:
            _LOG.warning("删除 Eagle 文件夹失败 %s: %s", folder_id, e)
            return False

    def get_or_create_folder(self, folder_name: str) -> Optional[str]:
        folders = self.list_folders()
        for f in folders:
            if f.get("name") == folder_name:
                return f.get("id")
        result = self.create_folder(folder_name)
        return result.get("data", {}).get("id")

    # ────────────── 标签操作 ──────────────

    def list_tags(self) -> list[str]:
        data = self._get("tag/list")
        return [t.get("name", "") for t in data.get("data", [])]

    def add_tags_to_item(self, item_id: str, tags: list[str]) -> dict:
        return self._post("item/addTag", {"itemId": item_id, "tags": tags})

    # ────────────── 工具 ──────────────

    def ping(self) -> bool:
        try:
            # 使用 folder/list 端点测试连接，_get 自动追加 token
            data = self._get("folder/list", timeout=PING_TIMEOUT)
            return data.get("status") == "success"
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            _LOG.debug("Eagle ping 失败: %s", e)
            return False
        except EagleConnectionError as e:
            _LOG.debug("Eagle 连接失败: %s", e)
            return False
        except EagleTimeoutError as e:
            _LOG.debug("Eagle 连接超时: %s", e)
            return False


def create_eagle_api(cfg: Optional[dict] = None) -> EagleAPI:
    """工厂函数：从配置创建 EagleAPI 实例

    Args:
        cfg: 配置字典（需包含 eagle.host）。
            为 None 时自动从 config.load_config() 加载。
    """
    if cfg is None:
        from eagle_watcher.config import load_config
        cfg = load_config()
    token = resolve_token(cfg)
    return EagleAPI(
        base_url=cfg["eagle"]["host"],
        token=token,
    )
