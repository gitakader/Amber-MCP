"""The MCP server, tested with no phone attached.

Task 8 ships exactly one tool -- `status` -- because it needs no selectors. The
point of the file is to prove the server, the serial plumbing and the driver
layering all speak before any call logic exists.

The stubs here carry more than `status` itself uses, because `status` hands the
hard part to `session.ensure_ready`, and that ladder reaches for five driver
methods: `adb`, `host_adb`, `shell`, `keyevent`, `current_focus`. A stub that
implements only some of them raises AttributeError inside rung 1 and reads like
a product bug rather than a test-harness gap. `adb`/`host_adb` answers are
scriptable so the locked-phone and offline-phone sentences can be provoked
separately -- that separation is the whole reason the ladder reports a rung and
not a boolean.
"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import get_type_hints

import pytest

from amber_mcp import adb, config, server
from amber_mcp.driver import ShellResult, TransportError

SERIAL = "192.0.2.9:5555"
# A stand-in for the adb binary, so a test that builds a real Driver never asks
# the host where adb lives (`adb.adb_path()` also publishes its answer into
# ADBUTILS_ADB_PATH, which is process state no unit test should touch).
FAKE_ADB = "C:/fake/platform-tools/adb.exe"
DASHBOARD = "com.amberit.dialer/.AgniDashboardActivity"
LAUNCHER = "com.miui.home/.Launcher"
KEYGUARD = "com.android.systemui/.statusbar.KeyguardScrim"

# A PIN that is genuinely in the environment while `status` runs, so the ladder's
# PIN rung has something to reach for. `PIN_CODES` is every keyevent that rung
# would put on the wire: one per digit, plus the centre key that submits them.
PIN = "987654"
PIN_CODES = frozenset(server.session.KEYCODE_0 + int(d) for d in PIN) | {
    server.session.KEYCODE_DPAD_CENTER
}

# The commands that would put a call on the phone. Nothing in this build may
# contain any of them.
FORBIDDEN = ("am start", "ACTION_CALL", "ACTION_DIAL", "tel:")


class StubDriver:
    """The full Driver surface both `status_tool` and the ladder touch, in one fake.

    `state` is what `adb(["get-state"])` prints, so an offline phone is a
    constant away. `focus` is what every screen read returns, so the locked and
    app-not-front rungs can each be aimed at directly. `sent` records writes so
    the never-dials rule has something concrete to check against. `reads` and
    `connects` count the two things the read-once rule and rung 1's reconnect are
    defined by: a status call that reads the screen twice, or that never calls
    `connect` at all, is exactly what they are there to catch.
    """

    serial = SERIAL

    def __init__(self, focus=DASHBOARD, state="device\n", sent=None):
        self._focus = focus
        self._state = state
        self.sent = sent if sent is not None else []
        self.reads = 0
        self.connects = []

    def current_focus(self):
        self.reads += 1
        if isinstance(self._focus, TransportError):
            raise self._focus
        return self._focus

    def is_keyguard_up(self):
        return "Keyguard" in self.current_focus() or self.current_focus() == "NotificationShade"

    def adb(self, args, timeout=None):
        return ShellResult(0, self._state, "")

    def host_adb(self, args, timeout=None):
        self.connects.append(list(args))
        return ShellResult(0, f"connected to {self.serial}\n", "")

    def shell(self, remote_cmd, timeout=None):
        self.sent.append(remote_cmd)
        return ShellResult(0, "", "")

    def keyevent(self, code):
        self.sent.append(f"input keyevent {code}")


def use(monkeypatch, driver):
    monkeypatch.setattr(server, "get_driver", lambda: driver)


@pytest.fixture(autouse=True)
def _no_real_waits_and_a_clean_registry(monkeypatch):
    # `status_tool` hands the hard part to `session.ensure_ready`, which sleeps
    # between rungs; the suite must not wait on those clocks. And the module-level
    # Driver singleton plus the serial env are per-process state, so each test
    # starts from none of them -- otherwise a lazily-built driver or a leftover
    # serial leaks between tests and a green run proves nothing about isolation.
    monkeypatch.setattr(server.session.time, "sleep", lambda *_: None)
    monkeypatch.delenv("AMBER_PHONE_SERIAL", raising=False)
    monkeypatch.delenv("REDMI_PIN", raising=False)
    monkeypatch.setattr(server, "_driver", None)


# -- one spoken line, no JSON, names what it saw -----------------------------


def test_status_is_one_spoken_line(monkeypatch):
    use(monkeypatch, StubDriver())
    text = asyncio.run(server.status_tool())
    assert len(text.splitlines()) <= 3 and "{" not in text and "}" not in text


def test_status_names_the_activity_it_saw(monkeypatch):
    use(monkeypatch, StubDriver(focus=DASHBOARD))
    assert "AgniDashboardActivity" in asyncio.run(server.status_tool())


def test_the_server_returns_a_string_not_a_structured_result():
    # `from __future__ import annotations` makes the raw signature a string, so
    # the check resolves the hint; the tool speaks, it does not return a dict.
    assert get_type_hints(server.status_tool)["return"] is str


# -- a dead phone is described, not reported as idle -------------------------


def test_a_dead_phone_is_described_not_reported_as_idle(monkeypatch):
    def boom():
        raise ConnectionError(f"no uiautomator2 session on {SERIAL}")

    monkeypatch.setattr(server, "get_driver", boom)
    out = asyncio.run(server.status_tool())
    assert "phone" in out.lower()
    assert len(out) < 160 and "{" not in out


def test_a_missing_serial_names_the_variable_and_never_tracebacks(monkeypatch):
    monkeypatch.setenv("AMBER_PHONE_SERIAL", "")
    with pytest.raises(ConnectionError) as exc:
        server.get_driver()
    assert "AMBER_PHONE_SERIAL" in str(exc.value)


def test_a_missing_serial_reaches_the_caller_as_a_sentence(monkeypatch):
    def no_serial():
        raise ConnectionError(
            "No phone configured. Set AMBER_PHONE_SERIAL to <ip>:<port> after `adb connect`."
        )

    monkeypatch.setattr(server, "get_driver", no_serial)
    out = asyncio.run(server.status_tool())
    assert "AMBER_PHONE_SERIAL" in out and "{" not in out and len(out) < 160


# -- the three sentences must be three different sentences (carry-forward 2) --


def test_the_lock_screen_gets_its_own_sentence(monkeypatch):
    use(monkeypatch, StubDriver(focus=KEYGUARD))
    assert "lock" in asyncio.run(server.status_tool()).lower()


def test_a_broken_read_is_the_wireless_sentence_not_a_generic_one(monkeypatch):
    # A hung dumpsys raises TransportError out of `current_focus`. It must not be
    # swallowed into "phone not ready": "wireless debugging dropped" is the truth,
    # and it is what a human can act on.
    use(monkeypatch, StubDriver(focus=TransportError("focus read did not succeed (rc 124)")))
    out = asyncio.run(server.status_tool())
    assert "wireless debugging" in out
    assert "lock" not in out.lower()


def test_an_app_that_is_not_on_screen_gets_the_app_sentence(monkeypatch):
    use(monkeypatch, StubDriver(focus=LAUNCHER))
    out = asyncio.run(server.status_tool())
    assert "AmberIT" in out
    assert "lock" not in out.lower()


def test_the_three_failure_sentences_are_mutually_distinct(monkeypatch):
    locked = asyncio.run(_one(monkeypatch, StubDriver(focus=KEYGUARD)))
    offline = asyncio.run(_one(monkeypatch, StubDriver(focus=TransportError("rc 124 hung"))))
    app = asyncio.run(_one(monkeypatch, StubDriver(focus=LAUNCHER)))
    assert len({locked, offline, app}) == 3


async def _one(monkeypatch, driver):
    use(monkeypatch, driver)
    return await server.status_tool()


# -- the ladder's adb rung is not re-swallowed (carry-forward 2) -------------


def test_status_does_not_catch_transporterror_into_the_reach_string(monkeypatch):
    # The driver raises TransportError for a broken read; the ladder converts it
    # to the adb rung with a human sentence. `status_tool` must not then catch it
    # back into "I can't reach the phone" -- those are different claims, and the
    # reach string belongs to a get_driver failure (no session), never to a read.
    use(monkeypatch, StubDriver(focus=TransportError("focus read did not succeed")))
    out = asyncio.run(server.status_tool())
    assert "can't reach" not in out.lower()


def test_a_dropped_serial_speaks_the_adb_rung(monkeypatch):
    # get-state offline (rc 0 with the refusal in stdout) is rung-1 news.
    driver = DroppedSerialDriver(revives=False)
    use(monkeypatch, driver)
    out = asyncio.run(server.status_tool())
    assert "wireless debugging" in out
    assert driver.connects == [["connect", SERIAL]], "rung 1 never tried its own repair"


class DroppedSerialDriver(StubDriver):
    """A wireless serial whose adb has gone -- and so has every screen read.

    Task 8's first version paired a dead `get-state` with screen reads that kept
    answering, a combination the real driver cannot produce: with the serial off
    adb, `current_focus()` reads a refusal and raises `TransportError`. That stub
    proved the sequence was reachable only because it wrote the outcome itself,
    which is why `status_tool`'s own pre-read no longer front-runs the ladder --
    here the ladder is the only thing that can be reached at all, and the only one
    that owns a repair. `revives` says whether the `connect` brings the handset
    back; the whole point of rung 1 is that the reconnect is asked before the
    serial is called gone.
    """

    def __init__(self, focus=DASHBOARD, revives=True):
        super().__init__(focus=focus)
        self.revives = revives
        self.attached = False

    def adb(self, args, timeout=None):
        if not self.attached:
            return ShellResult(0, "error: device offline\n", "")
        return ShellResult(0, "device\n", "")

    def host_adb(self, args, timeout=None):
        self.connects.append(list(args))
        if self.attached or not args or args[0] != "connect":
            return ShellResult(0, "", "")
        self.attached = self.revives
        return ShellResult(0, f"connected to {self.serial}\n", "")

    def current_focus(self):
        if not self.attached:
            raise TransportError(f"focus read did not succeed (rc 124) on {self.serial}")
        return super().current_focus()


def test_status_runs_the_reconnect_rather_than_advising_it(monkeypatch):
    # The case this design exists for: a wifi serial that dropped (or came back on
    # a new port). `status` is the tool a voice assistant reaches for precisely
    # then, so it has to do the reconnect it is capable of -- not hand the human a
    # `Reconnect wireless debugging.` instruction it could have carried out itself.
    driver = DroppedSerialDriver()
    use(monkeypatch, driver)
    out = asyncio.run(server.status_tool())
    assert driver.connects == [["connect", SERIAL]], "rung 1's reconnect never fired"
    assert "ready" in out.lower(), out
    assert "wireless debugging" not in out


def test_status_buys_no_screen_read_the_ladder_has_not_already_paid_for(monkeypatch):
    # The read-once rule that governs session.py governs this file too: a ready
    # phone costs exactly the one `dumpsys window` rung 2 already made. A pre-read
    # here would spend a second one -- and when its grep prints nothing, a second
    # 200 KB whole-dump fallback -- to answer a question the ladder answers anyway.
    driver = StubDriver(focus=DASHBOARD)
    use(monkeypatch, driver)
    asyncio.run(server.status_tool())
    assert driver.reads == 1, f"status read the screen {driver.reads} times"


# -- the tool surface (fastmcp 2.x, carry-forward 5) -------------------------


def test_exactly_one_tool_is_exposed_in_this_plan():
    tools = asyncio.run(server.mcp.get_tools())
    # fastmcp 2.14's `get_tools()` is a coroutine returning dict[str, Tool]; the
    # brief's `{t.name for t in <dict>}` would iterate the keys (strings). The
    # proven assertion reads the values.
    assert {t.name for t in tools.values()} == {"status"}


def test_the_status_tool_is_marked_read_only():
    tools = asyncio.run(server.mcp.get_tools())
    assert tools["status"].annotations.readOnlyHint is True


def test_the_read_only_hint_is_honest_about_the_side_effects_left():
    # `readOnlyHint` is a licence for a client to run the tool without asking, so
    # the description has to enumerate what the tool really does. Under the ladder
    # that is three actions, not one: rung 2's `KEYCODE_WAKEUP`, rung 3's unlock
    # swipe and rung 4's `monkey` launch -- all of them run under the hint, and all
    # benign, but a description naming only the wake under-states the two that
    # change what is on screen. Each one is pinned by its own assert, so a
    # description that quietly drops the swipe or the launch fails here rather than
    # keeping this test green on the strength of the word "wake". Flipping the hint
    # to false instead would make every voice client demand confirmation on a read,
    # which is the use case this tool exists for -- so `readOnlyHint` stays true and
    # the actions are said out loud, with `destructiveHint=False` stating the
    # precise half of the claim the read-only hint alone only implies.
    tools = asyncio.run(server.mcp.get_tools())
    status = tools["status"]
    description = status.description.lower()
    assert status.annotations.readOnlyHint is True
    assert status.annotations.destructiveHint is False, (
        "the actions are disclosed but the hint that says they are not destructive "
        "is missing"
    )
    assert "wake" in description, status.description
    assert "swipe" in description, status.description
    assert "launch" in description, status.description
    assert "pin" in description, "a read that could type the PIN must say it does not"
    assert "never" in description, status.description


# -- one driver per process, never built at import (spec) --------------------


def test_importing_the_server_needs_no_phone_attached():
    # A GUI MCP host launches this server before the phone is plugged in, so the
    # constraint is that `import amber_mcp.server` succeeds with nothing to talk
    # to. Asserting it in-process on `server._driver` proves only that a fixture
    # zeroed a global; a subprocess with the serial and the PIN scrubbed from the
    # environment, the real first-launch condition, proves the module imports --
    # or fails loudly, which is the bug this guards.
    env = {k: v for k, v in os.environ.items() if k not in {"AMBER_PHONE_SERIAL", "REDMI_PIN"}}
    root = Path(server.__file__).resolve().parents[1]
    done = subprocess.run(
        [sys.executable, "-c", "import amber_mcp.server"],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert done.returncode == 0, f"import crashed with no phone attached: {done.stderr[-1500:]}"


def test_get_driver_builds_one_driver_and_reuses_it(monkeypatch):
    built = []

    class RecordingDriver:
        def __init__(self, serial):
            built.append(serial)
            self.serial = serial

        def connect(self):
            pass

    monkeypatch.setattr(server, "Driver", RecordingDriver)
    monkeypatch.setenv("AMBER_PHONE_SERIAL", SERIAL)
    server._driver = None
    try:
        first = server.get_driver()
        second = server.get_driver()
    finally:
        server._driver = None
    assert first is second
    assert built == [SERIAL], "one phone was driven twice"


# -- the never-dials rule, in both directions --------------------------------


def test_no_command_status_sends_can_place_a_call(monkeypatch):
    sent = []
    use(monkeypatch, StubDriver(focus=LAUNCHER, sent=sent))
    asyncio.run(server.status_tool())
    for command in sent:
        for forbidden in FORBIDDEN:
            assert forbidden not in command, command


def test_the_server_module_contains_no_dial_intent():
    source = (Path(server.__file__).read_text(encoding="utf-8"))
    for forbidden in FORBIDDEN:
        assert forbidden not in source


# -- the PIN is never typed by a status poll (F3) ----------------------------


def test_status_never_types_the_pin_even_where_the_pin_rung_is_reachable(monkeypatch):
    # The only shape in which `status` can reach the PIN rung at all: a keyguard
    # that survives the swipe. The old version of this test aimed at a locked
    # screen and returned before the ladder ran, so it asserted the digit string
    # was absent from a sentence that never had a chance to type anything -- it
    # could not fail. Here the PIN is in the environment, the ladder is one
    # keyevent away from using it, and what stops it is `pin=None` at the call
    # site: a status poll does not unlock by force, so the proof is the keyevents
    # that never went out, not merely the absence of a digit from the text.
    monkeypatch.setenv("REDMI_PIN", PIN)
    sent: list[str] = []
    use(monkeypatch, StubDriver(focus=KEYGUARD, sent=sent))
    out = asyncio.run(server.status_tool())

    codes = {int(line.rsplit(" ", 1)[1]) for line in sent if line.startswith("input keyevent")}
    assert PIN_CODES.isdisjoint(codes), f"status typed the PIN with {sorted(codes)}"
    assert PIN not in out
    assert "lock" in out.lower()


# -- the per-action ceiling is what a call uses (carry-forward 4) ------------


def test_the_action_ceiling_is_the_timeout_the_wire_actually_gets(monkeypatch):
    # `inspect.signature` defaults only report what a call *would* use if nothing
    # shadowed them, and `>=` on the reconnect passed happily when host_adb's
    # generous timeout was cut to the ceiling exactly -- the regression
    # carry-forward 4 asked to be provable. So each public method is run through a
    # spy on the driver's one run seam, and the timeout it hands `subprocess.run`
    # is what gets asserted: the ceiling for the two actions a voice turn waits
    # on, and strictly more for the reconnect that is allowed to be slow.
    #
    # The binary resolution is stubbed alongside it, because a real `Driver` asks
    # `adb.adb_path()` for the binary before it reaches the spy: that makes this
    # test fail for want of platform-tools on whatever host runs it, and -- worse
    # -- `adb_path()` publishes its answer into `ADBUTILS_ADB_PATH`, so a ceiling
    # test mutates the process environment the rest of the suite reads (see
    # conftest.py). Neither is news about timeouts, which is all this test asserts.
    seen = []
    resolutions = []

    def spy(self, argv, timeout):
        seen.append((list(argv), timeout))
        return ShellResult(0, "device\n", "")

    def fake_adb_path():
        resolutions.append(1)
        return FAKE_ADB

    monkeypatch.setattr(adb, "adb_path", fake_adb_path)
    monkeypatch.setattr(server.Driver, "_run", spy)
    driver = server.Driver(SERIAL)
    driver.adb(["get-state"])
    driver.shell("dumpsys window | grep mCurrentFocus")
    driver.host_adb(["connect", SERIAL])

    assert isinstance(config.ACTION_TIMEOUT_SECONDS, (int, float))
    assert config.ACTION_TIMEOUT_SECONDS < 30, "a voice assistant cannot wait 30s"
    (adb_argv, adb_timeout), (shell_argv, shell_timeout), (host_argv, host_timeout) = seen
    assert adb_timeout == shell_timeout == config.ACTION_TIMEOUT_SECONDS
    assert host_timeout > config.ACTION_TIMEOUT_SECONDS, "the reconnect lost its generous timeout"
    assert host_timeout == 30.0, "the reconnect timeout moved off the value it was set for"
    # The serial is pinned on a device action and absent from a host one, or the
    # timeouts above belong to commands that would not have run anyway.
    assert "-s" in adb_argv and "-s" in shell_argv and "-s" not in host_argv
    # And the argv the timeouts were paid for is the stub's binary, resolved once
    # and cached -- proof the spy saw this driver's own commands, not a host adb's.
    assert [argv[0] for argv, _ in seen] == [FAKE_ADB] * 3
    assert resolutions == [1], "the binary was resolved once per command, not once per driver"
    assert "ADBUTILS_ADB_PATH" not in os.environ, "the ceiling test published into the env"


# -- the server starts with no device attached (spec) ------------------------


def test_the_server_answers_a_call_with_no_device_attached(monkeypatch):
    # Startup behaviour, not object existence: the first thing a host does after
    # launching this server with the phone away is call `status`, and it must get
    # a sentence naming the variable -- through the real `get_driver()`, with no
    # stub in the way. The subprocess test above covers the import itself.
    monkeypatch.delenv("AMBER_PHONE_SERIAL", raising=False)
    out = asyncio.run(server.status_tool())
    assert "AMBER_PHONE_SERIAL" in out
    assert "Traceback" not in out and len(out) < 160
    assert "{" not in out and "}" not in out

