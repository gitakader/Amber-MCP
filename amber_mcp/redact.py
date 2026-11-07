"""Make a captured screen safe to commit.

Selectors are the product of discovery; the account is not. Anything that
identifies a person, a number or a balance is replaced while resource-ids,
class names, bounds and activity names are preserved exactly.

Two layers, deliberately apart, because they fail differently:

* the ``secrets`` layer -- exact strings the caller already knows (a declared
  PIN, a captured contact name), replaced anywhere in the document;
* the pattern layer -- shapes. BD mobile numbers are substituted bare, since no
  selector can look like one; money is scrubbed only inside ``text=`` and
  ``content-desc=`` values, which is what keeps the shape of
  ``bounds="[0,50][100,90]"`` usable as a selector.

Two entry points, because the output has two destinations: ``text()`` for
anything that is only ever read, and ``xml()`` for a document that is going to
be parsed again -- see its docstring for why the tokens have to be escaped.
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
# An amount with no glyph on it: "4,250.75", "1,250,000.50". Android renders the
# currency symbol as a sibling TextView often enough that requiring the glyph
# leaves the balance — the single most sensitive field Task 6 captures — in the
# clear. Thousands separator plus a two-digit fraction is the shape; applied only
# inside the attributes that can hold user-visible text, so it can never reach a
# selector. Over-redacting a version number costs a token; under-redacting a
# balance costs someone their account.
_MONEY_RUN = re.compile(r"\d{1,3}(?:[,.]\d{3})*[,.]\d{2}\b")


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
        if _MONEY_RUN.search(content):
            return f'{attr}="{BALANCE_TOKEN}"'
        return match.group(0)

    return _ATTR_TEXT.sub(scrub, value)


def xml(value: str, secrets: Sequence[str] = ()) -> str:
    """`text()`, escaped for a document that is going to be parsed again.

    The tokens are `<number>`, `<balance>` and `<name>`. They are harmless as
    prose and malformed as an XML attribute value: a node whose `text=` is a
    Bangladeshi mobile number redacts to `text="<number>"`, and ElementTree then
    refuses the *whole* hierarchy -- so a committed capture would be readable by
    a human and useless to plan 2, which is the half of this pipeline that has to
    work. Escaping the three tokens on the way out fixes it, and any parser
    resolves them back to the same string. uiautomator emits well-formed XML, so
    a literal `<` inside an attribute value can only have come from a token and
    no real text is touched.

    The escaping deliberately runs *after* redaction, and the order is
    load-bearing: applied afterwards it can only ever add characters to output
    that has already been scrubbed, so it cannot break up a number-shaped run and
    erase a match. Escaping first would show the patterns `&lt;` and `&gt;` where
    they expect `<number>`, and the redactor would quietly stop matching without
    raising anything. `text()` is left exactly as it was for non-XML callers.
    """
    value = text(value, secrets)
    for token in (NUMBER_TOKEN, BALANCE_TOKEN, NAME_TOKEN):
        value = value.replace(token, token.replace("<", "&lt;").replace(">", "&gt;"))
    return value
