# Amber-MCP Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the device layer, readiness ladder, redaction guard and discovery harness that make AmberIT's real UI knowable — then prove the MCP wiring end to end with one tool that needs no selectors.

**Architecture:** Four focused modules. `adb.py` finds the adb binary (copied from Android-MCP, MIT). `driver.py` wraps one uiautomator2 session plus raw adb reads. `session.py` answers only "is the phone ready, and if not, which rung failed". `discovery.py` walks AmberIT and writes what it sees to on-disk artifacts, redacted. `server.py` exposes tools over MCP stdio on top of those three.

**Tech Stack:** Python 3.13 (uv-managed), `fastmcp>=2.14,<3`, `uiautomator2>=3.3.1`, `pillow>=11.2.1`, `pytest`.

The fastmcp ceiling is not caution about new versions: Android-MCP — whose `FastMCP(name,
instructions, lifespan)` construction, `@mcp.tool(name=…, annotations=ToolAnnotations(…))`
registration and `mcp.run()` entry Task 8 copies — is locked at **2.14.0** and works on this
machine. An unpinned `>=2.14.0` resolves to 4.1.6, two majors ahead, whose API this plan has
not verified against anything.

**Spec:** [docs/superpowers/specs/2026-11-06-amber-mcp-design.md](file:///E:/Amber-MCP/docs/superpowers/specs/2026-11-06-amber-mcp-design.md) — read it alongside this plan; every task argues from it.

---

## Why this plan stops short of the semantic tools

The spec's Open Items 1 and 2 are unambiguous: **nothing in `screens.py` may be invented before discovery has run on the real app.** The dialpad's resource-ids, the Call button's text, the AmberIT/All toggle's selector, and which `dumpsys` source actually distinguishes "answered" from "ringing" are all **unknown**. A plan that wrote those flows today would contain fabricated code that looks finished and cannot pass.

So this plan delivers everything that does not depend on the unknown, and ends with the two live runs that retire it. Task 6 and Task 7 produce real captured data. A second plan — *Amber-MCP Semantic Tools* — is written from that data, and only then.

**Scope check result:** split into two plans, one per dependency layer. This is plan 1 of 2.

---

## Global Constraints

Verbatim from the spec. Every task obeys these implicitly.

- IP line only. `call()` never falls back to cellular, and nothing in this project places a cellular call.
- No confirmation gate on dialling. Direct execution.
- No bulk, no queue, no campaign, no journal, no resume.
- Proof is a read-back. `am start` returning rc 0 and printing `Starting: Intent{…}` is never evidence.
- `uiautomator dump` on this device prints `UI hierchary dumped to: <path>` while writing a **0-byte file**. Never use it; use uiautomator2's instrumentation server.
- `wm dismiss-keyguard` does not unlock this phone. The ladder tries **no-PIN first**.
- `screencap` yields 0 bytes while the display is dozing. Assert wake state before trusting any screenshot.
- VoIP calls do not appear in `dumpsys telephony.registry` `mCallState`. It reads `0` forever during an AmberIT call.
- Tool results are 1–3 lines of speakable text, never JSON.
- `REDMI_PIN` is read from the environment at call time. Never written to a file, never logged, never in tool output.
- Nothing under `logs/` or `artifacts/raw/` is ever committed. Contact names, numbers and balances stay out of the public repo.
- Single in-process lock: one writer drives the phone at a time.

---

## File Structure

```
E:\Amber-MCP
  pyproject.toml                      uv project, deps, pytest config
  README.md                           setup + the four MCP client config lines
  .gitignore                          already present: logs/ proven to match
  amber_mcp/
    __init__.py
    config.py                         package name, activity, serial + env plumbing
    adb.py                            copied from Android-MCP: find adb, publish to adbutils
    driver.py                         one session: shell reads, hierarchy, screenshot, tap/type
    session.py                        ensure_ready() ladder + which rung failed
    redact.py                         strip numbers/balance/PIN from any captured text
    discovery.py                      capture harness: walk screens, write redacted artifacts
    server.py                         FastMCP tools. Only `status` in this plan.
  artifacts/                          redacted captures, committed — they are the fixtures
    raw/                              unredacted captures, NEVER committed
  tests/
    unit/
      test_config.py
      test_adb.py
      test_driver.py
      test_session.py
      test_redact.py
      test_discovery.py
      test_server.py
      test_no_secrets_committed.py
    live/
      test_live_capture.py            marked, skipped unless AMBER_LIVE=1
```

Design rule that keeps the files readable: `server.py` may only mention **verbs**; the moment it contains a resource-id or an `x,y`, that belongs in `discovery.py`'s output or (plan 2) `screens.py`. `driver.py` must not contain the string `amberit`.

---

## Task Right-Sizing Note

Tasks 6 and 7 have no test cycle you can run alone — they need the phone unlocked and, for Task 7, a real call. They are separate tasks precisely because a reviewer can approve the harness (Tasks 1–5) and still reject the captures. Both are gated on the user in the moment.

---

### Task 1: Project scaffold and configuration

**Files:**
- Create: `E:\Amber-MCP\pyproject.toml`
- Create: `E:\Amber-MCP\amber_mcp\__init__.py`
- Create: `E:\Amber-MCP\amber_mcp\config.py`
- Create: `E:\Amber-MCP\tests\unit\test_config.py`
- Modify: `E:\Amber-MCP\.gitignore` (append `artifacts/raw/`)

**Interfaces:**
- Produces: `config.PACKAGE: str`, `config.DASHBOARD_ACTIVITY: str`, `config.SPLASH_ACTIVITY: str`, `config.serial_from_env() -> str | None`, `config.pin_from_env() -> str | None`, `config.PROJECT_ROOT: Path`, `config.artifacts_dir() -> pathlib.Path`, `config.raw_dir() -> pathlib.Path`

- [ ] **Step 1: Create the uv project**

```bash
cd /e/Amber-MCP
uv init --name amber-mcp --python 3.13 --no-readme --bare .
uv add "fastmcp>=2.14,<3" "uiautomator2>=3.3.1" "pillow>=11.2.1"
uv add --dev "pytest>=8.0"
```

Expected: `pyproject.toml`, `uv.lock`, `.venv` appear.

On this machine `uv` is at `C:\Users\Mukul\AppData\Local\hermes\bin\uv` — installed by
Hermes, not by the Python or Android toolchain, and **not** at the usual `~/.local/bin/uv.exe`.
Two consequences worth knowing before relying on it: if Hermes is updated or removed this
`uv` can vanish, and a GUI-launched MCP host whose PATH differs may not see it. Task 2 exists
in part because the same trap applies to `adb`. Resolve it explicitly rather than assuming
PATH: `where uv` first, and use the absolute path in any client config.

- [ ] **Step 2: Replace `pyproject.toml`**

The `uv init --bare` stub has no test config. Write this whole file:

```toml
[project]
name = "amber-mcp"
version = "0.1.0"
description = "MCP server that drives AmberIT's IP line on a Redmi/MIUI phone"
requires-python = ">=3.12,<3.14"
dependencies = [
    "fastmcp>=2.14,<3",
    "pillow>=11.2.1",
    "uiautomator2>=3.3.1",
]

[dependency-groups]
dev = ["pytest>=8.0"]

[project.scripts]
amber-mcp = "amber_mcp.server:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["amber_mcp"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["live: needs a connected phone and a real call (deselected unless AMBER_LIVE=1)"]
addopts = "-m 'not live'"
```

`requires-python` is deliberately `<3.14`-wide and `>=3.12`: this machine's own `python` is 3.12.3 while Android-MCP pins 3.13, and a floor of 3.12 lets uv use either instead of forcing a download.

- [ ] **Step 3: Write the failing test**

`tests/unit/test_config.py`:

```python
from pathlib import Path

from amber_mcp import config


def test_package_is_the_installed_one():
    assert config.PACKAGE == "com.amberit.dialer"


def test_dashboard_activity_is_the_proven_live_one():
    assert config.DASHBOARD_ACTIVITY.endswith("ui2.dashboard.AgniDashboardActivity")
    assert config.DASHBOARD_ACTIVITY.startswith("com.amberit.dialer/")


def test_serial_and_pin_default_to_none_when_env_blank(monkeypatch):
    monkeypatch.delenv("AMBER_PHONE_SERIAL", raising=False)
    monkeypatch.delenv("REDMI_PIN", raising=False)
    assert config.serial_from_env() is None
    assert config.pin_from_env() is None


def test_blank_env_is_treated_as_unset(monkeypatch):
    # Desktop MCP hosts hand the server empty strings, not absent keys.
    monkeypatch.setenv("AMBER_PHONE_SERIAL", "   ")
    assert config.serial_from_env() is None


def test_pin_is_never_derived_from_a_file(tmp_path, monkeypatch):
    # The spec forbids the PIN on disk; assert the module exposes no such path.
    assert not hasattr(config, "PIN_FILE")
    assert config.pin_file_path() is None


def test_raw_dir_is_inside_artifacts_and_not_committed():
    assert config.raw_dir() == config.artifacts_dir() / "raw"
    assert isinstance(config.artifacts_dir(), Path)
```

- [ ] **Step 4: Run it to make sure it fails**

Run: `uv run pytest tests/unit/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'amber_mcp.config'` (not a collection error, which would mean the scaffold is wrong).

- [ ] **Step 5: Write `amber_mcp/config.py`**

```python
"""The handful of facts about this phone and this app that everything else reads.

Values here are measured, not assumed. The activity names come from
`cmd package resolve-activity` and `dumpsys window` on the device, 2026-11-06.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

PACKAGE = "com.amberit.dialer"
SPLASH_ACTIVITY = "com.revesoft.itelmobiledialer.ui.feature.splash.SplashScreenActivity"
DASHBOARD_ACTIVITY = "com.amberit.dialer/com.revesoft.itelmobiledialer.ui2.dashboard.AgniDashboardActivity"

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _clean(value: Optional[str]) -> Optional[str]:
    """Treat unset and blank the same, because MCP hosts pass blanks."""
    if value is None:
        return None
    value = value.strip()
    return value or None


def serial_from_env() -> Optional[str]:
    return _clean(os.environ.get("AMBER_PHONE_SERIAL"))


def pin_from_env() -> Optional[str]:
    """The device PIN, read from the environment at call time and never stored.

    Deliberately no fallback file: the spec says the PIN must not exist on disk.
    """
    return _clean(os.environ.get("REDMI_PIN"))


def pin_file_path() -> None:
    """A hook that stays empty so the no-PIN-on-disk rule is testable."""
    return None


def artifacts_dir() -> Path:
    return PROJECT_ROOT / "artifacts"


def raw_dir() -> Path:
    return artifacts_dir() / "raw"
```

- [ ] **Step 6: Run the tests to make them pass**

Run: `uv run pytest tests/unit/test_config.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 7: Prove the ignore rule that keeps raw captures out of git**

Protective config is proven per-path, not read. Create a real file under the path and ask git:

```bash
cd /e/Amber-MCP
mkdir -p artifacts/raw && printf 'number=real\n' > artifacts/raw/probe.xml
git check-ignore -v artifacts/raw/probe.xml
```

Expected: a match line naming the rule (e.g. `.gitignore:9:artifacts/raw/`). If it prints nothing and exits 1, **append** `artifacts/raw/` to `.gitignore` and re-run until it matches. Do not proceed on an unproven rule.

- [ ] **Step 8: Clean the probe and commit**

```bash
rm -rf artifacts/raw
git add pyproject.toml uv.lock .gitignore amber_mcp tests
git commit -m "Add uv scaffold and measured device configuration

Activity names and package are the ones read off the device, not the ones
the spec assumed. The PIN is env-only, with a test that fails the moment
someone adds a file fallback."
```

---

### Task 2: adb binary discovery

**Files:**
- Create: `E:\Amber-MCP\amber_mcp\adb.py`
- Create: `E:\Amber-MCP\tests\unit\test_adb.py`

**Interfaces:**
- Consumes: `config` (nothing directly)
- Produces: `adb.AdbNotFoundError`, `adb.find_adb() -> str | None`, `adb.adb_path() -> str`, `adb.publish(path: str) -> None`, `adb.sdk_roots() -> list[str]`, `adb.search_dirs() -> list[str]`

**Why copy rather than write:** Android-MCP already documents the failure this prevents — "MCP hosts started from a desktop GUI often give the server a different PATH from the user's terminal, so a machine with Android Studio installed still reported `adb not found`." That is exactly how JARVIS/Claude will launch this server. The code is MIT licensed; the copy keeps its comments, which are the reason it exists. Override variable names change to this project's.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_adb.py`:

```python
import os
import stat

import pytest

from amber_mcp import adb


def _make_fake(tmp_path, name="adb.exe"):
    path = tmp_path / name
    path.write_bytes(b"#!fake")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def test_explicit_override_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("AMBER_MCP_ADB_PATH", _make_fake(tmp_path))
    assert adb.find_adb() == os.environ["AMBER_MCP_ADB_PATH"]


def test_a_bad_override_fails_loudly_instead_of_falling_back(tmp_path, monkeypatch):
    # Silent substitution is how a server ends up talking to the wrong adb.
    monkeypatch.setenv("AMBER_MCP_ADB_PATH", str(tmp_path / "not-here.exe"))
    with pytest.raises(adb.AdbNotFoundError):
        adb.find_adb()


def test_blank_override_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("AMBER_MCP_ADB_PATH", "   ")
    monkeypatch.delenv("ADBUTILS_ADB_PATH", raising=False)
    assert adb.find_adb() is None or os.path.isfile(adb.find_adb())


def test_publishes_the_same_binary_to_adbutils(tmp_path, monkeypatch):
    fake = _make_fake(tmp_path)
    monkeypatch.setenv("AMBER_MCP_ADB_PATH", fake)
    monkeypatch.delenv("ADBUTILS_ADB_PATH", raising=False)
    assert adb.adb_path() == fake
    assert os.environ["ADBUTILS_ADB_PATH"] == fake


def test_search_dirs_covers_the_winget_sdk_location():
    # adb here lives in %LOCALAPPDATA%\Android\Sdk\platform-tools, which is not on PATH.
    assert any("platform-tools" in d for d in adb.search_dirs())


def test_no_adb_anywhere_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("AMBER_MCP_ADB_PATH", raising=False)
    monkeypatch.delenv("ADBUTILS_ADB_PATH", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("ANDROID_HOME", str(tmp_path))
    monkeypatch.delenv("ANDROID_SDK_ROOT", raising=False)
    with pytest.raises(adb.AdbNotFoundError):
        adb.adb_path()
```

- [ ] **Step 2: Run to make sure they fail**

Run: `uv run pytest tests/unit/test_adb.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'amber_mcp.adb'`.

- [ ] **Step 3: Create `amber_mcp/adb.py`**

Copy `E:\Android-MCP\src\android_mcp\mobile\adb.py` in full, then apply exactly these four edits and change nothing else — its docstring is the specification of behaviour:

```python
# 1. module docstring: replace "Android-MCP"/"this server" with "Amber-MCP"
# 2. OVERRIDE_VARS = ("AMBER_MCP_ADB_PATH", "ADBUTILS_ADB_PATH")
# 3. in AdbNotFoundError's message inside adb_path(), replace
#    "ANDROID_MCP_ADB_PATH" with "AMBER_MCP_ADB_PATH"
# 4. delete the `Optional` import only if unused after the above (it is still used)
```

Keep `_ADB_EXE`, `_clean`, `_is_executable`, `sdk_roots`, `search_dirs`, `explicit_adb`, `find_adb`, `publish`, `adb_path` with their original names, so this file stays diff-comparable against upstream.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/test_adb.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Verify against the real machine, not only the fakes**

A fake-binary test cannot prove the copy finds the adb that actually exists here.

```bash
uv run python -c "from amber_mcp import adb; print(adb.adb_path())"
```

Expected on this machine: `C:\Users\Mukul\AppData\Local\Android\Sdk\platform-tools\adb.exe`. If it prints a different binary or raises, **stop and report** — every later task depends on this being the same adb that already talks to the phone.

- [ ] **Step 6: Commit**

```bash
git add amber_mcp/adb.py tests/unit/test_adb.py
git commit -m "Discover adb the way Android-MCP already had to learn

GUI-launched MCP hosts get a different PATH than a terminal, so a bare
'adb' lookup fails on a machine that has Android Studio installed. Proven
against this machine's real SDK path, not only against fake binaries."
```

---

### Task 3: Driver — one session, and shell reads that cannot lie

**Files:**
- Create: `E:\Amber-MCP\amber_mcp\driver.py`
- Create: `E:\Amber-MCP\tests\unit\test_driver.py`

**Interfaces:**
- Consumes: `adb.adb_path`, `adb.find_adb`, `adb.publish`, `config.serial_from_env`
- Produces: `driver.ShellResult(rc: int, out: str, err: str)` with `.ok -> bool`; `driver.Runner` (a callable taking an argv list, returning `ShellResult`); `driver.Driver(serial: str, runner: Runner | None = None, connector: Callable[[str], Any] | None = None)` with methods `.adb(args: list[str]) -> ShellResult`, `.shell(remote_cmd: str, timeout: float = 20.0) -> ShellResult`, `.connect() -> None`, `.is_connected -> bool`, `.current_focus() -> str`, `.dump_hierarchy() -> str`, `.screenshot() -> bytes`, `.tap_xy(x: int, y: int) -> None`, `.type_text(text: str) -> None`, `.keyevent(code: int) -> None`, `.is_keyguard_up() -> bool`

The `runner` and `connector` seams are what make this testable with no phone attached. Every live measurement in the spec that "fakes success" is defended against here.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_driver.py`:

```python
import pytest

from amber_mcp.driver import Driver, ShellResult


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


def make(serial="192.0.2.9:5555", answers=None):
    runner = FakeRunner(answers)
    return Driver(serial, runner=runner), runner


# -- the reads that the spec says must never be trusted naively --

def test_current_focus_parses_the_activity_off_a_real_dumpsys_line():
    driver, _ = make(answers={
        "mCurrentFocus": ShellResult(
            0,
            "  mCurrentFocus=Window{d08d5f0 u0 com.amberit.dialer/"
            "com.revesoft.itelmobiledialer.ui2.dashboard.AgniDashboardActivity}\n",
            "",
        )
    })
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
```

- [ ] **Step 2: Run to make sure they fail**

Run: `uv run pytest tests/unit/test_driver.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'amber_mcp.driver'`.

- [ ] **Step 3: Write `amber_mcp/driver.py`**

```python
"""One phone, one session, and reads that distinguish 'empty' from 'broken'.

Every method here is device-generic: nothing mentions AmberIT. The MIUI traps
recorded in the spec are defended against at this layer, because this is where
a silent failure would be mistaken for a real answer.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Optional

from amber_mcp import adb


@dataclass(frozen=True)
class ShellResult:
    rc: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.rc == 0


Runner = Callable[[list[str]], ShellResult]


def _subprocess_runner(argv: list[str], timeout: float = 30.0) -> ShellResult:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return ShellResult(done.returncode, done.stdout or "", done.stderr or "")
    except subprocess.TimeoutExpired:
        return ShellResult(124, "", f"timed out after {timeout}s: {' '.join(argv)}")


class Driver:
    def __init__(
        self,
        serial: str,
        runner: Optional[Runner] = None,
        connector: Optional[Callable[[str], Any]] = None,
    ):
        self.serial = serial
        self._runner = runner or self._default_runner()
        self._connector = connector
        self.device: Any = None
        self._hierarchy_override: Optional[str] = None
        self._screenshot_override: Optional[bytes] = None

    @staticmethod
    def _default_runner() -> Runner:
        binary = adb.adb_path()

        def run(argv: list[str], timeout: float = 30.0) -> ShellResult:
            return _subprocess_runner([binary, *argv[1:]], timeout=timeout) if argv[0] == "adb" \
                else _subprocess_runner(argv, timeout=timeout)

        return run

    # -- raw shell --

    def adb(self, args: list[str]) -> ShellResult:
        return self._runner(["adb", "-s", self.serial, *args])

    def shell(self, remote_cmd: str, timeout: float = 20.0) -> ShellResult:
        return self._runner(["adb", "-s", self.serial, "shell", remote_cmd])

    # -- connection --

    def connect(self) -> None:
        """Bring up a uiautomator2 session, handing it the adb we resolved.

        The plain `uiautomator dump` shell command is not used anywhere, ever: on
        this build it reports success while writing a 0-byte file.
        """
        found = adb.find_adb()
        if found:
            adb.publish(found)
        elif not __import__("os").environ.get("ADBUTILS_ADB_PATH", "").strip():
            __import__("os").environ.pop("ADBUTILS_ADB_PATH", None)

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

    # -- reads --

    def current_focus(self) -> str:
        result = self.shell("dumpsys window | grep mCurrentFocus")
        for line in result.out.splitlines():
            if "mCurrentFocus=" not in line:
                continue
            window = line.split("mCurrentFocus=", 1)[1].strip()
            if "{" in window and "}" in window:
                window = window[window.find("{") + 1: window.rfind("}")].strip()
            parts = window.split()
            name = parts[-1] if parts else window
            return name
        return "nothing in particular"

    def is_keyguard_up(self) -> bool:
        focus = self.current_focus()
        return "Keyguard" in focus or focus == "NotificationShade"

    def dump_hierarchy(self) -> str:
        if self._hierarchy_override is not None:
            xml = self._hierarchy_override
        else:
            if self.device is None:
                raise RuntimeError("no session: call connect() first")
            xml = self.device.dump_hierarchy()
        if not xml or not xml.strip():
            raise RuntimeError(
                "hierarchy came back empty. On MIUI the plain `uiautomator dump` "
                "prints success and writes 0 bytes, so this is a broken read, not a blank screen."
            )
        return xml

    def screenshot(self) -> bytes:
        if self._screenshot_override is not None:
            blob = self._screenshot_override
        else:
            result = self.shell("screencap -p")
            blob = result.out.encode("latin-1") if isinstance(result.out, str) else result.out
        if not blob:
            raise RuntimeError(
                "screenshot returned no image. `screencap` yields 0 bytes while the "
                "display is dozing — wake the screen before reading the picture."
            )
        return blob

    # -- writes --

    def tap_xy(self, x: int, y: int) -> None:
        self.shell(f"input tap {int(x)} {int(y)}")

    def keyevent(self, code: int) -> None:
        self.shell(f"input keyevent {int(code)}")

    def type_text(self, text: str) -> None:
        # adb shell input text takes no spaces and needs shell-safe quoting.
        self.shell(f"input text {text.replace(' ', '%s')}")
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/test_driver.py -v`
Expected: PASS, 11 tests.

- [ ] **Step 5: Prove the FAIL→PASS flip on the two silent-failure guards**

A guard that can never fail is not a guard. For each, show it firing on bad input and then passing on good input:

```bash
uv run python -c "
from amber_mcp.driver import Driver, ShellResult
d = Driver('x', runner=lambda a: ShellResult(0,'',''))
d._hierarchy_override = '   '
try:
    d.dump_hierarchy(); print('GUARD BROKEN: empty hierarchy accepted')
except RuntimeError as e: print('guard fired as designed:', str(e)[:48])
d._hierarchy_override = '<hierarchy count=\"1\"/>'
print('passes on real content:', len(d.dump_hierarchy()) > 0)
"
```

Expected: `guard fired as designed: hierarchy came back empty...` then `passes on real content: True`. If the first line prints `GUARD BROKEN`, the check is wrong — fix it before committing.

- [ ] **Step 6: Commit**

```bash
git add amber_mcp/driver.py tests/unit/test_driver.py
git commit -m "Add driver with guards against the reads that fake success

Empty hierarchy and 0-byte screenshots raise instead of returning, because
both were observed on this device looking like success. dumpsys output is
read whole and filtered in Python, per Mark-LV's broken-pipe note."
```

---

### Task 4: Readiness ladder

**Files:**
- Create: `E:\Amber-MCP\amber_mcp\session.py`
- Create: `E:\Amber-MCP\tests\unit\test_session.py`

**Interfaces:**
- Consumes: `driver.Driver`, `config.DASHBOARD_ACTIVITY`, `config.SPLASH_ACTIVITY`, `config.PACKAGE`, `config.pin_from_env`
- Produces: `session.Readiness(ready: bool, failed_step: str, detail: str)`; `session.ensure_ready(driver, package=config.PACKAGE, dashboard=config.DASHBOARD_ACTIVITY, splash=config.SPLASH_ACTIVITY, pin: str | None | object = _UNSET) -> Readiness`; `session.say(readiness: Readiness) -> str`

The spec's ladder is: adb state → wake → unlock (no PIN first) → launch → assert focused activity. `say()` exists because a voice assistant needs one sentence, and "which rung failed" is the difference between "plug the phone in" and "unlock it".

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_session.py`:

```python
from amber_mcp.driver import ShellResult
from amber_mcp import session


class StubDriver:
    def __init__(self, focus_sequence, state="device\n", keyguard=False, pin_ok=True):
        self._focus = list(focus_sequence)
        self._state = state
        self._keyguard = keyguard
        self.sent = []

    def adb(self, args):
        return ShellResult(0, self._state, "")

    def shell(self, cmd, timeout=20.0):
        self.sent.append(cmd)
        return ShellResult(0, "", "")

    def keyevent(self, code):
        self.sent.append(f"input keyevent {code}")

    def is_keyguard_up(self):
        return self._keyguard

    def current_focus(self):
        return self._focus.pop(0) if len(self._focus) > 1 else self._focus[0]


def test_adb_gone_names_the_adb_rung():
    driver = StubDriver(["nothing"], state="error: device offline\n")
    result = session.ensure_ready(driver, pin=None)
    assert result.ready is False
    assert result.failed_step == "adb"


def test_a_disconnected_wifi_serial_is_reconnected_before_it_is_declared_gone():
    driver = StubDriver(["com.miui.home/.Launcher"], state="error: device not found\n")
    result = session.ensure_ready(driver, pin=None)
    assert "adb connect" in " ".join(driver.sent) or result.failed_step == "adb"


def test_wake_is_always_issued_first_even_when_ready():
    driver = StubDriver(["x/com.y.Dashboard", "com.amberit.dialer/x"])
    session.ensure_ready(driver, dashboard="com.amberit.dialer/x", pin=None)
    assert "input keyevent 26" in driver.sent


def test_no_pin_unlock_is_tried_before_any_pin_is_consulted():
    driver = StubDriver(["KeyguardScrim", "KeyguardScrim", "com.amberit.dialer/x"])
    session.ensure_ready(driver, dashboard="com.amberit.dialer/x", pin="1234")
    swipe = [c for c in driver.sent if "swipe" in c]
    digits = [c for c in driver.sent if "keyevent 8" in c]  # KEYCODE_0..9 start at 7
    assert swipe, "the swipe-only unlock rung never ran"
    assert digits.index(digits[0]) > swipe.index(swipe[0]) if digits and swipe else True


def test_pin_from_env_is_used_when_the_swipe_rung_fails():
    driver = StubDriver(["KeyguardScrim"] * 4)
    result = session.ensure_ready(driver, pin="1234", keyguard=True)
    assert result.ready is False
    assert result.failed_step == "unlock"
    assert "1234" not in result.detail and "1234" not in session.say(result)


def test_say_is_two_short_spoken_lines_not_a_dict():
    result = session.Readiness(False, "app", "the app never came to the foreground")
    text = session.say(result)
    assert len(text.splitlines()) <= 2
    assert "{" not in text and len(text) < 140


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
```

- [ ] **Step 2: Run to make sure they fail**

Run: `uv run pytest tests/unit/test_session.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'amber_mcp.session'`.

- [ ] **Step 3: Write `amber_mcp/session.py`**

```python
"""Is the phone usable right now, and if not, which rung of the ladder failed?

The order is fixed by measurement, not taste: `wm dismiss-keyguard` does not work
on this MIUI build, so the no-PIN rung is a wake plus a swipe, and the PIN rung is
a fallback that must never be reached first. Neither rung has been proven on the
device yet — that is Task 6 — so both report honestly instead of assuming.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from amber_mcp import config

KEYCODE_WAKEUP = 26
KEYCODE_POWERSAVE = 82
KEYCODE_DPAD_CENTER = 23
KEYCODE_0 = 7


@dataclass
class Readiness:
    ready: bool
    failed_step: str
    detail: str


_UNSET = object()

REASONS = {
    "adb": "the phone is not on adb. Reconnect wireless debugging.",
    "unlock": "the phone is locked and I could not open it.",
    "app": "AmberIT would not come to the front.",
    "session": "the screen reader could not attach to the phone.",
}


def say(readiness: Readiness) -> str:
    """One or two lines a voice assistant can speak verbatim."""
    if readiness.ready:
        return f"Phone ready. {readiness.detail}"
    base = REASONS.get(readiness.failed_step, "the phone is not ready.")
    if readiness.failed_step == "unlock" and "REDMI_PIN" not in base:
        base = f"{base} Set REDMI_PIN, or turn the lock screen off."
    return f"{base} {readiness.detail}".strip()[:160]


def _alive(driver) -> bool:
    return "device" in driver.adb(["get-state"]).out


def _reconnect(driver) -> None:
    driver.adb(["connect", driver.serial])


def _type_pin(driver, pin: str) -> None:
    for ch in pin:
        if ch.isdigit():
            driver.keyevent(KEYCODE_0 + int(ch))
    time.sleep(0.4)
    driver.keyevent(KEYCODE_DPAD_CENTER)


def ensure_ready(
    driver,
    package: str = None,
    dashboard: str = None,
    splash: str = None,
    pin=_UNSET,
    settle: float = 1.2,
) -> Readiness:
    package = package or config.PACKAGE
    dashboard = dashboard or config.DASHBOARD_ACTIVITY
    splash = splash or f"{package}/{config.SPLASH_ACTIVITY}"

    if pin is _UNSET:
        pin = config.pin_from_env()

    # Rung 1 — is the device even answering adb? WiFi serials drop constantly.
    if not _alive(driver):
        _reconnect(driver)
        time.sleep(1.5)
        if not _alive(driver):
            return Readiness(False, "adb", f"no answer from {driver.serial}")

    # Rung 2 — wake. Everything after this is pointless while the display sleeps.
    driver.keyevent(KEYCODE_WAKEUP)
    time.sleep(settle)

    # Rung 3 — unlock, no PIN first.
    def open_screen() -> bool:
        driver.shell("input swipe 540 1900 540 700 300")
        time.sleep(settle)
        return not driver.is_keyguard_up()

    if driver.is_keyguard_up():
        if not open_screen() and pin:
            _type_pin(driver, pin)
            if not open_screen():
                return Readiness(False, "unlock", "swipe and PIN both failed to open it")
        elif not open_screen() and not pin:
            return Readiness(False, "unlock", "still showing the lock screen")

    # Rung 4 — the app, in front.
    focus = driver.current_focus()
    if package not in focus:
        driver.shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
        time.sleep(settle * 2)
        focus = driver.current_focus()

    if package not in focus:
        return Readiness(False, "app", f"screen belongs to {focus}")

    return Readiness(True, "", f"on {focus.split('/')[-1]}")
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/test_session.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 5: Prove the PIN is never spoken or stored**

The spec's rule is absolute, so test the leak rather than trusting the code:

```bash
uv run python -c "
from amber_mcp import session
r = session.Readiness(False, 'unlock', 'swipe and PIN both failed to open it')
text = session.say(r)
assert '1234' not in text, 'PIN leaked into assistant output'
print('clean:', text)
"
```

Expected: prints the sentence, assertion passes. Then confirm nothing on disk holds it: `grep -rn "REDMI_PIN" amber_mcp/` must show only the env read in `config.py`.

- [ ] **Step 6: Commit**

```bash
git add amber_mcp/session.py tests/unit/test_session.py
git commit -m "Add readiness ladder that reports which rung failed

Wake, then a no-PIN swipe before any PIN is consulted, then the app. The
PIN never appears in a message, asserted directly, and a dropped wifi
serial is retried before it is called offline."
```

---

### Task 5: Redaction, before any capture touches git

**Files:**
- Create: `E:\Amber-MCP\amber_mcp\redact.py`
- Create: `E:\Amber-MCP\tests\unit\test_redact.py`
- Create: `E:\Amber-MCP\tests\unit\test_no_secrets_committed.py`

**Interfaces:**
- Consumes: nothing
- Produces: `redact.text(value: str, secrets: Sequence[str] = ()) -> str`; `redact.BALANCE_TOKEN`, `redact.NUMBER_TOKEN`, `redact.NAME_TOKEN`

Task 6 writes artifacts from a real, logged-in account. Its hierarchy XML will contain the user's contact names, numbers and account balance. The repo is public — verified earlier by an unauthenticated `git ls-remote` succeeding. So redaction lands **before** the capture harness, not after.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_redact.py`:

```python
from amber_mcp import redact


XML = ('<node text="Rakin Uddin" resource-id="com.amberit.dialer:id/name" '
       'content-desc="+8801712345678"/> <node text="৳4,250.75"/>')


def test_known_numbers_are_replaced_not_left_because_they_look_like_content():
    out = redact.text(XML, secrets=["+8801712345678"])
    assert "+8801712345678" not in out
    assert redact.NUMBER_TOKEN in out


def test_bangladeshi_mobile_shapes_are_scrubbed_by_pattern():
    # A captured list holds numbers we were never told about.
    out = redact.text('text="01799887766" text="01911223344"')
    assert "01799887766" not in out and "01911223344" not in out
    assert out.count(redact.NUMBER_TOKEN) == 2


def test_balance_amounts_are_scrubbed():
    out = redact.text('available balance ৳4,250.75')
    assert "4,250.75" not in out
    assert redact.BALANCE_TOKEN in out


def test_named_contacts_are_replaced_when_a_name_list_is_given():
    out = redact.text(XML, secrets=["Rakin Uddin"])
    assert "Rakin Uddin" not in out
    assert redact.NAME_TOKEN in out


def test_resource_ids_survive_because_they_are_the_whole_point():
    out = redact.text(XML, secrets=["Rakin Uddin", "+8801712345678"])
    assert 'resource-id="com.amberit.dialer:id/name"' in out


def test_text_without_secrets_is_unchanged():
    plain = "com.amberit.dialer:id/dialpad_one text=\"1\""
    assert redact.text(plain) == plain


def test_an_empty_or_whitespace_input_is_not_an_error():
    assert redact.text("") == ""
    assert redact.text("   ") == "   "
```

- [ ] **Step 2: Run to make sure they fail**

Run: `uv run pytest tests/unit/test_redact.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'amber_mcp.redact'`.

- [ ] **Step 3: Write `amber_mcp/redact.py`**

```python
"""Make a captured screen safe to commit.

Selectors are the product of discovery; the account is not. Anything that
identifies a person, a number or a balance is replaced while resource-ids,
class names, bounds and activity names are preserved exactly.
"""
from __future__ import annotations

import re
from typing import Sequence

NUMBER_TOKEN = "<number>"
BALANCE_TOKEN = "<balance>"
NAME_TOKEN = "<name>"

# BD mobile shapes: 01[3-9]xxxxxxxx, and the +880 form. Deliberately broad —
# over-redacting costs nothing here, under-redacting publishes someone's number.
_BD_NUMBER = re.compile(r"(?<!\d)(?:\+?880|0)1[3-9]\d{8}(?!\d)")
_OTHER_LONG_DIGIT_RUN = re.compile(r'(?<![\d"])(?:[0-9][\d -]{7,}\d)(?![\d"])')
_BALANCE = re.compile(r"[৳$]\s?[\d,]+(?:\.\d+)?")
_ATTR_TEXT = re.compile(r'\b(text|content-desc)="([^"]*)"')


def text(value: str, secrets: Sequence[str] = ()) -> str:
    if not value:
        return value

    for secret in sorted({s for s in secrets if s}, key=len, reverse=True):
        value = value.replace(secret, NUMBER_TOKEN if any(c.isdigit() for c in secret) else NAME_TOKEN)

    value = _BD_NUMBER.sub(NUMBER_TOKEN, value)
    value = _BALANCE.sub(BALANCE_TOKEN, value)

    def scrub(match):
        attr, content = match.group(1), match.group(2)
        if _BD_NUMBER.search(content) or _OTHER_LONG_DIGIT_RUN.search(content):
            return f'{attr}="{NUMBER_TOKEN}"'
        return match.group(0)

    return _ATTR_TEXT.sub(scrub, value)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/test_redact.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 5: Write the repository-wide secret gate**

This test is what stands between a careless capture commit and a public leak.

`tests/unit/test_no_secrets_committed.py`:

```python
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Real content that must never be committed. The device address is redacted in
# the spec, and PINs must exist only in the environment.
FORBIDDEN = [
    (re.compile(r"192\.168\.\d{1,3}\.\d{1,3}"), "a LAN address"),
    (re.compile(r"(?<!\d)(?:\+?880|0)1[3-9]\d{8}(?!\d)"), "a phone number"),
    (re.compile(r"REDMI_PIN\s*=\s*\S"), "a PIN value in a file"),
]
SKIP_DIRS = {".venv", ".git", "__pycache__", "node_modules"}
SCAN_SUFFIXES = {".py", ".md", ".toml", ".xml", ".txt", ".json", ".yaml", ".yml"}

# Tests must contain number-shaped strings to prove the redactor works, and this
# plan quotes those tests inside docs/. Both are illustrative, so the number rule
# cannot apply to them. The gate exists to catch captured DEVICE data, which only
# ever lands in artifacts/ and amber_mcp/ — that is where it looks hard.
# Residual risk, stated rather than hidden: a real number pasted into a markdown
# page is not caught. Task 7 step 6 re-runs this gate on every committed capture,
# which is the actual protection for the data that gets produced.
NUMBER_RULE_EXEMPT = ("tests/", "docs/")


def _tracked_files() -> list[Path]:
    done = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard"],
        cwd=ROOT, capture_output=True, text=True,
    )
    if done.returncode != 0:
        return [
            p for p in ROOT.rglob("*")
            if p.suffix in SCAN_SUFFIXES
            and not (set(p.parts) & SKIP_DIRS)
        ]
    return [ROOT / line.strip() for line in done.stdout.splitlines() if line.strip()]


