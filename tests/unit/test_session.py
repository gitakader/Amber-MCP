"""The readiness ladder, tested with no phone attached.

Nothing in this file dials, and nothing reaches a handset: the only processes a
green run starts are Python's own. Two fakes are used, on purpose.

* `StubDriver` is the plan's fake -- a scripted list of focus answers plus a record
  of every command the ladder sent. The rung-by-rung and ordering assertions read
  against it, because a spoken expectation like "the swipe came before the PIN" is
  a statement about a command log.
* A **real** `Driver` over a counting runner answers the questions R3 cannot be
  stated without one: how many `dumpsys window` reads a readiness check costs, and
  whether the cheap grepped read is all it ever asks for.

Where a ruling from the plan review changed a test body, the reason is in the
comment above the assertion that carries it.
"""

import inspect

import pytest

from amber_mcp import config, session
from amber_mcp.driver import Driver, ShellResult, TransportError

SERIAL = "192.0.2.9:5555"
DASHBOARD = "com.amberit.dialer/x"
FOCUS_GREP = "dumpsys window | grep mCurrentFocus"
FOCUS_WHOLE = "dumpsys window"

# The two writes the ladder sends through `shell()` rather than through
# `keyevent()`, named exactly as they go out so a fake can answer each one alone.
SWIPE = session.SWIPE_UP
MONKEY_LAUNCH = f"monkey -p {config.PACKAGE} -c android.intent.category.LAUNCHER 1"

# One device-side refusal, in each of the two shapes the driver already refuses.
# rc 1 is the obvious one; rc 0 with the refusal in *stdout* is the shape adb and
# MIUI's own `input` use, and it is the one a bare rc check believes.
REFUSALS = {
    "rc-1": ShellResult(1, "input: could not open /dev/input\n", ""),
    "rc-0-error-in-stdout": ShellResult(0, "error: device offline\n", ""),
}

# KEYCODE_0 is 7, so the PIN 1234 is the four keyevents below -- and nothing else.
PIN = "1234"
PIN_KEYEVENTS = [f"input keyevent {7 + int(ch)}" for ch in PIN]

# Commands that would put a call on the phone, cellular or otherwise. The ladder
# has no business knowing any of them.
FORBIDDEN = ("am start", "ACTION_CALL", "ACTION_DIAL", "tel:", "dismiss-keyguard", "wm ")


class StubDriver:
    """The Driver surface the ladder uses, recording everything it was asked for.

    `serial` and `host_adb` are part of this fake for R2's sake: a stub without
    them would let the ladder run `adb(["connect", serial])` -- `-s` selector and
    all -- and still look green.

    `is_keyguard_up` is kept and *counted*, which is what makes R3 testable: the
    ladder reads the screen once per state and derives lock-ness from the focus
    string it already has, so the honest expectation is that this method is never
    reached from `ensure_ready` -- only from the drift test that compares the two
    rules.

    `state_sequence` is the one answer a constant `state` cannot give: a serial
    that is gone, is asked for, and comes back. It is read as
    `[before_the_connect, after_the_connect]`, and the switch is causal -- the
    phone only starts answering once `host_adb(["connect", ...])` has run. A
    sequence that advanced on a timer of its own would let a ladder that never
    reconnected climb past rung 1 anyway, which is the exact vacuity F3 is about.
    """

    def __init__(
        self,
        focus_sequence,
        state="device\n",
        keyguard=False,
        pin_ok=True,
        state_sequence=None,
    ):
        self.serial = SERIAL
        self._focus = list(focus_sequence)
        self._state = state
        self._states = list(state_sequence) if state_sequence else None
        self.connected = False
        self._keyguard = keyguard
        self.pin_ok = pin_ok
        self.sent = []
        self.state_reads = []
        self.host_calls = []
        self.keyguard_calls = 0

    def adb(self, args):
        # Deliberately not in `sent`: `sent` is what the ladder sent *to the
        # phone's screen*, and get-state is a question about the cable.
        self.state_reads.append(list(args))
        if self._states and args[:1] == ["get-state"]:
            return ShellResult(0, self._states[1 if self.connected else 0], "")
        return ShellResult(0, self._state, "")

    def host_adb(self, args):
        self.host_calls.append(list(args))
        self.sent.append("adb " + " ".join(args))
        if args[:1] == ["connect"]:
            self.connected = True
        return ShellResult(0, f"connected to {self.serial}\n", "")

    def shell(self, cmd, timeout=20.0):
        self.sent.append(cmd)
        return ShellResult(0, "", "")

    def keyevent(self, code):
        self.sent.append(f"input keyevent {code}")

    def is_keyguard_up(self):
        self.keyguard_calls += 1
        return self._keyguard

    def current_focus(self):
        return self._focus.pop(0) if len(self._focus) > 1 else self._focus[0]


