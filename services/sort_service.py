import logging
from pathlib import Path
from typing import Optional

from knowledge import match_by_filename, record_match
from eagle_api import EagleAPI

_LOG = logging.getLogger("sort_service")

UNSORTED_TAG = "待分类"


class SortService:

    def __init__(self, eagle: EagleAPI):
        self.eagle = eagle

    def get_inbox_items(self) -> list[dict]:
        return self.eagle.list_items(tags=UNSORTED_TAG)

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
            current_tags = item.get("tags", [])
            new_tags = [t for t in current_tags if t != UNSORTED_TAG]
            new_tags.extend(t for t in tags if t not in new_tags)
            result = self.eagle.update_item(item["id"], tags=new_tags)
            if result.get("status") == "success":
                record_match(filename, Path(filename).stem, theme, tags)
                _LOG.info("已确认: %s → %s (%s)", filename, theme, tags)
                return True
            _LOG.warning("Eagle update_item 失败: %s", result)
            return False
        except Exception as e:
            _LOG.error("确认失败: %s — %s", filename, e)
            return False