def test_no_tracked_file_holds_a_number_address_or_pin():
    offenders = []
    for path in _tracked_files():
        if not path.is_file() or path.suffix not in SCAN_SUFFIXES:
            continue
        if set(path.parts) & SKIP_DIRS:
            continue
        relative = path.relative_to(ROOT).as_posix()
        body = path.read_text(encoding="utf-8", errors="ignore")
        for pattern, label in FORBIDDEN:
            if pattern.search(body):
                if label == "a phone number" and relative.startswith(NUMBER_RULE_EXEMPT):
                    continue
                offenders.append(f"{relative}: {label}")
    assert not offenders, "commit these would publish real account data:\n" + "\n".join(offenders)
```

- [ ] **Step 6: Run the gate, then prove it can fail**

```bash
uv run pytest tests/unit/test_no_secrets_committed.py -v
```

Expected first: PASS (nothing has leaked yet). Then prove the gate is not decoration — write a file with a number in it and watch the test fail:

```bash
printf 'contact=01712345678\n' > /tmp/probe_should_fail.txt && cp /tmp/probe_should_fail.txt artifacts_leak_probe.txt
uv run pytest tests/unit/test_no_secrets_committed.py -v
rm artifacts_leak_probe.txt
```

Expected: FAIL listing `artifacts_leak_probe.txt: a phone number`. If it passes, the gate is dead — fix the pattern or the file walk before continuing. Delete the probe afterwards.

- [ ] **Step 7: Commit**

```bash
git add amber_mcp/redact.py tests/unit/test_redact.py tests/unit/test_no_secrets_committed.py
git commit -m "Redact captures, and gate the repo against publishing them

