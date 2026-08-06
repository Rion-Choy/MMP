from __future__ import annotations

from datetime import datetime

from app.models import MailFolder, MailMessage, MotherMailbox, SyncRun
from app.services.mail_sync import _pending_message_path, _select_provider_folders, sync_once


def test_pending_message_filter_uses_graph_datetimeoffset_literal_without_string_quotes() -> None:
    path = _pending_message_path("inbox", datetime(2026, 1, 1), 50)

    assert "receivedDateTime%20ge%202026-01-01T00%3A00%3A00.000Z" in path
    assert "receivedDateTime%20ge%20'2026-01-01T00%3A00%3A00.000Z'" not in path


class FakeGraph:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def iter_collection(self, path: str):
        self.calls.append(path)
        if path == "/me/mailFolders":
            return iter([
                {"id": "inbox", "displayName": "Inbox", "childFolderCount": 0},
                {"id": "junk", "displayName": "Junk Email", "childFolderCount": 0},
            ])
        if "/messages" in path:
            folder = path.split("/")[3]
            return iter([
                {
                    "id": f"{folder}-1",
                    "receivedDateTime": "2026-01-01T12:00:00Z",
                    "body": {"contentType": "text", "content": folder},
                    "toRecipients": [{"emailAddress": {"address": "private@example.com"}}],
                    "ccRecipients": [],
                }
            ])
        raise AssertionError(path)

    def get(self, path: str):
        self.calls.append(path)
        return {"id": "me", "mail": "mother@example.com"}


def test_folder_selection_matches_common_localized_graph_names_without_falling_back_to_all() -> None:
    folders = [
        {"id": "inbox", "displayName": "收件箱"},
        {"id": "junk", "displayName": "垃圾邮件"},
        {"id": "archive", "displayName": "存档"},
    ]

    selected = _select_provider_folders(folders, ["Inbox", "Archive"])

    assert [folder["id"] for folder in selected] == ["inbox", "archive"]


def test_sync_scopes_same_provider_ids_to_different_mailboxes() -> None:
    from app.database import Base, create_engine_for_tests, make_session_factory

    engine = create_engine_for_tests()
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    db = factory()
    first = MotherMailbox(
        email_address="a@example.com",
        normalized_email="a@example.com",
        client_id="client-a",
        authority="consumers",
        auth_method="manual",
        enabled=True,
    )
    second = MotherMailbox(
        email_address="b@example.com",
        normalized_email="b@example.com",
        client_id="client-b",
        authority="consumers",
        auth_method="manual",
        enabled=True,
    )
    db.add_all([first, second])
    db.flush()
    graph = FakeGraph()

    first_run = sync_once(db, graph, mother_mailbox_id=first.id, limit=50)
    second_run = sync_once(db, graph, mother_mailbox_id=second.id, limit=50)

    assert first_run.inserted_count == 2
    assert second_run.inserted_count == 2
    assert db.query(MailMessage).count() == 4
    assert db.query(MailFolder).count() == 4
    assert {message.mother_mailbox_id for message in db.query(MailMessage).all()} == {first.id, second.id}


def test_each_mailbox_has_its_own_fifty_message_cap() -> None:
    from app.database import Base, create_engine_for_tests, make_session_factory

    class ManyMessages(FakeGraph):
        def iter_collection(self, path: str):
            if "/messages" in path:
                folder = path.split("/")[3]
                return iter(
                    {
                        "id": f"{folder}-{index}",
                        "receivedDateTime": f"2026-01-01T12:{index:02d}:00Z",
                        "body": {"contentType": "text", "content": f"body-{folder}-{index}"},
                        "toRecipients": [{"emailAddress": {"address": "private@example.com"}}],
                        "ccRecipients": [],
                    }
                    for index in range(80)
                )
            return super().iter_collection(path)

    engine = create_engine_for_tests()
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    db = factory()
    first = MotherMailbox(
        email_address="a@example.com",
        normalized_email="a@example.com",
        client_id="client-a",
        authority="consumers",
        auth_method="manual",
        enabled=True,
    )
    second = MotherMailbox(
        email_address="b@example.com",
        normalized_email="b@example.com",
        client_id="client-b",
        authority="consumers",
        auth_method="manual",
        enabled=True,
    )
    db.add_all([first, second])
    db.flush()
    first_run = sync_once(db, ManyMessages(), mother_mailbox_id=first.id, limit=50)
    second_run = sync_once(db, ManyMessages(), mother_mailbox_id=second.id, limit=50)

    assert first_run.unique_count == 50
    assert second_run.unique_count == 50
    assert db.query(MailMessage).count() == 100


def _seed_folder_cursors(db) -> None:
    baseline = datetime(2025, 12, 31, 23, 59)
    db.add_all(
        [
            MailMessage(
                immutable_message_id="seed-inbox",
                received_at=baseline,
                body_text="seed",
                folder_name="Inbox",
                first_archived_at=baseline,
                last_seen_at=baseline,
            ),
            MailMessage(
                immutable_message_id="seed-junk",
                received_at=baseline,
                body_text="seed",
                folder_name="Junk Email",
                first_archived_at=baseline,
                last_seen_at=baseline,
            ),
        ]
    )
    db.commit()


def test_sync_uses_folder_cursors_and_processes_oldest_pending_messages_in_global_batches() -> None:
    from app.database import Base, create_engine_for_tests, make_session_factory

    class IncrementalGraph(FakeGraph):
        def __init__(self) -> None:
            super().__init__()
            self.message_calls: list[str] = []

        def iter_collection(self, path: str):
            if "/messages" in path:
                self.message_calls.append(path)
                folder = path.split("/")[3]
                values = [
                    {
                        "id": f"{folder}-{index}",
                        "receivedDateTime": f"2026-01-01T{12 if folder == 'inbox' else 13}:{index:02d}:00Z",
                        "body": {"contentType": "text", "content": f"{folder}-{index}"},
                        "toRecipients": [{"emailAddress": {"address": "private@example.com"}}],
                        "ccRecipients": [],
                    }
                    for index in range(40)
                ]
                return iter(values)
            return super().iter_collection(path)

    engine = create_engine_for_tests()
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    db = factory()
    _seed_folder_cursors(db)
    graph = IncrementalGraph()

    first = sync_once(db, graph, limit=50)
    first_ids = {message.immutable_message_id for message in db.query(MailMessage).all()}
    first_folders = {folder.provider_folder_id: folder for folder in db.query(MailFolder).all()}

    assert first.inserted_count == 50
    assert len(first_ids) == 52
    assert first_folders["inbox"].last_message_received_at is not None
    assert first_folders["junk"].last_message_received_at is not None

    second = sync_once(db, graph, limit=50)
    second_ids = {message.immutable_message_id for message in db.query(MailMessage).all()}

    assert second.inserted_count == 30
    assert second.unique_count == 30
    assert len(second_ids) == 82
    assert all("$filter=receivedDateTime" in call for call in graph.message_calls)
    assert all("$orderby=receivedDateTime%20asc" in call for call in graph.message_calls)


def test_sync_once_persists_messages_and_does_not_reprocess_cursor_boundary() -> None:
    from app.database import Base, create_engine_for_tests, make_session_factory

    engine = create_engine_for_tests()
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    db = factory()
    _seed_folder_cursors(db)

    graph = FakeGraph()
    first = sync_once(db, graph)
    second = sync_once(db, graph)

    assert first.inserted_count == 2
    assert second.inserted_count == 0
    assert second.duplicate_count == 0
    assert second.unique_count == 0
    assert db.query(MailMessage).count() == 4
    assert db.query(SyncRun).count() == 2
