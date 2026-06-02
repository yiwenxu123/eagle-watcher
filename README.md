# Eagle Watcher 🦅

A macOS menubar app that watches your Downloads folder and automatically sorts design assets into [Eagle](https://eagle.cool).

**Drag, drop, done.** No more piles of untitled screenshots.

## Features

- **📂 Auto-sort** — New files in Downloads are analyzed by filename, matched against your knowledge base, and imported to the right Eagle folder
- **🧠 AI vision** — When filenames are too vague (screenshot_2025-01-01.png), it uses Qwen-VL to analyze the actual image content
- **🗂️ Inbox management** — A floating HUD panel to review, tag, and organize unsorted assets
- **🏷️ Theme-based workflows** — Switch between projects. Files matching your active theme are prioritized
- **🌐 HTTP API** — Remote AI agents (Hermes, OpenClaw, etc.) can import assets via SSH tunnel
- **⌨️ CLI** — Quick imports from terminal

## Requirements

- **macOS 11+** (Big Sur or later)
- **[Eagle](https://eagle.cool)** app (with API enabled: Preferences → Developer Options)
- **Python 3.9+**

## Quick Start

```bash
# 1. Install
pip install eagle-watcher

# 2. (Optional but recommended) Enable macOS file monitoring + HUD panel
pip install 'eagle-watcher[full]'

# 3. Set up Eagle API key
#    Open Eagle → Preferences → Developer Options → copy your API Token
cp config.yaml.example ~/.eagle-watcher/config.yaml
#    Then edit ~/.eagle-watcher/config.yaml and paste your token

# 4. Run
eagle-watcher
```

### First Run

On first launch, it prints step-by-step instructions to get your Eagle API Token. After setup, the app creates a menubar icon. Click it to open the floating HUD panel.

## Usage

### Menubar

The menubar icon shows your current project. Click to open the panel or quit.

### HUD Panel

| Section | What you can do |
|---------|----------------|
| **Status** | See today's imports, pending inbox items, Eagle connection |
| **Inbox** | Review unsorted assets, apply AI-suggested themes, confirm sorting |
| **Folders** | Create/delete Eagle folders (categories) |
| **Themes** | Create/delete theme projects, switch active project |

### CLI

```bash
# Import a local file
eagle-import --file "poster.jpg" --project "Qin Dynasty" --tags "qin,terracotta"

# Import from URL
eagle-import --url "https://example.com/design.png" --project "Han Dynasty"

# Auto-detect project from filename
eagle-import --file "white_qi.jpg"
```

### HTTP API (for AI Agents)

```bash
# Start the HTTP server (no menubar needed)
eagle-server

# Then in another terminal:
curl http://localhost:9800/ping
curl http://localhost:9800/status

curl -X POST http://localhost:9800/import \
  -H "Content-Type: application/json" \
  -d '{"file_url": "https://example.com/img.jpg", "project": "Qin Dynasty"}'
```

For remote agents: `ssh -L 9800:localhost:9800 your-mac`

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/ping` | Health check |
| `GET` | `/status` | Current project, folders, stats |
| `POST` | `/import` | Import asset from URL or path |

## Architecture

```
eagle-watcher/
├── main.py              # Entry point: watcher thread + menubar
├── cli.py               # CLI import tool
├── server.py            # HTTP API server (standalone)
├── analyzer.py          # Filename analysis + theme matching
├── knowledge.py         # Auto-learning keyword→theme mappings
├── eagle_api.py         # Eagle HTTP API client (urllib, not httpx)
├── config.py            # YAML config management
├── services/
│   ├── state_manager.py # Thread-safe runtime state
│   ├── file_watcher.py  # FSEvents → polling fallback
│   └── sort_service.py  # Inbox sorting logic
└── pyui/
    ├── server.py        # HUD panel HTTP server
    ├── panel.py         # Native NSPanel + WKWebView
    └── panel.html       # Frontend UI (Tailwind)
```

## Contributing

PRs welcome! Please ensure tests pass:

```bash
pip install -e ".[test]"
python -m pytest tests/ -v
```

## License

MIT
