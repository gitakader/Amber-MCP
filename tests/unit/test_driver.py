"""Driver tests: a fake runner, a fake device, and no phone attached.

Nothing in this file taps, types, wakes, screenshots or dumps the real device.
The live phone belongs to Tasks 6 and 7; touching it from a unit run would be
invisible here and expensive there.

Non-ASCII payloads are spelled with escapes throughout, because a Windows
console on cp1252 cannot print them and a crashed test report proves nothing.
"""

import dataclasses
import io
import os
import pathlib
import sys

import pytest
from PIL import Image

from amber_mcp import adb
from amber_mcp import driver as driver_module
from amber_mcp.driver import Driver, ShellResult, TransportError

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PNG_IEND = b"IEND\xaeB\x60\x82"

# "Rafi" in Bangla -- a plausible contact name for this app's address book.
RAFI = "\u09b0\u09be\u09ab\u09bf"
# U+09E7 BENGALI DIGIT FIVE: a digit to a human, not ASCII, and so not dialable.
BENGALI_FIVE = "\u09eb"

UNSET = object()

DUMPSYS_WINDOW = "dumpsys window"
FOCUS_GREP = "dumpsys window | grep mCurrentFocus"
FOCUS_ACTIVITY_LINE = "  mCurrentFocus=Window{d08d5f0 u0 com.amberit.dialer/" \
                      "com.revesoft.itelmobiledialer.ui2.dashboard.AgniDashboardActivity}\n"


def png_image(size=(4, 4)):
    return Image.new("RGB", size, "red")


def png_bytes(size=(4, 4)):
    buf = io.BytesIO()
    png_image(size).save(buf, format="PNG")
    return buf.getvalue()


class FakeRunner:
    """Records argv and replays scripted answers, so no phone is needed."""

    def __init__(self, answers=None):
        self.calls: list[list[str]] = []
        self.answers = answers or {}

    def __call__(self, argv):
        self.calls.append(argv)
        for needle, result in self.answers.items():
            if needle in " ".join(argv):
                return result
        return ShellResult(0, "", "")


class ScriptedRunner(FakeRunner):
    """Answers by the exact remote command, so a test can prove which read ran.

    FakeRunner matches a substring, which cannot tell FOCUS_GREP apart from
    DUMPSYS_WINDOW: the whole-dump command is a prefix of the grepped one.
    """

    def __call__(self, argv):
        self.calls.append(argv)
        return self.answers.get(argv[-1], ShellResult(0, "", ""))


def make(serial="192.0.2.9:5555", answers=None):
    runner = FakeRunner(answers)
    return Driver(serial, runner=runner), runner


class FakeDevice:
    """Stands in for a uiautomator2 Device, with the three calls Driver makes."""

    def __init__(self, xml='<hierarchy rotation="0"><node/></hierarchy>', image=UNSET):
        self.xml = xml
        self.image = png_image() if image is UNSET else image
        self.sent: list[str] = []
        self.shells: list[str] = []
        self.screenshot_calls = 0
        self.screenshot_formats: list[str] = []
        self.dump_calls = 0

    @property
    def info(self):
        return {"displayWidth": 1080, "displayHeight": 2400}

    def send_keys(self, text, clear=False):
        self.sent.append(text)

    def shell(self, remote_cmd):
        self.shells.append(remote_cmd)
        return ShellResult(0, "", "")

    def dump_hierarchy(self, **kwargs):
        self.dump_calls += 1
        return self.xml

    def screenshot(self, filename=None, format="pillow", display_id=None):
        self.screenshot_calls += 1
        self.screenshot_formats.append(format)
        return self.image


def with_device(image=UNSET, xml='<hierarchy rotation="0"><node/></hierarchy>', answers=None):
    """A Driver connected through the connector seam to a FakeDevice."""
    runner = FakeRunner(answers)
    device = FakeDevice(xml=xml, image=image)
    driver = Driver("192.0.2.9:5555", runner=runner, connector=lambda serial: device)
    driver.connect()
    assert driver.is_connected is True
    return driver, runner, device


