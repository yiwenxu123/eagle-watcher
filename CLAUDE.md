# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

Eagle素材管家（eagle-watcher）是一个Mac本地常驻的Python应用，用于自动监控Downloads文件夹并将设计素材智能分类入库到Eagle。

## 核心架构

### 入口与运行模式
- **main.py**：主入口，启动watcher后台线程 + 菜单栏主线程
- **watcher.py**：后台监控Downloads文件夹，轮询检测新文件
- **menu_app.py**：macOS菜单栏应用（rumps），提供主题切换、状态查看、通用箱整理
- **server.py**：HTTP服务器（端口9800），供远程Agent调用
- **cli.py**：CLI命令，本地Agent调用入口

### 核心决策流程
```
新文件 → analyzer.decide()
  ├─ 用户设了当前主题？ → 强制归入
  ├─ 来源URL匹配？ → 归入对应主题
  ├─ 知识库关键词匹配？ → 归入对应主题
  ├─ 文件名模糊？ → AI视觉分析（Qwen-VL）
  └─ 都不匹配 → 进通用箱
```

### 关键模块
- **analyzer.py**：文件名解析 + 主题匹配决策引擎
- **ai_tagger.py**：AI视觉分析（DashScope Qwen-VL），仅在文件名模糊时调用
- **knowledge.py**：知识库管理，自动学习关键词-主题映射
- **eagle_api.py**：Eagle HTTP API封装（使用urllib，非httpx）
- **config.py**：配置管理（config.yaml + state.json + themes.yaml）

## 数据目录

所有数据存储在 `~/.eagle-watcher/`：
- `config.yaml`：设备配置（Eagle连接、监控路径）
- `themes.yaml`：主题列表（用户创建）
- `knowledge.yaml`：知识库（AI自动积累）
- `state.json`：运行时状态（当前主题）
- `log/`：日志目录

## 常用命令

### 启动应用
```bash
python main.py
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
pip install rumps pyyaml dashscope
```

## 技术约束

1. **Eagle API兼容性**：使用urllib而非httpx（httpx 0.28+与Eagle HTTP服务器不兼容）
2. **主题管理**：AI只能匹配已有主题，不能自动创建新主题
3. **AI调用时机**：仅在文件名模糊（纯数字/乱码）时调用Qwen-VL
4. **macOS原生**：使用rumps创建菜单栏应用，AppleScript显示对话框

## 代码规范

- Python 3.10+
- 使用type hints
- 日志使用logging模块
- 配置使用YAML格式
- 状态使用JSON格式
