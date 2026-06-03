import logging
import subprocess

_LOG = logging.getLogger("notifier")


def notify(title: str, message: str):
    # 转义 AppleScript 特殊字符：\ → \\, " → \", ' → "'", \n → 空格
    safe_title = title.replace("\\", "\\\\").replace('"', '\\"').replace("'", "'\\''").replace("\n", " ")
    safe_msg = message.replace("\\", "\\\\").replace('"', '\\"').replace("'", "'\\''").replace("\n", " ")
    try:
        subprocess.run(
            [
                "osascript",
                "-e",
                f'display notification "{safe_msg}" with title "{safe_title}"',
            ],
            capture_output=True,
            timeout=5,
        )
    except FileNotFoundError:
        _LOG.debug("osascript 不可用，跳过通知")
    except subprocess.TimeoutExpired:
        _LOG.debug("通知发送超时")
    except OSError as e:
        _LOG.debug("通知发送失败：%s", e)