# -- the reads the spec says must never be trusted naively --


def test_current_focus_parses_the_activity_off_a_real_dumpsys_line():
    driver, _ = make(answers={"mCurrentFocus": ShellResult(0, FOCUS_ACTIVITY_LINE, "")})
    assert driver.current_focus().endswith("AgniDashboardActivity")


def test_current_focus_names_a_panel_instead_of_returning_empty():
    # NotificationShade has no activity. An empty string here reads as "nothing
    # is on screen", which is a different claim from "a panel owns the screen".
    driver, _ = make(answers={
        "mCurrentFocus": ShellResult(0, "  mCurrentFocus=Window{e46f4a u0 NotificationShade}\n", "")
    })
    assert driver.current_focus() == "NotificationShade"


def test_zero_byte_hierarchy_is_a_failure_not_an_empty_screen():
    # MIUI prints "UI hierchary dumped to:" and writes nothing.
    driver, _ = make()
    driver._hierarchy_override = ""
    with pytest.raises(RuntimeError, match="empty"):
        driver.dump_hierarchy()


def test_zero_byte_screenshot_raises_rather_than_returning_a_blank_frame():
    # Observed directly: screencap gives 0 bytes while the display is dozing.
    driver, _ = make(answers={"screencap": ShellResult(0, "", "")})
    driver._screenshot_override = b""
    with pytest.raises(RuntimeError, match="no image"):
        driver.screenshot()


def test_dumpsys_is_read_whole_then_filtered_in_python():
    # Mark-LV: closing dumpsys's pipe early makes it write
    # "Failed to write while dumping service" into the answer.
    driver, runner = make(answers={"dumpsys telephony.registry": ShellResult(0, "mCallState=0\n", "")})
    driver.shell("dumpsys telephony.registry")
    assert not any("head" in " ".join(c) for c in runner.calls)
    assert not any("-m 1" in " ".join(c) for c in runner.calls)


def test_keyguard_is_detected_from_its_own_window_not_guessed():
    driver, _ = make(answers={
        "mCurrentFocus": ShellResult(0, "  mCurrentFocus=Window{1 u0 KeyguardScrim}\n", "")
    })
    assert driver.is_keyguard_up() is True


def test_shell_result_ok_is_rc_zero_alone():
    assert ShellResult(0, "ignored", "").ok is True
    assert ShellResult(1, "", "nope").ok is False


def test_unknown_device_is_reported_not_silently_retried():
    driver, _ = make(answers={"get-state": ShellResult(0, "error: device offline\n", "")})
    assert driver.adb(["get-state"]).out.strip() == "error: device offline"


def test_every_command_carries_the_serial():
    # An un-pinned serial is how two phones get driven by accident.
    driver, runner = make()
    driver.shell("input keyevent 26")
    assert runner.calls[-1][:3] == ["adb", "-s", "192.0.2.9:5555"]


def test_keyevent_and_tap_are_shell_shape():
    driver, runner = make()
    driver.keyevent(26)
    driver.tap_xy(540, 1200)
    assert runner.calls[-2][-1] == "input keyevent 26"
    assert runner.calls[-1][-1] == "input tap 540 1200"


# -- R2: host-level adb, where the serial is an argument, not a selector --


def test_host_adb_issues_connect_with_no_serial_selector():
    # `adb -s <serial> connect <serial>` is not a command that means anything:
    # `-s` selects a device that is already attached, and `connect` is how it
    # gets attached. Task 4's reconnect rung goes through here precisely so the
    # ladder cannot lean on adb tolerating an ignored flag.
    driver, runner = make()
    driver.host_adb(["connect", driver.serial])
    argv = runner.calls[-1]
    assert argv[0] == "adb"
    assert "-s" not in argv
    assert argv[1:] == ["connect", "192.0.2.9:5555"]


