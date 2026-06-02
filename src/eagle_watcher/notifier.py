import subprocess


def notify(title: str, message: str):
    # 转义 AppleScript 特殊字符：\ → \\, " → \", ' → "'", \n → 空格
    safe_title = title.replace("\\", "\\\\").replace('"', '\\"').replace("'", "'\\''").replace("\n", " ")
    safe_msg = message.replace("\\", "\\\\").replace('"', '\\"').replace("'", "'\\''").replace("\n", " ")
    subprocess.run(
        [
            "osascript",
            "-e",
            f'display notification "{safe_msg}" with title "{safe_title}"',
        ],
        capture_output=True,
    )
