"""Is the phone usable right now, and if not, which rung of the ladder failed?

The order is fixed by measurement, not taste: `wm dismiss-keyguard` does not work
on this MIUI build, so the no-PIN rung is a wake plus a swipe, and the PIN rung is
a fallback that must never be reached first. Neither rung has been proven on the
device yet -- that is Task 6 -- so both report honestly instead of assuming.

Two rules hold the file together.

* The PIN is read from `config.pin_from_env()` only when the caller passed
  nothing, is never typed before the swipe rung has failed by itself, and travels
  to the phone as keyevent codes: it appears in no `detail`, no `say()` line, no
  log and no command string -- including no *failed* command string, which is the
  one that used to reach a transcript as `input keyevent 15` (PIN digit 8, seven
  subtracted away, verbatim). That is asserted in `tests/unit/test_session.py`
  rather than trusted, because a comment cannot prove a "never".
* A rung answers for the state it read and nothing else. The driver raises
  `TransportError` when a read or a write did not happen, and this module names it
  at the adb rung: a dead or hung adb is an unreachable phone, not a locked screen
  and not a missing app. The rung's detail is the reason the driver gave with the
  device's address taken out of it (`_unaddressed`), because an adb rung is spoken
  out loud wherever the assistant logs. `Driver.shell()` is the one write path that raises
  nothing on its own -- the ladder runs its two screen writes through it, so it
  judges each `ShellResult` with the driver's own predicate rather than letting a
  refusal read back as a screen state.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

from amber_mcp import config
from amber_mcp.driver import TransportError, _transport_reason

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
# "session" is deliberately absent: no rung in the ladder, and nothing in the
# server, ever returns `failed_step="session"` -- the u2 attach is done once in
# `Driver.connect`, whose failure reaches the caller as a `ConnectionError` from
# `get_driver`, never as a ladder rung. Leaving its prose here would be dead
# text that reads like a live state (Task 8 review, carry-forward 3).
REASONS = {
    "adb": "the phone is not on adb. Reconnect wireless debugging.",
    "unlock": "the phone is locked and I could not open it.",
    "app": "AmberIT would not come to the front.",
}

_SAY_LIMIT = 140

# Where a rung's detail is about to be spoken, an address goes in and does not
# come back out. The serial is the phone's LAN IP and wireless-debugging port:
# naming it in an assistant transcript says where the handset answers adb, and
# `Driver._focus_read` and adb's own `error: device '<serial>' not found` both
# quote it. Stripping the driver's serial and any dotted-quad-with-port is what
# makes "no address in a detail" a property of the ladder rather than of each
# message every layer happens to build today.
_ADDRESS = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}(?::\d{1,5})?")

# Rung 1's own sentence, with nowhere in it to put a serial.
NO_ADB_ANSWER = "adb did not answer for the device, even after a reconnect"


def _unaddressed(text: str, driver) -> str:
    """The same words, with the device's address taken out of them."""
    out = text
    serial = str(getattr(driver, "serial", "") or "")
    if serial:
        out = out.replace(serial, "the device")
    return _ADDRESS.sub("the device", out)


def _rung_detail(driver, exc: BaseException) -> str:
    """What the adb rung reports about a failure: the reason, capped, unaddressed.

    The cut happens *after* the address is replaced: `str(exc)[:90]` on the raw
    text was free to stop halfway through `<lan-ip>:<port>` and speak the
    first half of it, which is the part that matters to anyone on the LAN.
    """
    return _unaddressed(str(exc), driver)[:90]


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


def _checked_write(driver, command: str, *, action: str) -> None:
    """Send a screen write through `shell()` and refuse to call a refused one done.

    `Driver.shell()` answers with a `ShellResult` and raises nothing: the guard in
    `Driver._write` lives in the wrapper, not in the command, so the two writes
    that go out bare -- the unlock swipe and the launcher -- came back saying
    nothing at all. A refusal is not a screen state, and here it was worse than
    silent: an `input: could not open /dev/input` under the swipe turned the
    unlock rung into "Set REDMI_PIN", advice about a lock screen for a phone whose
    input never answered. So the same judgement `Driver._write` makes is made here,
    with the driver's own predicate -- `_transport_reason`, the one function both
    `_write` and the focus read consult, which asks `error:`-in-stdout as well as
    rc, because adb writes its refusals at rc 0. Importing it rather than restating
    it keeps one definition of "this outcome cannot be believed"; the read-once
    argument for restating `_keyguard_shown` did not apply, because that rule needs
    a second screen read to call and this one needs no read at all.

    `action` is what the message names and `command` is what it does not, on
    `Driver._write`'s reasoning: this raise is quoted straight into a rung detail
    and spoken. Both commands here are fixed strings with no secret in them, but
    the ladder is one new write away from a command that has one, and the shape of
    a refusal should already be the safe one.

    The raise goes to the ladder's own `except`, which names the adb rung: the
    phone did not take the command, which is the rung-1 sentence and never a claim
    about a lock screen or a missing app.
    """
    result = driver.shell(command)
    reason = _transport_reason(result)
    if reason:
        raise TransportError(f"{action} was not carried out (rc {result.rc}): {reason}")


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
    package: str | None = config.PACKAGE,
    dashboard: str | None = config.DASHBOARD_ACTIVITY,
    pin: str | None | object = _UNSET,
    settle: float = 1.2,
) -> Readiness:
    """Climb the ladder and say which rung stopped: adb, wake, unlock, app.

    `package` and `dashboard` are normalised before anything is compared: blank
    and `None` both mean "use the configured value", because MCP hosts hand over
    empty strings as readily as they hand over set ones -- that is why
    `config._clean()` exists, and a ladder that compares a blank instead of
    replacing it is a ladder whose `"" in focus` is true of every screen.

    `pin` is the caller's override -- pass `None` to mean "no PIN, do not look".
    Left alone it comes from `config.pin_from_env()`, and no PIN is ever typed
    before the swipe rung has failed on its own. The `detail` that comes back is
    safe to speak and safe to log: it names screens, never the PIN, and never the
    device's address either.
    """
    package = package or config.PACKAGE
    dashboard = dashboard or config.DASHBOARD_ACTIVITY

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
            return Readiness(False, "adb", NO_ADB_ANSWER)

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
        # on a swipe-only lock and costs nothing when it does not. The read-back
        # under Verification is still the proof that the screen moved, but the
        # swipe's own outcome is checked first: a device that refused the write
        # has not been asked, and "not asked" is rung-1 news rather than the
        # still-locked answer that used to send the reader to REDMI_PIN.
        if _keyguard_shown(focus):
            _checked_write(driver, SWIPE_UP, action="the unlock swipe")
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
            _checked_write(driver, f"monkey -p {package} -c android.intent.category.LAUNCHER 1",
                           action="the app launch")
            time.sleep(settle * 2)
            focus = driver.current_focus()
        if package not in focus:
            return Readiness(
                False, "app", f"expected {_screen(dashboard)}, screen belongs to {_screen(focus)}"
            )

        return Readiness(True, "", f"on {_screen(focus)}")
    except TransportError as exc:
        return Readiness(False, "adb", _rung_detail(driver, exc))