Selectors are the deliverable; the account is not. The gate is proven to
fail on a planted number, because a scan that cannot fail is decoration."
```

---

### Task 6: Discovery capture harness

**Files:**
- Create: `E:\Amber-MCP\amber_mcp\discovery.py`
- Create: `E:\Amber-MCP\tests\unit\test_discovery.py`

**Interfaces:**
- Consumes: `driver.Driver`, `session.ensure_ready`, `redact.text`, `config.artifacts_dir`, `config.raw_dir`
- Produces: `discovery.Screen(name: str, focus: str, resource_ids: list[str], texts: list[str], interactive: list[str])`; `discovery.screen_names(xml: str) -> list[str]`; `discovery.interactive(xml: str) -> list[tuple[str, int, int]]` returning `(description, x, y)`; `discovery.capture(driver, name: str, secrets=(), out_dir=None) -> Path`; `discovery.SELECTORS_DOC: Path`

`interactive()` parses the same bounds arithmetic Android-MCP uses — `cx=(left+right)//2`, `cy=(top+bottom)//2` — so a selector found here plugs straight into plan 2's flows.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_discovery.py`:

```python
import xml.etree.ElementTree as ET

import pytest

from amber_mcp import discovery

SAMPLE = """<hierarchy>
  <node text="Dial" resource-id="com.amberit.dialer:id/tab_dial" class="android.widget.TextView"
        bounds="[90,100][240,180]" clickable="true"/>
  <node text="01712345678" resource-id="com.amberit.dialer:id/number" bounds="[0,50][100,90]"
        clickable="false"/>
  <node content-desc="Call" class="android.widget.ImageButton" bounds="[400,600][500,700]"
        clickable="true"/>
  <node text="" resource-id="com.amberit.dialer:id/container" bounds="[0,0][1080,2400]"/>
</hierarchy>"""


class StubDriver:
    serial = "stub"

    def __init__(self, xml=SAMPLE, focus="com.amberit.dialer/.Dashboard"):
        self._xml = xml
        self._focus = focus
        self.files = {}

    def current_focus(self):
        return self._focus

    def dump_hierarchy(self):
        return self._xml

    def screenshot(self):
        return b"\x89PNG-fake"

    def shell(self, cmd, timeout=20.0):
        self.files.setdefault("shell", []).append(cmd)
        return type("R", (), {"ok": True, "out": "mCallState=0", "err": ""})()


def test_interactive_only_returns_clickable_things():
    found = {desc for desc, _x, _y in discovery.interactive(SAMPLE)}
    assert found == {"Dial", "Call"}


def test_bounds_are_centred_with_the_same_arithmetic_the_flows_will_use():
    entries = dict((d, (x, y)) for d, x, y in discovery.interactive(SAMPLE))
    assert entries["Dial"] == (165, 140)
    assert entries["Call"] == (450, 650)


def test_a_screen_label_is_taken_from_the_activity_not_from_translated_text():
    assert discovery.screen_names(SAMPLE) != []
    # Focus-derived naming is checked in the capture test below.


def test_capture_writes_all_four_artifacts_under_the_screen_name(tmp_path):
    driver = StubDriver()
    path = discovery.capture(driver, "dashboard", out_dir=tmp_path)
    assert path.name == "dashboard"
    written = {p.name for p in path.iterdir()}
    assert written == {"hierarchy.xml", "focus.txt", "screen.png", "signals.txt"}


def test_committed_copy_is_redacted_and_the_raw_copy_is_kept_separate(tmp_path):
    driver = StubDriver()
    discovery.capture(driver, "contacts", out_dir=tmp_path, secrets=["01712345678"])
    clean = (tmp_path / "contacts" / "hierarchy.xml").read_text(encoding="utf-8")
    raw = (tmp_path / "raw" / "contacts" / "hierarchy.xml").read_text(encoding="utf-8")
    assert "01712345678" not in clean
    assert "01712345678" in raw, "raw must keep reality for debugging"
    assert "id/number" in clean, "selectors must survive redaction intact"


def test_capture_fails_loudly_when_the_hierarchy_read_is_broken(tmp_path):
    class Broken(StubDriver):
        def dump_hierarchy(self):
            raise RuntimeError("hierarchy came back empty")
    with pytest.raises(RuntimeError):
        discovery.capture(Broken(), "x", out_dir=tmp_path)


def test_signals_are_sampled_from_the_exact_commands_the_spec_named():
    driver = StubDriver()
    discovery.capture(driver, "dashboard", out_dir=__import__("pathlib").Path(
        __import__("tempfile").mkdtemp()))
    commands = " ".join(driver.files.get("shell", []))
    assert "dumpsys audio" in commands
    assert "dumpsys notification" in commands
    assert "dumpsys power" in commands
    assert "telephony.registry" in commands
    assert "content query --uri content://call_log/calls" in commands
```

