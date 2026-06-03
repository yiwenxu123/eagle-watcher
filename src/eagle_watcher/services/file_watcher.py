"""文件系统监控：FSEvents → inode 轮询分层回退"""

import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

_LOG = logging.getLogger("file_watcher")

TEMP_EXTENSIONS = frozenset({
    ".crdownload", ".tmp", ".part", ".download",
    ".filedownloading", ".downloadling",
})

LARGE_FILE_THRESHOLD = 10 * 1024 * 1024
LARGE_FILE_INTERVAL = 1.0
LARGE_FILE_CHECKS = 3
STABLE_CHECKS = 2
STABLE_INTERVAL = 0.5
STABILITY_TIMEOUT = 30


def _is_temp(name: str) -> bool:
    return (name.startswith(".") or Path(name).suffix.lower() in TEMP_EXTENSIONS)


# 适合 Eagle 管理的设计素材扩展名白名单
# Eagle 可以接受任何文件，但只有这些格式对设计师有用
ALLOWED_EXTENSIONS = frozenset({
    # ── 图片 ──
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".ico",
    ".tiff", ".tif", ".psd", ".ai", ".eps", ".raw", ".heic", ".heif",
    ".avif", ".exr", ".hdr", ".tga", ".jfif", ".jxl", ".jpe", ".insp",
    # ── 视频 ──
    ".mp4", ".mov", ".avi", ".webm", ".mkv", ".m4v", ".3gp", ".flv",
    ".wmv", ".mpeg", ".mpg", ".ts", ".mts",
    # ── 音频 ──
    ".mp3", ".wav", ".flac", ".aac", ".m4a", ".wma", ".ogg", ".aiff",
    # ── 设计源文件 ──
    ".pdf", ".sketch", ".fig", ".xd", ".afdesign", ".afphoto", ".psb",
    ".indd", ".ait",
    # ── 字体 ──
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
    # ── 3D ──
    ".obj", ".fbx", ".stl", ".glb", ".gltf", ".usdz", ".blend", ".c4d",
    # ── 素材压缩包 ──
    ".zip", ".rar", ".7z",
})


def _is_allowed(name: str) -> bool:
    """检查文件扩展名是否在白名单内（是否适合 Eagle 管理）"""
    return Path(name).suffix.lower() in ALLOWED_EXTENSIONS


def _wait_for_stable(file_path: str, interval: float = STABLE_INTERVAL, checks: int = STABLE_CHECKS) -> bool:
    sizes = []
    # 对大文件使用更严格的检查
    try:
        size = os.path.getsize(file_path)
        if size > LARGE_FILE_THRESHOLD:
            interval = LARGE_FILE_INTERVAL
            checks = LARGE_FILE_CHECKS
    except OSError:
        return False

    for _ in range(checks):
        try:
            sizes.append(os.path.getsize(file_path))
        except OSError:
            return False
        time.sleep(interval)
    return len(set(sizes)) == 1 and sizes[0] > 0


# ── 第一层: FSEventsWatcher (PyObjC) ──

_HAS_FSEVENTS = False
try:
    from FSEvents import (
        CFRunLoopGetCurrent, CFRunLoopRun, CFRunLoopStop,
        FSEventStreamCreate, FSEventStreamRelease,
        FSEventStreamScheduleWithRunLoop, FSEventStreamStart,
        FSEventStreamStop, FSEventStreamInvalidate,
        kCFAllocatorDefault, kCFRunLoopDefaultMode,
        kFSEventStreamCreateFlagFileEvents, kFSEventStreamCreateFlagNoDefer,
        kFSEventStreamEventFlagItemCreated, kFSEventStreamEventFlagItemRemoved,
        kFSEventStreamEventFlagItemRenamed, kFSEventStreamEventFlagItemModified,
        kFSEventStreamEventFlagItemIsDir,
        kFSEventStreamEventIdSinceNow,
    )
    import AppKit
    _HAS_FSEVENTS = True
except ImportError:
    pass


