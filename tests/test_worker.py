from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from app.database import Base, create_engine_for_tests, make_session_factory
from app.models import MotherMailbox, SyncCycle, SyncTrigger
from app.services.settings_service import set_setting
from app.worker import FixedTriggerGate, FileSyncLock, SyncWorker


def test_file_sync_lock_is_exclusive_between_instances(tmp_path: Path) -> None:
    path = tmp_path / "sync.lock"
    first = FileSyncLock(path)
    second = FileSyncLock(path)

    assert first.acquire(blocking=False) is True
    assert second.acquire(blocking=False) is False
    first.release()
    assert second.acquire(blocking=False) is True
    second.release()


def test_worker_does_not_create_graph_client_when_scheduled_sync_is_disabled(tmp_path: Path) -> None:
    engine = create_engine_for_tests()
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    db = session_factory()
    set_setting(db, "sync_enabled", "0")
    db.commit()
    db.close()

    def graph_factory() -> object:
        raise AssertionError("graph client must not be created while scheduled sync is disabled")

    from app.worker import SyncWorker

    worker = SyncWorker(session_factory, graph_factory, lock=FileSyncLock(tmp_path / "sync.lock"))

    assert worker.run_once() is None


def test_worker_reuses_graph_client_across_enabled_sync_rounds(tmp_path: Path, monkeypatch) -> None:
    engine = create_engine_for_tests()
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    db = session_factory()
    set_setting(db, "sync_enabled", "1")
    db.commit()
    db.close()

    graph = object()
    factory_calls: list[object] = []
    sync_graphs: list[object] = []
    sync_limits: list[int] = []

    def graph_factory() -> object:
        factory_calls.append(graph)
        return graph

    def fake_sync_once(db, graph_client, *, folder_names, limit):
        sync_graphs.append(graph_client)
        sync_limits.append(limit)
        return "success"

    monkeypatch.setattr("app.worker.sync_once", fake_sync_once)
    worker = SyncWorker(session_factory, graph_factory, lock=FileSyncLock(tmp_path / "sync.lock"))

    assert worker.run_once() == "success"
    assert worker.run_once() == "success"
    assert factory_calls == [graph]
    assert sync_graphs == [graph, graph]
    assert sync_limits == [50, 50]


def _mother_mailbox(db, email: str, *, enabled: bool = True) -> MotherMailbox:
    mailbox = MotherMailbox(
        email_address=email,
        normalized_email=email.casefold(),
        client_id=f"client-{email}",
        authority="consumers",
        auth_method="manual",
        enabled=enabled,
    )
    db.add(mailbox)
    db.flush()
    return mailbox


def test_fixed_trigger_gate_uses_fixed_points_and_discards_missed_points() -> None:
    gate = FixedTriggerGate(interval=30, start=0.0)

    assert gate.poll(0.0) == [0.0]
    assert gate.poll(35.0) == [30.0]
    assert gate.poll(60.0) == [60.0]
    assert gate.poll(95.0) == [90.0]
    assert gate.next_due == 120.0


def test_worker_cycle_processes_all_mailboxes_in_order_with_independent_limits(tmp_path: Path, monkeypatch) -> None:
    engine = create_engine_for_tests()
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    db = session_factory()
    first = _mother_mailbox(db, "a@example.com")
    second = _mother_mailbox(db, "b@example.com")
    third = _mother_mailbox(db, "c@example.com")
    db.commit()
    db.close()

    calls: list[tuple[int, int]] = []
    graphs: list[int] = []

    def graph_factory(mailbox: MotherMailbox) -> object:
        graphs.append(mailbox.id)
        return mailbox.id

    def fake_sync_once(db, graph_client, *, folder_names, limit, mother_mailbox_id, cycle_id):
        calls.append((mother_mailbox_id, limit))
        return SimpleNamespace(status="success", inserted_count=0, updated_count=0, duplicate_count=0)

    monkeypatch.setattr("app.worker.sync_once", fake_sync_once)
    worker = SyncWorker(session_factory, graph_factory, lock=FileSyncLock(tmp_path / "sync.lock"))

    cycle = worker.run_cycle(triggered_at=datetime(2026, 1, 1))

    assert [mailbox_id for mailbox_id, _ in calls] == [first.id, second.id, third.id]
    assert [limit for _, limit in calls] == [50, 50, 50]
    assert graphs == [first.id, second.id, third.id]
    assert cycle.status == "success"
    assert cycle.finished_at is not None


def test_worker_cycle_continues_after_one_mailbox_failure(tmp_path: Path, monkeypatch) -> None:
    engine = create_engine_for_tests()
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    db = session_factory()
    first = _mother_mailbox(db, "a@example.com")
    second = _mother_mailbox(db, "b@example.com")
    third = _mother_mailbox(db, "c@example.com")
    db.commit()
    db.close()

    calls: list[int] = []

    def graph_factory(mailbox: MotherMailbox) -> object:
        return mailbox.id

    def fake_sync_once(db, graph_client, *, folder_names, limit, mother_mailbox_id, cycle_id):
        calls.append(mother_mailbox_id)
        if mother_mailbox_id == second.id:
            raise RuntimeError("B failed")
        return SimpleNamespace(status="success", inserted_count=0, updated_count=0, duplicate_count=0)

    monkeypatch.setattr("app.worker.sync_once", fake_sync_once)
    worker = SyncWorker(session_factory, graph_factory, lock=FileSyncLock(tmp_path / "sync.lock"))

    cycle = worker.run_cycle(triggered_at=datetime(2026, 1, 1))

    assert calls == [first.id, second.id, third.id]
    assert cycle.status == "partial"
    assert cycle.completed_mailbox_count == 2
    assert cycle.failed_mailbox_count == 1


def test_skipped_trigger_creates_no_cycle_or_mailbox_runs(tmp_path: Path) -> None:
    engine = create_engine_for_tests()
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    db = session_factory()
    _mother_mailbox(db, "a@example.com")
    db.commit()
    db.close()

    worker = SyncWorker(session_factory, lambda mailbox: object(), lock=FileSyncLock(tmp_path / "sync.lock"))
    worker._cycle_running = True

    trigger = worker.record_skipped_trigger(datetime(2026, 1, 1), "cycle_already_running")

    assert trigger.status == "skipped"
    assert trigger.skip_reason == "cycle_already_running"
    db = session_factory()
    try:
        assert db.query(SyncCycle).count() == 0
    finally:
        db.close()