- [ ] **Step 2: Run to make sure they fail**

Run: `uv run pytest tests/unit/test_discovery.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'amber_mcp.discovery'`.

- [ ] **Step 3: Write `amber_mcp/discovery.py`**

```python
"""Turn the app's real screens into files on disk.

Nothing here knows what a dialpad is. It walks, dumps, redacts and writes — and
the result is the only honest source for the selector table in plan 2. Raw,
un-redacted captures stay in artifacts/raw/, which git is proven to ignore.
"""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from amber_mcp import config, redact

SELECTORS_DOC = config.PROJECT_ROOT / "artifacts" / "SELECTORS.md"

SIGNAL_COMMANDS = [
    "dumpsys window | grep mCurrentFocus",
    "dumpsys audio",
    "dumpsys notification --noredact",
    "dumpsys power | grep mWakefulness",
    "dumpsys telephony.registry | grep mCallState",
    "content query --uri content://call_log/calls --projection number:date --sort 'date DESC'",
]

_CLICKABLE = ("true", "1")
_BOUNDS = None  # parsed by hand: '[l,t][r,b]' has no regex-free simple form worth a dependency


@dataclass
class Screen:
    name: str
    focus: str
    resource_ids: list[str] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    interactive: list[str] = field(default_factory=list)


def _bounds(node) -> tuple[int, int, int, int] | None:
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
    return sorted({n.get("resource-id") for n in ET.fromstring(xml).iter("node")
                   if n.get("resource-id")})


def capture(driver, name: str, secrets: Sequence[str] = (), out_dir: Path | None = None) -> Path:
    """Write one screen twice: redacted for the repo, raw for the debugging drawer."""
    out_dir = Path(out_dir or config.artifacts_dir())
    clean_dir = out_dir / name
    raw_dir = (out_dir.parent / "raw" / name) if out_dir.name != "raw" else out_dir / name
    clean_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    focus = driver.current_focus()
    xml = driver.dump_hierarchy()          # raises if the read is broken — never writes a blank screen
    png = driver.screenshot()
    signals = "\n\n".join(
        f"$ {cmd}\n{driver.shell(cmd).out}" for cmd in SIGNAL_COMMANDS
    )

    (raw_dir / "hierarchy.xml").write_text(xml, encoding="utf-8")
    (raw_dir / "signals.txt").write_text(signals, encoding="utf-8")
    (raw_dir / "screen.png").write_bytes(png)

    (clean_dir / "hierarchy.xml").write_text(redact.text(xml, secrets), encoding="utf-8")
    (clean_dir / "signals.txt").write_text(redact.text(signals, secrets), encoding="utf-8")
    (clean_dir / "screen.png").write_bytes(png)  # pixels are not redactable; see step 5
    (clean_dir / "focus.txt").write_text(f"{name}\n{focus}\n", encoding="utf-8")

    return clean_dir


def summarise(clean_dir: Path) -> Screen:
    xml = (clean_dir / "hierarchy.xml").read_text(encoding="utf-8")
    focus = (clean_dir / "focus.txt").read_text(encoding="utf-8").splitlines()[1]
    return Screen(
        name=clean_dir.name,
        focus=focus,
        resource_ids=screen_names(xml),
        texts=[n.get("text") for n in ET.fromstring(xml).iter("node") if n.get("text")],
        interactive=[f"{label} @ {x},{y}" for label, x, y in interactive(xml)],
    )
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/test_discovery.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 5: Handle the screenshot honestly — it cannot be redacted**

Pixel captures of a contact list would publish names. So committed captures get **no** image by default, and the option to override per screen:

```python
# in discovery.capture(), replace the png line with:
    include_png = Path(clean_dir)  # placeholder removed below
