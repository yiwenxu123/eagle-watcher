"""测试 watchdog 监控功能"""

import os
import time
from pathlib import Path

# 创建测试文件
test_dir = Path.home() / "Downloads"
test_file = test_dir / "test_watchdog.txt"

print("🧪 watchdog 测试开始...")
print(f"📁 监控目录：{test_dir}")
print()

# 确保目录存在
test_dir.mkdir(parents=True, exist_ok=True)

# 删除旧的测试文件
if test_file.exists():
    test_file.unlink()
    print("🗑  删除旧测试文件")

# 等待一下让 watchdog 初始化
print("⏳ 等待 3 秒...")
time.sleep(3)

# 创建测试文件
print("📝 创建测试文件...")
test_file.write_text("这是一个 watchdog 测试文件")
print(f"✅ 已创建：{test_file}")

# 等待 watchdog 检测
print("⏳ 等待 watchdog 检测（5秒）...")
time.sleep(5)

# 清理
if test_file.exists():
    test_file.unlink()
    print("🗑  清理测试文件")

print()
print("测试完成！请查看素材管家终端是否有检测日志。")
print("如果没有日志，可能需要重启素材管家应用。")