class RecordingRunner:
    """Answers a real `Driver` and counts the screen reads it is asked for.

    Keyed by the exact remote command, because `dumpsys window` is a prefix of
    `dumpsys window | grep mCurrentFocus` and a substring match cannot tell the
    cheap read from the 200 KB one.
    """

    def __init__(self, focus_sequence, state="device\n", answers=None):
        self._focus = list(focus_sequence)
        self.state = state
        self.answers = answers or {}
        self.argv = []
        self.grep_reads = 0
        self.whole_reads = 0

    def __call__(self, argv):
        self.argv.append(list(argv))
        remote = argv[-1]
        if remote in self.answers:
            return self.answers[remote]
        if remote == "get-state":
            return ShellResult(0, self.state, "")
        if remote in (FOCUS_GREP, FOCUS_WHOLE):
            if remote == FOCUS_GREP:
                self.grep_reads += 1
            else:
                self.whole_reads += 1
            return ShellResult(0, f"  mCurrentFocus=Window{{a u0 {self._next_focus()}}}\n", "")
        return ShellResult(0, "", "")

    def _next_focus(self):
        return self._focus.pop(0) if len(self._focus) > 1 else self._focus[0]


def phone(focus_sequence, state="device\n", answers=None):
    """A real Driver -- the class Task 8 runs on -- over the counting fake."""
    runner = RecordingRunner(focus_sequence, state=state, answers=answers or {})
    return Driver(SERIAL, runner=runner), runner


@pytest.fixture(autouse=True)
def waits(monkeypatch):
    """The ladder waits; the suite does not.

    Every delay is recorded, so a test can also assert that a wait happened
    rather than watching it happen in real time.
    """
    recorded = []
    monkeypatch.setattr(session.time, "sleep", recorded.append)
    return recorded


# -- rung 1: is the phone answering adb at all --


def test_adb_gone_names_the_adb_rung():
    driver = StubDriver(["nothing"], state="error: device offline\n")
    result = session.ensure_ready(driver, pin=None)
    assert result.ready is False
    assert result.failed_step == "adb"


def test_a_refused_get_state_is_not_alive_however_the_substring_reads():
    # The word "device" sits inside every adb refusal, and adb writes them at
    # rc 0. A ladder that asks `"device" in out` calls a dead cable alive, walks
    # up the screen rungs, and reports an unattached phone as a locked one --
    # which is a different sentence to say, and the wrong one.
    for state in ("error: device offline\n", f"error: device '{SERIAL}' not found\n"):
        driver = StubDriver([DASHBOARD], state=state)
        result = session.ensure_ready(driver, dashboard=DASHBOARD, pin=None)
        assert result.failed_step == "adb", state

    # The flip side, so the guard is not "nothing is ever alive".
    alive = StubDriver([f"{config.PACKAGE}/.AgniDashboardActivity"])
    assert session.ensure_ready(alive, pin=None).ready is True


def test_rc_is_the_other_half_of_the_alive_question():
    # An offline device frequently answers `get-state` at rc 1 with the refusal
    # in stderr instead, and adb puts daemon chatter on stderr at rc 0. Neither
    # stream is a status, so the ladder asks for both before it walks up the rungs.
    class Unhealthy:
        serial = SERIAL

        def adb(self, args):
            return ShellResult(1, "", "error: device offline")

    class Silent:
        serial = SERIAL

        def adb(self, args):
            return ShellResult(0, "* daemon not running. starting now *\n", "")

    class ChattyButAnswering:
        serial = SERIAL

        def adb(self, args):
            return ShellResult(0, "* daemon started *\ndevice\n", "")

    class RefusedButPleasant:
        serial = SERIAL

        def adb(self, args):
            # The shape `ShellResult`'s own contract refuses: rc is the status, so
            # a command that did not run may not answer as though it had -- even
            # with the word `device` sitting in its stdout.
            return ShellResult(1, "device\n", "error: closed")

    assert session._alive(Unhealthy()) is False
    assert session._alive(Silent()) is False
    assert session._alive(ChattyButAnswering()) is True
    assert session._alive(RefusedButPleasant()) is False
    assert session._alive(StubDriver([DASHBOARD])) is True


