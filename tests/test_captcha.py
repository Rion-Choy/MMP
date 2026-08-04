from __future__ import annotations

import re

from app.services.captcha import generate_captcha_answer, render_captcha_png


def test_captcha_answer_is_four_alphanumeric_characters() -> None:
    answer = generate_captcha_answer()

    assert len(answer) == 4
    assert re.fullmatch(r"[A-Za-z0-9]{4}", answer)


def test_captcha_png_does_not_embed_plain_answer() -> None:
    answer = "Ab3d"
    image = render_captcha_png(answer)

    assert image.startswith(b"\x89PNG\r\n\x1a\n")
    assert answer.encode() not in image