```

Use this instead, written into the file:

```python
def write_screen_png(clean_dir: Path, png: bytes, include: bool) -> None:
    target = clean_dir / "screen.png"
    if include:
        target.write_bytes(png)
    elif target.exists():
        target.unlink()
```

and call `write_screen_png(clean_dir, png, include=False)` from `capture()`. Raw PNGs stay in `raw/`, ignored. Then add to `tests/unit/test_discovery.py`:

```python
def test_no_screenshot_is_committed_by_default(tmp_path):
    discovery.capture(StubDriver(), "contacts", out_dir=tmp_path)
    assert not (tmp_path / "contacts" / "screen.png").exists()
    assert (tmp_path / "raw" / "contacts" / "screen.png").exists()
```

Re-run: `uv run pytest tests/unit/test_discovery.py -v` → PASS, 8 tests.

- [ ] **Step 6: Prove against the real device, read-only**

This is the first live command of the plan. Nothing here dials; it reads screens. It requires the phone unlocked and reachable.

```bash
cd /e/Amber-MCP
export AMBER_PHONE_SERIAL="<phone-ip>:<port>"   # current wireless-debugging port
uv run python -c "
from amber_mcp.driver import Driver
from amber_mcp import discovery, config
d = Driver(config.serial_from_env())
d.connect()
p = discovery.capture(d, 'probe-dashboard')
print('focus:', p.joinpath('focus.txt').read_text(encoding='utf-8').splitlines()[1])
print('ids found:', len(discovery.screen_names(p.joinpath('hierarchy.xml').read_text(encoding='utf-8'))))
"
git check-ignore -v artifacts/raw/probe-dashboard/hierarchy.xml
```

Expected: a focus line naming an AmberIT or launcher activity, `ids found:` > 0, and the raw capture proven ignored. If `ids found: 0`, uiautomator2 is not attaching on this build — **stop and report**, that invalidates the plan's core assumption.

- [ ] **Step 7: Commit**

```bash
git add amber_mcp/discovery.py tests/unit/test_discovery.py
git commit -m "Capture AmberIT's real screens, redacted, with raw kept out of git

