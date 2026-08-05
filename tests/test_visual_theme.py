from pathlib import Path


TEMPLATES = [
    "app/templates/public/captcha.html",
    "app/templates/public/messages.html",
    "app/templates/admin/login.html",
    "app/templates/admin/dashboard.html",
    "app/templates/admin/targets.html",
    "app/templates/admin/mother_mailbox.html",
    "app/templates/admin/settings.html",
    "app/templates/admin/messages.html",
]


def test_all_pages_use_shared_visual_theme() -> None:
    for relative in TEMPLATES:
        text = Path(relative).read_text(encoding="utf-8")
        assert "/static/style.css" in text, relative
        assert "glass-panel" in text or "page-shell" in text, relative


def test_theme_defines_reference_palette_and_responsive_layout() -> None:
    css = Path("app/static/style.css").read_text(encoding="utf-8")

    for token in ("--bg-base", "--accent", "--glass-bg", "--radius-lg"):
        assert token in css
    assert ".glass-panel" in css
    assert "@media" in css


def test_matched_mailbox_display_is_larger_than_small_metadata_text() -> None:
    css = Path("app/static/style.css").read_text(encoding="utf-8")

    mailbox_rule = css.split(".matched-mailbox {", 1)[1].split("}", 1)[0]
    strong_rule = css.split(".matched-mailbox strong {", 1)[1].split("}", 1)[0]

    assert "font-size: .86rem" in mailbox_rule
    assert "font-size: .94rem" in strong_rule
