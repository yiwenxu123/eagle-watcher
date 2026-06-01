# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## 项目概述

Eagle素材管家（eagle-watcher）是一个Mac本地常驻的Python应用，用于自动监控Downloads文件夹并将设计素材智能分类入库到Eagle。

## 核心架构

### 入口与运行模式
- **main.py**：主入口，启动watcher后台线程 + 菜单栏主线程 + 每日通知重置
- **watcher.py**：后台监控Downloads文件夹（分层可回退：FSEvents → inode轮询）
- **menu_app.py**：macOS菜单栏应用（rumps），提供主题切换、状态查看、通用箱整理
- **server.py**：HTTP服务器（端口9800），供远程Agent调用（无全局副作用）
- **cli.py**：CLI命令，本地Agent调用入口

### 模块结构
```
entrypoints/          ← IO、线程
  main.py, watcher.py, menu_app.py, server.py, cli.py

services/             ← 编排层、状态管理
  state_manager.py    ← 线程安全状态单例（RLock + 写穿透）
  file_watcher.py     ← 分层文件监控（FSEvents + inode轮询）
  sort_service.py     ← 通用箱整理（标签方案，非伪操作）

domain/               ← 纯逻辑、数据层
  analyzer.py, knowledge.py, eagle_api.py, ai_tagger.py

tests/                ← 测试
  conftest.py, test_analyzer.py, test_eagle_api.py, test_state_manager.py
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
- **analyzer.py**：文件名解析 + 主题匹配决策引擎
- **ai_tagger.py**：AI视觉分析（DashScope Qwen-VL），仅在文件名模糊时调用
- **knowledge.py**：知识库管理，自动学习关键词-主题映射
- **eagle_api.py**：Eagle HTTP API封装（使用urllib，非httpx）
- **config.py**：配置管理（config.yaml + themes.yaml）
- **services/state_manager.py**：线程安全状态管理（替代裸state.json读写）
- **services/file_watcher.py**：分层文件监控（PyObjC FSEvents → inode轮询）
- **services/sort_service.py**：通用箱整理（调用item/update添加标签）

## 数据目录

所有数据存储在 `~/.eagle-watcher/`：
- `config.yaml`：设备配置（Eagle连接、监控路径）
- `themes.yaml`：主题列表（用户创建）
- `knowledge.yaml`：知识库（AI自动积累）
- `state.json`：运行时状态（由 StateManager 线程安全管理）
- `log/`：日志目录

## 常用命令

### 启动应用
```bash
python main.py
```

### 运行测试
```bash
python -m pytest tests/ -v
python -m pytest tests/ --cov=services --cov=analyzer --cov=eagle_api
```

### CLI导入素材
```bash
python cli.py --file "白起.jpg" --project "武安侯" --tags "白起,战国"
python cli.py --url "https://example.com/img.jpg" --project "秦始皇"
```

### 启动HTTP服务器
```bash
python server.py
```

### 依赖安装
```bash
pip install rumps pyyaml dashscope pyobjc-framework-FSEvents pytest pytest-cov
```

## 技术约束

1. **Eagle API兼容性**：使用urllib而非httpx（httpx 0.28+与Eagle HTTP服务器不兼容）
2. **主题管理**：AI只能匹配已有主题，不能自动创建新主题
3. **AI调用时机**：仅在文件名模糊（纯数字/乱码）时调用Qwen-VL
4. **macOS原生**：使用rumps创建菜单栏应用
5. **无item/move端点**：Eagle API不支持移动素材到不同文件夹，通用箱整理基于标签

## 代码规范

- Python 3.9+
- 使用type hints
- 日志使用logging模块
- 配置使用YAML格式
- 状态使用JSON格式（通过StateManager线程安全读写）