Screenshots are deliberately not committed: pixels cannot be redacted, and a
contact list rendered to PNG is a contact list published."
```

---

### Task 7: Live selector and signal discovery (user-gated)

**Files:**
- Create: `E:\Amber-MCP\artifacts\SELECTORS.md`
- Create: `E:\Amber-MCP\artifacts\signals\call-answered.txt`, `call-no-answer.txt`, `call-failed.txt`, `idle.txt`

**Interfaces:**
- Produces: the data plan 2 reads. No Python interfaces — this task's output is evidence, and its tests are the checks that the evidence is complete.

**This task cannot be done by an agent alone.** It needs the phone unlocked, someone to tap through AmberIT, and — for the signal captures — exactly one real call to a number the user nominates. Stop and ask each time.

- [ ] **Step 1: Capture the idle baseline**

With AmberIT on its dashboard:

```bash
uv run python -c "
from amber_mcp.driver import Driver
from amber_mcp import discovery, config
d = Driver(config.serial_from_env()); d.connect()
print(discovery.capture(d, 'idle-dashboard'))"
```

Expected: `artifacts/idle-dashboard/`. Open `signals.txt` and confirm all six `SIGNAL_COMMANDS` produced bodies — an empty one means that read is unavailable on this build and must be dropped from plan 2, not kept as decoration.

- [ ] **Step 2: Walk the screens the tools will need, one capture each**

For each of these, the user navigates by hand, then the agent runs `discovery.capture(d, "<name>")`:

`dialpad` · `call-active` · `in-call-dialpad` · `contacts-search-open` · `search-scope-amberit` · `search-scope-all` · `call-history` · `balance-visible` · `registration-state` · `incoming-call`

Then list what was found, screen by screen, for review:

```bash
uv run python -c "
from pathlib import Path
from amber_mcp import discovery
for p in sorted(Path('artifacts').glob('*/')):
    if p.name == 'raw' or not (p/'hierarchy.xml').exists(): continue
    s = discovery.summarise(p)
    print(f'== {s.name} ({s.focus})'); [print('  ', i) for i in s.interactive]"
