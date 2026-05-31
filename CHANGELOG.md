# Changelog

## [0.3.0] — 2026-05-31

### Added
- Open-source release preparation: LICENSE, README, CHANGELOG
- PyPI package with dependency extras (`[ui]`, `[fsevents]`, `[full]`)
- `config.yaml.example` template for new users
- Friendly error message when UI dependencies are missing

### Changed
- `rumps` moved to optional `[ui]` extra — core package installs on any platform
- README now used as package description (was CLAUDE.md)
- Version bump to 0.3.0

## [0.2.0] — 2026-05-30

### Added
- NSPanel + WKWebView floating HUD panel with Tailwind frontend
- Folder/theme management: create, delete, rename (syncs to Eagle)
- Inbox management: review, AI-suggest, confirm sorting
- Pin-to-top and drag-to-move panel behavior
- HUD panel HTTP server (`pyui/server.py`) with full CRUD API

### Changed
- Categories now reflect real Eagle folders (not local-only)
- Panel window controls: Closable, Resizable (no Miniaturizable)
- Traffic light buttons no longer overlap content

### Fixed
- Panel not showing on initial launch
- Tag merge in sort confirmation (preserves existing tags)

## [0.1.0] — 2026-05-22

### Added
- Initial prototype: rumps menu bar + Downloads watcher
- CLI import tool (`eagle-import`)
- HTTP API server (`eagle-server`) for remote agents
- Auto-sort by filename keyword matching
- Qwen-VL AI analysis for vague filenames
- Knowledge base (auto-learns keyword→theme mappings)
- Eagle API client (urllib-based)
- Thread-safe state management
- FSEvents file monitoring with inode polling fallback
