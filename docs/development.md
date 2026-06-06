# Eagle Watcher 开发指南

## 环境搭建

### 前置条件

- macOS（仅支持 macOS，依赖 PyObjC FSEvents）
- Python 3.9+
- Eagle 应用已安装并运行

### 安装

```bash
# 克隆仓库
git clone <repo-url> eagle-watcher
cd eagle-watcher

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install -e ".[dev,ui]"

# 或手动安装
pip install rumps pyyaml dashscope pyobjc-framework-FSEvents pytest pytest-cov
```

### 配置

首次运行会自动创建 `~/.eagle-watcher/config.yaml`：

```yaml
eagle:
  host: http://localhost:41595
  token: your-eagle-api-token

paths:
  downloads: ~/Downloads

ai:
  api_key: your-dashscope-api-key  # 可选，环境变量 DASHSCOPE_API_KEY 也行
```

## 项目结构

```
src/eagle_watcher/    ← 源代码
  main.py             ← 入口
  watcher.py          ← 文件监控 + 处理
  menu_app.py         ← 菜单栏 GUI
  cli.py              ← CLI
  server/             ← HTTP 服务器包
  services/           ← 服务层
  domain/             ← 领域层（实际在 src 根目录）
  pyui/               ← UI 组件

tests/                ← 测试
docs/                 ← 文档
pyproject.toml        ← 项目配置
```

## 开发工作流

### 运行应用

```bash
# 主应用（菜单栏 + watcher）
python -m eagle_watcher.main

# HTTP 服务器
eagle-server

# CLI
python -m eagle_watcher.cli --file "test.jpg" --project "测试"
```

### 运行测试

```bash
# 推荐：排除 flaky 的 watcher 测试
python -m pytest tests/ -q --tb=short --ignore=tests/test_watcher.py

# 完整测试（test_watcher.py 可能 hang）
python -m pytest tests/ -v

# 单个模块
python -m pytest tests/test_state_manager.py -v

# 带覆盖率
python -m pytest tests/ --cov=src/eagle_watcher --cov-report=term-missing --ignore=tests/test_watcher.py
```

### 测试注意事项

- `test_watcher.py` 有已知的 mock 问题，可能导致 pytest hang。日常开发用 `--ignore=tests/test_watcher.py`
- `test_pyui_server.py` 中的 monkeypatch 使用子模块路径（如 `eagle_watcher.server.base.create_eagle_api`）
- `StateManager` 使用延迟写入，持久化测试需要先调用 `sm.flush()`

## 代码规范

### Python 风格

- Python 3.9+，使用 type hints
- 日志使用 `logging` 模块，统一 `%s` 格式化
- 异常使用项目自定义层次（`exceptions.py`）

### 命名规范

- 模块：`snake_case`
- 类：`PascalCase`
- 函数/方法：`snake_case`
- 私有：`_前缀`
- 常量：`UPPER_SNAKE_CASE`

### 错误处理

```python
from eagle_watcher.exceptions import EagleAPIError, wrap_exception

try:
    result = eagle.some_api_call()
except Exception as e:
    raise wrap_exception(e, EagleAPIError) from e
```

### 并发安全

- 共享状态用 `threading.Lock` 保护
- 文件操作用 `fcntl.flock` 文件锁
- 避免嵌套锁（死锁风险）
- 文件 stat 操作放在锁外（`_stat_with_timeout`）

## 添加新功能

### 添加新的 API 端点

1. 在 `server/panel.py` 的 `do_GET` 或 `do_POST` 中添加路由
2. 实现处理方法（`_handle_xxx`）
3. 在 `tests/test_pyui_server.py` 中添加测试
4. 更新 `docs/api.md`

### 添加新的配置项

1. 在 `config.py` 的 `_default_config()` 中添加默认值
2. 在 `validate_config()` 中添加验证规则
3. 在 `tests/test_config.py` 中添加测试

### 添加新的异常类型

1. 在 `exceptions.py` 中定义异常类
2. 使用 `wrap_exception()` 进行异常转换
3. 在 `tests/` 中测试异常场景

## 发布流程

```bash
# 更新版本号
# pyproject.toml → version = "x.y.z"

# 运行测试
python -m pytest tests/ -q --tb=short --ignore=tests/test_watcher.py

# 构建
python -m build

# 发布
twine upload dist/*
```

## 调试技巧

### 日志

```bash
# 查看应用日志
tail -f ~/.eagle-watcher/log/eagle-watcher.log

# 设置日志级别
export EAGLE_WATCHER_LOG_LEVEL=DEBUG
```

### Eagle API 调试

```bash
# 测试 Eagle 连接
curl http://localhost:41595/api/ping

# 查看 Eagle 素材列表
curl "http://localhost:41595/api/item/list?orderBy=-btime&limit=10"
```

### 状态检查

```bash
# 查看运行时状态
cat ~/.eagle-watcher/state.json | python -m json.tool

# 查看知识库
cat ~/.eagle-watcher/knowledge.yaml
```
