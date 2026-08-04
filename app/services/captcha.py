from __future__ import annotations

import html
import io
import secrets
from collections.abc import Mapping

from PIL import Image, ImageDraw, ImageFont

from app.services.instance_secrets import secret_mac

# Exclude visually ambiguous characters while retaining letters and digits.
CAPTCHA_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"


def generate_captcha_answer(length: int = 4) -> str:
    if length != 4:
        raise ValueError("captcha length must be four")
    return "".join(secrets.choice(CAPTCHA_ALPHABET) for _ in range(length))


def captcha_answer_mac(secret: str, answer: str) -> str:
    return secret_mac(secret, answer.casefold())


def verify_captcha_answer(secret: str, expected_mac: str, answer: str) -> bool:
    return secrets.compare_digest(expected_mac, captcha_answer_mac(secret, answer.strip()))


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationMono-Bold.ttf",
    )
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def render_captcha_png(answer: str) -> bytes:
    """Render a local PNG challenge without putting answer text in metadata."""
    if len(answer) != 4:
        raise ValueError("captcha answer must have four characters")
    image = Image.new("RGB", (180, 60), (244, 244, 245))
    draw = ImageDraw.Draw(image)
    for _ in range(10):
        draw.line(
            (secrets.randbelow(180), secrets.randbelow(60), secrets.randbelow(180), secrets.randbelow(60)),
            fill=(156, 163, 175),
            width=1,
        )
    font = _font(28)
    for index, character in enumerate(answer):
        x = 20 + index * 38 + secrets.randbelow(5)
        y = 12 + secrets.randbelow(8)
        draw.text((x, y), character, font=font, fill=(23, 32, 51))
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def render_captcha_svg(answer: str) -> str:
    """Render a deliberately simple, local-only SVG challenge."""
    safe_answer = html.escape(answer, quote=True)
    lines = []
    for _ in range(7):
        x1 = secrets.randbelow(180)
        y1 = secrets.randbelow(60)
        x2 = secrets.randbelow(180)
        y2 = secrets.randbelow(60)
        lines.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" />')
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="180" height="60" '
        'viewBox="0 0 180 60" role="img" aria-label="验证码">'
        '<rect width="180" height="60" fill="#f4f4f5" />'
        '<g stroke="#9ca3af" stroke-width="1">'
        + "".join(lines)
        + '</g><text x="90" y="40" text-anchor="middle" '
        'font-family="monospace" font-size="28" font-weight="700" letter-spacing="5" '
        'fill="#172033" transform="rotate(-2 90 30)">' 
        + safe_answer
        + "</text></svg>"
    )
