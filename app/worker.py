from __future__ import annotations

import fcntl
import inspect
import time
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock, Thread, current_thread
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import oauth_config_path, sync_lock_path
from app.models import MotherMailbox, SyncCycle, SyncRun, SyncTrigger
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


class FixedTriggerGate:
    """Monotonic fixed-point clock; missed points are discarded, never replayed."""

    def __init__(self, interval: float, *, start: float = 0.0) -> None:
        if interval <= 0:
            raise ValueError("interval must be positive")
        self.interval = float(interval)
        self.next_due = float(start)

    def poll(self, now: float) -> list[float]:
        if now < self.next_due:
            return []
        due = self.next_due
        self.next_due += self.interval
        while self.next_due <= now:
            self.next_due += self.interval
        return [due]


class SyncWorker:
    def __init__(
        self,
        session_factory: sessionmaker,
        graph_factory: Callable[..., object],
        *,
        lock: SyncLock | FileSyncLock | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.graph_factory = graph_factory
        self.lock = lock or FileSyncLock(sync_lock_path())
        self._mailbox_paths: dict[int, Path] = {}
        self.stop_requested = False
        self._graph_clients: dict[int, object] = {}
        self._legacy_graph_client: object | None = None
        self._cycle_guard = Lock()
        self._cycle_running = False
        self._threads: set[Thread] = set()

    def _factory_takes_mailbox(self) -> bool:
        try:
            signature = inspect.signature(self.graph_factory)
        except (TypeError, ValueError):
            return True
        positional = [
            parameter
            for parameter in signature.parameters.values()
            if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
        return bool(positional) or any(
            parameter.kind == inspect.Parameter.VAR_POSITIONAL
            for parameter in signature.parameters.values()
        )

    def _get_graph_client(self, mailbox: MotherMailbox | None) -> object:
        if mailbox is None:
            if self._legacy_graph_client is None:
                self._legacy_graph_client = self.graph_factory()
            return self._legacy_graph_client
        cached = self._graph_clients.get(mailbox.id)
        if cached is None:
            path = oauth_config_path(mailbox.id)
            self._mailbox_paths[mailbox.id] = path
            if self._factory_takes_mailbox():
                cached = self.graph_factory(mailbox)
            else:
                cached = self.graph_factory(path)
            self._graph_clients[mailbox.id] = cached
        return cached

    def invalidate_mailbox_client(self, mailbox_id: int) -> None:
        self._graph_clients.pop(mailbox_id, None)
        self._mailbox_paths.pop(mailbox_id, None)

    def _enabled_mailbox_ids(self, db: Session) -> list[int]:
        return list(
            db.scalars(
                select(MotherMailbox.id)
                .where(MotherMailbox.enabled.is_(True))
                .order_by(MotherMailbox.id.asc())
            )
        )

    def _has_multi_mailbox_schema_data(self, db: Session) -> bool:
        return db.scalar(select(MotherMailbox.id).limit(1)) is not None

    def record_skipped_trigger(self, triggered_at: datetime, reason: str) -> SyncTrigger:
        db = self.session_factory()
        try:
            trigger = SyncTrigger(
                triggered_at=triggered_at,
                observed_at=datetime.utcnow(),
                status="skipped",
                skip_reason=reason,
                trigger_source="scheduled",
            )
            db.add(trigger)
            db.commit()
            db.refresh(trigger)
            return trigger
        finally:
            db.close()

    def _create_cycle(self, db: Session, triggered_at: datetime, mailbox_ids: list[int]) -> SyncCycle:
        trigger = SyncTrigger(
            triggered_at=triggered_at,
            observed_at=datetime.utcnow(),
            status="started",
            skip_reason=None,
            trigger_source="scheduled",
        )
        db.add(trigger)
        db.flush()
        cycle = SyncCycle(
            trigger_id=trigger.id,
            started_at=datetime.utcnow(),
            status="running",
            mailbox_count=len(mailbox_ids),
        )
        db.add(cycle)
        db.commit()
        db.refresh(cycle)
        return cycle

    def _record_failed_mailbox(self, db: Session, mailbox_id: int, cycle: SyncCycle, error: Exception) -> None:
        now = datetime.utcnow()
        run = SyncRun(
            mother_mailbox_id=mailbox_id,
            cycle_id=cycle.id,
            cycle_started_at=cycle.started_at,
            started_at=now,
            finished_at=now,
            status="failed",
            error_message=str(error)[:2000],
        )
        db.add(run)
        mailbox = db.get(MotherMailbox, mailbox_id)
        if mailbox is not None:
            mailbox.last_sync_at = run.finished_at
            mailbox.last_sync_status = "failed"
            mailbox.last_sync_error = str(error)[:2000]
        db.commit()

    def _run_mailbox(self, mailbox_id: int, cycle: SyncCycle) -> bool:
        db = self.session_factory()
        try:
            mailbox = db.get(MotherMailbox, mailbox_id)
            if mailbox is None or not mailbox.enabled:
                return False
            try:
                graph = self._get_graph_client(mailbox)
                result = sync_once(
                    db,
                    graph,
                    folder_names=get_enabled_folder_names(db),
                    limit=50,
                    mother_mailbox_id=mailbox.id,
                    cycle_id=cycle.id,
                )
            except Exception as exc:
                db.rollback()
                self._record_failed_mailbox(db, mailbox.id, cycle, exc)
                return False
            finished_at = getattr(result, "finished_at", None) or datetime.utcnow()
            mailbox.last_sync_at = finished_at
            mailbox.last_sync_status = getattr(result, "status", "success")
            mailbox.last_sync_error = getattr(result, "error_message", None)
            db.commit()
            return getattr(result, "status", "success") == "success"
        finally:
            db.close()

    def run_cycle(self, *, triggered_at: datetime | None = None) -> SyncCycle | None:
        """Run one complete A->B->C cycle; this method never imposes a deadline."""
        db = self.session_factory()
        try:
            if not get_sync_enabled(db):
                return None
            mailbox_ids = self._enabled_mailbox_ids(db)
            if not mailbox_ids:
                return None
            trigger_dt = datetime.utcnow() if triggered_at is None else triggered_at
            cycle = self._create_cycle(db, trigger_dt, mailbox_ids)
        finally:
            db.close()

        completed = 0
        failed = 0
        for mailbox_id in mailbox_ids:
            if self._run_mailbox(mailbox_id, cycle):
                completed += 1
            else:
                failed += 1

        db = self.session_factory()
        try:
            persisted = db.get(SyncCycle, cycle.id)
            if persisted is None:
                return None
            persisted.completed_mailbox_count = completed
            persisted.failed_mailbox_count = failed
            persisted.status = "success" if failed == 0 else ("failed" if completed == 0 else "partial")
            persisted.finished_at = datetime.utcnow()
            db.commit()
            db.refresh(persisted)
            return persisted
        finally:
            db.close()

    def run_once(self):
        """Run one manual cycle without waiting for a busy lock."""
        if not self.lock.acquire(blocking=False):
            return None
        try:
            db = self.session_factory()
            try:
                if not get_sync_enabled(db):
                    return None
                if self._has_multi_mailbox_schema_data(db):
                    return self.run_cycle(triggered_at=datetime.utcnow())
                if self._legacy_graph_client is None:
                    self._legacy_graph_client = self.graph_factory()
                return sync_once(db, self._legacy_graph_client, folder_names=get_enabled_folder_names(db), limit=50)
            finally:
                db.close()
        finally:
            self.lock.release()

    def _scheduled_datetime(self, triggered_at: float) -> datetime:
        return datetime.utcnow() + timedelta(seconds=max(0.0, triggered_at - time.monotonic()))

    def _scheduled_start(self, triggered_at: float) -> None:
        if not self._cycle_guard.acquire(blocking=False):
            self.record_skipped_trigger(datetime.utcnow(), "cycle_already_running")
            return
        self._cycle_running = True
        started = False
        try:
            if not self.lock.acquire(blocking=False):
                self.record_skipped_trigger(datetime.utcnow(), "sync_lock_busy")
                return

            db = self.session_factory()
            try:
                if not get_sync_enabled(db):
                    self.record_skipped_trigger(datetime.utcnow(), "sync_disabled")
                    return
                if not self._enabled_mailbox_ids(db):
                    self.record_skipped_trigger(datetime.utcnow(), "no_enabled_mailboxes")
                    return
            finally:
                db.close()

            thread = Thread(
                target=self._background_cycle,
                args=(self._scheduled_datetime(triggered_at),),
                name="mail-portal-sync-cycle",
                daemon=True,
            )
            self._threads.add(thread)
            started = True
            thread.start()
        finally:
            if not started:
                if self.lock._handle is not None:
                    self.lock.release()
                self._cycle_running = False
                self._cycle_guard.release()

    def _background_cycle(self, triggered_at: datetime) -> None:
        current = current_thread()
        try:
            self.run_cycle(triggered_at=triggered_at)
        finally:
            self.lock.release()
            self._cycle_running = False
            self._cycle_guard.release()
            self._threads.discard(current)

    def run_forever(self, *, sleep_fn: Callable[[float], None] = time.sleep) -> None:
        gate = FixedTriggerGate(self._read_interval(), start=time.monotonic())
        while not self.stop_requested:
            now = time.monotonic()
            for due in gate.poll(now):
                self._scheduled_start(due)
            delay = max(0.0, gate.next_due - time.monotonic())
            sleep_fn(min(1.0, delay) if delay else 0.01)
        for thread in list(self._threads):
            thread.join(timeout=5)

    def _read_interval(self) -> int:
        db = self.session_factory()
        try:
            return get_sync_interval(db)
        finally:
            db.close()