def test_host_adb_still_pins_nothing_and_adb_still_pins_everything():
    # The pair that keeps this from being a general "drop the serial" loophole:
    # a host command carries no selector, a device command always does.
    driver, runner = make()
    driver.host_adb(["devices"])
    driver.shell("input keyevent 26")
    assert runner.calls[-2] == ["adb", "devices"]
    assert runner.calls[-1][:3] == ["adb", "-s", "192.0.2.9:5555"]


def test_host_adb_uses_the_resolved_binary_like_every_other_command(monkeypatch):
    # Same resolution seam as `_argv`: a GUI-launched host with adb off PATH is
    # exactly as broken for `connect` as it is for `shell`.
    seen = []
    monkeypatch.setattr(adb, "adb_path", lambda: "C:/sdk/platform-tools/adb.exe")
    monkeypatch.setattr(
        driver_module, "_subprocess_runner",
        lambda argv, timeout: seen.append(argv) or ShellResult(0, "", ""),
    )
    Driver("192.0.2.9:5555").host_adb(["connect", "192.0.2.9:5555"])
    assert seen[0][0] == "C:/sdk/platform-tools/adb.exe"
    assert seen[0][1:] == ["connect", "192.0.2.9:5555"]


def test_host_adb_answers_with_the_shell_result_the_command_actually_got():
    # `adb connect` prints its refusal in stdout, so the caller needs rc and text
    # back, exactly as `adb()` gives them. Nothing here is swallowed.
    driver, _ = make(answers={"connect": ShellResult(0, "failed to connect to 192.0.2.9:5555\n", "")})
    result = driver.host_adb(["connect", "192.0.2.9:5555"])
    assert result.ok is True
    assert "failed to connect" in result.out


# -- the seam contract: a frozen result, and the real subprocess path --


def test_shell_result_is_frozen():
    result = ShellResult(0, "out", "")
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.rc = 1


def test_the_resolved_adb_binary_replaces_the_literal_one(monkeypatch):
    # PATH-only resolution is what made Android-MCP report "adb not found" from a
    # GUI-launched host, so the runner-less path must use adb.adb_path()'s answer
    # rather than the bare "adb" the seam is shown.
    seen = []
    monkeypatch.setattr(adb, "adb_path", lambda: "C:/sdk/platform-tools/adb.exe")
    monkeypatch.setattr(
        driver_module, "_subprocess_runner",
        lambda argv, timeout: seen.append(argv) or ShellResult(0, "", ""),
    )
    Driver("192.0.2.9:5555").shell("dumpsys window")
    assert seen[0][0] == "C:/sdk/platform-tools/adb.exe"
    assert seen[0][1:3] == ["-s", "192.0.2.9:5555"]


def test_the_shell_timeout_reaches_the_process_not_just_the_signature(monkeypatch):
    # A `timeout` parameter dropped on the floor is a hang waiting to happen
    # against a dumpsys that never returns.
    seen = {}

    def spy(argv, timeout):
        seen["timeout"] = timeout
        return ShellResult(0, "", "")

    monkeypatch.setattr(adb, "adb_path", lambda: "C:/fake/platform-tools/adb.exe")
    driver = Driver("192.0.2.9:5555")
    monkeypatch.setattr(driver_module, "_subprocess_runner", spy)
    driver.shell("dumpsys window", timeout=3.5)
    assert seen["timeout"] == 3.5
    driver.adb(["get-state"])
    assert seen["timeout"] == 30.0


def test_a_binary_that_vanished_after_it_was_resolved_is_a_result_not_a_traceback(tmp_path):
    # adb.adb_path() checks the file exists; the file can still go afterwards.
    # A read must answer with an rc, not with a Python stack, or the caller
    # cannot tell a dead tool from a dead phone.
    gone = str(tmp_path / "deleted-adb.exe")
    result = driver_module._subprocess_runner([gone, "-s", "192.0.2.9:5555", "get-state"], timeout=5)
    assert result.ok is False
    assert result.rc == 127
    assert "not found" in result.err


