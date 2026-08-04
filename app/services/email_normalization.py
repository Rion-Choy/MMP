from __future__ import annotations

import re
from collections.abc import Mapping


_EMAIL_RE = re.compile(r"^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$")
_DISPLAY_NAME_RE = re.compile(r"<([^<>]+)>")


def normalize_email_address(value: str) -> str:
    """Return a lowercase, exact-matchable email address."""
    if not isinstance(value, str):
        raise ValueError("email address must be a string")
    candidate = value.strip()
    match = _DISPLAY_NAME_RE.search(candidate)
    if match:
        candidate = match.group(1).strip()
    candidate = candidate.casefold()
    if not _EMAIL_RE.fullmatch(candidate):
        raise ValueError("invalid email address")
    return candidate


def parse_graph_recipient(value: Mapping[str, object]) -> str:
    """Extract and normalize a Microsoft Graph recipient object."""
    email_address = value.get("emailAddress")
    if not isinstance(email_address, Mapping):
        raise ValueError("recipient has no emailAddress object")
    address = email_address.get("address")
    if not isinstance(address, str):
        raise ValueError("recipient has no address")
    return normalize_email_address(address)


def parse_recipient_list(values: object) -> list[str]:
    """Parse a Graph recipient list, ignoring malformed entries."""
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        try:
            if isinstance(value, Mapping):
                address = parse_graph_recipient(value)
            elif isinstance(value, str):
                address = normalize_email_address(value)
            else:
                continue
        except ValueError:
            continue
        if address not in result:
            result.append(address)
    return result