def test_a_disconnected_wifi_serial_is_reconnected_before_it_is_declared_gone():
    driver = StubDriver(["com.miui.home/.Launcher"], state="error: device not found\n")
    result = session.ensure_ready(driver, pin=None)
    # R2: the plan's `or` here was satisfied by the adb failure alone, so this
    # test passed with the reconnect deleted. Both halves are required now: the
    # connect must really have run, and run as a host command with no `-s`.
    assert driver.host_calls == [["connect", SERIAL]], "the connect never ran, or ran as a device command"
    assert "-s" not in " ".join(driver.host_calls[0])
    assert result.failed_step == "adb"
    assert not any("keyevent" in c for c in driver.sent), "the ladder woke a phone that is not attached"


def test_a_broken_screen_read_is_the_phone_being_unreachable_not_a_locked_screen():
    # R1. `current_focus()` raises rather than naming a screen it did not see; a
    # hung dumpsys is rung-1 news, and it must not fall through to the unlock or
    # app rungs, which are claims about a screen nobody read.
    driver, _ = phone(["KeyguardScrim"], answers={FOCUS_GREP: ShellResult(124, "", "timed out after 20.0s")})
    result = session.ensure_ready(driver, pin=None)
    assert result.ready is False
    assert result.failed_step == "adb"
    assert "wireless debugging" in session.say(result)


def test_a_refused_focus_read_is_never_read_as_no_keyguard_proceed():
    # The rc-0-with-`error:` shape of the same defect, and the one that used to
    # put a finger on a locked screen: a refusal must not become "no keyguard".
    driver, _ = phone(["KeyguardScrim"], answers={FOCUS_GREP: ShellResult(0, "error: device offline\n", "")})
    assert session.ensure_ready(driver, pin=None).failed_step == "adb"


def test_a_wake_the_device_refused_is_rung_one_news_not_a_traceback():
    # Every tool calls this function first, so an escaping exception is a crashed
    # assistant instead of a sentence. A refused `input keyevent 26` says the
    # same thing a refused read does: the phone did not take the command.
    driver, _ = phone(
        ["KeyguardScrim"],
        answers={"input keyevent 26": ShellResult(1, "", "input: could not open /dev/input")},
    )
    result = session.ensure_ready(driver, pin=None)
    assert result.failed_step == "adb"


def test_a_refused_pin_keyevent_is_the_adb_rung_too():
    driver, _ = phone(
        ["KeyguardScrim"] * 3,
        answers={"input keyevent 8": ShellResult(124, "", "timed out after 20.0s")},
    )
    result = session.ensure_ready(driver, pin=PIN)
    assert result.failed_step == "adb"


def test_a_serial_the_reconnect_brought_back_climbs_past_rung_1():
    # F3. Every other stub answers `get-state` with a constant, so the second
    # `_alive()` after `_reconnect()` can never pass and the branch that means
    # "the reconnect worked" was never walked: deleting it entirely kept the
    # suite green. Here the serial is gone, the ladder asks for it back with one
    # host-level connect, and the phone answers -- so the ladder must go on and
    # say "Phone ready", not "the phone is not on adb".
    driver = StubDriver(
        [f"{config.PACKAGE}/.AgniDashboardActivity"],
        state_sequence=["error: device not found\n", "device\n"],
    )
    result = session.ensure_ready(driver, pin=None)

    assert result.ready is True, f"stuck at {result.failed_step}: {result.detail}"
    # Exactly one connect, host-level, with no `-s` selector (R2), and it is the
    # only reason the second `get-state` is a different answer than the first.
    assert driver.host_calls == [["connect", SERIAL]], driver.host_calls
    assert "-s" not in " ".join(sum(driver.host_calls, []))
    assert driver.state_reads == [["get-state"], ["get-state"]], driver.state_reads
    # Past rung 1 means onto the screen. The mirror of the dead-serial test's
    # "no keyevent was sent to a phone that is not attached": here one must be.
    assert any("keyevent" in c for c in driver.sent), driver.sent