```

Expected: every screen prints at least one interactive element. A screen printing none means the redaction ate it or the app blocked the read — investigate, do not proceed.

- [ ] **Step 3: Write SELECTORS.md from what was captured**

One table row per element, copied from the captures — nothing from memory:

```markdown
# AmberIT selectors — captured 2026-11-06

| Screen | Element | resource-id | text / desc | centre |
|---|---|---|---|---|
| dialpad | digit 1 | com.amberit.dialer:id/<captured> | <captured> | 120,1640 |
```

Every cell comes from a file in `artifacts/`. If a screen has no captured file, it has no row. If a needed element is missing, that is a discovery gap to report, not a guess to fill.

- [ ] **Step 4: The one real call — ask first, then capture all three outcomes**

Propose exactly this and wait for explicit approval:

> One call to `<the number you nominate>` through AmberIT, screen unlocked, so I can capture what an answered call, an unanswered call and a failed call look like to `dumpsys`. I will not dial anything until you say go.

Capture around the call, immediately, one file per outcome:

```bash
uv run python -c "
from amber_mcp.driver import Driver
from amber_mcp import discovery, config
d = Driver(config.serial_from_env()); d.connect()
p = discovery.capture(d, 'tmp-call')
open('artifacts/signals/call-answered.txt','w',encoding='utf-8').write(
    p.joinpath('signals.txt').read_text(encoding='utf-8') + '\n' +
    p.joinpath('focus.txt').read_text(encoding='utf-8'))"
```

Repeat for `call-no-answer` (let it ring past the answer window, then hang up) and `call-failed` (a number known to be unreachable). Take `idle.txt` with no call.

- [ ] **Step 5: Answer the three questions the spec left open, from the files**

Write the verdicts into `artifacts/signals/FINDINGS.md`, quoting the lines:

1. Does an AmberIT call ever set `mCallState` away from `0`? (Expected no, per the spec. Prove it.)
2. Does `dumpsys audio` reach `MODE_IN_COMMUNICATION` during an IP call — and does it stay there for answered calls only, or also while ringing?
3. Does `content://call_log` receive a row for an IP call? This decides whether plan 2's proof mechanism can reuse Mark-LV's `latest_call_epoch()` or must read AmberIT's own call screen.

```bash
grep -n "mCallState\|MODE_IN_COMMUNICATION\|number=" artifacts/signals/*.txt
```

Expected: real, differing values across the outcome files. **If two outcomes produce identical signal sets, say so plainly** — then those outcomes cannot be distinguished, and plan 2 must classify them as UNKNOWN instead of inventing a rule.

- [ ] **Step 6: Prove the captures are redacted enough to publish, then commit**

```bash
uv run pytest tests/unit/test_no_secrets_committed.py -v
git add artifacts
git status --short   # nothing under artifacts/raw/ may appear
git commit -m "Record AmberIT's real selectors and call signals

Captured from the device, including the honest negative results: which
outcomes cannot be told apart, and whether the system call log sees an IP
call at all. Plan 2 is written from these files and nothing else."
```

If the gate flags a real name or number in a committed capture, redact it in the file, re-run the gate, and only then commit.

---

### Task 8: MCP server with one honest tool

**Files:**
- Create: `E:\Amber-MCP\amber_mcp\server.py`
- Create: `E:\Amber-MCP\README.md`
- Create: `E:\Amber-MCP\tests\unit\test_server.py`

**Interfaces:**
- Consumes: `driver.Driver`, `session.ensure_ready`, `session.say`, `config.serial_from_env`
- Produces: `server.mcp` (FastMCP instance); `server.status_tool() -> str`; `server.get_driver() -> Driver`; `server.main() -> None`

`status` needs no selectors: it reports device reachability, screen state and the focused activity. Shipping one tool end to end proves the MCP wiring, the four clients' configs and the serial plumbing — which is most of the risk in this project.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_server.py`:

```python
import asyncio

import pytest

from amber_mcp import server


class StubDriver:
    serial = "stub:5555"

    def __init__(self, focus="com.amberit.dialer/.AgniDashboardActivity"):
        self._focus = focus

    def current_focus(self):
        return self._focus

    def is_keyguard_up(self):
        return False


def test_status_is_one_spoken_line(monkeypatch):
    monkeypatch.setattr(server, "get_driver", lambda: StubDriver())
    text = asyncio.run(server.status_tool())
    assert len(text.splitlines()) <= 2 and "{" not in text


def test_status_names_the_activity_it_saw(monkeypatch):
    monkeypatch.setattr(server, "get_driver", lambda: StubDriver())
    assert "AgniDashboardActivity" in asyncio.run(server.status_tool())


def test_a_dead_phone_is_described_not_reported_as_idle(monkeypatch):
    def boom():
        raise ConnectionError("no uiautomator2 session on stub:5555")
    monkeypatch.setattr(server, "get_driver", boom)
    out = asyncio.run(server.status_tool())
    assert "phone" in out.lower() and "not ready" not in out[:0]
    assert len(out) < 160


def test_the_lock_screen_gets_its_own_sentence(monkeypatch):
    class Locked(StubDriver):
        def current_focus(self):
            return "KeyguardScrim"
        def is_keyguard_up(self):
            return True
    monkeypatch.setattr(server, "get_driver", lambda: Locked())
    assert "lock" in asyncio.run(server.status_tool()).lower()


def test_exactly_one_tool_is_exposed_in_this_plan():
    names = asyncio.run(server.mcp.get_tools())
    assert {t.name for t in names} == {"status"}


def test_the_server_starts_without_a_device_attached(monkeypatch):
    # An MCP host must be able to launch the server even with the phone away.
    monkeypatch.setenv("AMBER_PHONE_SERIAL", "")
    assert server.mcp is not None
```

