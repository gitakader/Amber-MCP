"""Is the phone usable right now, and if not, which rung of the ladder failed?

The order is fixed by measurement, not taste: `wm dismiss-keyguard` does not work
on this MIUI build, so the no-PIN rung is a wake plus a swipe, and the PIN rung is
a fallback that must never be reached first. Neither rung has been proven on the
device yet -- that is Task 6 -- so both report honestly instead of assuming.

Two rules hold the file together.

* The PIN is read from `config.pin_from_env()` only when the caller passed
  nothing, is never typed before the swipe rung has failed by itself, and travels
  to the phone as keyevent codes: it appears in no `detail`, no `say()` line, no
  log and no command string. That is asserted in `tests/unit/test_session.py`
  rather than trusted, because a comment cannot prove a "never".
* A rung answers for the state it read and nothing else. The driver raises
  `TransportError` when a read or a write did not happen, and this module names it
  at the adb rung: a dead or hung adb is an unreachable phone, not a locked screen
  and not a missing app.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from amber_mcp import config
from amber_mcp.driver import TransportError

KEYCODE_WAKEUP = 26
KEYCODE_DPAD_CENTER = 23
KEYCODE_0 = 7

# One swipe up the whole height of the lock screen, on the 1080x2400 panel this
# phone reports. Unproven on the handset -- Task 6 measures both unlock rungs --
# so the rungs below report what they saw rather than what this hopes for.
SWIPE_UP = "input swipe 540 1900 540 700 300"

# Waits that belong to the ladder rather than to a caller's patience: a dropped
# wifi serial needs the adb server's own reconnect time, and a PIN needs the IME
# to catch up before the screen is read.
RECONNECT_WAIT = 1.5
PIN_WAIT = 0.4

# -- the read-once rule -------------------------------------------------------
# `current_focus()` costs one `dumpsys window | grep mCurrentFocus`, and when the
# grep prints nothing it costs a second read of the whole dump -- roughly 200 KB
# off the handset, over wifi, per call. So every rung reads the screen ONCE and
# reuses that answer, including the answer that means "still locked": the read
# after the swipe is also the read before the PIN, and the read after the PIN is
# also rung 4's first look. `Driver.is_keyguard_up()` is therefore not called
# here -- it re-reads the screen to answer a question the focus string in hand
# already answered -- and the tests pin that helper's rule against this module's
# so the two cannot drift. A ladder that polls, that re-reads to "make sure",
# triples this cost on every tool call and turns a voice turn into a stutter.
# Do not add a screen read; reuse the one above it.
# ---------------------------------------------------------------------------

# The lock-screen sentences carry their own fix, because the two cases need
# different advice: telling someone to set a variable they have already set is
# how a rung stops being believed.
NO_PIN_UNLOCK = "swipe did not open it and no PIN is set. Set REDMI_PIN, or turn the lock off."
PIN_FAILED_UNLOCK = "swipe and PIN both failed to open it. Check REDMI_PIN, or unlock it by hand."


@dataclass
class Readiness:
    ready: bool
    failed_step: str
    detail: str


_UNSET = object()

# One sentence per rung the ladder can fail on. `say()` falls back for anything
# not in here, so a new rung is never a silent "the phone is not ready".
REASONS = {
    "adb": "the phone is not on adb. Reconnect wireless debugging.",
    "unlock": "the phone is locked and I could not open it.",
    "app": "AmberIT would not come to the front.",
    "session": "the screen reader could not attach to the phone.",
}

_SAY_LIMIT = 140


def _spoken(text: str) -> str:
    """Flatten a rung's answer into one line a voice assistant can speak.

    A detail can arrive from an exception -- multi-line, quoting reprs -- so the
    whitespace is collapsed and the braces go before anything is spoken, and the
    length is cut at a word rather than at a character.
    """
    flat = " ".join(text.replace("{", " ").replace("}", " ").split())
    if len(flat) <= _SAY_LIMIT:
        return flat
    cut = flat.rfind(" ", 0, _SAY_LIMIT)
    return flat[: cut if cut > 0 else _SAY_LIMIT]


def say(readiness: Readiness) -> str:
    """One or two lines a voice assistant can speak verbatim."""
    if readiness.ready:
        return _spoken(f"Phone ready. {readiness.detail}")
    base = REASONS.get(readiness.failed_step, "the phone is not ready.")
    return _spoken(f"{base} {readiness.detail}")


def _alive(driver) -> bool:
    """Does adb answer for this serial at all?

    `get-state` prints exactly `device` when it answers, and writes its refusals
    into *stdout* at rc 0 -- where the word "device" sits inside every one of them
    (`error: device offline`, `error: device ... not found`). So rc is not the
    answer and neither is a substring: the state line is, whole, on a line of its
    own. A ladder that asks `"device" in out` calls a dead cable alive, walks up
    the screen rungs, and reports a phone that is not attached as one that is
    merely locked.
    """
    result = driver.adb(["get-state"])
    if not result.ok:
        return False
    return any(line.strip().lower() == "device" for line in (result.out or "").splitlines())


def _reconnect(driver) -> None:
    """Put the wifi serial back on adb.

    `host_adb`, not `adb`: `-s <serial> connect <serial>` asks adb to connect a
    device it is already talking to, and the ladder should not lean on adb
    tolerating a flag that command ignores. The answer is not read here -- rung 1
    asks `get-state` again straight after, and that read-back is the proof.
    """
    driver.host_adb(["connect", driver.serial])


def _keyguard_shown(focus: str) -> bool:
    """Lock-ness, derived from a focus string that has already been paid for.

    The same rule `Driver.is_keyguard_up()` applies, restated rather than called,
    because calling it spends a second `dumpsys window` on a question this module
    already has the answer to. See the read-once rule above; the test suite pins
    the two predicates against each other so they cannot drift apart.
    """
    return "Keyguard" in focus or focus == "NotificationShade"


def _screen(focus: str) -> str:
    """A window named in as few words as identify it.

    `dumpsys window` writes a launcher window as `pkg/.Launcher`, and that leading
    dot is the whole identity of the screen: cutting at the last slash would
    report `.Launcher` and leave a human to guess whose launcher. So an abbreviated
    activity keeps its package, a fully-qualified one is shortened to its class
    name -- the app it belongs to has just been asserted -- and a panel name, which
    has no slash at all, is already complete.
    """
    if "/" not in focus:
        return focus
    package, _, activity = focus.partition("/")
    if activity.startswith("."):
        return focus
    return activity.rsplit(".", 1)[-1]


def _type_pin(driver, pin: str) -> None:
    """One keyevent per digit, then the centre key to submit it.

    Not `input text`: that would put the PIN into a command string, and command
    strings are what the ladder hands to `detail`, to logs and to exception text.

    ASCII digits only, and that is `KEYCODE_0 + n` talking rather than taste:
    `str.isdigit()` is true of characters `int()` raises on, and a rung that
    crashes on the lock screen leaves the caller no sentence at all.
    """
    for ch in pin:
        if ch in "0123456789":
            driver.keyevent(KEYCODE_0 + int(ch))
    time.sleep(PIN_WAIT)
    driver.keyevent(KEYCODE_DPAD_CENTER)


def ensure_ready(
    driver,
    package: str = config.PACKAGE,
    dashboard: str = config.DASHBOARD_ACTIVITY,
    pin: str | None | object = _UNSET,
    settle: float = 1.2,
) -> Readiness:
    """Climb the ladder and say which rung stopped: adb, wake, unlock, app.

    `pin` is the caller's override -- pass `None` to mean "no PIN, do not look".
    Left alone it comes from `config.pin_from_env()`, and no PIN is ever typed
    before the swipe rung has failed on its own. The `detail` that comes back is
    safe to speak and safe to log: it names screens and serials, never the PIN.
    """
    if pin is _UNSET:
        pin = config.pin_from_env()
    elif pin is not None and not isinstance(pin, str):
        # A caller bug, raised as one rather than spoken as a rung -- the same rule
        # `type_digits` follows. Coercing it would be worse than crashing: `str(12.34)`
        # types 1234, a *different* PIN than the one that was handed over. And the
        # message names the type, never the value.
        raise TypeError(f"pin is a string of digits or None, not {type(pin).__name__}")

    # Rung 1 -- is the device even answering adb? WiFi serials drop constantly and
    # the port changes when they come back, so a dropped serial gets one reconnect
    # before it is called offline.
    if not _alive(driver):
        _reconnect(driver)
        time.sleep(RECONNECT_WAIT)
        if not _alive(driver):
            return Readiness(False, "adb", f"no answer from {driver.serial}")

    # Rungs 2, 3 and 4 all touch or read the phone, and every one of them can be
    # refused. A refusal is not a screen state, so the whole ladder below is one
    # try: a dead or hung adb leaves by the adb rung, with a sentence, never as a
    # traceback into whichever tool called this first.
    try:
        # Rung 2 -- wake. Everything after this is pointless while the display
        # sleeps, and the wait is what makes the read after it read a lit screen.
        driver.keyevent(KEYCODE_WAKEUP)
        time.sleep(settle)

        # One read of the screen. Every rung below acts on this answer.
        focus = driver.current_focus()

        # Rung 3 -- unlock, no PIN first, because the swipe is the rung that works
        # on a swipe-only lock and costs nothing when it does not. The swipe's own
        # rc is not checked here on purpose: the read-back under Verification is
        # the proof, and a swipe the device swallowed but did not move reads back
        # as still locked -- which is the same answer either way.
        if _keyguard_shown(focus):
            driver.shell(SWIPE_UP)
            time.sleep(settle)
            focus = driver.current_focus()  # after the swipe, and so before the PIN
            if _keyguard_shown(focus):
                if not pin:
                    return Readiness(False, "unlock", NO_PIN_UNLOCK)
                _type_pin(driver, pin)
                focus = driver.current_focus()  # after the PIN
                if _keyguard_shown(focus):
                    return Readiness(False, "unlock", PIN_FAILED_UNLOCK)

        # Rung 4 -- the app in front, from the same answer that just said the
        # screen was open. The check is the package rather than the dashboard
        # activity, because a live IP call puts another of AmberIT's activities on
        # screen and readiness must not fail then (the spec's rung-4 parenthetical).
        # `dashboard` is what the launch aims at, so it is the name given when the
        # app does not come up.
        if package not in focus:
            driver.shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
            time.sleep(settle * 2)
            focus = driver.current_focus()
        if package not in focus:
            return Readiness(
                False, "app", f"expected {_screen(dashboard)}, screen belongs to {_screen(focus)}"
            )

        return Readiness(True, "", f"on {_screen(focus)}")
    except TransportError as exc:
        return Readiness(False, "adb", str(exc)[:90])
