def test_database_tables_are_created() -> None:
    from app.database import Base, create_engine_for_tests
    from app import models  # noqa: F401 - register ORM models

    engine = create_engine_for_tests()
    Base.metadata.create_all(engine)
    table_names = set(Base.metadata.tables)

    assert {"private_targets", "mail_messages", "mail_recipients", "public_sessions", "app_settings"} <= table_names
