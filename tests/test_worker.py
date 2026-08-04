from __future__ import annotations

from pathlib import Path

from app.database import Base, create_engine_for_tests, make_session_factory
from app.worker import FileSyncLock
from app.worker import SyncWorker
from app.services.settings_service import set_setting


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
