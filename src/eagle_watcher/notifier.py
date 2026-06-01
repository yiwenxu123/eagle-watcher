import subprocess


def notify(title: str, message: str):
    safe_title = title.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    safe_msg = message.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    subprocess.run(
        [
            "osascript",
            "-e",
            f'display notification "{safe_msg}" with title "{safe_title}"',
        ],
        capture_output=True,
    )
