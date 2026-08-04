from __future__ import annotations

from app.services.email_normalization import normalize_email_address, parse_graph_recipient


def test_normalize_email_is_case_insensitive_and_trimmed() -> None:
    assert normalize_email_address("  Private@Example.COM ") == "private@example.com"


def test_parse_graph_recipient_supports_display_name() -> None:
    value = {"emailAddress": {"name": "Private", "address": "PRIVATE@example.com"}}

    assert parse_graph_recipient(value) == "private@example.com"


def test_invalid_email_is_rejected() -> None:
    import pytest

    with pytest.raises(ValueError):
        normalize_email_address("not-an-email")
