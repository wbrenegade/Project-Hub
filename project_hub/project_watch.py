"""Background watcher that reports files changed inside each project's folder.

Qt's QFileSystemWatcher is not recursive and would need one handle per folder, which does not scale to
projects with thousands of directories. Instead this polls modification times, skipping the same
dependency/build folders the project scan skips, so it stays cheap.
"""
from __future__ import annotations

import os
import threading

from PySide6.QtCore import QThread, Signal

from project_services import IGNORE

POLL_SECONDS = 3
MAX_FILES = 50000


def snapshot(root):
    """{relative path: mtime_ns} for the files under root.

    DirEntry.stat() reuses what Windows returned with the directory listing, so this needs no per-file system call.
    """
    files = {}
    stack = [(root, '')]
    while stack:
        folder, rel = stack.pop()
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name not in IGNORE:
                                stack.append((entry.path, os.path.join(rel, entry.name)))
                        else:
                            files[os.path.join(rel, entry.name)] = entry.stat(follow_symlinks=False).st_mtime_ns
                    except OSError:
                        continue
        except OSError:
            continue
        if len(files) >= MAX_FILES:
            break
    return files


def diff(old, new):
    """Relative paths that were added, removed or modified."""
    return sorted({p for p in old.keys() | new.keys() if old.get(p) != new.get(p)})


class FolderWatcher(QThread):
    changed = Signal(str, list)  # project id, changed relative paths

    def __init__(self, parent=None):
        super().__init__(parent)
        self._folders = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()

    def set_folders(self, folders):
        """folders: {project id: folder path}. New or moved folders get a fresh baseline."""
        with self._lock:
            self._folders = dict(folders)
        self._wake.set()

    def stop(self):
        self._stop.set()
        self._wake.set()
        self.wait(5000)

    def run(self):
        known = {}  # project id -> (folder, snapshot)
        while not self._stop.is_set():
            with self._lock:
                folders = dict(self._folders)
            for pid in list(known):
                if folders.get(pid) != known[pid][0]:
                    del known[pid]
            for pid, folder in folders.items():
                if self._stop.is_set():
                    return
                if not folder or not os.path.isdir(folder):
                    known.pop(pid, None)
                    continue
                now = snapshot(folder)
                if pid in known:
                    changes = diff(known[pid][1], now)
                    if changes:
                        self.changed.emit(pid, changes)
                known[pid] = (folder, now)
            self._wake.wait(POLL_SECONDS)
            self._wake.clear()
