"""素材管家入口：watcher 后台线程 + 菜单栏主线程"""

import warnings
warnings.filterwarnings("ignore", message=".*NotOpenSSLWarning.*")

import logging
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from config import load_config, ensure_data_dir, validate_config, DATA_DIR
from services.state_manager import get_state_manager
from eagle_api import EagleAPI


def setup_logging():
    log_dir = Path(DATA_DIR) / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"main_{datetime.now().strftime('%Y%m%d')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(str(log_file), encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def start_daily_reset():
    def _loop():
        while True:
            now = datetime.now()
            tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0)
            sleep_sec = (tomorrow - now).total_seconds()
            time.sleep(sleep_sec)
            get_state_manager().reset_daily_flags()
    threading.Thread(target=_loop, daemon=True, name="daily-reset").start()


def main():
    setup_logging()
    _LOG = logging.getLogger("main")
    ensure_data_dir()

    start_daily_reset()
    cfg = load_config()

    # 验证配置
    errors = validate_config(cfg)
    if errors:
        for error in errors:
            _LOG.error(f"配置错误：{error}")
        _LOG.error("请检查 ~/.eagle-watcher/config.yaml 配置文件")
        return

    eagle = EagleAPI(
        base_url=cfg["eagle"]["host"],
        token=cfg["eagle"]["token"],
    )

    if not eagle.ping():
        _LOG.warning("Eagle 未运行，菜单栏仍可启动但 watcher 暂停")
    else:
        _LOG.info("Eagle 连接正常，启动 watcher")

    from watcher import run_watcher

    watcher_thread = threading.Thread(target=run_watcher, daemon=True, args=(eagle,))
    watcher_thread.start()
    _LOG.info("watcher 线程已启动")

    from menu_app import EagleWatcherMenu
    _LOG.info("启动菜单栏...")
    EagleWatcherMenu().run()


if __name__ == "__main__":
    main()
