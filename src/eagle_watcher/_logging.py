import logging
import sys
from datetime import datetime
from pathlib import Path

DATA_DIR = Path.home() / ".eagle-watcher"


def setup_logging(name: str = "eagle-watcher"):
    """配置日志：文件 + 控制台（幂等，不会重复添加 handler）"""
    root = logging.getLogger()
    if root.handlers:
        return

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log_file = DATA_DIR / "log" / f"{name}_{datetime.now():%Y%m%d}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")

    root.setLevel(logging.INFO)

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)

    ch = logging.StreamHandler(sys.stderr)
    ch.setFormatter(fmt)
    root.addHandler(ch)
