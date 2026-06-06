# Eagle Watcher 架构设计

## 系统概述

Eagle Watcher 是一个 macOS 本地常驻 Python 应用，自动监控 Downloads 文件夹并将设计素材智能分类入库到 Eagle。

## 整体架构

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│  macOS GUI  │     │  HTTP Server │     │   CLI       │
│  (rumps)    │     │  (9800/9801) │     │  (cli.py)   │
└──────┬──────┘     └──────┬───────┘     └──────┬──────┘
       │                   │                     │
       └───────────────────┼─────────────────────┘
                           │
                    ┌──────▼──────┐
                    │  Watcher    │ ← 后台线程
                    │  (监控+处理) │
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
        ┌─────▼─────┐ ┌───▼───┐ ┌─────▼─────┐
        │ Analyzer   │ │ Eagle │ │ AI Tagger │
        │ (决策引擎) │ │ API   │ │ (Qwen-VL) │
        └─────┬─────┘ └───┬───┘ └───────────┘
              │            │
        ┌─────▼─────┐ ┌───▼───┐
        │ Knowledge │ │Export │
        │ (知识库)  │ │(工作区)│
        └───────────┘ └───────┘
```

## 数据流

### 文件处理流程

```
新文件下载到 Downloads
       │
       ▼
FSEvents/inode 检测到文件
       │
       ▼
_should_process_file() — 过滤临时文件、不支持的格式
       │
       ▼
StateManager.is_file_processed() — 本地 inode:size 去重
       │
       ▼
Eagle 去重（_get_recent_items 缓存，30秒TTL）
  └─ 同名+同大小 → 跳过
       │
       ▼
analyzer.decide(filename) — 决策引擎
  ├─ 当前主题已设置？ → 强制归入
  ├─ 知识库关键词匹配？ → 归入对应主题
  ├─ 文件名模糊？ → AI 视觉分析（Qwen-VL）
  └─ 都不匹配 → 通用箱
       │
       ▼
eagle.add_from_path() — 导入 Eagle
       │
       ▼
_check_result() — 通知、导出工作区、标记已处理
```

### 决策引擎（analyzer.py）

```
decide(filename)
  │
  ├─ 1. 用户设置了当前主题？ → 强制归入
  │
  ├─ 2. 知识库关键词匹配？ → match_by_filename()
  │     └─ 匹配成功 → 归入对应主题 + 标签
  │
  ├─ 3. 文件名模糊？（纯数字/乱码/无意义）
  │     └─ AI 视觉分析（Qwen-VL）→ 分析结果
  │
  └─ 4. 都不匹配 → 通用箱（tags=["待分类"]）
```

## 模块职责

### 入口层

| 模块 | 职责 | 线程模型 |
|------|------|----------|
| `main.py` | 应用入口，启动 watcher + 菜单栏 + 每日重置 | 主线程（rumps NSRunLoop） |
| `menu_app.py` | macOS 菜单栏 GUI | 主线程 |
| `cli.py` | CLI 命令行接口 | 单次执行 |
| `server/` | HTTP 服务器包 | 每请求一线程 |

### 服务层（services/）

| 模块 | 职责 | 关键设计 |
|------|------|----------|
| `state_manager.py` | 线程安全状态管理 | 延迟批量写入（2秒窗口），`atexit` flush |
| `file_watcher.py` | 分层文件监控 | FSEvents → inode 轮询回退 |
| `sort_service.py` | 通用箱整理 | 标签方案，幂等操作 |
| `history.py` | 操作历史 | JSONL 格式，高效倒序读取 |

### 领域层（domain/）

| 模块 | 职责 | 关键设计 |
|------|------|----------|
| `analyzer.py` | 文件名解析 + 决策 | 多层匹配：主题→知识库→AI→通用箱 |
| `knowledge.py` | 知识库管理 | 文件锁（fcntl.flock），并发安全 |
| `eagle_api.py` | Eagle HTTP API | urllib 封装，指数退避重试 |
| `ai_tagger.py` | AI 视觉分析 | DashScope Qwen-VL，内存+文件缓存 |
| `config.py` | 配置管理 | YAML 格式，验证+迁移 |
| `exporter.py` | 导出工作区 | 大小限制（10GB），LRU 清理 |

### 服务器层（server/）

| 模块 | 职责 |
|------|------|
| `_common.py` | 共享状态（缓存、Token、常量） |
| `base.py` | BaseHandler 基类（JSON/HTML 响应、CORS） |
| `remote.py` | RemoteHandler（端口 9800，API Key 认证） |
| `panel.py` | PanelHandler（端口 9801，Session Token 认证） |

## 并发模型

```
主线程：rumps NSRunLoop（菜单栏 GUI）
  │
  ├── daemon: watcher 线程（文件监控 + 处理）
  ├── daemon: daily-reset 线程（每日通知重置）
  ├── daemon: eagle-reconnect 线程（Eagle 离线重连）
  │
  ├── HTTP Server（端口 9800）— 每请求一线程
  └── HTTP Server（端口 9801）— 每请求一线程
```

### 线程安全机制

- **StateManager**: `threading.Lock` + 延迟批量写入
- **知识库**: `fcntl.flock` 文件锁
- **AI 缓存**: `threading.Lock` + `Semaphore` 并发控制
- **状态缓存**: `threading.Lock` 保护的 dict
- **文件指纹缓存**: 已移除（死代码）

## 数据存储

所有数据存储在 `~/.eagle-watcher/`：

```
~/.eagle-watcher/
  config.yaml      ← 设备配置
  themes.yaml      ← 主题列表
  knowledge.yaml   ← 知识库（AI 自动积累）
  state.json       ← 运行时状态（延迟写入）
  history.jsonl    ← 操作历史（追加写入）
  cache/           ← AI 分析缓存
  log/             ← 日志
  export/          ← 导出工作区
```

## 性能优化

| 优化项 | 机制 | 收益 |
|--------|------|------|
| Eagle 去重缓存 | `_get_recent_items` 30秒 TTL | 批量导入 N 文件：HTTP 从 N 次降到 1 次 |
| 状态延迟写入 | `_schedule_flush` 2秒窗口 | 10 文件：磁盘写入从 ~23 次降到 ~4 次 |
| AI 分析缓存 | 文件+内存双层缓存 | 相同文件不重复调用 API |
| 状态查询缓存 | 10秒 TTL | 避免每 5 秒重复查询 Eagle |
| Eagle 离线降频 | 30秒重试间隔 | 离线时减少无效连接 |

## 异常层次

```
EagleWatcherError (base)
  ├── ConfigError
  │     ├── ConfigLoadError
  │     └── ConfigValidationError
  ├── EagleError
  │     ├── EagleConnectionError
  │     ├── EagleAPIError
  │     └── EagleImportError
  ├── AIError
  │     ├── AIKeyError
  │     ├── AIModelError
  │     └── AICacheError
  ├── FileWatcherError
  ├── ExportError
  └── KnowledgeError
```

使用 `wrap_exception(e, TargetError)` 进行链式异常转换。