if _HAS_FSEVENTS:

    class FSEventsWatcher:

        def __init__(self, path: str, callback: Callable[[str], None]):
            self._path = path
            self._callback = callback
            self._thread: Optional[threading.Thread] = None
            self._run_loop = None
            self._stream_ref = None
            self._running = False
            self._processing: set[str] = set()
            self._processing_lock = threading.Lock()

            self._stream_ref = FSEventStreamCreate(
                kCFAllocatorDefault,
                self._callback_fsevents,
                None,
                [path],
                kFSEventStreamEventIdSinceNow,
                1.0,
                kFSEventStreamCreateFlagNoDefer | kFSEventStreamCreateFlagFileEvents,
            )
            if self._stream_ref is None:
                raise OSError("FSEventStreamCreate 失败")

        def _callback_fsevents(self, stream_ref, info, num_events, paths, flags, event_ids):
            for p, f in zip(paths, flags):
                try:
                    file_path = p.decode("utf-8") if isinstance(p, bytes) else p
                except Exception:
                    continue

                if bool(f & kFSEventStreamEventFlagItemIsDir):
                    continue

                f_created = bool(f & kFSEventStreamEventFlagItemCreated)
                f_renamed = bool(f & kFSEventStreamEventFlagItemRenamed)
                f_removed = bool(f & kFSEventStreamEventFlagItemRemoved)

                filename = os.path.basename(file_path)

                if f_removed:
                    continue
                if _is_temp(filename):
                    continue
                if not _is_allowed(filename):
                    _LOG.debug("FSEvents 跳过不支持的扩展名: %s", filename)
                    continue

                if f_renamed or f_created:
                    with self._processing_lock:
                        if file_path in self._processing:
                            _LOG.debug("FSEvents 跳过重复: %s", filename)
                            continue
                        self._processing.add(file_path)
                    _LOG.debug("FSEvents: %s (created=%s renamed=%s)", filename, f_created, f_renamed)
                    self._dispatch(file_path)

        def _dispatch(self, file_path: str):
            if not os.path.exists(file_path):
                with self._processing_lock:
                    self._processing.discard(file_path)
                return
            if _wait_for_stable(file_path):
                self._callback(file_path)
                with self._processing_lock:
                    self._processing.discard(file_path)
            else:
                _LOG.debug("文件不稳定，延迟重试: %s", os.path.basename(file_path))
                threading.Thread(
                    target=self._retry_until_stable,
                    args=(file_path,),
                    daemon=True,
                ).start()

        def _retry_until_stable(self, file_path: str, timeout: int = STABILITY_TIMEOUT):
            start = time.time()
            while time.time() - start < timeout:
                if _wait_for_stable(file_path, interval=0.5, checks=2):
                    self._callback(file_path)
                    with self._processing_lock:
                        self._processing.discard(file_path)
                    return
            _LOG.warning("文件稳定性超时: %s", os.path.basename(file_path))
            with self._processing_lock:
                self._processing.discard(file_path)

        def start(self):
            if self._running:
                return
            self._running = True

            def _run():
                pool = AppKit.NSAutoreleasePool.alloc().init()
                self._run_loop = CFRunLoopGetCurrent()
                FSEventStreamScheduleWithRunLoop(
                    self._stream_ref, self._run_loop, kCFRunLoopDefaultMode
                )
                if not FSEventStreamStart(self._stream_ref):
                    raise OSError("FSEventStreamStart 失败")
                CFRunLoopRun()
                FSEventStreamStop(self._stream_ref)
                FSEventStreamInvalidate(self._stream_ref)
                FSEventStreamRelease(self._stream_ref)
                del pool

            self._thread = threading.Thread(target=_run, daemon=True)
            self._thread.start()

        def stop(self):
            self._running = False
            if self._run_loop is not None:
                CFRunLoopStop(self._run_loop)


# ── 第二层: PollingWatcher (inode 感知) ──

@dataclass
class _FileRecord:
    path: str
    inode: int
    size: int
    mtime: float = 0.0


class PollingWatcher:

    def __init__(self, path: str, callback: Callable[[str], None], interval: float = 1.0):
        self._path = path
        self._callback = callback
        self._interval = interval
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._known_inodes: dict[int, _FileRecord] = {}
        self._processing: set[str] = set()
        self._processing_lock = threading.Lock()
        self._scan()

    def _scan(self):
        self._known_inodes.clear()
        try:
            for entry in os.scandir(self._path):
                if not entry.is_file():
                    continue
                name = entry.name
                if _is_temp(name):
                    continue
                if not _is_allowed(name):
                    _LOG.debug("PollingWatcher 扫描跳过不支持的扩展名: %s", name)
                    continue
                try:
                    st = entry.stat()
                    self._known_inodes[st.st_ino] = _FileRecord(
                        path=entry.path, inode=st.st_ino, size=st.st_size, mtime=st.st_mtime
                    )
                except OSError:
                    continue
        except (PermissionError, FileNotFoundError):
            pass

    def _poll_once(self):
        new_inodes: dict[int, _FileRecord] = {}
        ready: list[str] = []

        try:
            for entry in os.scandir(self._path):
                if not entry.is_file():
                    continue
                name = entry.name
                if _is_temp(name):
                    continue
                if not _is_allowed(name):
                    _LOG.debug("PollingWatcher 轮询跳过不支持的扩展名: %s", name)
                    continue
                try:
                    st = entry.stat()
                    rec = _FileRecord(path=entry.path, inode=st.st_ino, size=st.st_size, mtime=st.st_mtime)
                    new_inodes[st.st_ino] = rec
                    known = self._known_inodes.get(st.st_ino)
                    if known is None:
                        is_new = True
                    elif known.path != rec.path:
                        is_new = True  # inode 被重用，文件路径不同
                    elif known.mtime != rec.mtime:
                        is_new = True  # 同名文件被覆盖（mtime 变化）
                    else:
                        is_new = False
                    if is_new:
                        if rec.path not in self._processing and _wait_for_stable(rec.path):
                            ready.append(rec.path)
                except OSError:
                    continue
        except (PermissionError, FileNotFoundError):
            pass

        self._known_inodes = new_inodes
        for fp in ready:
            self._processing.add(fp)
            try:
                self._callback(fp)
            finally:
                self._processing.discard(fp)

    def start(self):
        if self._running:
            return
        self._running = True

        def _loop():
            while self._running:
                try:
                    self._poll_once()
                except Exception:
                    _LOG.exception("轮询异常")
                time.sleep(self._interval)

        self._thread = threading.Thread(target=_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False


# ── 工厂函数 ──


def create_watcher(path: str, callback: Callable[[str], None], poll_interval: float = 1.0):
    if _HAS_FSEVENTS:
        try:
            w = FSEventsWatcher(path, callback)
            _LOG.info("使用 FSEventsWatcher (PyObjC FSEvents)")
            return w
        except Exception as e:
            _LOG.warning("FSEventsWatcher 初始化失败: %s，降级到轮询", e)

    _LOG.info("使用 PollingWatcher (inode 感知轮询)")
    return PollingWatcher(path, callback, interval=poll_interval)