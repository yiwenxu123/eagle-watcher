# Eagle Watcher API 文档

## 概述

Eagle Watcher 提供两个 HTTP 服务：

| 服务 | 端口 | 用途 | 认证方式 |
|------|------|------|----------|
| RemoteHandler | 9800 | 远程 Agent 导入素材 | `X-API-Key` header |
| PanelHandler | 9801 | HUD 面板 + 管理 API | `X-Session-Token` header（POST） |

## 认证

### RemoteHandler（端口 9800）

在 `config.yaml` 中配置 `server.api_key`，请求时通过 `X-API-Key` header 传入。
未配置 api_key 时向后兼容，允许所有请求。`/ping` 端点始终开放。

```bash
curl -H "X-API-Key: your-key" http://localhost:9800/status
```

### PanelHandler（端口 9801）

POST 请求需要 `X-Session-Token` header。Token 注入在面板 HTML 页面的 `<meta name="session-token">` 标签中。

```bash
curl -H "X-Session-Token: <token>" -X POST http://localhost:9801/api/current-project \
  -d '{"project": "武安侯"}'
```

---

## RemoteHandler 端点（端口 9800）

### GET /ping

健康检查端点，无需认证。

**响应 200:**
```json
{"status": "ok", "eagle_online": true}
```

### GET /status

查询当前状态。

**响应 200:**
```json
{
  "status": "ok",
  "data": {
    "project": "武安侯",
    "projects": ["武安侯", "秦始皇"],
    "eagle_online": true,
    "folders": ["武安侯", "秦始皇"]
  }
}
```

### POST /import

导入素材到 Eagle。支持 `file_url`（远程 URL）或 `file_path`（本地路径），至少提供一个。

**请求体:**
```json
{
  "file_url": "https://example.com/img.jpg",
  "file_path": "",
  "project": "武安侯",
  "tags": ["白起", "战国"],
  "folder": "武安侯"
}
```

**响应 200:**
```json
{
  "status": "success",
  "message": "已入库：武安侯",
  "data": {"tags": ["白起", "战国"], "folder": "武安侯"}
}
```

**错误响应:**
- `400`: `{"status": "error", "message": "file_url or file_path is required"}`
- `503`: `{"status": "error", "message": "Eagle 未运行"}`

---

## PanelHandler 端点（端口 9801）

### GET 端点

#### GET /ping

同 RemoteHandler，无需认证。

#### GET / 或 /panel

返回 HUD 面板 HTML 页面。

#### GET /api/status

获取完整状态（带缓存，10秒TTL）。

**响应 200:**
```json
{
  "current_project": "武安侯",
  "categories": {"武安侯": {"projects": [...]}},
  "projects": {"武安侯": {...}},
  "eagle_online": true,
  "permission_denied": false,
  "permission_path": "",
  "today_count": 5,
  "inbox_count": 12,
  "last_processed": {"filename": "白起.jpg", "theme": "武安侯", "time": "..."},
  "ai_configured": true,
  "ai_model": "qwen-vl-max",
  "watch_dirs": [{"path": "/Users/.../Downloads", "exists": true, "type": "downloads"}]
}
```

#### GET /api/inbox

查询待分类素材（分页）。

**查询参数:**
- `limit` (int, 默认 50)
- `offset` (int, 默认 0)
- `media_type` (str: `all`|`image`|`video`|`media`)

**响应 200:**
```json
{
  "items": [
    {
      "id": "xxx",
      "name": "白起",
      "thumbnail": "...",
      "tags": ["待分类"],
      "suggested_theme": "武安侯",
      "suggested_tags": ["白起"],
      "confidence": 0.85
    }
  ],
  "total": 100,
  "offset": 0,
  "limit": 50,
  "has_more": true
}
```

#### GET /api/history

获取最近 50 条操作历史。

**响应 200:** `{"items": [...]}`

#### GET /api/watch-dirs

获取监控目录列表。

**响应 200:**
```json
{
  "dirs": [
    {"path": "/Users/.../Downloads", "exists": true, "type": "downloads"},
    {"path": "/tmp/import", "exists": true, "type": "temp"}
  ]
}
```

#### GET /api/knowledge

查询知识库（分页 + 搜索）。

**查询参数:** `search`, `theme`, `sort` (默认 "confidence"), `page` (默认 1), `per_page` (默认 20)

**响应 200:** 分页关键词列表 + `stats` 对象。

#### GET /api/export/status

获取导出工作区状态。

#### GET /api/token/refresh

获取新的 Session Token。

**响应 200:** `{"token": "new-token-string"}`

---

### POST 端点

所有 POST 端点需要 `X-Session-Token` header。

#### POST /import

同 RemoteHandler 的 `/import`。

#### POST /api/current-project

设置当前项目。

**请求:** `{"project": "武安侯"}`

#### POST /api/projects/create

创建项目。

**请求:** `{"name": "白起", "category": "武安侯", "tags": ["战国"]}`

#### POST /api/projects/delete

删除项目。

**请求:** `{"name": "白起"}`

#### POST /api/categories/create

创建分类。

**请求:** `{"name": "战国名将"}`

#### POST /api/categories/delete

删除分类（级联确认机制）。

**请求:** `{"name": "战国名将", "confirm": true}`

首次不带 `confirm` 会返回 400 + 受影响项目列表。

#### POST /api/sort/confirm

确认通用箱素材分类。

**请求:**
```json
{
  "id": "item-id",
  "tags": ["白起"],
  "replace_tags": true,
  "folder": "武安侯",
  "name": "白起",
  "idempotency_key": "unique-key"
}
```

#### POST /api/sort/skip

跳过通用箱素材。

**请求:** `{"id": "item-id", "idempotency_key": "unique-key"}`

#### POST /api/watch-dirs/add

添加临时监控目录。

**请求:** `{"path": "/tmp/import"}`

#### POST /api/watch-dirs/remove

移除临时监控目录。

**请求:** `{"path": "/tmp/import"}`

#### POST /api/watch-dirs/scan

扫描目录。

**请求:** `{"path": "/tmp/import", "filter": "image"}`

#### POST /api/set-pinned

设置面板置顶状态。

**请求:** `{"pinned": true}`

#### POST /api/config/token

配置 Eagle API Token。

**请求:** `{"token": "your-eagle-token"}`

#### POST /api/knowledge/update

更新知识库关键词。

**请求:** `{"keyword": "白起", "theme": "武安侯", "tags": ["战国"]}`

#### POST /api/knowledge/delete

删除知识库关键词。

**请求:** `{"keyword": "白起"}`

#### POST /api/ai/cache/clear

清除 AI 分析缓存。

#### POST /api/export/config

配置导出工作区。

**请求:** `{"enabled": true, "dir": "/path/to/export", "auto": true, "structure": "theme"}`

#### POST /api/export/item

导出单个素材。

**请求:** `{"file_path": "/path/to/file.jpg", "theme": "武安侯", "filename": "file.jpg"}`

#### POST /api/export/clear

清空导出工作区（需确认）。

**请求:** `{"confirm": true}`

#### POST /api/export/theme

按主题批量导出。

**请求:** `{"theme": "武安侯"}`

---

## 错误码

| HTTP 状态码 | 含义 |
|-------------|------|
| 200 | 成功 |
| 400 | 请求参数错误 |
| 401 | 认证失败（API Key 或 Session Token 无效） |
| 404 | 端点不存在 |
| 405 | HTTP 方法不支持 |
| 500 | 服务器内部错误 |
| 503 | Eagle 未运行 |
