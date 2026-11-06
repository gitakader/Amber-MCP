"""Turn the app's real screens into files on disk.

Nothing here knows what a dialpad is. It walks, dumps, redacts and writes — and
the result is the only honest source for the selector table in plan 2. Raw,
un-redacted captures stay in artifacts/raw/, which git is proven to ignore.

Two rules hold this module together, and both come from the way the plan's draft
failed:

1. **The raw copy is only safe while it is inside the ignored directory.** It
   holds the account, the contacts and the call log exactly as the screen shows
   them, so its location is load-bearing: `out_dir/raw/<name>/`. Put it one
   level up and `git check-ignore` stops matching it, which is how un-redacted
   reality ends up one careless `git add -A` away from being published.
2. **A capture that could not be taken is not a blank screen.** `dump_hierarchy`
   raises straight through, and the signals file records a refused command as
   refused rather than as an empty answer.

This module performs no taps, types nothing and places no call: `capture()` is
read-only against the handset by construction. Deciding that the phone is *ready*
to be read belongs to `session.ensure_ready` and to the caller, not here — so a
caller who only wants to look at whatever is on screen cannot be routed into
waking, unlocking or launching anything.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from amber_mcp import config, redact

# Where plan 2 writes the table this harness produces.
#
# Constraint to keep in mind before that happens: this one path is derived from
# `config.PROJECT_ROOT`, which is `parent.parent` of the package. That is
# repo-root-correct under `uv run` (how everything here is launched) and wrong
# under a real wheel install, where it would point inside site-packages. So
# nothing here may take a default output path from `__file__`: `capture()` is
# given an explicit `out_dir` and defaults it to `config.artifacts_dir()`, and
# only that default is allowed to carry the ambiguity. Fixing PROJECT_ROOT itself
# is a config concern, not a discovery one.
SELECTORS_DOC = config.PROJECT_ROOT / "artifacts" / "SELECTORS.md"

# The spec's own list of places that might tell an IP call apart from a cellular
# one. The call-log query is here precisely because it is doubted: `content://call_log`
# is the cellular provider's record, and whether an AmberIT call writes a row to
# it is an open question this harness exists to settle rather than assume.
SIGNAL_COMMANDS = [
    "dumpsys window | grep mCurrentFocus",
    "dumpsys audio",
    "dumpsys notification --noredact",
    "dumpsys power | grep mWakefulness",
    "dumpsys telephony.registry | grep mCallState",
    "content query --uri content://call_log/calls --projection number:date --sort 'date DESC'",
]

_CLICKABLE = ("true", "1")


@dataclass
class Screen:
    name: str
    focus: str
    resource_ids: list[str] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    interactive: list[str] = field(default_factory=list)


def _bounds(node) -> tuple[int, int, int, int] | None:
    """`[left,top][right,bottom]`, parsed by hand.

    The shape is fixed by Android and short enough that a regex is the longer
    answer. Anything that does not parse returns None rather than a guess: an
    off-by-one centre here is a tap on the wrong widget, so a bounds we cannot
    read is a bounds nobody may tap.
    """
    raw = (node.get("bounds") or "").strip()
    if not (raw.startswith("[") and raw.count("[") == 2):
        return None
    try:
        left_top, right_bottom = raw[1:-1].split("][")
        left, top = (int(v) for v in left_top.split(","))
        right, bottom = (int(v) for v in right_bottom.split(","))
    except ValueError:
        return None
    return left, top, right, bottom


def interactive(xml: str) -> list[tuple[str, int, int]]:
    """Every clickable thing on the screen, as `(label, x, y)`.

    The centre is `(left + right) // 2, (top + bottom) // 2` — the same
    arithmetic Android-MCP uses and plan 2's flows will use, so a target found
    here plugs into a tap without conversion.
    """
    out: list[tuple[str, int, int]] = []
    for node in ET.fromstring(xml).iter("node"):
        if node.get("clickable") not in _CLICKABLE:
            continue
        box = _bounds(node)
        if box is None:
            continue
        label = node.get("text") or node.get("content-desc") or node.get("resource-id") or ""
        if not label:
            continue
        out.append((label, (box[0] + box[2]) // 2, (box[1] + box[3]) // 2))
    return out


def screen_names(xml: str) -> list[str]:
    """The resource-ids on a screen, sorted, deduplicated.

    Named for what it gives a selector table: the stable identifiers a flow can
    address, which is the only part of a capture that survives a translation or
    a redesign.
    """
    return sorted({n.get("resource-id") for n in ET.fromstring(xml).iter("node")
                   if n.get("resource-id")})


def write_screen_png(clean_dir: Path, png: bytes, include: bool) -> None:
    """Put the picture in the committed copy, or make sure it is not.

    Pixels cannot be redacted: a screenshot of a contact list *is* a contact
    list, and one of the dashboard is the balance. So the default is that no
    image is committed at all, and an override that turns `include` on is a
    deliberate, per-screen, reviewed decision. The `unlink` half matters as much
    as the write half — a re-capture over an old directory must not leave a
    previously-committed screenshot behind.
    """
    target = clean_dir / "screen.png"
    if include:
        target.write_bytes(png)
    elif target.exists():
        target.unlink()


def _signal(driver, command: str) -> str:
    """One signal source, transcribed.

    A command that did not run is written down as having not run, with its rc.
    Silent absence and an empty answer are different facts, and a classifier
    built later from these files cannot tell them apart unless the difference is
    recorded here, at the only moment it is known.
    """
    result = driver.shell(command)
    out = (getattr(result, "out", "") or "").rstrip()
    if getattr(result, "ok", True):
        return f"$ {command}\n{out}"
    reason = (getattr(result, "err", "") or "").strip() or f"exit status {getattr(result, 'rc', '?')}"
    return f"$ {command}\n[did not run: {reason}]\n{out}"


def _xml_safe(value: str) -> str:
    """Escape the redaction tokens so a redacted dump is still a parseable one.

    `redact.text` substitutes the bare token `<number>`, which is exactly the
    wrong shape for an XML attribute: written straight into `hierarchy.xml` it
    yields `text="<number>"`, and ElementTree refuses the whole document. The
    committed capture would then be readable only by a human and not by plan 2,
    which is the half of this harness that matters. uiautomator emits
    well-formed XML, so a literal `<` inside an attribute value can only have
    come from a token — escaping the tokens is enough, and no real text is
    touched.

    Redaction is a security boundary, so this is applied *after* it and can only
    ever add characters to the output; it cannot undo a substitution.
    """
    for token in (redact.NUMBER_TOKEN, redact.BALANCE_TOKEN, redact.NAME_TOKEN):
        value = value.replace(token, token.replace("<", "&lt;").replace(">", "&gt;"))
    return value


def capture(driver, name: str, secrets: Sequence[str] = (), out_dir: Path | None = None,
            *, include_png: bool = False) -> Path:
    """Write one screen twice: redacted for the repo, raw for the debugging drawer.

    Returns the *committed* directory. `out_dir` defaults to
    `config.artifacts_dir()` — see the note on `SELECTORS_DOC` for why no
    `__file__`-derived default is allowed here.
    """
    out_dir = Path(out_dir if out_dir is not None else config.artifacts_dir())
    clean_dir = out_dir / name
    # Inside `out_dir`, never beside it: `artifacts/raw/` is the ignored path,
    # and `out_dir.parent / "raw"` would escape it.
    raw_dir = out_dir / "raw" / name
    clean_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    focus = driver.current_focus()
    # Raises if the read is broken — a screen nobody captured is never written
    # down as a blank one.
    xml = driver.dump_hierarchy()
    png = driver.screenshot()
    signals = "\n\n".join(_signal(driver, cmd) for cmd in SIGNAL_COMMANDS)

    (raw_dir / "hierarchy.xml").write_text(xml, encoding="utf-8")
    (raw_dir / "signals.txt").write_text(signals, encoding="utf-8")
    (raw_dir / "focus.txt").write_text(f"{name}\n{focus}\n", encoding="utf-8")
    (raw_dir / "screen.png").write_bytes(png)

    (clean_dir / "hierarchy.xml").write_text(_xml_safe(redact.text(xml, secrets)), encoding="utf-8")
    (clean_dir / "signals.txt").write_text(redact.text(signals, secrets), encoding="utf-8")
    (clean_dir / "focus.txt").write_text(f"{name}\n{focus}\n", encoding="utf-8")
    write_screen_png(clean_dir, png, include=include_png)

    return clean_dir


def summarise(clean_dir: Path) -> Screen:
    """Read a committed capture back as the record plan 2 works from.

    The screen's identity is the focused activity, not any text on it: AmberIT
    ships Bangla and English labels for the same button, and a flow that found
    its screen by reading a label breaks the moment the handset's language does.
    """
    xml = (clean_dir / "hierarchy.xml").read_text(encoding="utf-8")
    lines = (clean_dir / "focus.txt").read_text(encoding="utf-8").splitlines()
    focus = lines[1] if len(lines) > 1 else ""
    return Screen(
        name=clean_dir.name,
        focus=focus,
        resource_ids=screen_names(xml),
        texts=[n.get("text") for n in ET.fromstring(xml).iter("node") if n.get("text")],
        interactive=[f"{label} @ {x},{y}" for label, x, y in interactive(xml)],
    )
