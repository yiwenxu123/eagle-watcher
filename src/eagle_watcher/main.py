"""素材管家入口：watcher 后台线程 + 菜单栏主线程"""

import warnings
warnings.filterwarnings("ignore", message=".*NotOpenSSLWarning.*")

import logging
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from eagle_watcher.config import load_config, ensure_data_dir, validate_config, DATA_DIR, CONFIG_PATH, save_config, _default_config
from eagle_watcher.services.state_manager import get_state_manager
from eagle_watcher.eagle_api import EagleAPI, create_eagle_api


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


def first_run_check() -> bool:
    """首次运行检查，返回是否需要退出"""
    if not CONFIG_PATH.exists():
        print("\n" + "=" * 55)
        print("  🖼  欢迎使用 Eagle 素材管家")
        print("=" * 55)
        print(f"\n配置文件不存在，已自动创建：")
        print(f"  {CONFIG_PATH}")
        print(f"\n使用前请完成以下步骤：")
        print(f"  1. 打开 Eagle")
        print(f"  2. 进入 Eagle → 偏好设置 → 插件")
        print(f"  3. 勾选「允许其他应用连接」")
        print(f"  4. 复制 API Token 到配置文件中的 eagle.token")
        print(f"  5. 保存后重新运行：python main.py\n")
        save_config(_default_config())
        return True
    return False


def main():
    setup_logging()
    _LOG = logging.getLogger("main")
    ensure_data_dir()

    if first_run_check():
        return

    start_daily_reset()
    cfg = load_config()

    # 验证配置
    errors = validate_config(cfg)
    if errors:
        for error in errors:
            _LOG.error(f"配置错误：{error}")
        _LOG.error("请检查 ~/.eagle-watcher/config.yaml 配置文件")
        return

    # 检查 AI API Key
    if not os.environ.get("DASHSCOPE_API_KEY"):
        _LOG.warning("DASHSCOPE_API_KEY 未设置，AI 视觉分析不可用")
        print("  ⚠️  环境变量 DASHSCOPE_API_KEY 未设置，AI 视觉分析功能不可用")
        print("     如需使用 AI 自动分类，请先设置：export DASHSCOPE_API_KEY=你的Key\n")

    eagle = create_eagle_api(cfg)

    if not eagle.ping():
        _LOG.warning("Eagle 未运行，菜单栏仍可启动但 watcher 暂停")
        get_state_manager().set_eagle_online(False)
    else:
        _LOG.info("Eagle 连接正常，启动 watcher")
        get_state_manager().set_eagle_online(True)

    from eagle_watcher.watcher import run_watcher

    watcher_thread = threading.Thread(target=run_watcher, daemon=True, args=(eagle,))
    watcher_thread.start()
    _LOG.info("watcher 线程已启动")

    try:
        from eagle_watcher.menu_app import EagleWatcherMenu
    except ImportError:
        print("\n  ❌ 缺少 UI 依赖。请执行：pip install 'eagle-watcher[ui]'")
        print("     或者运行 eagle-server 启动 HTTP 服务模式\n")
        return
    _LOG.info("启动菜单栏...")
    EagleWatcherMenu().run()


if __name__ == "__main__":
    main()
