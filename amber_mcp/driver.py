"""One phone, one session, and reads that distinguish 'empty' from 'broken'.

Every method here is device-generic. The MIUI traps recorded in the spec are
defended against at this layer, because this is where a silent failure would be
mistaken for a real answer: an empty hierarchy that reads as a blank screen, a
screenshot that is non-empty but unopenable, a name that was typed as nothing.

The rule is the same for a read and for a write, and both are covered here: a
command that did not run may not answer as though it did. A focus read whose
dumpsys hung, or whose adb vanished, raises `TransportError` instead of naming
"nothing in particular" -- the same exception a refused `input tap` raises
instead of returning None. Silence from a device that could not be asked is not
evidence about the device, and an accepted write is not a performed one.

Two seams make this testable with no phone attached: `runner` receives every
argv list before it would reach a process, and `connector` supplies the
uiautomator2 device. The unit suite uses them for every phone read and write,
and the only processes it really starts are Python's own -- so a green run here
has never touched the live handset.
"""
from __future__ import annotations

import io
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Optional

from PIL import Image

from amber_mcp import adb


@dataclass(frozen=True)
class ShellResult:
    """One command's outcome. `ok` is rc zero alone: adb puts real errors in
    stdout (`error: device offline`) and idle chatter in stderr, so neither
    stream is a status and nothing may treat one as proof."""

    rc: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.rc == 0


# The seam's contract: it takes an argv list. A timeout is not passed to it, so
# a fake never has to accept a keyword the real path would add.
Runner = Callable[[list[str]], ShellResult]


class TransportError(RuntimeError):
    """The adb call did not succeed, so the screen's state is unknown.

    Not the same claim as "the screen shows nothing". A hung dumpsys, a vanished
    adb, or a device that answered `error: device offline` has told us nothing
    about the handset, and a read that reports those as an empty screen hands the
    caller a licence to act on a screen nobody has seen -- the exact way a locked
    phone gets tapped. Task 4's readiness ladder catches this name at rung 1 and
    reports the phone unreachable; it is never a rung-3 "no keyguard, proceed".
    """


_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_PNG_IEND = b"IEND\xaeB\x60\x82"
_DIALPAD = re.compile(r"[0-9+]+")
_GREP_NO_MATCH = 1

# Window names dumpsys prints when no window owns the screen. Both are an honest
# "no focus" and neither is a screen: returning either as a name would send the
# caller looking for an activity that does not exist, which is how a `null`
# focus became a place to tap.
_NOT_A_WINDOW = frozenset({"(none)", "null"})

# Two shapes of one read. The grepped form answers in a few kilobytes; the whole
# dump is the fallback, filtered here rather than on the device, because a grep
# that printed nothing is three different situations and only one of them is
# honestly "nothing has focus".
FOCUS_GREP_COMMAND = "dumpsys window | grep mCurrentFocus"
FOCUS_WHOLE_COMMAND = "dumpsys window"


def _refused_in_stdout(result: ShellResult) -> str:
    """The line adb writes when it did not do the thing.

    It goes to *stdout*, and often at rc 0 -- `error: device offline`, `error:
    closed`, `ERROR: ...` from the device's own `input`. So an rc that looks fine
    is not proof, and this is the second question every checked call asks.

    The check errs toward refusing: a dumpsys line that only looks like a
    refusal raises, and the caller reads "unreachable" at rung 1 where a wrong
    screen claim would have gone on to tap. Task 6's live capture is where a
    false refusal here would show up, on purpose rather than by accident.
    """
    for line in result.out.splitlines():
        if line.lstrip().lower().startswith("error:"):
            return line.strip()
    return ""


