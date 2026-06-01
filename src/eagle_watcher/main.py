"""素材管家入口：watcher 后台线程 + 菜单栏主线程"""

import warnings
warnings.filterwarnings("ignore", message=".*NotOpenSSLWarning.*")

import logging
import sys
if sys.platform != "darwin":
    print("错误：Eagle Watcher 仅支持 macOS")
    sys.exit(1)
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from eagle_watcher.config import load_config, ensure_data_dir, validate_config, DATA_DIR, CONFIG_PATH, save_config, _default_config
from eagle_watcher.services.state_manager import get_state_manager
from eagle_watcher.eagle_api import EagleAPI, create_eagle_api


def start_daily_reset():
    def _loop():
        while True:
            now = datetime.now()
            tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0)
            sleep_sec = (tomorrow - now).total_seconds()
            time.sleep(sleep_sec)
            get_state_manager().reset_daily_flags()
            # 每日清理过期知识库条目
            try:
                from eagle_watcher.knowledge import cleanup_stale_entries
                cleanup_stale_entries()
            except Exception as e:
                logging.getLogger("main").warning("知识库清理失败: %s", e)
    threading.Thread(target=_loop, daemon=True, name="daily-reset").start()


def _wait_for_eagle(eagle: EagleAPI):
    """Eagle 离线时等待重连，恢复后自动启动 watcher"""
    _LOG = logging.getLogger("main")
    _LOG.info("等待 Eagle 上线，每 30 秒重试...")
    while True:
        time.sleep(30)
        if eagle.ping():
            _LOG.info("Eagle 已上线，启动 watcher")
            get_state_manager().set_eagle_online(True)
            from eagle_watcher.watcher import run_watcher
            watcher_thread = threading.Thread(target=run_watcher, daemon=True, args=(eagle,))
            watcher_thread.start()
            return


def first_run_check() -> bool:
    """首次运行检查，返回是否需要阻止 watcher 启动"""
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
        print(f"  5. 保存后重新运行配置引导：python main.py\n")
        save_config(_default_config())
        # 发送 macOS 通知
        try:
            from eagle_watcher.notifier import notify
            notify("素材管家", "欢迎使用！请先配置 Eagle API Token：\n打开 Eagle → 偏好设置 → 插件 → 复制 Token")
        except Exception:
            pass
        # 不再退出，继续启动 GUI
        return False
    return False


def main():
    from eagle_watcher._logging import setup_logging
    setup_logging()
    _LOG = logging.getLogger("main")
    ensure_data_dir()

    if first_run_check():
        return

    start_daily_reset()
    cfg = load_config()

    # 验证配置
    errors, warnings = validate_config(cfg)
    if warnings:
        for w in warnings:
            _LOG.warning(f"配置警告: {w}")
    if errors:
        for e in errors:
            _LOG.error(f"配置错误: {e}")
        # DON'T return here - continue to start GUI

    # 检查 AI API Key（config.yaml 优先，env 回退）
    ai_key = cfg.get("ai", {}).get("api_key") or os.environ.get("DASHSCOPE_API_KEY")
    if not ai_key:
        _LOG.warning("AI API Key 未配置（config.yaml 或 DASHSCOPE_API_KEY），AI 视觉分析不可用")
        print("  ⚠️  AI API Key 未配置，AI 视觉分析功能不可用")
        print("     请设置 ~/.eagle-watcher/config.yaml 中 ai.api_key 或环境变量 DASHSCOPE_API_KEY\n")

    eagle = create_eagle_api(cfg)

    if not eagle.ping():
        _LOG.warning("Eagle 未运行，等待重连...")
        get_state_manager().set_eagle_online(False)
        # 启动重连线程，Eagle 上线后自动启动 watcher
        threading.Thread(target=_wait_for_eagle, daemon=True, args=(eagle,)).start()
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
