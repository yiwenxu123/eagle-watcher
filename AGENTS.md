# AGENTS.md

This file helps new OpenCode sessions avoid mistakes and ramp up quickly.

## 源码结构（实际路径 vs 逻辑分层）

源码统一在 `src/eagle_watcher/` 下，无 `entrypoints/` 或 `domain/` 子目录。

```
src/eagle_watcher/         ← IO、线程（main, watcher, menu, server, cli）
  _logging.py, _constants.py
  main.py, watcher.py, menu_app.py, server.py, cli.py
  analyzer.py, knowledge.py       ← 决策引擎 + 知识库
  eagle_api.py, ai_tagger.py      ← Eagle API + AI 视觉
  config.py, keychain.py, notifier.py
  services/                       ← state_manager, file_watcher, sort_service, history
  pyui/                           ← NSPanel + WKWebView + panel.html
```

## 启动方式

| 命令 | 说明 |
|------|------|
| `eagle-watcher` | 完整 GUI（菜单栏 + watcher + HUD 面板） |
| `eagle-server`  | 纯 HTTP API（端口 9800），无 GUI 依赖 |
| `eagle-import --file X --project Y` | CLI 单次导入 |
| `python -m eagle_watcher.main` | 开发模式启动 GUI |
| `python -m eagle_watcher.server` | 开发模式启动 HTTP |
| `python -m eagle_watcher.cli --help` | CLI 帮助 |

## 测试

```bash
pip install -e ".[test]"            # 首次
python -m pytest tests/ -v          # 全部
python -m pytest tests/test_analyzer.py -v  # 单个模块
```

**测试坑**：
- `conftest.py` 的 `mock_data_dir` 自动重置 `server._eagle_offline_since` 全局变量
- `test_watcher.py` 包含长时间运行测试（`run_watcher` 系列），单独跑超时
- `PyObjC` 导入在 LSP 上报大量 false positive 错误（`NSWorkspace`、`NSPanel` 等），不影响运行

## 技术约束

- **Eagle API**：必须用 `urllib`，`httpx>=0.28` 与 Eagle HTTP 服务器不兼容（始终返回 502）
- **无 item/move**：Eagle API 不支持移动素材到不同文件夹，通用箱整理只能改标签
- **AI 不创建主题**：AI 视觉分析只能匹配已有主题，不能自动创建新主题
- **AI 触发条件**：仅文件名模糊（`is_vague_name` 返回 True）时调用 Qwen-VL。CJK 截图名（截屏、微信图片、mmexport、IMG_ 等）也会触发。
- **AI 标签过滤**：`watcher._GENERIC_AI_TAGS` 中的通用英文词（portrait、landscape 等）不会写入知识库
- **线程安全**：`_processing_files` 是全局 set 无锁（已知竞态），`_status_cache` 已加锁。编写新全局可变状态时必须加锁。

## 决策流

```
新文件 → analyzer.decide()
  ├─ 设了当前主题？ → 强制归入（不检查文件名）
  ├─ source_url 匹配 → 归入对应主题
  ├─ 知识库关键词匹配 → 归入对应主题
  ├─ 文件名模糊 + 图片 → AI 视觉分析（Qwen-VL）
  └─ 都不匹配 → 进通用箱（标签"待分类"）
```

## HTTP API 端点（面板端口 9801）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/status` | 状态（含缓存，TTL=10s） |
| GET | `/api/inbox?limit=50&offset=N` | 通用箱分页 |
| GET | `/api/history` | 操作历史 |
| GET | `/api/watch-dirs` | 监控目录列表 |
| GET | `/api/watch-dirs/scan-status` | 扫描进度 |
| GET | `/api/watch-dirs/picker-result` | 文件夹选择器结果 |
| GET | `/api/watch-dirs/scan-preview?path=X` | 扫描前文件统计预览 |
| POST | `/import` | 导入素材（file_url/file_path + project/tags/folder） |
| POST | `/api/sort/confirm` | 确认通用箱整理（带幂等性 key） |
| POST | `/api/sort/skip` | 跳过通用箱素材 |
| POST | `/api/watch-dirs/add` | 添加临时监控目录 |
| POST | `/api/watch-dirs/scan` | 启动批量扫描 |

**注意**：所有 POST 需要 `X-Session-Token` header（从 `<meta name="session-token">` 获取）。GET 不需要。

## 数据目录 `~/.eagle-watcher/`

| 文件 | 说明 |
|------|------|
| `config.yaml` | Eagle 连接、监控路径、通知设置、AI key |
| `themes.yaml` | 主题/项目列表（categories + projects） |
| `knowledge.yaml` | 知识库（关键词→主题映射） |
| `state.json` | 运行时状态（StateManager 线程安全读写） |
| `history.jsonl` | 操作历史（JSONL 格式，自动截断 2000 条） |
| `cache/` | AI 视觉分析 MD5 缓存（30 天过期） |
| `log/` | 每日日志文件 |

## 代码规范

- Python 3.9+, type hints, `logging` 模块日志
- 状态写穿透：`StateManager` 每次 set 都同步写 `state.json`
- 文件写全部用临时文件 + `os.replace` 原子替换（`tempfile.mkstemp` + `os.replace`）
- 导入不走 `decide()` 的场景（CLI 不带 project 时、API 直接 import）需要手动调 `record_match` 学习知识库