@pytest.mark.parametrize("shape", list(REFUSALS))
def test_a_refused_unlock_swipe_is_the_adb_rung_not_the_unlock_rung(shape):
    # F1. The swipe went out through `shell()`, whose result was discarded, so a
    # device that refused the write (`input: could not open /dev/input`) read back
    # as a still-Keyguard screen and the ladder told the user to set REDMI_PIN --
    # advice for a lock screen, on a phone whose input was never reached. The
    # identical condition on a `keyevent` write already names `adb`, so the two
    # write paths must not disagree about what a refusal means.
    driver, _ = phone(["KeyguardScrim"] * 3, answers={SWIPE: REFUSALS[shape]})
    result = session.ensure_ready(driver, pin=None)
    assert result.failed_step == "adb", shape
    assert result.ready is False
    # The sentence must not send the reader to an environment variable.
    assert "REDMI_PIN" not in session.say(result)


@pytest.mark.parametrize("shape", list(REFUSALS))
def test_a_refused_app_launch_is_the_adb_rung_not_the_app_rung(shape):
    # The other discarded `shell()` result: a launch the device refused is not
    # "AmberIT would not come to the front" -- nothing was asked of the front --
    # and the ladder may not claim an app rung it never ran.
    driver, _ = phone(["com.miui.home/.Launcher"] * 2, answers={MONKEY_LAUNCH: REFUSALS[shape]})
    result = session.ensure_ready(driver, pin=None)
    assert result.failed_step == "adb", shape
    assert result.ready is False


# -- rung 2: wake --


def test_wake_is_always_issued_first_even_when_ready():
    driver = StubDriver(["x/com.y.Dashboard", "com.amberit.dialer/x"])
    session.ensure_ready(driver, dashboard="com.amberit.dialer/x", pin=None)
    assert "input keyevent 26" in driver.sent
    # "first" is part of the claim: `adb()` is not in `sent`, so this is the
    # first thing the ladder put on the phone.
    assert driver.sent[0] == "input keyevent 26"


def test_every_read_is_preceded_by_a_wait_not_by_another_command(waits):
    # A wake followed straight by a dumpsys reads the screen that was there
    # before the panel lit up; a reconnect followed straight by `get-state`
    # answers before adb has re-attached the port. Both waits are proven, and
    # that they are the only ones the rung needed -- an extra sleep is the same
    # voice-turn stall this file's read budget is about.
    ready = StubDriver([f"{config.PACKAGE}/.AgniDashboardActivity"])
    session.ensure_ready(ready, pin=None)
    assert waits == [1.2], waits

    waits.clear()
    dead = StubDriver([DASHBOARD], state="error: device offline\n")
    session.ensure_ready(dead, dashboard=DASHBOARD, pin=None)
    assert waits == [session.RECONNECT_WAIT], waits


# -- rung 3: unlock, no PIN first --


def test_no_pin_unlock_is_tried_before_any_pin_is_consulted():
    driver = StubDriver(["KeyguardScrim", "KeyguardScrim", "com.amberit.dialer/x"])
    session.ensure_ready(driver, dashboard="com.amberit.dialer/x", pin=PIN)
    swipe = [c for c in driver.sent if "swipe" in c]
    digits = [c for c in driver.sent if "keyevent 8" in c]  # KEYCODE_0..9 start at 7
    assert swipe, "the swipe-only unlock rung never ran"
    # R4: the plan ended with `... if digits and swipe else True`, which is true
    # whenever either list is empty -- so it stayed green with the swipe rung
    # deleted and with the PIN typed first. Both lists are required, then the
    # order is compared by real index in the one shared command log.
    assert digits, "the PIN rung never ran either, so the order below proves nothing"
    assert driver.sent.index(digits[0]) > driver.sent.index(swipe[0])


def test_a_swipe_that_opens_the_screen_never_reaches_the_pin_rung():
    driver = StubDriver(["KeyguardScrim", f"{config.PACKAGE}/.AgniDashboardActivity"])
    result = session.ensure_ready(driver, pin=PIN)
    assert result.ready is True
    assert not [c for c in driver.sent if c in PIN_KEYEVENTS], "the PIN was typed after the swipe had worked"


