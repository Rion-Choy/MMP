from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import AppSetting
from app.services.settings_service import (
    get_captcha_enabled,
    get_sync_interval,
    set_captcha_enabled,
    set_sync_interval,
)


def test_captcha_enabled_defaults_to_true() -> None:
    from app.database import Base, create_engine_for_tests, make_session_factory

    engine = create_engine_for_tests()
    Base.metadata.create_all(engine)
    db = make_session_factory(engine)()

    assert get_captcha_enabled(db) is True


def test_captcha_enabled_can_be_changed() -> None:
    from app.database import Base, create_engine_for_tests, make_session_factory

    engine = create_engine_for_tests()
    Base.metadata.create_all(engine)
    db = make_session_factory(engine)()

    set_captcha_enabled(db, False)
    db.commit()

    assert get_captcha_enabled(db) is False
    assert db.scalar(select(AppSetting).where(AppSetting.setting_key == "captcha_enabled")) is not None


def test_sync_interval_defaults_to_thirty_seconds() -> None:
    from app.database import Base, create_engine_for_tests, make_session_factory

    engine = create_engine_for_tests()
    Base.metadata.create_all(engine)
    db = make_session_factory(engine)()

    assert get_sync_interval(db) == 30


def test_sync_interval_can_be_updated_within_bounds() -> None:
    from app.database import Base, create_engine_for_tests, make_session_factory

    engine = create_engine_for_tests()
    Base.metadata.create_all(engine)
    db = make_session_factory(engine)()

    set_sync_interval(db, 10)
    db.commit()

    assert get_sync_interval(db) == 10
    assert db.scalar(select(AppSetting).where(AppSetting.setting_key == "sync_interval_seconds")) is not None


@pytest.mark.parametrize("value", [0, 9, 86401, -1])
def test_sync_interval_rejects_out_of_bounds(value: int) -> None:
    from app.database import Base, create_engine_for_tests, make_session_factory

    engine = create_engine_for_tests()
    Base.metadata.create_all(engine)
    db = make_session_factory(engine)()

    with pytest.raises(ValueError):
        set_sync_interval(db, value)
