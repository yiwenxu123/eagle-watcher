"""
Eagle HTTP API 封装
参考：http://localhost:41595/ (Eagle 内建 API 文档)
"""

import json
import urllib.parse
import urllib.request
import urllib.error
from typing import Optional

# httpx 0.28+ 与 Eagle 的 HTTP 服务器不兼容（始终返回 502），
# 故使用标准库 urllib 替代


class EagleAPI:

    def __init__(self, base_url: str = "http://localhost:41595", token: str = ""):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _url(self, path: str) -> str:
        return f"{self.base_url}/api/{path.lstrip('/')}"

    def _get(self, path: str, timeout: int = 30) -> dict:
        url = self._url(path)
        if self.token:
            sep = "&" if "?" in path else "?"
            url += f"{sep}token={self.token}"
        resp = urllib.request.urlopen(url, timeout=timeout)
        return json.loads(resp.read().decode())

    def _post(self, path: str, body: Optional[dict] = None, timeout: int = 30) -> dict:
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
        resp = urllib.request.urlopen(req, timeout=timeout)
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
        return self._post("item/addFromPath", body, timeout=60)

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
        return self._post("item/addFromURL", body, timeout=120)

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
                   tags: Optional[str] = None) -> list[dict]:
        params = {}
        if folders:
            params["folders"] = folders
        if tags:
            params["tags"] = tags
        path = "item/list"
        if params:
            path += "?" + urllib.parse.urlencode(params)
        data = self._get(path)
        return data.get("data", [])

    # ────────────── 文件夹操作 ──────────────

    def list_folders(self) -> list[dict]:
        data = self._get("folder/list")
        return data.get("data", [])

    def create_folder(self, folder_name: str) -> dict:
        return self._post("folder/create", {"folderName": folder_name})

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
            # 使用 folder/list 端点测试连接
            resp = urllib.request.urlopen(self._url("folder/list"), timeout=5)
            return resp.status < 500
        except Exception:
            return False