def test_pin_from_env_is_used_when_the_swipe_rung_fails():
    # The plan wrote `ensure_ready(driver, pin="1234", keyguard=True)`, and the
    # ladder takes no `keyguard` argument -- RED showed the TypeError. `keyguard`
    # is the stub's lock-screen flag, so it goes where the constructor puts it.
    driver = StubDriver(["KeyguardScrim"] * 4, keyguard=True)
    result = session.ensure_ready(driver, pin=PIN)
    assert result.ready is False
    assert result.failed_step == "unlock"
    assert PIN not in result.detail and PIN not in session.say(result)
    # R5: the plan never checked that the PIN rung ran, so a ladder with a dead
    # PIN branch stayed green. The keyevents are the evidence.
    assert all(k in driver.sent for k in PIN_KEYEVENTS), "the PIN was never typed as keyevents"
    assert f"input keyevent {23}" in driver.sent, "nothing submitted the PIN"


def test_the_pin_is_read_from_the_environment_only_when_the_caller_passed_nothing(monkeypatch):
    monkeypatch.setenv("REDMI_PIN", PIN)

    unset = StubDriver(["KeyguardScrim"] * 4)
    session.ensure_ready(unset)
    assert all(k in unset.sent for k in PIN_KEYEVENTS), "no env PIN was consulted, though the caller passed none"

    refused = StubDriver(["KeyguardScrim"] * 4)
    result = session.ensure_ready(refused, pin=None)
    assert not any(k in refused.sent for k in PIN_KEYEVENTS), "pin=None reached into the environment anyway"
    assert result.failed_step == "unlock"


def test_a_pin_with_a_digit_that_is_not_ascii_is_not_a_crash():
    # U+00B2 SUPERSCRIPT TWO answers `str.isdigit()` with True and `int()` with a
    # ValueError, so the plan's `isdigit` loop turns a lock screen into a traceback
    # instead of a sentence. The ASCII digits travel as keyevents and the rest are
    # skipped: a PIN that cannot be expressed as keycodes then fails the rung
    # loudly, as a wrong PIN does, rather than crashing the caller's whole turn.
    driver = StubDriver(["KeyguardScrim"] * 4)
    result = session.ensure_ready(driver, pin="12\u00b234")
    assert result.failed_step == "unlock"
    assert driver.sent.count("input keyevent 23") == 1
    typed = [c for c in driver.sent if c in PIN_KEYEVENTS + ["input keyevent 23"]]
    assert typed == PIN_KEYEVENTS + ["input keyevent 23"]
    assert "\u00b2" not in " ".join(driver.sent)


def test_a_pin_that_is_not_a_string_is_refused_before_the_phone_is_touched():
    # A PIN of the wrong type is a caller bug and reads as one, exactly as
    # `type_digits` refuses "017a". Coercion would be the worse option: it types a
    # *different* PIN than the one handed over. Nothing was sent, in either
    # direction -- the refusal comes before rung 1, not halfway up the ladder.
    driver = StubDriver(["KeyguardScrim"] * 4)
    with pytest.raises(TypeError, match="string of digits"):
        session.ensure_ready(driver, pin=1234)
    assert driver.sent == [] and driver.state_reads == [] and driver.host_calls == []

    quiet = StubDriver(["KeyguardScrim"] * 4)
    assert session.ensure_ready(quiet, pin=None).failed_step == "unlock"


def test_the_ladder_never_buys_a_second_keyguard_read():
    # R3: `is_keyguard_up()` re-reads the screen to answer a question the focus
    # string in hand has already answered. The ladder derives lock-ness from the
    # read it made for everything else, so reaching that method at all -- from
    # this ladder -- is the defect this counts.
    driver = StubDriver(["KeyguardScrim", "KeyguardScrim", f"{config.PACKAGE}/.AgniDashboardActivity"])
    session.ensure_ready(driver, pin=None)
    assert driver.keyguard_calls == 0, "a second screen read for what the first one already said"


def test_the_ladders_keyguard_rule_and_the_drivers_own_agree():
    # `_keyguard_shown` is restated rather than called, so the one thing that must
    # not happen is the two rules drifting: a ladder that stops believing in a
    # keyguard is a ladder that taps a locked screen and calls it the dashboard.
    # Checked against a real Driver, on every window name the reads return.
    for window in (
        "KeyguardScrim",
        "com.android.systemui/.statusbar.KeyguardScrim",
        "NotificationShade",
        config.DASHBOARD_ACTIVITY,
        "com.miui.home/.Launcher",
        "nothing in particular",
    ):
        driver, _ = phone([window])
        focus = driver.current_focus()
        assert driver.is_keyguard_up() == session._keyguard_shown(focus), window