def _transport_reason(result: ShellResult, *, honest_no_match: bool = False) -> str:
    """Why this outcome cannot be believed, or "" when it can.

    `honest_no_match` is grep's exemption, and only grep earns it: rc 1 with a
    clean stdout is grep's own answer for "no line matched", which is a real
    reading of the dump rather than a failure to read it. rc 1 with an `error:`
    line is a different event, so the exemption is conditioned on the stdout
    being clean, and every other non-zero rc -- 124 (timed out), 127 (no adb),
    1 (device refused) -- is a failure to ask.
    """
    refused = _refused_in_stdout(result)
    if refused:
        return refused
    if result.ok:
        return ""
    if honest_no_match and result.rc == _GREP_NO_MATCH:
        return ""
    return (result.err or "").strip() or f"exit status {result.rc}"


def _decode(raw: bytes) -> str:
    """Bytes off adb, as lines.

    ``text=True`` would decode with the locale codec, which is cp1252 on this
    host and raises on bytes adb does not translate. Decoding by hand also
    undoes adb's LF->CRLF translation -- the same defect that corrupts a picture
    routed through the shell, and harmless for text either way.
    """
    return (raw or b"").decode("utf-8", errors="replace").replace("\r\n", "\n")


def _subprocess_runner(argv: list[str], timeout: float = 30.0) -> ShellResult:
    try:
        done = subprocess.run(argv, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return ShellResult(124, "", f"timed out after {timeout}s: {' '.join(argv)}")
    except FileNotFoundError:
        # The binary was resolvable when we asked and is gone now. Say which one,
        # rather than raising a traceback out of a read.
        return ShellResult(127, "", f"adb binary not found: {argv[0]}")
    return ShellResult(done.returncode, _decode(done.stdout), _decode(done.stderr))


class Driver:
    """Everything that touches one phone, and nothing that names the app on it."""

    def __init__(
        self,
        serial: str,
        runner: Optional[Runner] = None,
        connector: Optional[Callable[[str], Any]] = None,
    ):
        self.serial = serial
        self._runner = runner
        self._connector = connector
        self.device: Any = None
        self._binary: Optional[str] = None
        # Test seams: when set they stand in for what the phone would answer, so
        # the guards below are provable without a device attached.
        self._hierarchy_override: Optional[str] = None
        self._screenshot_override: Optional[bytes] = None

    # -- raw shell --

    def adb(self, args: list[str], timeout: float = 30.0) -> ShellResult:
        return self._run(self._argv(list(args)), timeout)

    def host_adb(self, args: list[str], timeout: float = 30.0) -> ShellResult:
        """adb host-level commands — connect, devices, kill-server — where the serial is an argument, never a `-s` selector."""
        return self._run(self._host_argv(list(args)), timeout)

    def shell(self, remote_cmd: str, timeout: float = 20.0) -> ShellResult:
        return self.adb(["shell", remote_cmd], timeout=timeout)

    def _command_prefix(self) -> list[str]:
        """The binary, resolved once and cached, or the seam's literal `adb`.

        Every argv shape starts here, so a host-level command cannot quietly end
        up resolving a different binary than a device command does -- which is
        how a GUI-launched host with adb off PATH gets two adb servers.
        """
        if self._runner is not None:
            return ["adb"]
        if self._binary is None:
            self._binary = adb.adb_path()
        return [self._binary]

    def _argv(self, args: list[str]) -> list[str]:
        """Pin the serial on every invocation. An un-pinned one is how two
        phones get driven by accident, and with an override var pointing at a
        stale binary it is how a deleted tmp path gets answered instead."""
        return [*self._command_prefix(), "-s", self.serial, *args]

    def _host_argv(self, args: list[str]) -> list[str]:
        """No selector at all. `connect <serial>` takes the serial *as its
        argument*; `-s` asks adb to run the command against a device that is
        already attached, which is the thing the caller is trying to attach.
        Platform-tools tolerates the ignored flag; tolerance is not meaning, and
        the readiness ladder must not depend on it.

        The `ShellResult` comes back whole, as `adb()` gives it: `connect` writes
        `failed to connect ...` to stdout at rc 0, so the caller judges rc *and*
        text rather than this method guessing what a reconnect proves.
        """
        return [*self._command_prefix(), *args]

    def _run(self, argv: list[str], timeout: float) -> ShellResult:
        if self._runner is not None:
            return self._runner(argv)
        return _subprocess_runner(argv, timeout=timeout)

    # -- connection --

    def connect(self) -> None:
        """Bring up a uiautomator2 session, handing it the adb we resolved.

        The plain `uiautomator dump` shell command is not used anywhere, ever: on
        this build it reports success while writing a 0-byte file.
        """
        found = adb.find_adb()
        if found:
            adb.publish(found)
        elif not (os.environ.get("ADBUTILS_ADB_PATH") or "").strip():
            # We could not resolve an adb ourselves, so a blank override would
            # only hand adbutils a path that cannot exist. Drop it and let
            # adbutils search, rather than trusting something we could not find.
            os.environ.pop("ADBUTILS_ADB_PATH", None)

        connect = self._connector
        if connect is None:
            import uiautomator2 as u2

            connect = u2.connect
        try:
            self.device = connect(self.serial)
            self.device.info
        except Exception as exc:  # u2 raises several unrelated types
            self.device = None
            raise ConnectionError(f"no uiautomator2 session on {self.serial}: {exc}") from exc

    @property
    def is_connected(self) -> bool:
        return self.device is not None

    def _require_device(self) -> Any:
        if self.device is None:
            raise RuntimeError("no session: call connect() first")
        return self.device

    # -- reads --

    def current_focus(self) -> str:
        """The window that owns the screen, named, or 'nothing in particular'.

        A panel such as NotificationShade has no activity, and an empty string
        here would read as "the screen is empty" -- a different claim from "a
        panel owns the screen", which is what actually happened.

        The third claim is the one this read used to steal: "we could not ask".
        A hung dumpsys and a vanished adb print no `mCurrentFocus=` line either,
        so both rc values are checked before anything is parsed, and a read that
        did not happen raises rather than naming a screen. That is what makes
        'nothing in particular' an honest answer: it is only ever reached from a
        dump that came back.
        """
        focus = self._focus_from(self._focus_read(FOCUS_GREP_COMMAND, honest_no_match=True))
        if focus is not None:
            return focus
        return self._focus_from(self._focus_read(FOCUS_WHOLE_COMMAND)) or "nothing in particular"

    def _focus_read(self, command: str, *, honest_no_match: bool = False) -> ShellResult:
        """Run one shape of the focus read, and refuse to parse a broken one."""
        result = self.shell(command)
        reason = _transport_reason(result, honest_no_match=honest_no_match)
        if reason:
            raise TransportError(
                f"focus read did not succeed (rc {result.rc}) for {command!r}: {reason} "
                f"on {self.serial}. The screen's state is unknown, so nothing may be "
                "claimed about it -- not even that it is empty."
            )
        return result

    @staticmethod
    def _focus_from(result: ShellResult) -> Optional[str]:
        for line in result.out.splitlines():
            if "mCurrentFocus=" not in line:
                continue
            window = line.split("mCurrentFocus=", 1)[1].strip()
            if "{" in window and "}" in window:
                window = window[window.find("{") + 1: window.rfind("}")].strip()
            parts = window.split()
            name = parts[-1] if parts else ""
            # `(none)` and `null` are what dumpsys prints for no focused window.
            # Returning either would name a screen that does not exist, so they
            # are treated as no answer at all -- the same treatment, because a
            # name nobody can show is not a name worth passing on.
            if not name or name.lower() in _NOT_A_WINDOW:
                continue
            return name
        return None

    def is_keyguard_up(self) -> bool:
        """Whether a lock panel owns the screen.

        A read that could not ask raises straight through, and that is the
        point: "we do not know" must never reach a caller as "no keyguard,
        proceed", because the rung that follows taps what it just was told is
        unlocked.
        """
        focus = self.current_focus()
        return "Keyguard" in focus or focus == "NotificationShade"

    def dump_hierarchy(self) -> str:
        if self._hierarchy_override is not None:
            xml = self._hierarchy_override
        else:
            xml = self._require_device().dump_hierarchy()
        if not xml or not xml.strip():
            raise RuntimeError(
                "hierarchy came back empty. On MIUI the plain `uiautomator dump` "
                "prints success and writes 0 bytes, so this is a broken read, not a blank screen."
            )
        return xml

    def screenshot(self) -> bytes:
        """PNG bytes of the screen, or an error explaining which failure it was."""
        if self._screenshot_override is not None:
            blob = self._screenshot_override
        else:
            blob = self._capture_png()
        if not blob:
            raise RuntimeError(
                "screenshot returned no image. The capture comes back empty when "
                "the display is dozing -- wake the screen before reading the picture."
            )
        if not self._is_png(blob):
            raise RuntimeError(
                f"screenshot came back corrupt: {len(blob)} bytes that are not a "
                "decodable PNG. A picture routed through `adb shell` is the usual "
                "cause: that path rewrites every LF as CRLF on Windows, which "
                "leaves a file that is non-empty, unopenable, and passes any "
                "check that only asks whether it has bytes."
            )
        return blob

    def _capture_png(self) -> bytes:
        # Not `screencap -p`: see the corrupt-payload error above. u2's own
        # screenshot returns a decoded image, so no byte ever crosses a text pipe.
        image = self._require_device().screenshot(format="pillow")
        if image is None:
            return b""
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue()

    @staticmethod
    def _is_png(blob: bytes) -> bool:
        """Three checks, each one measured against the payloads the tests name.

        PIL accepts a PNG whose IEND chunk has been cut off, so the tail is
        checked separately; that truncation is exactly what a half-written adb
        read leaves behind, and the header check alone would have shipped it.
        """
        if not blob.startswith(_PNG_MAGIC) or not blob.endswith(_PNG_IEND):
            return False
        try:
            with Image.open(io.BytesIO(blob)) as image:
                image.load()
        except Exception:  # UnidentifiedImageError or OSError, depending where it broke
            return False
        return True

    # -- writes --

    def _write(self, remote_cmd: str) -> None:
        """Run a write, and refuse to call a refused one done.

        Every write here used to discard its `ShellResult`, so `input: could not
        open /dev/input`, an offline device and a timeout all read back as
        "done" -- a tap that never landed looks identical to one that did, and
        the flow then acts on a screen it did not change.

        rc is the status and the *length* of the answer is not: several
        `input keyevent` codes legitimately print nothing, so silence from a
        healthy device must not become a failure either. What adb wrote in stdout
        is checked too, because adb reports `error: device offline` at rc 0.
        """
        result = self.shell(remote_cmd)
        reason = _transport_reason(result)
        if reason:
            raise TransportError(
                f"{remote_cmd!r} was not carried out (rc {result.rc}) on {self.serial}: "
                f"{reason}. Nothing downstream may assume this happened."
            )

    def tap_xy(self, x: int, y: int) -> None:
        self._write(f"input tap {int(x)} {int(y)}")

    def keyevent(self, code: int) -> None:
        self._write(f"input keyevent {int(code)}")

    def type_text(self, text: str) -> None:
        """Inject through the session's keyboard; `input text` is not used for text.

        `adb shell input text` cannot carry non-ASCII: it drops those characters
        without complaining, so a search for a contact whose name is written in
        Bangla would type nothing and leave the flow tapping a stale screen. The
        device's own IME takes the string as UTF-8 and it arrives whole.
        """
        self._require_device().send_keys(text)

    def type_digits(self, digits: str) -> None:
        """Type on the dialpad. ASCII digits survive `input text`; nothing else does.

        Validated first, and refusing is the point: half-typing a number is worse
        than not typing it, because the call that follows looks like the one that
        was asked for. The same reasoning covers the device: an `input text` it
        rejected leaves precisely those digits missing, so `_write` reads its rc
        and raises rather than returning as though the number were on the screen.
        """
        if not _DIALPAD.fullmatch(digits or ""):
            raise ValueError(f"type_digits takes ASCII digits and '+', got {digits!r}")
        self._write(f"input text {digits}")
