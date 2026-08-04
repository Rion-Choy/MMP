from __future__ import annotations

import fcntl
import time
from pathlib import Path
from threading import Lock
from typing import Callable

from sqlalchemy.orm import sessionmaker

from app.config import sync_lock_path
from app.services.mail_sync import sync_once
from app.services.settings_service import get_enabled_folder_names, get_sync_enabled, get_sync_interval


class SyncLock:
    def __init__(self) -> None:
        self._lock = Lock()

    def acquire(self, blocking: bool = True) -> bool:
        return self._lock.acquire(blocking=blocking)

    def release(self) -> None:
        self._lock.release()


class FileSyncLock:
    """Cross-process non-blocking lock for Web manual sync and Worker."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle = None

    def acquire(self, blocking: bool = True) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+")
        flags = fcntl.LOCK_EX
        if not blocking:
            flags |= fcntl.LOCK_NB
        try:
            fcntl.flock(handle.fileno(), flags)
        except BlockingIOError:
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None


class SyncWorker:
    def __init__(
        self,
        session_factory: sessionmaker,
        graph_factory: Callable[[], object],
        *,
        lock: SyncLock | FileSyncLock | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.graph_factory = graph_factory
        self.lock = lock or FileSyncLock(sync_lock_path())
        self.stop_requested = False
        self._graph_client: object | None = None

    def run_once(self):
        if not self.lock.acquire(blocking=False):
            return None
        db = self.session_factory()
        try:
            if not get_sync_enabled(db):
                return None
            if self._graph_client is None:
                self._graph_client = self.graph_factory()
            return sync_once(db, self._graph_client, folder_names=get_enabled_folder_names(db), limit=50)
        finally:
            db.close()
            self.lock.release()

    def run_forever(self, *, sleep_fn: Callable[[float], None] = time.sleep) -> None:
        while not self.stop_requested:
            self.run_once()
            interval = self._read_interval()
            remaining = interval
            while remaining > 0 and not self.stop_requested:
                delay = min(5, remaining)
                sleep_fn(delay)
                remaining -= delay

    def _read_interval(self) -> int:
        db = self.session_factory()
        try:
            return get_sync_interval(db)
        finally:
            db.close()