def test_a_window_is_named_by_whatever_part_of_it_identifies_it():
    # The rule the app rung's sentence depends on: `.Launcher` alone names nothing,
    # and a fully-qualified class is too long to speak.
    assert session._screen("com.miui.home/.Launcher") == "com.miui.home/.Launcher"
    assert session._screen(config.DASHBOARD_ACTIVITY) == "AgniDashboardActivity"
    assert session._screen("NotificationShade") == "NotificationShade"
    assert "com.revesoft" not in session.say(
        session.Readiness(True, "", f"on {session._screen(config.DASHBOARD_ACTIVITY)}")
    )


# -- rung 4: the app, in front --


def test_ready_reports_the_activity_that_was_seen():
    driver = StubDriver(["com.miui.home/.Launcher", "com.amberit.dialer/AgniDashboardActivity"])
    result = session.ensure_ready(
        driver, dashboard="com.amberit.dialer/AgniDashboardActivity", pin=None
    )
    assert result.ready is True
    assert "AgniDashboardActivity" in result.detail


def test_a_locked_phone_with_no_pin_says_so_in_the_way_a_human_can_act_on():
    driver = StubDriver(["KeyguardScrim"] * 4, keyguard=True)
    result = session.ensure_ready(driver, pin=None)
    assert result.failed_step == "unlock"
    assert "REDMI_PIN" in session.say(result)


def test_an_app_that_is_not_in_front_is_raised_by_its_launcher():
    driver, runner = phone(
        ["com.miui.home/.Launcher", config.DASHBOARD_ACTIVITY],
    )
    result = session.ensure_ready(driver, pin=None)
    assert result.ready is True
    assert any("monkey -p com.amberit.dialer" in argv[-1] for argv in runner.argv)


def test_an_app_that_never_comes_to_the_front_names_the_screen_it_is_on():
    driver, _ = phone(["com.miui.home/.Launcher", "com.miui.home/.Launcher"])
    result = session.ensure_ready(driver, pin=None)
    assert result.ready is False
    assert result.failed_step == "app"
    assert "com.miui.home" in result.detail


@pytest.mark.parametrize("blank", ["", None], ids=["empty-string", "none"])
def test_a_blank_package_falls_back_to_the_configured_package(blank):
    # F2. `package=""` made the rung-4 test `"" in focus`, which is true of every
    # screen ever drawn: the ladder reported "Phone ready" without checking
    # anything, and `None` raised TypeError out of the function every tool calls
    # first. Blanks are a known input in this project -- `config._clean()` exists
    # because MCP hosts pass empty strings -- so the ladder normalises them the
    # same way before it compares anything.
    driver = StubDriver(["com.miui.home/.Launcher"] * 2)
    result = session.ensure_ready(driver, package=blank, pin=None)
    assert result.ready is False, "a blank package matched every screen"
    assert result.failed_step == "app"
    # The fallback is the configured package, and it was really used: the launch
    # names it, exactly once.
    assert driver.sent.count(MONKEY_LAUNCH) == 1, driver.sent


@pytest.mark.parametrize("blank", ["", None], ids=["empty-string", "none"])
def test_a_blank_dashboard_is_still_named_by_the_configured_dashboard(blank):
    # `dashboard` is not compared to the screen -- it is the name the app rung
    # gives when it says what it aimed at. Blank leaves "expected , screen
    # belongs to …", and `None` crashes `_screen` outright, so both fall back.
    driver = StubDriver(["com.miui.home/.Launcher"] * 2)
    result = session.ensure_ready(driver, dashboard=blank, pin=None)
    assert result.failed_step == "app"
    assert session._screen(config.DASHBOARD_ACTIVITY) in result.detail, result.detail


# -- the read budget (R3) --


def test_a_ready_phone_costs_one_screen_read():
    driver, runner = phone([config.DASHBOARD_ACTIVITY])
    result = session.ensure_ready(driver, pin=None)
    assert result.ready is True
    assert runner.grep_reads == 1
    # The cheap grepped read answers a ready phone, so the whole dump -- about
    # 200 KB off the handset -- must not run on the happy path.
    assert runner.whole_reads == 0


