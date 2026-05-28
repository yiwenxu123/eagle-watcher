import logging
from pathlib import Path
from typing import Optional

from knowledge import match_by_filename, record_match
from eagle_api import EagleAPI

_LOG = logging.getLogger("sort_service")

INBOX_NAMES = ["_通用箱", "通用箱", "_inbox", "inbox"]


class SortService:

    def __init__(self, eagle: EagleAPI):
        self.eagle = eagle

    def get_inbox_id(self) -> Optional[str]:
        folders = self.eagle.list_folders()
        for f in folders:
            if f.get("name") in INBOX_NAMES:
                return f.get("id")
        return None

    def get_inbox_items(self) -> list[dict]:
        inbox_id = self.get_inbox_id()
        if not inbox_id:
            return []
        return self.eagle.list_items(folders=inbox_id)

    def analyze(self, item: dict) -> dict:
        filename = f"{item['name']}.{item['ext']}"
        kb_match = match_by_filename(filename)
        if kb_match:
            return {
                "item": item,
                "filename": filename,
                "suggested_theme": kb_match["theme"],
                "suggested_tags": kb_match["tags"],
                "confidence": kb_match["confidence"],
            }
        return {
            "item": item,
            "filename": filename,
            "suggested_theme": "（未匹配）",
            "suggested_tags": [],
            "confidence": 0,
        }

    def confirm(self, item: dict, theme: str, tags: list[str],
                filename: str) -> bool:
        try:
            result = self.eagle.update_item(item["id"], tags=tags)
            if result.get("status") == "success":
                record_match(filename, Path(filename).stem, theme, tags)
                _LOG.info("已确认: %s → %s (%s)", filename, theme, tags)
                return True
            _LOG.warning("Eagle update_item 失败: %s", result)
            return False
        except Exception as e:
            _LOG.error("确认失败: %s — %s", filename, e)
            return False