def test_a_hung_command_times_out_instead_of_running_forever():
    # Real subprocess, real clock: rc 124 and an explanation, never a hang.
    result = driver_module._subprocess_runner(
        [sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.3
    )
    assert result.ok is False
    assert result.rc == 124
    assert "timed out" in result.err


def test_stdout_is_decoded_without_losing_the_line_structure():
    # adb hands back CRLF on Windows and bytes that are not always valid UTF-8.
    # Both must arrive as lines the parser can read, never as a crash.
    script = (
        "import sys;"
        "sys.stdout.buffer.write(b'  mCurrentFocus=Window{a u0 b.Activity}\\r\\n\\xff\\xfe odd')"
    )
    result = driver_module._subprocess_runner([sys.executable, "-c", script], timeout=30)
    assert result.ok is True
    assert "\r" not in result.out
    assert result.out.splitlines()[0] == "  mCurrentFocus=Window{a u0 b.Activity}"
    assert result.out.endswith(" odd")
    assert result.out.count("\ufffd") == 2


# -- ruling 1: the screenshot may not travel through the shell --


def test_screenshot_comes_from_the_session_and_never_from_the_shell():
    driver, runner, device = with_device()
    blob = driver.screenshot()
    assert device.screenshot_calls == 1
    assert not any("screencap" in " ".join(c) for c in runner.calls)
    assert blob.startswith(PNG_MAGIC)


def test_the_session_image_becomes_a_decodable_png():
    driver, _, device = with_device()
    blob = driver.screenshot()
    assert device.screenshot_formats == ["pillow"]
    assert blob.endswith(PNG_IEND)
    with Image.open(io.BytesIO(blob)) as img:
        img.load()
        assert img.size == (4, 4)


def test_a_corrupt_but_non_empty_payload_is_rejected():
    # 0 bytes is not the only broken read. A PNG that travelled through a
    # text-mode pipe is non-empty and unopenable, so an empty-check alone would
    # have shipped it. Each shape below is proven non-empty before it is refused,
    # and each is measured against PIL rather than assumed.
    cases = {
        "lf turned crlf": png_bytes().replace(b"\n", b"\r\n"),
        "truncated mid-chunk": png_bytes()[:18],
        "header and nothing else": PNG_MAGIC,
        "missing its iend tail": png_bytes()[:-4],
        "someone elses bytes": b"NOT A PNG AT ALL, BUT LONG ENOUGH",
    }
    driver, _ = make()
    for label, payload in cases.items():
        assert payload, label
        driver._screenshot_override = payload
        with pytest.raises(RuntimeError, match="corrupt"):
            driver.screenshot()


def test_a_real_capture_passes_the_same_guard():
    # The FAIL/PASS flip: the guard above must not refuse a good image.
    driver, _ = make()
    driver._screenshot_override = png_bytes()
    assert driver.screenshot() == png_bytes()


def test_a_session_that_returns_no_image_is_the_empty_error():
    driver, _, device = with_device(image=None)
    with pytest.raises(RuntimeError, match="no image"):
        driver.screenshot()
    assert device.screenshot_calls == 1


def test_a_screenshot_without_a_session_says_so():
    driver, _ = make()
    with pytest.raises(RuntimeError, match="no session"):
        driver.screenshot()


# -- ruling 2: text injection, and digits as their own operation --


def test_type_text_delivers_a_non_ascii_string_to_the_device_exactly_once():
    # Supersedes the plan's `input text` argv assertion. `adb shell input text`
    # drops every non-ASCII byte without complaining, so a Bangla contact search
    # would type nothing and then tap a stale screen. The intent the old test
    # carried -- the text reaches the device exactly once -- is asserted here.
    driver, runner, device = with_device()
    driver.type_text(RAFI)
    assert device.sent == [RAFI]
    assert runner.calls == []


def test_type_text_never_touches_the_shell_path():
    driver, runner, device = with_device()
    driver.type_text("a b c")
    assert device.sent == ["a b c"]
    assert not any("input text" in " ".join(c) for c in runner.calls)


def test_type_text_without_a_session_says_so():
    driver, _ = make()
    with pytest.raises(RuntimeError, match="no session"):
        driver.type_text(RAFI)


def test_type_digits_uses_the_shell_because_digits_survive_it():
    driver, runner, device = with_device()
    driver.type_digits("0171234567")
    assert runner.calls[-1][-1] == "input text 0171234567"
    assert device.sent == []


def test_type_digits_accepts_an_international_number():
    driver, runner, _ = with_device()
    driver.type_digits("+880171234567")
    assert runner.calls[-1][-1] == "input text +880171234567"


def test_type_digits_refuses_to_half_type():
    # "017a" would put 017 on the dialpad and drop the rest: a wrong number that
    # looks like a completed action, which is the failure this task exists to kill.
    driver, runner, device = with_device()
    for bad in ("017a", "", "017123456 7", "017" + BENGALI_FIVE, "one-two", None):
        with pytest.raises(ValueError, match="digits"):
            driver.type_digits(bad)
    assert runner.calls == []
    assert device.sent == []


# -- hierarchy and session shape --


def test_hierarchy_comes_from_the_session_not_from_the_broken_dump():
    driver, runner, device = with_device()
    xml = driver.dump_hierarchy()
    assert device.dump_calls == 1
    assert xml.strip()
    assert not any("uiautomator dump" in " ".join(c) for c in runner.calls)


def test_a_whitespace_only_hierarchy_is_a_broken_read_too():
    driver, _ = make()
    driver._hierarchy_override = "   \n\t"
    with pytest.raises(RuntimeError, match="empty"):
        driver.dump_hierarchy()


def test_a_session_that_answers_nothing_is_reported_as_empty():
    driver, _, _device = with_device(xml="   ")
    with pytest.raises(RuntimeError, match="empty"):
        driver.dump_hierarchy()


def test_hierarchy_without_a_session_says_so():
    driver, _ = make()
    with pytest.raises(RuntimeError, match="no session"):
        driver.dump_hierarchy()


def test_connect_uses_the_connector_and_reports_connected():
    driver, _runner, device = with_device()
    assert driver.device is device
    assert driver.serial == "192.0.2.9:5555"
    assert driver.is_connected is True


def test_a_failed_session_is_a_connection_error_that_names_the_serial():
    def boom(serial):
        raise OSError("instrumentation did not start")

    driver = Driver("192.0.2.9:5555", runner=FakeRunner(), connector=boom)
    with pytest.raises(ConnectionError, match="192.0.2.9:5555"):
        driver.connect()
    assert driver.is_connected is False


def test_connect_publishes_the_resolved_adb_to_adbutils(monkeypatch):
    # uiautomator2 starts its own adb server through adbutils; if it finds a
    # different binary, two servers fight over port 5037.
    published = []
    monkeypatch.setattr(adb, "find_adb", lambda: "C:/sdk/platform-tools/adb.exe")
    monkeypatch.setattr(adb, "publish", published.append)
    Driver("192.0.2.9:5555", runner=FakeRunner(), connector=lambda s: FakeDevice()).connect()
    assert published == ["C:/sdk/platform-tools/adb.exe"]


def test_a_blank_override_does_not_survive_a_driver_that_cannot_find_adb(tmp_path, monkeypatch):
    # adb.publish writes real process env, so connect() may only ever leave
    # behind a path that exists. Nothing here may resolve a deleted tmp path.
    for name in adb.OVERRIDE_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("ANDROID_HOME", str(tmp_path))
    monkeypatch.delenv("ANDROID_SDK_ROOT", raising=False)
    monkeypatch.setenv("ADBUTILS_ADB_PATH", "   ")

    Driver("192.0.2.9:5555", runner=FakeRunner(), connector=lambda s: FakeDevice()).connect()
    assert "ADBUTILS_ADB_PATH" not in os.environ


# -- reads that cannot lie, continued --


def test_an_empty_grep_answer_falls_back_to_the_whole_read():
    # `dumpsys window | grep mCurrentFocus` says nothing in three different
    # situations: no focus yet, no grep on the device, a window-manager
    # transition. Only the first is honestly "nothing", so the driver re-reads
    # the whole dump and parses it in Python before it claims anything.
    runner = ScriptedRunner({
        FOCUS_GREP: ShellResult(1, "", ""),
        DUMPSYS_WINDOW: ShellResult(
            0, "  Current window manager state:\n  mCurrentFocus=Window{e46f4a u0 NotificationShade}\n", ""
        ),
    })
    driver = Driver("192.0.2.9:5555", runner=runner)
    assert driver.current_focus() == "NotificationShade"
    assert [c[-1] for c in runner.calls] == [FOCUS_GREP, DUMPSYS_WINDOW]


def test_the_fallback_is_not_invented_when_the_whole_read_is_empty_too():
    runner = ScriptedRunner({})
    driver = Driver("192.0.2.9:5555", runner=runner)
    assert driver.current_focus() == "nothing in particular"
    assert [c[-1] for c in runner.calls] == [FOCUS_GREP, DUMPSYS_WINDOW]


def test_a_literal_none_focus_is_not_returned_as_a_window_name():
    # Real dumpsys prints `mCurrentFocus=(none)`; "(none)" read back as an
    # activity name would name a screen that does not exist.
    driver, _ = make(answers={"mCurrentFocus": ShellResult(0, "  mCurrentFocus=(none)\n", "")})
    assert driver.current_focus() == "nothing in particular"


def test_current_focus_never_pipes_dumpsys_into_a_truncating_filter():
    driver, runner = make(answers={
        "mCurrentFocus": ShellResult(0, "  mCurrentFocus=Window{a u0 com.x/Activity}\n", "")
    })
    driver.current_focus()
    driver.is_keyguard_up()
    assert runner.calls
    for call in runner.calls:
        remote = call[-1]
        assert "| head" not in remote
        assert "grep -m 1" not in remote
        assert "am start" not in remote


def test_keyguard_is_not_claimed_when_an_app_owns_the_screen():
    driver, _ = make(answers={
        "mCurrentFocus": ShellResult(0, "  mCurrentFocus=Window{a u0 com.x/.Dashboard}\n", "")
    })
    assert driver.is_keyguard_up() is False


# -- finding 1: a focus read that could not ask must not answer "nothing" --
#
# "Empty" and "broken" are the two claims this read must never confuse, and the
# grepped form conflates four rc values: 1 (no line, honest), 124 (the dumpsys
# never returned), 127 (adb is gone), 0-with-`error:` (the device refused).
# Only the first is an answer about the screen. The rest mean the screen's state
# is unknown, and Task 4's ladder must be told that as rung 1 -- an unreachable
# phone -- rather than rung 3 or 4, which would tap a locked screen.
#
# Each guard below is paired with the case it must NOT catch, because a guard
# that raises on everything is as useless as one that raises on nothing: grep's
# own rc 1, and a keyevent that worked and printed nothing, are both honest.


def test_the_transport_error_type_is_part_of_the_interface():
    assert issubclass(driver_module.TransportError, RuntimeError)


def test_a_timed_out_focus_read_is_not_an_empty_screen():
    # rc 124 is the hung dumpsys the timeout plumbing was added to catch. If it
    # falls through to the parser, the read answers "nothing in particular" and
    # the ladder proceeds against a screen nobody saw.
    driver, _ = make(answers={
        "dumpsys window": ShellResult(124, "", "timed out after 20.0s: adb -s x shell dumpsys window")
    })
    with pytest.raises(TransportError, match="did not succeed"):
        driver.current_focus()


def test_a_missing_adb_binary_is_not_an_empty_screen():
    driver, _ = make(answers={
        "dumpsys window": ShellResult(127, "", "adb binary not found: C:/sdk/platform-tools/adb.exe")
    })
    with pytest.raises(TransportError, match="did not succeed"):
        driver.current_focus()


def test_a_refused_read_is_raised_even_when_adb_exited_zero():
    # The one that matters most: adb puts `error: device offline` in *stdout* and
    # still exits 0. A check on rc alone would call that a successful read of a
    # screen with nothing on it.
    driver, _ = make(answers={
        "mCurrentFocus": ShellResult(0, "error: device offline\n", "")
    })
    with pytest.raises(TransportError, match="offline"):
        driver.current_focus()


def test_a_refused_whole_dump_is_raised_even_when_adb_exited_zero():
    # The rc-0-with-error shape is not a grep-only hazard, and the whole dump is
    # the read the fallback trusts, so it is checked with the same rule.
    runner = ScriptedRunner({
        FOCUS_GREP: ShellResult(1, "", ""),
        DUMPSYS_WINDOW: ShellResult(0, "error: closed\n", ""),
    })
    driver = Driver("192.0.2.9:5555", runner=runner)
    with pytest.raises(TransportError, match="did not succeed"):
        driver.current_focus()
    assert [c[-1] for c in runner.calls] == [FOCUS_GREP, DUMPSYS_WINDOW]


def test_greps_own_no_match_rc_is_still_an_honest_empty_read():
    # The FAIL/PASS flip for the guard above: rc 1 with nothing printed is grep
    # saying "no line matched", which is not a transport failure and must not
    # raise -- the fallback read answers, and the answer here is genuinely
    # "nothing has focus".
    runner = ScriptedRunner({
        FOCUS_GREP: ShellResult(1, "", ""),
        DUMPSYS_WINDOW: ShellResult(0, "  mCurrentFocus=(none)\n", ""),
    })
    driver = Driver("192.0.2.9:5555", runner=runner)
    assert driver.current_focus() == "nothing in particular"
    assert [c[-1] for c in runner.calls] == [FOCUS_GREP, DUMPSYS_WINDOW]


def test_rc_one_is_not_an_honest_empty_read_when_the_device_said_error():
    # "grep found nothing" and "grep could not run because the device is gone"
    # both arrive as rc 1 -- the stdout text is what separates them, so the rc 1
    # exemption is conditioned on a clean answer.
    driver, _ = make(answers={
        "mCurrentFocus": ShellResult(1, "error: device '192.0.2.9:5555' not found\n", "")
    })
    with pytest.raises(TransportError, match="did not succeed"):
        driver.current_focus()


def test_a_broken_fallback_read_raises_after_an_honest_empty_grep():
    # The exemption only covers the read that earned it. Once the grepped form
    # has honestly said "no line", the whole dump is still a read that can break,
    # and its rc 124 is not "nothing on screen".
    runner = ScriptedRunner({
        FOCUS_GREP: ShellResult(1, "", ""),
        DUMPSYS_WINDOW: ShellResult(124, "", "timed out after 20.0s"),
    })
    driver = Driver("192.0.2.9:5555", runner=runner)
    with pytest.raises(TransportError, match="did not succeed"):
        driver.current_focus()
    assert [c[-1] for c in runner.calls] == [FOCUS_GREP, DUMPSYS_WINDOW]


def test_a_literal_null_focus_is_not_returned_as_a_window_name():
    # Several Android builds print `mCurrentFocus=null`. Like `(none)`, it is a
    # window that does not exist, and "null" read back as an activity name would
    # send the ladder looking for a screen nobody has ever seen.
    driver, _ = make(answers={"mCurrentFocus": ShellResult(0, "  mCurrentFocus=null\n", "")})
    assert driver.current_focus() == "nothing in particular"


def test_a_null_focus_is_not_a_keyguard_answer_either():
    # The other half of the same defect: is_keyguard_up reads the name, so a
    # window named "null" would be a screen with no keyguard on it.
    driver, _ = make(answers={"mCurrentFocus": ShellResult(0, "  mCurrentFocus=Window{0 u0 null}\n", "")})
    assert driver.is_keyguard_up() is False
    assert "null" not in driver.current_focus()


def test_keyguard_is_not_reported_down_when_the_read_could_not_ask():
    # The exact chain that puts a finger on a locked screen: a hung dumpsys makes
    # current_focus say "nothing in particular", is_keyguard_up says False, and
    # the ladder reads "no keyguard, proceed". The failure has to reach Task 4.
    driver, _ = make(answers={
        "dumpsys window": ShellResult(124, "", "timed out after 20.0s")
    })
    with pytest.raises(TransportError, match="did not succeed"):
        driver.is_keyguard_up()


# -- finding 2: a write that was refused is not a write that happened --


def test_a_refused_tap_raises_instead_of_reporting_success():
    # `input: could not open /dev/input` and an offline device both come back
    # through rc. Tap is the one that acts on the screen, so it may not be the
    # one that answers "done" for a command the device never ran.
    driver, _ = make(answers={"input tap": ShellResult(1, "", "input: could not open /dev/input")})
    with pytest.raises(TransportError, match="input tap 540 1200"):
        driver.tap_xy(540, 1200)


def test_a_refused_keyevent_raises():
    driver, _ = make(answers={"input keyevent": ShellResult(124, "", "timed out after 20.0s")})
    with pytest.raises(TransportError, match="not carried out"):
        driver.keyevent(26)


def test_a_write_refused_at_rc_zero_raises_too():
    # The write half of the ShellResult rule: adb prints `error:` and exits 0,
    # so a write that only asked "was rc zero?" calls this a performed tap.
    driver, _ = make(answers={"input tap": ShellResult(0, "error: device offline\n", "")})
    with pytest.raises(TransportError, match="offline"):
        driver.tap_xy(10, 20)


def test_a_keyevent_that_worked_and_said_nothing_is_not_a_failure():
    # The exception the ruling names: several keyevent codes legitimately print
    # nothing. rc is the status; the length of the answer is evidence of nothing,
    # in either direction.
    driver, _ = make(answers={"input keyevent": ShellResult(0, "", "")})
    driver.keyevent(26)
    driver.tap_xy(1, 1)


def test_a_refused_digit_injection_raises_rather_than_leaving_a_half_typed_number():
    # type_digits' own docstring argues half-typing is worse than not typing. A
    # rejected `input text` produces exactly that, silently, unless the rc is read.
    driver, _ = make(answers={"input text": ShellResult(1, "", "ERROR: requires input methods")})
    with pytest.raises(TransportError, match="not carried out"):
        driver.type_digits("0171234567")


def test_a_refused_write_still_validates_its_arguments_first():
    # Ordering: a bad number is a caller bug and must read as one (ValueError)
    # even on a device whose every command fails.
    driver, _ = make(answers={"input text": ShellResult(1, "", "nope")})
    with pytest.raises(ValueError, match="digits"):
        driver.type_digits("017a")


# -- ruling 3: the suite may not inherit adb resolution from another test --


def test_the_suite_starts_from_an_isolated_adb_env():
    # conftest.py clears these for every test. Without it, a test that published
    # a tmp-path adb leaves ADBUTILS_ADB_PATH set for the whole pytest process,
    # and a driver test could then resolve a deleted path and pass for the wrong
    # reason. This assertion is that leak detector.
    assert "ADBUTILS_ADB_PATH" not in os.environ
    assert "AMBER_MCP_ADB_PATH" not in os.environ


# -- the generic-device rule --


def test_the_module_is_device_generic():
    # driver.py is the layer Tasks 4, 6, 7 and 8 reuse for any phone, so the
    # AmberIT package name may not appear in it.
    source = pathlib.Path(driver_module.__file__).read_text(encoding="utf-8")
    assert "amberit" not in source.lower()
