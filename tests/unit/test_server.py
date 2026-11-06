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
import inspect
from pathlib import Path
from typing import get_type_hints

import pytest

from amber_mcp import config, server
from amber_mcp.driver import ShellResult, TransportError

SERIAL = "192.0.2.9:5555"
DASHBOARD = "com.amberit.dialer/.AgniDashboardActivity"
LAUNCHER = "com.miui.home/.Launcher"
KEYGUARD = "com.android.systemui/.statusbar.KeyguardScrim"

# The commands that would put a call on the phone. Nothing in this build may
# contain any of them.
FORBIDDEN = ("am start", "ACTION_CALL", "ACTION_DIAL", "tel:")


class StubDriver:
    """The full Driver surface both `status_tool` and the ladder touch, in one fake.

    `state` is what `adb(["get-state"])` prints, so an offline phone is a
    constant away. `focus` is what every screen read returns, so the locked and
    app-not-front rungs can each be aimed at directly. `sent` records writes so
    the never-dials rule has something concrete to check against.
    """

    serial = SERIAL

    def __init__(self, focus=DASHBOARD, state="device\n", sent=None):
        self._focus = focus
        self._state = state
        self.sent = sent if sent is not None else []

    def current_focus(self):
        if isinstance(self._focus, TransportError):
            raise self._focus
        return self._focus

    def is_keyguard_up(self):
        return "Keyguard" in self.current_focus() or self.current_focus() == "NotificationShade"

    def adb(self, args, timeout=None):
        return ShellResult(0, self._state, "")

    def host_adb(self, args, timeout=None):
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
    use(monkeypatch, OfflineDriver())
    out = asyncio.run(server.status_tool())
    assert "wireless debugging" in out


class OfflineDriver(StubDriver):
    """Answers every screen read as if the phone were up, but `get-state` is dead.

    `status_tool`'s own first read succeeds, so the failure surfaces only once
    the ladder asks adb whether the device is there -- which is exactly the
    dropped-wifi-serial-after-the-port-changed case that motivated this task.
    """

    def __init__(self, focus=DASHBOARD):
        super().__init__(focus=focus)

    def adb(self, args, timeout=None):
        return ShellResult(0, "error: device offline\n", "")


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


# -- one driver per process, never built at import (spec) --------------------


def test_the_driver_is_not_constructed_at_import_time():
    # An MCP host starts the server before the phone is plugged in; importing
    # must not touch adb at all.
    assert server._driver is None


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


# -- the PIN never appears (spec) --------------------------------------------


def test_the_pin_never_appears_in_the_status_output(monkeypatch):
    monkeypatch.setenv("REDMI_PIN", "987654")
    use(monkeypatch, StubDriver(focus=KEYGUARD))
    assert "987654" not in asyncio.run(server.status_tool())


# -- the per-action ceiling is what a call uses (carry-forward 4) ------------


def test_the_action_ceiling_is_a_config_value():
    assert isinstance(config.ACTION_TIMEOUT_SECONDS, (int, float))
    assert config.ACTION_TIMEOUT_SECONDS < 30, "a voice assistant cannot wait 30s"

    # The driver's per-action default is the ceiling...
    assert inspect.signature(server.Driver.adb).parameters["timeout"].default == (
        config.ACTION_TIMEOUT_SECONDS
    )
    assert inspect.signature(server.Driver.shell).parameters["timeout"].default == (
        config.ACTION_TIMEOUT_SECONDS
    )
    # ...while the reconnect keeps the generous host-command timeout.
    assert inspect.signature(server.Driver.host_adb).parameters["timeout"].default >= (
        config.ACTION_TIMEOUT_SECONDS
    )


# -- the server starts with no device attached (spec) ------------------------


def test_the_server_starts_without_a_device_attached(monkeypatch):
    monkeypatch.setenv("AMBER_PHONE_SERIAL", "")
    assert server.mcp is not None
