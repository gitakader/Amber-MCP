"""Make a captured screen safe to commit.

Selectors are the product of discovery; the account is not. Anything that
identifies a person, a number or a balance is replaced while resource-ids,
class names, bounds and activity names are preserved exactly.
"""
from __future__ import annotations

import re
from typing import Sequence

NUMBER_TOKEN = "<number>"
BALANCE_TOKEN = "<balance>"
NAME_TOKEN = "<name>"

# BD mobile shapes: 01[3-9]xxxxxxxx, and the +880 form. Deliberately broad —
# over-redacting costs nothing here, under-redacting publishes someone's number.
_BD_NUMBER = re.compile(r"(?<!\d)(?:\+?880|0)1[3-9]\d{8}(?!\d)")
_OTHER_LONG_DIGIT_RUN = re.compile(r'(?<![\d"])(?:[0-9][\d -]{7,}\d)(?![\d"])')
_BALANCE = re.compile(r"[৳$]\s?[\d,]+(?:\.\d+)?")
_ATTR_TEXT = re.compile(r'\b(text|content-desc)="([^"]*)"')


def text(value: str, secrets: Sequence[str] = ()) -> str:
    if not value:
        return value

    for secret in sorted({s for s in secrets if s}, key=len, reverse=True):
        value = value.replace(secret, NUMBER_TOKEN if any(c.isdigit() for c in secret) else NAME_TOKEN)

    value = _BD_NUMBER.sub(NUMBER_TOKEN, value)
    value = _BALANCE.sub(BALANCE_TOKEN, value)

    def scrub(match):
        attr, content = match.group(1), match.group(2)
        if _BD_NUMBER.search(content) or _OTHER_LONG_DIGIT_RUN.search(content):
            return f'{attr}="{NUMBER_TOKEN}"'
        return match.group(0)

    return _ATTR_TEXT.sub(scrub, value)
