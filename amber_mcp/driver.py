"""One phone, one session, and reads that distinguish 'empty' from 'broken'.

Every method here is device-generic. The MIUI traps recorded in the spec are
defended against at this layer, because this is where a silent failure would be
mistaken for a real answer: an empty hierarchy that reads as a blank screen, a
screenshot that is non-empty but unopenable, a name that was typed as nothing.

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

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_PNG_IEND = b"IEND\xaeB\x60\x82"
_DIALPAD = re.compile(r"[0-9+]+")
_NO_WINDOW = "(none)"

# Two shapes of one read. The grepped form answers in a few kilobytes; the whole
# dump is the fallback, filtered here rather than on the device, because a grep
# that printed nothing is three different situations and only one of them is
# honestly "nothing has focus".
FOCUS_GREP_COMMAND = "dumpsys window | grep mCurrentFocus"
FOCUS_WHOLE_COMMAND = "dumpsys window"


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

    def shell(self, remote_cmd: str, timeout: float = 20.0) -> ShellResult:
        return self.adb(["shell", remote_cmd], timeout=timeout)

    def _argv(self, args: list[str]) -> list[str]:
        """Pin the serial on every invocation. An un-pinned one is how two
        phones get driven by accident, and with an override var pointing at a
        stale binary it is how a deleted tmp path gets answered instead."""
        pinned = ["-s", self.serial, *args]
        if self._runner is not None:
            return ["adb", *pinned]
        if self._binary is None:
            self._binary = adb.adb_path()
        return [self._binary, *pinned]

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
        """
        focus = self._focus_from(self.shell(FOCUS_GREP_COMMAND))
        if focus is not None:
            return focus
        return self._focus_from(self.shell(FOCUS_WHOLE_COMMAND)) or "nothing in particular"

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
            # `(none)` is what dumpsys prints; returning it would name a window
            # that does not exist, so it is treated as no answer at all.
            if not name or name == _NO_WINDOW:
                continue
            return name
        return None

    def is_keyguard_up(self) -> bool:
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

    def tap_xy(self, x: int, y: int) -> None:
        self.shell(f"input tap {int(x)} {int(y)}")

    def keyevent(self, code: int) -> None:
        self.shell(f"input keyevent {int(code)}")

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
        was asked for.
        """
        if not _DIALPAD.fullmatch(digits or ""):
            raise ValueError(f"type_digits takes ASCII digits and '+', got {digits!r}")
        self.shell(f"input text {digits}")
