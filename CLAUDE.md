# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

Eagle素材管家（eagle-watcher）是一个Mac本地常驻的Python应用，用于自动监控Downloads文件夹并将设计素材智能分类入库到Eagle。

## 核心架构

### 入口与运行模式
- **main.py**：主入口，启动watcher后台线程 + 菜单栏主线程 + 每日通知重置
- **watcher.py**：后台监控Downloads文件夹（分层可回退：FSEvents → inode轮询）
- **menu_app.py**：macOS菜单栏应用（rumps），提供主题切换、状态查看、通用箱整理
- **server/**：HTTP服务器包（端口9800远程API + 端口9801 HUD面板），供远程Agent调用
- **cli.py**：CLI命令，本地Agent调用入口

### 模块结构
```
src/eagle_watcher/    ← 主要源代码
  main.py             ← 主入口，启动watcher后台线程 + 菜单栏主线程
  watcher.py          ← 后台监控Downloads文件夹（分层可回退：FSEvents → inode轮询）
  menu_app.py         ← macOS菜单栏应用（rumps），提供主题切换、状态查看
  cli.py              ← CLI命令，本地Agent调用入口

  server/             ← HTTP服务器包（端口9800远程API + 端口9801 HUD面板）
    __init__.py       ← 向后兼容重导出 + start_server 启动函数
    _common.py        ← 常量、缓存状态、工具函数
    base.py           ← BaseHandler 基类（JSON/HTML 响应、CORS）
    remote.py         ← RemoteHandler（端口9800，API Key 认证）
    panel.py          ← PanelHandler（端口9801，HUD 面板 API，38个方法）

  services/           ← 编排层、状态管理
    state_manager.py  ← 线程安全状态单例（延迟批量写入，2秒窗口合并）
    file_watcher.py   ← 分层文件监控（FSEvents + inode轮询）
    sort_service.py   ← 通用箱整理（标签方案，非伪操作）
    history.py        ← 操作历史日志（JSONL格式，支持高效倒序读取）

  domain/             ← 纯逻辑、数据层
    analyzer.py       ← 文件名解析 + 主题匹配决策引擎
    knowledge.py      ← 知识库管理（并发安全，使用文件锁）
    eagle_api.py      ← Eagle HTTP API封装（带重试机制）
    ai_tagger.py      ← AI视觉分析（DashScope Qwen-VL，带缓存并发控制）
    config.py         ← 配置管理（config.yaml + themes.yaml）
    exporter.py       ← 导出工作区（带大小限制和LRU清理）

  pyui/               ← UI组件
    panel.py          ← HUD面板组件
    server.py         ← 面板服务器（thin wrapper，重导出 server 包）

tests/                ← 测试
  conftest.py         ← 测试配置（mock_data_dir、mock_eagle_api 等）
  test_analyzer.py    ← 决策引擎测试
  test_eagle_api.py   ← Eagle API测试
  test_state_manager.py ← 状态管理测试（含延迟写入基准）
  test_watcher.py     ← 文件监控测试
  test_ai_tagger.py   ← AI分析测试
  test_exporter.py    ← 导出功能测试
  test_pyui_server.py ← 面板服务器测试（PanelHandler + RemoteHandler）
  test_knowledge.py   ← 知识库测试
  test_config.py      ← 配置管理测试
  test_modules.py     ← 模块导入和常量验证
```

### 核心决策流程
```
新文件 → analyzer.decide()
  ├─ 用户设了当前主题？ → 强制归入
  ├─ 知识库关键词匹配？ → 归入对应主题
  ├─ 文件名模糊？ → AI视觉分析（Qwen-VL）
  └─ 都不匹配 → 进通用箱
```

### 关键模块
- **analyzer.py**：文件名解析 + 主题匹配决策引擎（支持多层匹配：当前主题→知识库→AI分析→通用箱）
- **ai_tagger.py**：AI视觉分析（DashScope Qwen-VL），仅在文件名模糊时调用，支持重试机制和缓存并发控制
- **knowledge.py**：知识库管理，自动学习关键词-主题映射，使用文件锁保证并发安全
- **eagle_api.py**：Eagle HTTP API封装（使用urllib，非httpx），带重试装饰器（指数退避）
- **config.py**：配置管理（config.yaml + themes.yaml），支持配置验证和迁移
- **exporter.py**：导出工作区，支持大小限制（默认10GB）和LRU清理策略
- **watcher.py**：文件监控 + 去重逻辑，Eagle去重使用带30秒TTL的本地缓存（`_get_recent_items`）
- **services/state_manager.py**：线程安全状态管理，延迟批量写入（2秒窗口合并，`atexit` 保证关闭时 flush）
- **services/file_watcher.py**：分层文件监控（PyObjC FSEvents → inode轮询）
- **services/sort_service.py**：通用箱整理（调用item/update添加标签）
- **services/history.py**：操作历史日志（JSONL格式，支持高效倒序读取）
- **server/**：HTTP服务器包，`_common.py` 共享缓存状态，`base.py` 基类，`remote.py` 远程API，`panel.py` HUD面板

## 数据目录

所有数据存储在 `~/.eagle-watcher/`：
- `config.yaml`：设备配置（Eagle连接、监控路径、导出设置等）
- `themes.yaml`：主题列表（用户创建，支持分类和项目）
- `knowledge.yaml`：知识库（AI自动积累，使用文件锁保证并发安全）
- `state.json`：运行时状态（由 StateManager 线程安全管理）
- `history.jsonl`：操作历史日志（JSONL格式，支持高效倒序读取）
- `cache/`：AI分析缓存目录（带并发控制和过期机制）
- `log/`：日志目录

## 常用命令

### 启动应用
```bash
python main.py
```

### 运行测试
```bash
# 快速测试（推荐，排除 flaky 的 test_watcher.py）
python -m pytest tests/ -q --tb=short --ignore=tests/test_watcher.py

# 完整测试（test_watcher.py 有已知的 mock 问题，可能 hang）
python -m pytest tests/ -v

# 带覆盖率
python -m pytest tests/ --cov=src/eagle_watcher --cov-report=term-missing --ignore=tests/test_watcher.py
```

### CLI导入素材
```bash
python cli.py --file "白起.jpg" --project "武安侯" --tags "白起,战国"
python cli.py --url "https://example.com/img.jpg" --project "秦始皇"
```

### 启动HTTP服务器
```bash
# 远程 Agent API（端口 9800）
python -m eagle_watcher.server
# 或
eagle-server

# 面板 API（端口 9801）
python -c "from eagle_watcher.server import start_panel_server; start_panel_server()"
```

### 依赖安装
```bash
pip install rumps pyyaml dashscope pyobjc-framework-FSEvents pytest pytest-cov
```

## 技术约束

1. **Eagle API兼容性**：使用urllib而非httpx（httpx 0.28+与Eagle HTTP服务器不兼容），带重试机制（指数退避）
2. **主题管理**：AI只能匹配已有主题，不能自动创建新主题
3. **AI调用时机**：仅在文件名模糊（纯数字/乱码）时调用Qwen-VL，支持缓存并发控制
4. **macOS原生**：使用rumps创建菜单栏应用，优化更新策略（30秒间隔+哈希检测变化）
5. **无item/move端点**：Eagle API不支持移动素材到不同文件夹，通用箱整理基于标签
6. **并发安全**：知识库和状态管理使用文件锁保证并发安全，避免死锁设计
7. **资源限制**：导出工作区支持大小限制（默认10GB）和LRU清理策略
8. **安全机制**：面板服务器使用Session Token（24小时过期），支持自动刷新

## 代码规范

- Python 3.9+
- 使用type hints
- 日志使用logging模块（统一使用%s格式化，性能更好）
- 配置使用YAML格式
- 状态使用JSON格式（通过StateManager延迟批量写入）
- 并发安全：使用文件锁（fcntl.flock）保护共享资源
- 错误处理：统一异常层次（`exceptions.py`），区分可重试错误和不可重试错误
- 测试覆盖：核心功能测试覆盖率 ≥ 80%
- 测试 monkeypatch：server 包拆分后需使用子模块路径（如 `eagle_watcher.server.base.create_eagle_api`）

## 异常层次

所有自定义异常继承自 `EagleWatcherError`（`exceptions.py`），包含错误码和详情字典：
- `ConfigError` → 配置相关
- `EagleError` → Eagle API 相关
- `AIError` → AI 分析相关（`AIKeyError`, `AIModelError`, `AICacheError`）
- `FileWatcherError` → 文件监控相关
- `ExportError` → 导出相关
- `KnowledgeError` → 知识库相关

使用 `wrap_exception(e, TargetError)` 进行异常链式转换。