"""
eagle-import CLI 命令

本地 Agent 调用方式：
  python cli.py --file "白起.jpg" --project "武安侯" --tags "白起,战国"
  python cli.py --url "https://example.com/img.jpg" --project "秦始皇"

远程 Agent（Hermes/OpenClaw）也通过此命令（SSH 直连或 HTTP 桥接）。
"""

import argparse
import sys
from pathlib import Path

from eagle_watcher.config import load_config, ensure_data_dir
from eagle_watcher.eagle_api import EagleAPI, create_eagle_api
from eagle_watcher.analyzer import decide


def main():
    parser = argparse.ArgumentParser(description="导入素材到 Eagle")
    parser.add_argument("--file", help="本地文件路径")
    parser.add_argument("--url", help="远程文件 URL")
    parser.add_argument("--project", help="主题名", default=None)
    parser.add_argument("--tags", help="标签，逗号分隔", default="")
    parser.add_argument("--folder", help="Eagle 文件夹名", default=None)
    args = parser.parse_args()

    if not args.file and not args.url:
        print("❌ 请提供 --file 或 --url")
        sys.exit(1)

    ensure_data_dir()
    cfg = load_config()

    eagle = create_eagle_api(cfg)

    if not eagle.ping():
        print("❌ Eagle 未运行，请先打开 Eagle")
        sys.exit(1)

    tags = [t.strip() for t in args.tags.split(",") if t.strip()]

    if not args.project and args.file:
        decision = decide(Path(args.file).name)
        project = decision["theme"]
        tags = decision["tags"] + tags
        folder = decision["folder"]
        if decision["action"] == "inbox":
            folder = "_通用箱"
    else:
        project = args.project
        folder = args.folder or project

    folder_id = None
    if folder:
        folder_id = eagle.get_or_create_folder(folder)

    if args.file:
        result = eagle.add_from_path(
            args.file,
            name=Path(args.file).stem,
            tags=tags,
            folder_id=folder_id,
        )
    else:
        result = eagle.add_from_url(
            args.url,
            tags=tags,
            folder_id=folder_id,
        )

    if result.get("status") == "success":
        print(f"✅ 已入库：{folder or '未分类'} ｜ 标签：{', '.join(tags)}")
    else:
        print(f"❌ 入库失败：{result}")


if __name__ == "__main__":
    main()