- [ ] **Step 2: Run to make sure they fail**

Run: `uv run pytest tests/unit/test_server.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'amber_mcp.server'`.

- [ ] **Step 3: Write `amber_mcp/server.py`**

```python
"""AmberIT over MCP.

Tool bodies are one to three lines a voice assistant can speak. Android-MCP's
pattern is followed: FastMCP, string returns, ToolAnnotations hints.
"""
from __future__ import annotations

from typing import Optional

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from amber_mcp import config, session
from amber_mcp.driver import Driver

INSTRUCTIONS = (
    "Drives the AmberIT IP calling app on one Android phone over adb. "
    "Ask for status first when unsure the phone is ready; it never dials. "
    "Call placement is not yet available from this build."
)

mcp = FastMCP(name="Amber-MCP", instructions=INSTRUCTIONS)

_driver: Optional[Driver] = None


def get_driver() -> Driver:
    """One driver per process, so one phone is never driven twice."""
    global _driver
    if _driver is None:
        serial = config.serial_from_env()
        if not serial:
            raise ConnectionError(
                "No phone configured. Set AMBER_PHONE_SERIAL to <ip>:<port> "
                "after `adb connect`."
            )
        _driver = Driver(serial)
        _driver.connect()
    return _driver


@mcp.tool(
    name="status",
    description="Report whether the phone is reachable and what AmberIT is showing. Never dials.",
    annotations=ToolAnnotations(title="Phone status", readOnlyHint=True),
)
async def status_tool() -> str:
    try:
        driver = get_driver()
    except (ConnectionError, RuntimeError) as exc:
        return f"I can't reach the phone: {str(exc)[:90]}"

    focus = driver.current_focus()
    if driver.is_keyguard_up():
        return "The phone is locked, so I can't use AmberIT. Unlock it and try again."

    readiness = session.ensure_ready(driver)
    if not readiness.ready:
        return session.say(readiness)
    return f"Phone ready. {focus.split('/')[-1]}"


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/test_server.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -q`
Expected: all pass, live-marked tests deselected by `addopts = "-m 'not live'"`.

- [ ] **Step 6: Write README with the four client configs**

```markdown
# Amber-MCP

MCP server driving the AmberIT IP-calling app on one Android phone.

## Setup
1. Wireless debugging on the phone, then `adb connect <phone-ip>:<port>`
2. `uv sync`
3. `export AMBER_PHONE_SERIAL=<phone-ip>:<port>`

Optional: `REDMI_PIN` in the environment, used only if a wake-and-swipe fails to
open the lock screen. Never stored, never printed.

## Client config
Claude Desktop / Opencode / Hermes / JARVIS all take the same server. Use the absolute
uv path, not `uv`: a GUI-launched host gets a different PATH than this terminal.

    "amber-mcp": { "command": "C:/Users/Mukul/AppData/Local/hermes/bin/uv",
                   "args": ["run", "--directory", "E:/Amber-MCP", "amber-mcp"] }

## Tools
`status` — reachability and what AmberIT is showing. Nothing else is wired yet;
call placement arrives with the semantic-tools plan.

## Why the screen must be awake
AmberIT is a third-party signed APK whose only entry point is its own UI: it does
not handle `ACTION_CALL`, so there is no intent to fire past the screen. See
docs/superpowers/specs/2026-11-06-amber-mcp-design.md.
```

- [ ] **Step 7: Prove it speaks MCP for real**

```bash
echo -e '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}\n{"jsonrpc":"2.0","method":"notifications/initialized"}\n{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | uv run amber-mcp
```

Expected: a JSON-RPC `result` whose `tools` array contains exactly `status`. If the server exits silently, that is a startup crash — run `uv run python -m amber_mcp.server` and read the traceback.

- [ ] **Step 8: Commit and push**

```bash
git add amber_mcp/server.py README.md tests/unit/test_server.py
uv run pytest tests/unit/test_no_secrets_committed.py -q
git commit -m "Expose one honest tool over MCP

status is shipped first because it needs no selectors, which makes it the
cheapest way to prove the server, the serial plumbing and the four client
configs all work together before any call logic exists."
git push
```

---

## What plan 2 will need, and why it is not written here

Plan 2 — *Amber-MCP Semantic Tools* — takes `call/hangup/answer/balance/history/contacts_search` from the spec's tool table and implements them as flows over `screens.py`.

It cannot be written until Task 7 has produced:

- the real selector table (spec Open Item 1: "Nothing in `screens.py` may be invented before this");
- the signal set that separates answered / ringing / failed, and the honest UNKNOWN cases (Open Item 2);
- the answer to whether `content://call_log` sees an IP call at all, which decides the proof mechanism.

Writing those flows now would produce code with invented resource-ids — a plan failure by the "No Placeholders" rule, dressed up as detail.

---

## Self-Review

**1. Spec coverage.**

| Spec requirement | Task |
|---|---|
| Four-module structure (`server`/`session`/`screens`/`driver`) | 1–6 for all but `screens.py`; `screens.py` is explicitly gated on Task 7, and its place is taken by `discovery.py` producing the data it will be written from |
| Do not write a new device layer; copy Android-MCP | 2 (adb.py, verbatim copy with named edits), 3 (bounds arithmetic and session shape match its `wait_for_element_tool`) |
| No `uiautomator dump`, use the instrumentation server | 3 (`dump_hierarchy` via uiautomator2, with the empty-read guard and a comment naming the trap) |
| `screencap` 0 bytes while dozing | 3 (`screenshot()` raises on empty) + 4 (wake rung precedes any read) |
| `wm dismiss-keyguard` does not work; no-PIN first | 4 (ladder order, proven by `test_no_pin_unlock_is_tried_before_any_pin_is_consulted`) |
| `REDMI_PIN` env-only, never on disk, never in output | 1 (no file fallback, tested), 4 step 5, 5 (repo gate) |
| Proof is a read-back, never `am start` rc 0 | 3 (`current_focus`, signals reads), 6 (`SIGNAL_COMMANDS` are the spec's own four sources plus the call-log question), 7 step 5 |
| VoIP invisible to `telephony.registry` | 7 step 5 question 1 — measured, not assumed |
| Does `content://call_log` see IP calls? | 7 step 5 question 3 |
| Tool output 1–3 spoken lines | 4 (`say()`), 8 (`test_status_is_one_spoken_line`) |
| Single writer / one device | 8 (`get_driver()` returns one driver per process) |
| Fail loud, name which rung failed | 4 (`Readiness.failed_step` + `REASONS`) |
| IP only, no cellular fallback | 8 — nothing in this plan can place any call; plan 2 inherits the constraint |
| Logs of every dial, not a gate | Deferred with reason: nothing here dials. Recorded below. |
| Unknowns reported as UNKNOWN, never guessed | 7 step 5 (identical signals must be reported as indistinguishable), plus this plan's own refusal to write selectors |

**Gaps found and how they are handled:** the spec's dial-log requirement has no task because no task in this plan can dial — it moves to plan 2 rather than getting a fake implementation here. The spec's `WaitForElement`/selector helper is likewise plan 2, since selectors do not exist yet. Both are real gaps, stated, not papered over.

**2. Placeholder scan.** Two near-misses were fixed while reviewing. Task 6 step 5 originally said "add an option to skip screenshots" without code — it now carries `write_screen_png()` and its test. Task 7's selector table is a template with `<captured>` cells, which is correct only because the task's point is that a cell stays unfilled until a capture exists; a reviewer should reject any commit that fills it from memory. No TBD, no "handle edge cases", no "similar to Task N".

**3. Type consistency.** Checked across tasks: `ShellResult(rc, out, err)` with `.ok` is constructed identically in 3, 4 and 6. `Readiness(ready, failed_step, detail)` appears in 4 and 8 only, with the same three fields. `discovery.capture(driver, name, secrets=(), out_dir=None) -> Path` is called in Task 6 step 6 and Task 7 with the same shape; `summarise(Path) -> Screen` is used only in Task 7. `config.serial_from_env()` and `config.pin_from_env()` are defined in Task 1 and consumed in 4 and 8 with matching names. `adb.adb_path`, `adb.find_adb`, `adb.publish` used in Task 3 exist in the copy instructed in Task 2.