def test_a_locked_phone_that_never_opens_costs_three_reads():
    driver, runner = phone(["KeyguardScrim"] * 3)
    result = session.ensure_ready(driver, pin=PIN)
    assert result.failed_step == "unlock"
    # One read per state the ladder acts on: after wake (= before the swipe),
    # after the swipe (= before the PIN), after the PIN. The plan's shape re-read
    # inside every attempt, and swiped twice on the way.
    assert runner.grep_reads == 3
    assert runner.whole_reads == 0
    assert sum("swipe" in argv[-1] for argv in runner.argv) == 1, "the swipe rung ran twice"
    assert not any("monkey" in argv[-1] for argv in runner.argv), "rung 4 ran behind a locked screen"


def test_a_phone_the_swipe_opens_costs_two_reads():
    # The rung after unlock must reuse the read that proved the screen open: the
    # plan's ladder called `is_keyguard_up()` and then `current_focus()` for what
    # is one question with one answer.
    driver, runner = phone(["KeyguardScrim", config.DASHBOARD_ACTIVITY])
    result = session.ensure_ready(driver, pin=PIN)
    assert result.ready is True
    assert runner.grep_reads == 2
    assert runner.whole_reads == 0
    assert not any(argv[-1].startswith("input keyevent 8") for argv in runner.argv)


def test_an_app_that_has_to_be_launched_costs_exactly_one_more_read():
    driver, runner = phone(["com.miui.home/.Launcher", config.DASHBOARD_ACTIVITY])
    assert session.ensure_ready(driver, pin=None).ready is True
    assert runner.grep_reads == 2
    assert runner.whole_reads == 0


# -- say(): one speakable sentence per rung --


def test_say_is_two_short_spoken_lines_not_a_dict():
    result = session.Readiness(False, "app", "the app never came to the foreground")
    text = session.say(result)
    assert len(text.splitlines()) <= 2
    assert "{" not in text and len(text) < 140


def test_say_survives_a_detail_that_came_off_an_exception():
    # A TransportError message is multi-line and quotes reprs. The rung's answer
    # is still one sentence a voice assistant can speak, never a stack of them.
    broken = TransportError('focus read did not succeed (rc 124) for "dumpsys window": {"error": true}\nmore stderr')
    text = session.say(session.Readiness(False, "adb", str(broken)[:90]))
    assert len(text.splitlines()) <= 2
    assert "{" not in text and "}" not in text
    assert len(text) <= 140


def test_every_rung_speaks_its_own_sentence():
    # "Phone is locked" and "phone is offline" are different sentences, which is
    # the whole reason this ladder reports the rung rather than a boolean.
    sentences = [session.say(session.Readiness(False, step, "detail")) for step in ("adb", "unlock", "app", "session")]
    assert len(set(sentences)) == 4
    assert "wireless debugging" in sentences[0]
    unknown = session.say(session.Readiness(False, "no-such-rung", "detail"))
    assert unknown == "the phone is not ready. detail"


# -- the constraints the spec binds every task to --


def test_ensure_ready_takes_no_splash_parameter():
    # R6: the body never read it. A parameter nobody reads is a promise nobody
    # keeps, and the fix is to drop it rather than to invent a use.
    assert "splash" not in inspect.signature(session.ensure_ready).parameters


def test_nothing_the_ladder_sends_can_place_a_call():
    runs = [
        StubDriver([config.DASHBOARD_ACTIVITY]),                                  # ready
        StubDriver(["KeyguardScrim"] * 4, keyguard=True),                         # locked, no PIN
        StubDriver(["com.miui.home/.Launcher", "com.miui.home/.Launcher"]),       # app never surfaces
        StubDriver([DASHBOARD], state="error: device offline\n"),                 # dead serial
    ]
    for driver in runs:
        session.ensure_ready(driver, dashboard=DASHBOARD, pin=PIN)
        for command in driver.sent:
            for forbidden in FORBIDDEN:
                assert forbidden not in command, command


def test_the_pin_appears_in_no_command_no_message_and_no_repr():
    # Asserted, not assumed: the digits travel as keyevent codes, so the literal
    # PIN string exists nowhere in what the ladder records, returns, or says.
    driver = StubDriver(["KeyguardScrim"] * 4, keyguard=True)
    result = session.ensure_ready(driver, pin=PIN)
    assert result.ready is False
    assert PIN not in " ".join(driver.sent), "the PIN travelled as text, not as keyevents"
    assert PIN not in result.detail
    assert PIN not in session.say(result)
    assert PIN not in repr(result)
