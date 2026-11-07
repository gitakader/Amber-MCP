"""Turn the app's real screens into files on disk.

Nothing here knows what a dialpad is. It walks, dumps, redacts and writes — and
the result is the only honest source for the selector table in plan 2. Raw,
un-redacted captures stay in `raw/` under whatever scratch directory the caller
chose, which is outside the repository and never published.

Five rules hold this module together, and three of them come from the way the
plan's draft failed:

1. **A capture never writes inside the repository.** `out_dir` is refused with
   `ValueError` if it resolves at or under `config.PROJECT_ROOT`, before a
   directory is made or a command is issued. The raw copy holds real contact
   data and this repo is public, so "git will not add it" was never enough:
   ignored files still leave a laptop in zips, sync folders and copies.
2. **Only classifier lines are committed.** A whole `dumpsys` is not redactable
   into safety. `redact.text` has no model of prose, and ~52k timestamped
   `date=<epoch>` rows survive as a record of who was called, when and how
   often, which no rule in the gate can see. So the commands still all run, and
   `raw/` keeps every line of what they answered, while the committed
   `signals.txt` is built from `COMMITTED_SIGNAL_KEYS` alone -- a named constant,
   per command, empty where nothing may be published.
3. **A capture that could not be taken is not a blank screen.** `dump_hierarchy`
   raises straight through, and the signals file records a refused command as
   refused rather than as an empty answer.
4. **The harness is an observer and says so.** `capture()` does not call
   `session.ensure_ready()` and does not use `config.raw_dir`: asking to *look*
   at a phone must not wake, unlock or launch it, because promoting this harness
   to a driver would make the stray `input tap` class of accident permanent.
   That reasoning is enforced, not rhetorical -- `_refuse_a_writable_driver`
   turns away any driver exposing a write method by name.
5. **The UI tree is not publishable, redacted or not.** A screenshot is closed
   by its own default (`include_png=False`) and notification prose is closed by
   an empty allowlist tuple, but a `hierarchy.xml` used to be closed only by
   sentences in the evidence files: `redact` substitutes what it is handed, and
   `secrets` defaults to `()`, so a history row `text="Rabin Bose"` passed
   through byte-for-byte into the committed copy -- and the gate's three rules
   (LAN, BD phone number, PIN) do not match a name. So `include_hierarchy`
   defaults to False too: the committed copy holds no UI tree at all unless a
   named argument asks for one, and the gate now refuses a committed XML under
   `artifacts/` whose `text=`/`content-desc=` values are not redaction tokens.
   Raw keeps everything, unchanged, outside the repository.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from amber_mcp import config, redact

# Where plan 2 writes the table this harness produces.
#
# Constraint to keep in mind before that happens: this one path is derived from
# `config.PROJECT_ROOT`, which is `parent.parent` of the package. That is
# repo-root-correct under `uv run` (how everything here is launched) and wrong
# under a real wheel install, where it would point inside site-packages. So
# nothing here may take a default *output* path from `__file__`: `capture()` is
# given an explicit `out_dir` and defaults it to `config.scratch_dir()`, which is
# outside the repository by construction. A SELECTORS.md is a deliverable, not
# account data, so writing it at the repo root is the intent, not a leak.
SELECTORS_DOC = config.PROJECT_ROOT / "artifacts" / "SELECTORS.md"

# The spec's own list of places that might tell an IP call apart from a cellular
# one. The call-log query is here precisely because it is doubted: `content://call_log`
# is the cellular provider's record, and whether an AmberIT call writes a row to
# it is an open question this harness exists to settle rather than assume.
#
# R1 changes what is *published* from these, never whether they *run*: Task 7's
# whole purpose is to observe what each one says about an IP call, so the list
# and the six commands are exactly as they were. They are named as constants so
# the allowlist below can reference the same strings instead of duplicating them
# and drifting.
FOCUS_COMMAND = "dumpsys window | grep mCurrentFocus"
AUDIO_COMMAND = "dumpsys audio"
NOTIFICATION_COMMAND = "dumpsys notification --noredact"
POWER_COMMAND = "dumpsys power | grep mWakefulness"
TELEPHONY_COMMAND = "dumpsys telephony.registry | grep mCallState"
CALL_LOG_COMMAND = ("content query --uri content://call_log/calls "
                    "--projection number:date --sort 'date DESC'")

SIGNAL_COMMANDS = [
    FOCUS_COMMAND,
    AUDIO_COMMAND,
    NOTIFICATION_COMMAND,
    POWER_COMMAND,
    TELEPHONY_COMMAND,
    CALL_LOG_COMMAND,
]

# What of each dump may reach a committed `signals.txt`: a line is published only
# if it contains one of that command's keys. Data, deliberately, not a regex in
# the middle of a join -- the whole question is answerable by reading this table,
# and a test can read it back.
#
# Keyed *per command*, because a global list of tokens would let `MODE_` fish a
# line out of the notification dump that the notification block was never allowed
# to publish. A command mapped to `()` contributes nothing at all, and a command
# absent from the table contributes nothing either: an unrecognised block is
# treated as unpublished, not as harmless.
#
# `dumpsys notification --noredact` is dropped whole. Its message prose reaches
# the copy, `redact.text` has no model of prose, and the committed file must not
# contain notification content in any form -- so `--noredact` stays in the
# command (raw has to show Task 7 what the notification state really is) and out
# of the publishable one.
COMMITTED_SIGNAL_KEYS: dict[str, tuple[str, ...]] = {
    FOCUS_COMMAND: ("mCurrentFocus=",),
    # Current state only. `dumpsys audio` also carries a timestamped `setMode(...)`
    # history -- 20 lines on this handset -- naming which app asked for each mode,
    # so "MODE_" as a key quietly published every WhatsApp and cellular call the
    # user made, with times. Raw keeps the history; the publishable copy may not.
    AUDIO_COMMAND: ("Requested mode", "Actual mode"),
    NOTIFICATION_COMMAND: (),
    POWER_COMMAND: ("mWakefulness=",),
    TELEPHONY_COMMAND: ("mCallState=",),
    CALL_LOG_COMMAND: (),
}

# The call-log query is not filtered but replaced: every row is one number and one
# millisecond timestamp, for every call the handset remembers, so the committed
# copy carries a row count and the span of *days* the rows cover instead.
SUMMARISED_COMMANDS: tuple[str, ...] = (CALL_LOG_COMMAND,)

# The write surface, by name. A harness that is only ever supposed to look must
# refuse a driver that can act, rather than trusting the caller to hand it a
# polite one -- and checking names, not `isinstance`, keeps a future read-only
# driver usable while a `Driver` is not.
DRIVER_WRITE_METHODS: tuple[str, ...] = ("tap_xy", "keyevent", "type_text", "type_digits")

_CLICKABLE = ("true", "1")
_CALL_LOG_DATE = re.compile(r"\bdate=(\d+)")

# The one name both the writer and the reader of a capture's UI tree use, so a
# default capture that withholds it and a `summarise()` that expects it cannot
# drift apart on a spelling.
HIERARCHY_NAME = "hierarchy.xml"


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


def write_committed_hierarchy(clean_dir: Path, xml: str, secrets, include: bool) -> None:
    """Put the redacted UI tree in the committed copy — normally nowhere.

    The same shape as `write_screen_png`, for the same reason, which the last
    review round found missing: redaction only substitutes what it is handed, and
    `capture(secrets=())` hands it nothing, so every contact name in a history
    row survived into `clean_dir/hierarchy.xml` verbatim. A name is not a LAN
    address, not a BD number and not a PIN, so the secret gate's three rules
    stayed green over a published contact list. The redaction cannot be made to
    catch names (there is no model of names, and a name-shaped string is also a
    button label), so the default is that the tree never enters the committed
    directory; `include_hierarchy=True` is a per-screen, reviewed override, and
    the `unlink` half means a re-capture without it removes a tree an earlier run
    put there. Selectors are read from `raw/` in the scratch directory, which is
    outside the repository and holds reality un-redacted.
    """
    target = clean_dir / HIERARCHY_NAME
    if include:
        target.write_text(redact.xml(xml, secrets), encoding="utf-8")
    elif target.exists():
        target.unlink()


@dataclass(frozen=True)
class _Signal:
    """One command's answer, kept in three pieces so both copies can be built.

    The raw copy wants the whole text, the committed copy wants the classifier
    lines out of it, and a classifier cannot be filtered after the blocks have
    been joined — by then the command that produced a line is gone. So the join
    happens twice, from this.
    """

    command: str
    ok: bool
    out: str
    reason: str = ""


def _signal(driver, command: str) -> _Signal:
    """One signal source, transcribed.

    A command that did not run is written down as having not run, with its rc.
    Silent absence and an empty answer are different facts, and a classifier
    built later from these files cannot tell them apart unless the difference is
    recorded here, at the only moment it is known.
    """
    result = driver.shell(command)
    out = (getattr(result, "out", "") or "").rstrip()
    ok = bool(getattr(result, "ok", True))
    reason = ""
    if not ok:
        reason = ((getattr(result, "err", "") or "").strip()
                  or f"exit status {getattr(result, 'rc', '?')}")
    return _Signal(command=command, ok=ok, out=out, reason=reason)


def _block(command: str, body: str) -> str:
    """`$ <command>` and what came back, with the refusal recorded if there was one."""
    if body:
        return f"$ {command}\n{body}"
    return f"$ {command}\n"


def _raw_signals(signals: Sequence[_Signal]) -> str:
    """Every line every command answered. Un-redacted, by design: this is the
    drawer the user keeps to debug with, and R1 narrowed what leaves it, not what
    is in it."""
    return "\n\n".join(
        _block(sig.command,
               sig.out if sig.ok else f"[did not run: {sig.reason}]\n{sig.out}")
        for sig in signals
    )


def _day(epoch_ms: int) -> str:
    """An epoch in milliseconds, as a day. UTC, so the answer is not whichever
    machine read the file. `date` in `content://call_log` is stored in
    milliseconds; a value read as seconds lands in 1970 and is visibly wrong."""
    return datetime.fromtimestamp(epoch_ms // 1000, timezone.utc).date().isoformat()


def _call_log_summary(sig: _Signal) -> str:
    """Two values instead of a row per call: how many, and over what days.

    Redacting the rows does not make them publishable. `51,768 numbers` become
    `51,768 <number>` tokens sitting on `date=<epoch>` timestamps, and the result
    still says who was called, when and how often — while matching no rule in the
    gate, because a timestamp is not a phone number and prose is not a pattern.
    """
    rows = [line for line in sig.out.splitlines() if "date=" in line]
    epochs = [int(found.group(1)) for line in rows if (found := _CALL_LOG_DATE.search(line))]
    if not epochs:
        return f"call-log: rows={len(rows)}"
    return (f"call-log: rows={len(rows)} oldest_day={_day(min(epochs))} "
            f"newest_day={_day(max(epochs))} (utc)")


def _committed_body(sig: _Signal) -> str:
    """Only what the allowlist permits from one command's dump.

    Anything not in the table -- a new signal source, a command whose spelling
    changed -- yields nothing here. That is the safe direction to be wrong in.
    """
    if sig.command in SUMMARISED_COMMANDS:
        return _call_log_summary(sig)
    keys = COMMITTED_SIGNAL_KEYS.get(sig.command, ())
    return "\n".join(
        line.strip() for line in sig.out.splitlines() if any(key in line for key in keys))


def _committed_signals(signals: Sequence[_Signal], secrets: Sequence[str]) -> str:
    """The publishable `signals.txt`: the classifier lines, still redacted.

    Redaction is applied to the filtered remainder and not instead of it. A
    classifier line can carry a number the patterns would catch, and defence in
    depth costs nothing here.
    """
    blocks = []
    for sig in signals:
        body = _committed_body(sig)
        if not sig.ok:
            body = f"[did not run: {sig.reason}]" + (f"\n{body}" if body else "")
        blocks.append(_block(sig.command, body))
    return redact.text("\n\n".join(blocks), secrets)


def _refuse_the_repository(out_dir: Path) -> None:
    """Stop a capture from writing where the repository can publish it.

    Decided on resolved *parents*, not on a string prefix: `E:\\Amber-MCP_x` is a
    sibling, not something inside `E:\\Amber-MCP`, and a `startswith` check would
    refuse it while happily accepting a symlink back into the tree.
    """
    root = config.PROJECT_ROOT
    resolved = out_dir.resolve()
    if resolved == root or root in resolved.parents:
        raise ValueError(
            f"refusing to capture inside the repository: {resolved} is within "
            f"{root}. A raw capture holds the user's real contacts, numbers, "
            "balance and call history, and this repository is public -- git "
            "ignoring the path is not the same as the path never leaving the "
            "machine. Give capture() a directory outside the repo, or nothing "
            "at all and it will use config.scratch_dir() "
            "(override with AMBER_CAPTURE_DIR)."
        )


def _refuse_a_writable_driver(driver) -> None:
    """Turn away a driver that can change the phone, before asking it for a look.

    `capture()` is a read-only observer by ruling, and the accident this exists
    to prevent is the ordinary one: a harness handed the full `Driver` starts
    being used as a driver, and a stray `input tap` on a live handset becomes
    normal. Refusing is cheaper than remembering.

    `read_only()` is the way through, and wrapping is deliberate work rather than
    a forgotten argument. The real guarantee is narrower than this tripwire:
    `capture()` only ever issues the fixed `SIGNAL_COMMANDS` plus the three read
    calls, so what this check protects is a future refactor, not the present code.
    """
    present = tuple(name for name in DRIVER_WRITE_METHODS
                    if callable(getattr(driver, name, None)))
    if present:
        raise TypeError(
            "capture() is read-only and this driver exposes write capability: "
            f"{', '.join(present)}. Hand it a read-only view of the phone "
            "(current_focus, dump_hierarchy, screenshot, shell) instead, so "
            "looking at a screen cannot become acting on one."
        )


class ReadOnlyPhone:
    """The four reads a capture needs, and nothing that can change the phone.

    A `shell` handle is kept because the signal commands are shell reads; the
    capture path never builds a command from caller input, so the surface stays
    as narrow as `SIGNAL_COMMANDS`.
    """

    _READS = ("current_focus", "dump_hierarchy", "screenshot", "shell")

    def __init__(self, driver):
        self._driver = driver

    def __getattr__(self, name: str):
        if name in ReadOnlyPhone._READS:
            return getattr(self._driver, name)
        raise AttributeError(f"a read-only phone view has no {name!r}")


def read_only(driver) -> ReadOnlyPhone:
    """Wrap a driver so a capture can look at it but not act on it."""
    return ReadOnlyPhone(driver)


def capture(driver, name: str, secrets: Sequence[str] = (), out_dir: Path | None = None,
            *, include_png: bool = False,
            include_hierarchy: bool = False) -> Path:
    """Write one screen twice: what may be published, and raw for the drawer.

    Returns the *committed* directory. `out_dir` defaults to
    `config.scratch_dir()` -- outside the repository, because a raw capture is
    real account data and this repo is public; an `out_dir` inside it is refused
    outright rather than exempted (see `_refuse_the_repository`). A driver that
    can write to the phone is refused too. Both refusals happen before the
    repository is touched or a command is issued, so neither can half-run.

    The committed copy is `focus.txt` and `signals.txt` and nothing else. Both
    the picture and the UI tree are withheld by default, each by its own keyword
    (`include_png`, `include_hierarchy`) and each reviewed per screen: a
    screenshot cannot be redacted at all, and a redacted hierarchy still carries
    every contact name the caller did not think to hand over as a secret -- which
    is why names in a committed XML are now a gate failure rather than a promise
    in a markdown file. `raw/` gets all four files unconditionally, because a
    capture nobody can re-take is worse than a file in a scratch directory.
    """
    out_dir = Path(out_dir) if out_dir is not None else config.scratch_dir()
    _refuse_the_repository(out_dir)
    _refuse_a_writable_driver(driver)

    clean_dir = out_dir / name
    # Inside `out_dir`, never beside it, so one location decides both copies.
    raw_dir = out_dir / "raw" / name
    clean_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    focus = driver.current_focus()
    # Raises if the read is broken — a screen nobody captured is never written
    # down as a blank one.
    xml = driver.dump_hierarchy()
    png = driver.screenshot()
    signals = [_signal(driver, cmd) for cmd in SIGNAL_COMMANDS]

    (raw_dir / HIERARCHY_NAME).write_text(xml, encoding="utf-8")
    (raw_dir / "signals.txt").write_text(_raw_signals(signals), encoding="utf-8")
    (raw_dir / "focus.txt").write_text(f"{name}\n{focus}\n", encoding="utf-8")
    (raw_dir / "screen.png").write_bytes(png)

    write_committed_hierarchy(clean_dir, xml, secrets, include=include_hierarchy)
    (clean_dir / "signals.txt").write_text(_committed_signals(signals, secrets),
                                           encoding="utf-8")
    (clean_dir / "focus.txt").write_text(f"{name}\n{focus}\n", encoding="utf-8")
    write_screen_png(clean_dir, png, include=include_png)

    return clean_dir


def summarise(clean_dir: Path) -> Screen:
    """Read a capture back as the record plan 2 works from.

    The screen's identity is the focused activity, not any text on it: AmberIT
    ships Bangla and English labels for the same button, and a flow that found
    its screen by reading a label breaks the moment the handset's language does.

    This needs the directory that holds the UI tree, which since the final review
    is `out_dir/raw/<name>` rather than the committed copy: a default capture
    publishes no hierarchy at all, because a redacted one still publishes every
    name in it. The absence is said out loud rather than left as a half-built
    `Screen` or a bare OSError from `read_text` -- a record with empty selector
    lists reads as "this screen has nothing clickable", which is exactly the
    wrong conclusion to draw from a file that was withheld.
    """
    tree = clean_dir / HIERARCHY_NAME
    if not tree.is_file():
        raise FileNotFoundError(
            f"no {HIERARCHY_NAME} in {clean_dir}, so nothing can be summarised from it. "
            "A default capture keeps the UI tree in the raw copy: pass the matching "
            "raw directory (the one named raw/<name> beside this one), or re-capture "
            "with include_hierarchy=True."
        )
    xml = tree.read_text(encoding="utf-8")
    lines = (clean_dir / "focus.txt").read_text(encoding="utf-8").splitlines()
    focus = lines[1] if len(lines) > 1 else ""
    return Screen(
        name=clean_dir.name,
        focus=focus,
        resource_ids=screen_names(xml),
        texts=[n.get("text") for n in ET.fromstring(xml).iter("node") if n.get("text")],
        interactive=[f"{label} @ {x},{y}" for label, x, y in interactive(xml)],
    )
