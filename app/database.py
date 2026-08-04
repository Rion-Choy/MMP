from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


def _enable_sqlite_pragmas(dbapi_connection, connection_record) -> None:
    del connection_record
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


def create_engine_for_url(url: str, *, testing: bool = False) -> Engine:
    connect_args: dict[str, object] = {}
    kwargs: dict[str, object] = {"future": True}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        if testing and ":memory:" in url:
            kwargs["poolclass"] = StaticPool
    engine = create_engine(url, connect_args=connect_args, **kwargs)
    if engine.dialect.name == "sqlite":
        event.listen(engine, "connect", _enable_sqlite_pragmas)
    return engine


def create_engine_for_tests() -> Engine:
    return create_engine_for_url("sqlite+pysqlite:///:memory:", testing=True)


def ensure_database_parent(url: str) -> None:
    if url.startswith("sqlite") and "///" in url:
        raw_path = url.split("///", 1)[1].split("?", 1)[0]
        if raw_path and raw_path != ":memory:":
            Path(raw_path).expanduser().parent.mkdir(parents=True, exist_ok=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def get_db(session_factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    db = session_factory()
    try:
        yield db
    finally:
        db.close()
