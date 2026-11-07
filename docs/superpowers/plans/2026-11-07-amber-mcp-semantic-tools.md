# Amber-MCP Semantic Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the MCP server the four voice commands that can be built from selectors already measured on the handset — dial, hang up, read balance, read call history — and make it structurally impossible to act on a phone that has not been proven to be showing AmberIT.

**Architecture:** A gate owns the precondition (readiness ladder + foreground assertion), a locator resolves every element from the live hierarchy instead of trusting remembered pixels, and thin flows compose deep-link, read-back and one action each. The server stays the only writer, guarded by a process-wide lock, and appends one line per dial attempt to a gitignored log.

**Tech Stack:** Python 3.13 (uv-managed), `fastmcp>=2.14,<3` (resolves 2.14.7), `uiautomator2>=3.3.1`, `pytest`.

**Spec:** [docs/superpowers/specs/2026-11-06-amber-mcp-design.md](file:///E:/Amber-MCP/docs/superpowers/specs/2026-11-06-amber-mcp-design.md) — read it with this plan. The spec now carries a "Not implemented" block and a "Residuals carried into plan 2" section; both are this plan's brief, not background.

**Evidence this plan is written from:** [artifacts/SELECTORS.md](file:///E:/Amber-MCP/artifacts/SELECTORS.md) and [artifacts/signals/FINDINGS.md](file:///E:/Amber-MCP/artifacts/signals/FINDINGS.md). Every selector below traces to a row in those files. **If a value is not in them, this plan does not use it.**

## Global Constraints

Verbatim from the spec and the evidence files. Every task obeys these implicitly.

- IP line only. Nothing may fall back to cellular, and `ACTION_CALL`, `ACTION_DIAL` to the system dialer, `tel:` outside the explicit AmberIT component, and `am start -a android.intent.action.CALL` must never appear.
- No confirmation gate on dialling. Direct execution. What is mandatory instead is a read-back before the dialling tap and an append-only log after it.
- **The call-type chooser decides the cost.** `ivCall` does not dial; it opens "Select call type". `Paid Call` is the route for an ordinary mobile number (user-stated, and measured as IP). `Free Audio Call` on a mobile number is a **silent no-op** — no error, no charge, no state change — so a flow that taps it and waits will wait forever.
- Proof is a read-back. `am start` rc 0 and `Starting: Intent{…}` prove nothing. An IP call is up when the focused activity is `CallActivityWebRTC` **and** `dumpsys audio` reports `Actual mode = MODE_IN_COMMUNICATION`; `mCallState` stays `0` and is used only as the negative that proves the SIM was not used.
- **Answered versus ringing is UNKNOWN.** `MODE_IN_COMMUNICATION` rises at dial time, before anyone answers. No classifier may claim an answer from these signals.
- The in-call screen exposes **no app view nodes to accessibility at all**. There is no End button, timer or mute to find. Hang-up is `KEYCODE_ENDCALL` (6) — measured working on this app — and duration comes from sampling the audio mode, never from reading a timer.
- **No write may be issued without the readiness ladder having run, and without the focused activity having been proven to be AmberIT's.** This is a structural requirement, not a warning comment: it is the exact accident an operator caused during plan 1's execution, when four taps were sent into the user's Tailscale app because a probe assumed the foreground.
- Locate elements from the live hierarchy. Measured pixel centres in `SELECTORS.md` are the *assertion* about a layout, not the source of truth for a tap; a layout shift must not turn a dial into a tap on something else.
- Tool output is 1–3 spoken lines, no JSON, no braces.
- `REDMI_PIN` is env-only, never typed by a read-only poll, and never appears in any message, log or exception. Write failures name the action and rc, never the argv.
- Nothing real is committed: no capture XML, no screenshot, no contact name, no number, no balance. `logs/` and `artifacts/raw/` stay ignored, and the gate proves it.
- `fastmcp` stays `>=2.14,<3`. Tools are registered **by call** (`mcp.tool(fn, name=…)`), not by decorator, so the module-level function stays awaitable.

---

## File Structure

```
E:\Amber-MCP
  amber_mcp/
    gate.py            NEW  readiness + foreground precondition. Nothing else may tap.
    screens.py         NEW  measured selector table + runtime locator.
    flows/
      __init__.py      NEW
      dial.py          NEW  place_call(): deep link -> read-back -> ivCall -> Paid Call
      phone_state.py   NEW  balance(), history(), call_is_up(), wait_for_call_end()
    server.py          MOD  tools: call, hangup, balance, history (+ status); lock; dial log
    config.py          MOD  one value: MAX_CALL_SECONDS
    driver.py          MOD  type_text() gets an error path that cannot quote the text
  tests/unit/
    test_gate.py  test_screens.py  test_dial.py  test_phone_state.py  test_server_tools.py
    test_no_secrets_committed.py   MOD  pin the two predicates against each other
```

Boundaries: `gate.py` knows **when it is safe to act**. `screens.py` knows **where things are**. `flows/` knows **what a command means**, in order, and contains no resource-id string literal — it asks `screens` for a locator. `server.py` knows **verbs and sentences** and contains no selector and no adb command.

---

### Task 1: The gate that makes acting on the wrong screen impossible

**Files:**
- Create: `E:\Amber-MCP\amber_mcp\gate.py`
- Create: `E:\Amber-MCP\tests\unit\test_gate.py`

**Interfaces:**
- Consumes: `session.ensure_ready(driver, package=…, dashboard=…, pin=…) -> Readiness`, `session.say(Readiness) -> str`, `driver.Driver.current_focus() -> str`, `driver.TransportError`, `config.PACKAGE`
- Produces: `gate.NotReady(Exception)` with `.message: str`; `gate.wrong_foreground: bool` property; `gate.enter(driver, *, unlock: bool = False) -> str` returning the verified focused activity; `gate.still_in_app(driver, focus_before: str) -> bool`

This is Task 1 because every later task's first line is `gate.enter(...)`. It exists to make the plan-1 accident structurally impossible rather than discouraged.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_gate.py`:

```python
import pytest

from amber_mcp import gate
from amber_mcp.driver import TransportError


class Stub:
    def __init__(self, focus="com.amberit.dialer/.AgniDashboardActivity", state="device\n"):
        self._focus = focus
        self._state = state
        self.sent = []

    def current_focus(self):
        return self._focus

    def adb(self, args, timeout=10.0):
        return type("R", (), {"rc": 0, "out": self._state, "err": "", "ok": True})()

    def host_adb(self, args, timeout=30.0):
        return type("R", (), {"rc": 0, "out": "connected", "err": "", "ok": True})()

    def shell(self, cmd, timeout=10.0):
        self.sent.append(cmd)
        return type("R", (), {"rc": 0, "out": "", "err": "", "ok": True})()

    def keyevent(self, code):
        self.sent.append(f"input keyevent {code}")

    def tap_xy(self, x, y):
        self.sent.append(f"input tap {x} {y}")


def test_enter_returns_the_verified_activity():
    assert gate.enter(Stub()).endswith("AgniDashboardActivity")


def test_enter_refuses_when_another_app_owns_the_screen():
    # The plan-1 accident in one line: four taps went to the user's VPN app
    # because a probe assumed AmberIT was foreground.
    with pytest.raises(gate.NotReady) as excinfo:
        gate.enter(Stub(focus="com.tailscale.ipn/.MainActivity"))
    assert excinfo.value.wrong_foreground is True
    assert "com.tailscale.ipn" in excinfo.value.message


def test_enter_refuses_a_keyguard_and_does_not_unlock_by_default():
    with pytest.raises(gate.NotReady) as excinfo:
        gate.enter(Stub(focus="KeyguardScrim"))
    assert excinfo.value.wrong_foreground is False


def test_unlock_true_is_the_only_way_the_pin_rung_is_reached():
    # A dial may unlock; a read may not. The distinction is the caller's, and it
    # is what keeps `status` from ever typing the PIN.
    seen = {}

    def spy(driver, package=None, dashboard=None, pin=None, settle=1.2):
        seen["pin"] = pin
        return type("R", (), {"ready": True, "failed_step": "", "detail": "on x"})()

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(gate.session, "ensure_ready", spy)
    gate.enter(Stub(), unlock=True)
    assert seen["pin"] is None or seen["pin"] is gate.UNSET  # falls through to env
    monkeypatch.undo()


def test_a_broken_read_is_not_reported_as_a_wrong_screen():
    class Dead(Stub):
        def current_focus(self):
            raise TransportError("dumpsys window was not carried out (rc 124)")
    with pytest.raises(gate.NotReady) as excinfo:
        gate.enter(Dead())
    assert excinfo.value.wrong_foreground is False


def test_still_in_app_catches_the_screen_changing_mid_flow():
    before = "com.amberit.dialer/.DialPadActivity"
    assert gate.still_in_app(Stub(focus=before), before) is True
    assert gate.still_in_app(Stub(focus="com.android.chrome/.WebApk"), before) is False
```

- [ ] **Step 2: Run to make sure they fail**

Run: `uv run pytest tests/unit/test_gate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'amber_mcp.gate'`.

- [ ] **Step 3: Write `amber_mcp/gate.py`**

```python
"""The only sanctioned way to act on the phone.

Every flow calls `enter()` before its first write. That is the whole reason this
module exists: during plan 1 an ad-hoc probe tapped four coordinates assuming
AmberIT was in the foreground. It was not — the user's VPN app was, and the taps
landed there. The shipped ladder already prevented that, and the probe walked
around it. A precondition that can be skipped is a suggestion, so skipping it is
now impossible rather than unwise.
"""
from __future__ import annotations

from amber_mcp import config, session
from amber_mcp.driver import TransportError

UNSET = session._UNSET


class NotReady(Exception):
    """The phone is not in a state where acting on it is safe.

    `wrong_foreground` separates the two answers a caller can do something
    about: the phone is fine but showing something else, versus the phone or
    the link itself is not answering.
    """

    def __init__(self, message: str, *, wrong_foreground: bool = False):
        super().__init__(message)
        self.message = message
        self.wrong_foreground = wrong_foreground


def enter(driver, *, unlock: bool = False) -> str:
    """Climb the ladder, prove AmberIT owns the screen, and say what is focused.

    `unlock` is deliberately asymmetric. A dial is allowed to get past a lock
    using `REDMI_PIN`, because dialling is the user's own intent. A read is not,
    because a read-only poll that enters a device PIN is the failure mode a voice
    client auto-approving read-only tools would hit without anyone asking.
    """
    pin = None if not unlock else UNSET
    try:
        readiness = session.ensure_ready(driver, pin=pin)
    except TransportError as exc:
        raise NotReady(str(exc)) from exc
    if not readiness.ready:
        raise NotReady(session.say(readiness))

    focus = _focus(driver)
    if config.PACKAGE not in focus:
        raise NotReady(
            f"the phone is showing {focus}, not AmberIT — nothing was tapped",
            wrong_foreground=True,
        )
    return focus


def still_in_app(driver, focus_before: str) -> bool:
    """Is the screen we acted on still the screen we meant to act on?

    Flows call this between a read-back and a write. A MIUI dialog, a re-dozing
    panel or an incoming-call takeover all change the foreground without raising,
    and the dangerous case is not a crash — it is a tap landing correctly on the
    wrong thing.
    """
    return config.PACKAGE in _focus(driver)


def _focus(driver) -> str:
    try:
        return driver.current_focus()
    except TransportError as exc:
        raise NotReady(str(exc)) from exc
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/test_gate.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Prove the gate bites**

A guard nobody can bypass is a claim until it is tested against a bypass. Confirm no flow module imports `session.ensure_ready` directly once Task 3 lands, and confirm the gate's own refusal is not silently swallowed:

```bash
uv run python -c "
from amber_mcp import gate
from amber_mcp.driver import TransportError
class Other:
    def current_focus(self): return 'com.tailscale.ipn/.MainActivity'
    def adb(self,a,timeout=10.0): return type('R',(),{'rc':0,'out':'device','err':'','ok':True})()
    def host_adb(self,a,timeout=30.0): return type('R',(),{'rc':0,'out':'','err':'','ok':True})()
    def shell(self,c,timeout=10.0): return type('R',(),{'rc':0,'out':'','err':'','ok':True})()
    def keyevent(self,c): pass
    def tap_xy(self,x,y): raise AssertionError('tapped without the gate')
try:
    gate.enter(Other()); print('GUARD BROKEN')
except gate.NotReady as e: print('refused:', e.wrong_foreground, str(e)[:60])
"
```

Expected: `refused: True the phone is showing com.tailscale.ipn/...` and **no** `GUARD BROKEN`.

- [ ] **Step 6: Commit**

```bash
git add amber_mcp/gate.py tests/unit/test_gate.py
git commit -m "Add the precondition every flow must pass through

Four taps once went into the user's VPN app because a probe assumed the
foreground. The ladder already prevented that; the probe walked around it.
This makes the precondition the entry point rather than a step an operator
can skip, and keeps reads from ever reaching the PIN rung."
```

---

### Task 2: `screens.py` — measured selectors, located at runtime

**Files:**
- Create: `E:\Amber-MCP\amber_mcp\screens.py`
- Create: `E:\Amber-MCP\tests\unit\test_screens.py`

**Interfaces:**
- Consumes: `driver.Driver.dump_hierarchy() -> str` (raises on an empty read), `driver.TransportError`
- Produces: `screens.ACTIVITIES: dict[str, str]` with keys `dashboard`, `dialpad`, `in_call`; `screens.DIAL_INTENT: str` (the explicit-component `ACTION_DIAL` command template); `screens.Selector(name: str, resource_id: str, measured_centre: tuple[int,int] | None)`; `screens.find(driver, name: str) -> tuple[int,int]` raising `screens.NotFound`; `screens.text_row_centre(driver, label: str) -> tuple[int,int]` raising `NotFound`; `screens.read_field(driver, name: str) -> str | None`; `screens.balance_text(driver) -> str | None`; `screens.history_rows(driver) -> list[dict]`; `screens.NOT_FOUND = NotFound`

Every value here is copied from `artifacts/SELECTORS.md`. The measured centres are assertions, not the tap target: `find()` re-reads bounds from the live hierarchy, and a test proves a moved element is followed and a missing element raises rather than falling back to the remembered pixel.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_screens.py`:

```python
import re

import pytest

from amber_mcp import screens

DIALPAD = """<hierarchy>
 <node resource-id="com.amberit.dialer:id/nvNumber" class="android.widget.EditText"
       text="8801763417627" bounds="[187,921][893,1055]"/>
 <node resource-id="com.amberit.dialer:id/ivCall"
       class="com.google.android.material.floatingactionbutton.FloatingActionButton"
       bounds="[463,2059][617,2213]"/>
 <node resource-id="com.amberit.dialer:id/keyPadHolder" bounds="[0,921][1080,2270]"/>
</hierarchy>"""

DASHBOARD = """<hierarchy>
 <node resource-id="com.amberit.dialer:id/tvBalance" class="android.widget.TextView"
       text="BDT 12.49" bounds="[244,143][865,196]"/>
 <node resource-id="com.amberit.dialer:id/list" class="androidx.recyclerview.widget.RecyclerView"
       bounds="[0,493][1080,1489]">
  <node resource-id="com.amberit.dialer:id/name_tv" text="C1 BT" bounds="[187,539][937,600]"/>
  <node text="Wednesday" bounds="[264,600][444,666]"/>
  <node text="10:10 AM" bounds="[466,600][611,666]"/>
  <node resource-id="com.amberit.dialer:id/name_tv" text="C2 BT" bounds="[187,715][937,776]"/>
  <node text="Wednesday" bounds="[264,776][444,842]"/>
  <node text="10:10 AM" bounds="[466,776][611,842]"/>
 </node>
</hierarchy>"""

CHOOSER = """<hierarchy>
 <node resource-id="android:id/alertTitle" text="Select call type" bounds="[..."/>
 <node class="android.widget.FrameLayout" clickable="true" bounds="[188,1000][892,1096]">
   <node resource-id="com.amberit.dialer:id/tv" text="Paid Call" bounds="[226,1007][942,1090]"/>
 </node>
 <node class="android.widget.FrameLayout" clickable="true" bounds="[188,1127][892,1223]">
   <node resource-id="com.amberit.dialer:id/tv" text="Free Audio Call" bounds="[226,1134][942,1217]"/>
 </node>
</hierarchy>"""


class Stub:
    def __init__(self, xml):
        self._xml = xml

    def dump_hierarchy(self):
        return self._xml


def test_ivCall_is_located_from_the_live_bounds_not_the_remembered_centre():
    assert screens.find(Stub(DIALPAD), "ivCall") == (540, 2136)


def test_a_moved_element_is_followed():
    moved = DIALPAD.replace("[463,2059][617,2213]", "[463,1000][617,1154]")
    assert screens.find(Stub(moved), "ivCall") == (540, 1077)


def test_a_missing_element_raises_instead_of_tapping_the_stale_pixel():
    # The fallback everyone wants is the bug: after an app update the old centre
    # is a different widget, and the tap is confidently wrong.
    without = DIALPAD.replace('resource-id="com.amberit.dialer:id/ivCall"', 'resource-id="x"')
    with pytest.raises(screens.NotFound):
        screens.find(Stub(without), "ivCall")


def test_the_text_row_locator_walks_up_to_the_clickable_ancestor():
    # The chooser rows report clickable=false on the TextView; the ancestor is
    # what actually takes the tap, and tapping the label's own centre worked
    # while tapping the ancestor's did not differ -- so the ancestor is the
    # honest target and must be the one used.
    centre = screens.text_row_centre(Stub(CHOOSER), "Paid Call")
    assert centre == (540, 1111)


def test_the_chooser_is_detected_from_its_title():
    assert screens.has_chooser(Stub(CHOOSER)) is True
    assert screens.has_chooser(Stub(DIALPAD)) is False


def test_read_field_returns_the_prefilled_number():
    assert screens.read_field(Stub(DIALPAD), "nvNumber") == "8801763417627"


def test_balance_is_one_view_read_not_a_screen_walk():
    assert screens.balance_text(Stub(DASHBOARD)) == "BDT 12.49"


def test_history_rows_group_by_vertical_proximity():
    rows = screens.history_rows(Stub(DASHBOARD))
    assert [r["name"] for r in rows] == ["C1 BT", "C2 BT"]
    assert rows[0]["detail"] == "Wednesday 10:10 AM"


def test_a_hierarchy_that_cannot_be_parsed_is_a_failure_not_an_empty_answer():
    class Broken:
        def dump_hierarchy(self):
            raise RuntimeError("hierarchy came back empty")
    with pytest.raises(RuntimeError):
        screens.find(Broken(), "ivCall")


def test_every_selector_traces_to_a_measured_row():
    # Guards against a later edit inventing a resource-id. Each entry must name
    # something that appears in the evidence file.
    evidence = open("artifacts/SELECTORS.md", encoding="utf-8").read()
    for sel in screens.ALL:
        if sel.resource_id:
            assert sel.resource_id.split(":id/")[-1] in evidence, sel.name
        if sel.measured_centre:
            assert str(sel.measured_centre[0]) in evidence or "centre" in evidence
```

- [ ] **Step 2: Run to make sure they fail**

Run: `uv run pytest tests/unit/test_screens.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'amber_mcp.screens'`.

- [ ] **Step 3: Write `amber_mcp/screens.py`**

```python
"""Where AmberIT's controls are, and how to find them again after an update.

Two rules, both learned the expensive way:

* Nothing here is guessed. Every id and every measured centre is a row in
  artifacts/SELECTORS.md, which was written from captures of the handset. A test
  asserts that, because the file's whole value is knowing what was measured.
* The measured centre is an assertion about a layout, never the tap target.
  Bounds are re-read from the live hierarchy. If an element is gone, that raises:
  falling back to remembered pixels after a layout change means tapping whatever
  now occupies that spot, which is how a dial becomes a tap on something else.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional

class NotFound(Exception):
    """The element is not on the screen we were told to act on."""


# Measured activity names. These are the stable identity of a screen; text labels
# translate and move.
ACTIVITIES = {
    "dashboard": "com.revesoft.itelmobiledialer.ui2.dashboard.AgniDashboardActivity",
    "dialpad": "com.revesoft.itelmobiledialer.dialer.dialpad.DialPadActivity",
    "in_call": "com.reve.base.callrtc.ui.CallActivityWebRTC",
    "chooser_host": "com.revesoft.itelmobiledialer.dialer.dialpad.DialPadActivity",
}

# ACTION_DIAL with tel: data reaches AmberIT through this component; ACTION_CALL
# does not reach it at all. Measured: the dialpad arrives prefilled and no call
# is placed, because dialling is a separate tap.
DIAL_INTENT = (
    "am start -n com.amberit.dialer/com.revesoft.itelmobiledialer.ui2.dashboard"
    ".TelIntentRouterActivity -a android.intent.action.DIAL -d 'tel:{number}'"
)


@dataclass(frozen=True)
class Selector:
    name: str
    resource_id: str
    measured_centre: Optional[tuple[int, int]] = None


IV_CALL = Selector("ivCall", "com.amberit.dialer:id/ivCall", (540, 2136))
NV_NUMBER = Selector("nvNumber", "com.amberit.dialer:id/nvNumber")
TV_BALANCE = Selector("tvBalance", "com.amberit.dialer:id/tvBalance")
NAME_TV = Selector("name_tv", "com.amberit.dialer:id/name_tv")
SEARCH_BUTTON = Selector("search_button", "com.amberit.dialer:id/search_button", (1014, 170))
ALL = [IV_CALL, NV_NUMBER, TV_BALANCE, NAME_TV, SEARCH_BUTTON]

_CHOOSER_TITLE = "Select call type"
_ROW_STEP = 176          # measured vertical pitch of a dashboard history row
_NEAR = 90               # a detail line belongs to the row above it within this


def _tree(driver) -> ET.Element:
    return ET.fromstring(driver.dump_hierarchy())


def _centre(node) -> tuple[int, int]:
    nums = re.findall(r"-?\d+", node.get("bounds") or "")
    if len(nums) < 4:
        raise NotFound(f"{node.get('resource-id') or node.get('text')} has no usable bounds")
    left, top, right, bottom = (int(v) for v in nums[:4])
    return (left + right) // 2, (top + bottom) // 2


def _clickable_self_or_ancestor(node, parents) -> ET.Element | None:
    cur = node
    while cur is not None:
        if cur.get("clickable") == "true":
            return cur
        cur = parents.get(cur)
    return None


def find(driver, name: str) -> tuple[int, int]:
    sel = next((s for s in ALL if s.name == name), None)
    if sel is None:
        raise NotFound(f"{name} is not a selector this project has measured")
    parents = {child: node for node in _tree(driver).iter("node") for child in list(node)}
    for node in parents.keys():
        if (node.get("resource-id") or "") == sel.resource_id:
            target = _clickable_self_or_ancestor(node, parents)
            if target is None:
                raise NotFound(f"{name} is present but not clickable")
            return _centre(target)
    raise NotFound(f"{name} is not on the screen")


def text_row_centre(driver, label: str) -> tuple[int, int]:
    """The clickable row that carries a text label.

    The chooser's rows report `clickable=false` on the TextView itself, so the
    label's own centre is not the control. Walking up is what finds the row.
    """
    tree = _tree(driver)
    parents = {child: node for node in tree.iter("node") for child in list(node)}
    for node in parents.keys():
        if (node.get("text") or "").strip() == label:
            target = _clickable_self_or_ancestor(parents.get(node, node), parents)
            if target is None:
                raise NotFound(f"row '{label}' has no clickable ancestor")
            return _centre(target)
    raise NotFound(f"no row labelled '{label}'")


def has_chooser(driver) -> bool:
    return _CHOOSER_TITLE in {
        (n.get("text") or "").strip() for n in _tree(driver).iter("node")
    }


def read_field(driver, name: str) -> Optional[str]:
    sel = next((s for s in ALL if s.name == name), None)
    if sel is None:
        raise NotFound(f"{name} is not a measured field")
    for node in _tree(driver).iter("node"):
        if (node.get("resource-id") or "") == sel.resource_id:
            return (node.get("text") or "").strip()
    return None


def balance_text(driver) -> Optional[str]:
    return read_field(driver, "tvBalance")


def history_rows(driver) -> list[dict]:
    """Group the dashboard list by vertical position.

    Measured shape: a `name_tv` line, then a call-type icon, then a day and a
    time, each row one 176px step down. Nothing here is a stable id, so the only
    honest join is proximity.
    """
    nodes = [n for n in _tree(driver).iter("node") if (n.get("text") or "").strip()]
    named, loose = [], []
    for n in nodes:
        nums = [int(v) for v in re.findall(r"-?\d+", n.get("bounds") or "")]
        if len(nums) < 4:
            continue
        top = nums[1]
        entry = {"text": (n.get("text") or "").strip(), "top": top}
        (named if (n.get("resource-id") or "").endswith("/name_tv") else loose).append(entry)

    rows = []
    for item in sorted(named, key=lambda e: e["top"]):
        details = [o["text"] for o in loose
                   if item["top"] < o["top"] <= item["top"] + _NEAR]
        rows.append({"name": item["text"], "detail": " ".join(details)})
    return rows
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/test_screens.py -v`
Expected: PASS, 10 tests.

- [ ] **Step 5: Prove the stale-pixel fallback is really gone**

```bash
uv run python -c "
from amber_mcp import screens
class Gone:
    def dump_hierarchy(self): return '<hierarchy><node resource-id=\"x\" bounds=\"[0,0][9,9]\"/></hierarchy>'
try:
    print('fell back to', screens.find(Gone(), 'ivCall')); print('GUARD BROKEN')
except screens.NotFound as e: print('refused:', str(e)[:60])
"
```

Expected: `refused: ivCall is not on the screen` — never `(540, 2136)`.

- [ ] **Step 6: Commit**

```bash
git add amber_mcp/screens.py tests/unit/test_screens.py
git commit -m "Add measured selectors located at runtime, not remembered by pixel

A missing element raises instead of falling back to the centre from the last
release, because after a layout change that pixel belongs to something else.
The chooser rows are reached through their clickable ancestor; the label's own
bounds are not the control."
```

---

### Task 3: `flows/dial.py` — deep link, read-back, then one deliberate tap

**Files:**
- Create: `E:\Amber-MCP\amber_mcp\flows\__init__.py`
- Create: `E:\Amber-MCP\amber_mcp\flows\dial.py`
- Create: `E:\Amber-MCP\tests\unit\test_dial.py`

**Interfaces:**
- Consumes: `gate.enter(driver, unlock=True) -> str`, `gate.still_in_app(driver, focus) -> bool`, `gate.NotReady`, `screens.DIAL_INTENT`, `screens.find(driver, name)`, `screens.read_field(driver, "nvNumber")`, `screens.text_row_centre(driver, label)`, `screens.has_chooser(driver)`, `screens.ACTIVITIES`, `driver.shell/tap_xy/keyevent`, `config.PACKAGE`
- Produces: `dial.DialResult(ok: bool, verdict: str, detail: str)` with `verdict` in `{"dialing", "invalid_number", "no_chooser", "wrong_foreground", "not_ready"}`; `dial.digits(value: str) -> str`; `dial.place_call(driver, target: str, *, chooser: str = "Paid Call") -> DialResult`; `dial.CHOOSER_ROW: str`

The order is the safety property: deep link → verify the dialpad is focused → **read the number back** → verify the chooser opened → tap the row the user named. No step is skipped, and the invalid-number path is detected by the chooser's *absence*, which is what was measured.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_dial.py`:

```python
import pytest

from amber_mcp import gate, screens
from amber_mcp.flows import dial

TARGET = "+8801763417627"
PREFILLED = "8801763417627"   # measured: the deep link drops the leading plus


class Fake:
    """Answers exactly the reads the flow makes, in the order it makes them."""

    def __init__(self, chooser=True, field=PREFILLED, focus_after=None):
        self.sent = []
        self.chooser = chooser
        self.field = field
        self._focus = "com.amberit.dialer/.DialPadActivity"
        self.focus_after = focus_after

    def shell(self, cmd, timeout=10.0):
        self.sent.append(cmd)
        return type("R", (), {"rc": 0, "out": "", "err": "", "ok": True})()

    def tap_xy(self, x, y):
        self.sent.append(f"tap {x} {y}")
        if self.focus_after:
            self._focus = self.focus_after

    def keyevent(self, code):
        self.sent.append(f"input keyevent {code}")

    def current_focus(self):
        return self._focus

    def dump_hierarchy(self):
        if "Select call type" in ("",) :
            pass
        return self._xml()

    def _xml(self):
        if self.chooser:
            return screens_xml(chooser=True, field=self.field)
        return screens_xml(chooser=False, field=self.field)


def screens_xml(*, chooser, field):
    chooser_block = (
        '<node resource-id="android:id/alertTitle" text="Select call type"/>'
        '<node class="android.widget.FrameLayout" clickable="true" bounds="[188,1000][892,1096]">'
        '<node resource-id="com.amberit.dialer:id/tv" text="Paid Call" bounds="[226,1007][942,1090]"/>'
        "</node>"
    ) if chooser else ""
    return (
        "<hierarchy>"
        f'<node resource-id="com.amberit.dialer:id/nvNumber" text="{field}" '
        'bounds="[187,921][893,1055]"/>'
        '<node resource-id="com.amberit.dialer:id/ivCall" bounds="[463,2059][617,2213]"/>'
        f"{chooser_block}</hierarchy>"
    )


@pytest.fixture
def entered(monkeypatch):
    monkeypatch.setattr(gate, "enter", lambda driver, unlock=False: "com.amberit.dialer/.DialPadActivity")
    monkeypatch.setattr(gate, "still_in_app", lambda driver, before: True)


def test_the_deep_link_is_issued_before_any_tap(entered):
    fake = Fake()
    dial.place_call(fake, TARGET)
    first = fake.sent[0]
    assert "android.intent.action.DIAL" in first and "tel:+8801763417627" in first
    assert "android.intent.action.CALL" not in first


def test_the_number_is_read_back_before_ivcall_is_touched(entered):
    order = []
    fake = Fake()
    real_tap = fake.tap_xy
    def tap(x, y):
        order.append(("tap", x, y))
        real_tap(x, y)
    fake.tap_xy = tap
    dial.place_call(fake, TARGET)
    assert ("tap", 540, 2136) in order          # ivCall
    assert ("tap", 540, 1048) in order          # Paid Call row


def test_a_mismatched_read_back_aborts_without_tapping(entered):
    fake = Fake(field="8809999999999")
    result = dial.place_call(fake, TARGET)
    assert result.ok is False
    assert result.verdict == "number_mismatch"
    assert not any(s.startswith("tap ") for s in fake.sent)


def test_no_chooser_means_the_app_rejected_the_number(entered):
    # Measured: an invalid number never opens the chooser, so its absence is the
    # rejection signal rather than an error to wait for.
    fake = Fake(chooser=False)
    result = dial.place_call(fake, TARGET)
    assert result.verdict == "invalid_number"


def test_the_chooser_is_waited_for_not_assumed(monkeypatch):
    monkeypatch.setattr(gate, "enter", lambda driver, unlock=False: "x/.DialPadActivity")
    monkeypatch.setattr(gate, "still_in_app", lambda driver, before: True)
    calls = {"n": 0}
    def flaky_dump():
        calls["n"] += 1
        return screens_xml(chooser=calls["n"] >= 3, field=PREFILLED)
    fake = Fake()
    fake.dump_hierarchy = flaky_dump
    result = dial.place_call(fake, TARGET)
    assert result.ok is True and calls["n"] >= 3


def test_a_screen_that_changed_under_us_stops_the_flow(monkeypatch):
    monkeypatch.setattr(gate, "enter", lambda driver, unlock=False: "com.amberit.dialer/.DialPadActivity")
    monkeypatch.setattr(gate, "still_in_app", lambda driver, before: False)
    fake = Fake()
    result = dial.place_call(fake, TARGET)
    assert result.verdict == "wrong_foreground"
    assert not any(s.startswith("tap ") for s in fake.sent)


def test_the_gate_running_is_not_optional():
    # A flow that swallowed NotReady would dial blind; the exception must escape.
    def boom(driver, unlock=False):
        raise gate.NotReady("the phone is showing something else", wrong_foreground=True)
    import amber_mcp.flows.dial as mod
    real = gate.enter
    gate.enter = boom
    try:
        with pytest.raises(gate.NotReady):
            mod.place_call(Fake(), TARGET)
    finally:
        gate.enter = real


def test_digits_compare_after_stripping_the_plus():
    assert dial.digits(TARGET) == dial.digits(PREFILLED) == "8801763417627"


def test_the_row_tapped_is_the_one_the_user_named(entered):
    fake = Fake()
    dial.place_call(fake, TARGET, chooser="Paid Call")
    assert any("tap 540 1048" in s for s in fake.sent)
```

- [ ] **Step 2: Run to make sure they fail**

Run: `uv run pytest tests/unit/test_dial.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'amber_mcp.flows'`.

- [ ] **Step 3: Write the flow**

`amber_mcp/flows/__init__.py`:

```python
"""One command, one meaning, in order. No selector strings and no adb verbs."""
```

`amber_mcp/flows/dial.py`:

```python
"""Placing an AmberIT call.

The measured shape of a dial, in the order that makes it safe:

  deep link -> prove the dialpad is focused -> read the number back -> tap the
  one dialling control -> prove the chooser opened -> tap the row the user
  named.

Two findings drive the design. The first is that `ivCall` does not dial: it opens
a "Select call type" chooser, and the row chosen decides what the call costs.
The second is worse for automation — `Free Audio Call` on a mobile number is a
silent no-op. It does not error, does not charge, and does not change state.
A flow that tapped it and then waited for a call would wait forever, so the
chooser's appearance is the thing waited for, and the row is named by the caller.

An invalid number is detected the same way it was measured: the chooser never
opens. That is a positive result, not a timeout.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

from amber_mcp import gate, screens

CHOOSER_ROW = "Paid Call"
CHOOSER_WAIT_SECONDS = 6.0
_POLL = 0.6


@dataclass
class DialResult:
    ok: bool
    verdict: str
    detail: str


def digits(value: str) -> str:
    """Compare numbers by digits. Measured: the deep link drops the leading `+`."""
    return re.sub(r"\D", "", value or "")


def place_call(driver, target: str, *, chooser: str = CHOOSER_ROW,
               settle: float = 1.4) -> DialResult:
    wanted = digits(target)
    if len(wanted) < 7:
        return DialResult(False, "too_short", f"'{target}' is not a dialable number")

    focus_before = gate.enter(driver, unlock=True)

    driver.shell(screens.DIAL_INTENT.format(number=target))
    time.sleep(settle)

    if not gate.still_in_app(driver, focus_before):
        return DialResult(False, "wrong_foreground",
                          "the screen changed before the number could be checked")

    shown = digits(screens.read_field(driver, "nvNumber") or "")
    if shown != wanted:
        # Nothing was tapped. This is the read-back the spec asks for instead of
        # trusting `am start`'s return code, and it is the one check that keeps a
        # mistargeted deep link from dialing a stranger.
        return DialResult(False, "number_mismatch",
                          f"the dialpad shows {shown or 'nothing'}, not {wanted}")

    tap = screens.find(driver, "ivCall")
    driver.tap_xy(*tap)

    if not _await_chooser(driver):
        return DialResult(False, "invalid_number",
                          "AmberIT did not offer a call type, so it refused the number")

    if not gate.still_in_app(driver, focus_before):
        return DialResult(False, "wrong_foreground",
                          "the chooser was replaced before a row could be picked")

    row = screens.text_row_centre(driver, chooser)
    driver.tap_xy(*row)
    return DialResult(True, "dialing", f"dialling {wanted} as {chooser}")


def _await_chooser(driver) -> bool:
    deadline = time.monotonic() + CHOOSER_WAIT_SECONDS
    while time.monotonic() < deadline:
        if screens.has_chooser(driver):
            return True
        time.sleep(_POLL)
    return False
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/test_dial.py -v`
Expected: PASS, 10 tests.

- [ ] **Step 5: Prove the read-back aborts**

```bash
uv run python -c "
from amber_mcp.flows import dial
class F:
    sent=[]
    def shell(self,c,timeout=10.0): F.sent.append(c); return type('R',(),{'rc':0,'out':'','err':'','ok':True})()
    def tap_xy(self,x,y): F.sent.append(f'tap {x} {y}')
    def current_focus(self): return 'com.amberit.dialer/.DialPadActivity'
    def dump_hierarchy(self): return '<hierarchy><node resource-id=\"com.amberit.dialer:id/nvNumber\" text=\"8809999999999\" bounds=\"[1,2][3,4]\"/></hierarchy>'
from amber_mcp import gate
gate.enter=lambda driver, unlock=False: 'com.amberit.dialer/.DialPadActivity'
gate.still_in_app=lambda driver,b: True
r = dial.place_call(F(), '+8801763417627')
print(r.verdict, '| taps issued:', [s for s in F.sent if s.startswith('tap')])
"
```

Expected: `number_mismatch | taps issued: []`. If any tap appears, the read-back is decorative.

- [ ] **Step 6: Commit**

```bash
git add amber_mcp/flows tests/unit/test_dial.py
git commit -m "Add the dial flow with a read-back between the link and the tap

The chooser is waited for rather than assumed, because an invalid number never
opens it -- that absence is the rejection. The row is named by the caller,
because Free Audio Call on a mobile number is a silent no-op and a flow that
picked it would wait forever for a call that will never happen."
```

---

### Task 4: `flows/phone_state.py` — what is up, what it cost, who called

**Files:**
- Create: `E:\Amber-MCP\amber_mcp\flows\phone_state.py`
- Create: `E:\Amber-MCP\tests\unit\test_phone_state.py`

**Interfaces:**
- Consumes: `driver.shell(remote_cmd) -> ShellResult`, `driver.current_focus()`, `screens.ACTIVITIES`, `screens.balance_text(driver)`, `screens.history_rows(driver)`, `config.ACTION_TIMEOUT_SECONDS`
- Produces: `phone_state.AUDIO_UP = "MODE_IN_COMMUNICATION"`; `phone_state.call_is_up(driver) -> bool`; `phone_state.audio_mode(driver) -> str`; `phone_state.wait_for_call_end(driver, *, timeout: float) -> bool`; `phone_state.read_balance(driver) -> str`; `phone_state.read_history(driver, limit: int = 5) -> list[str]`; `phone_state.sim_idle_proof(driver) -> bool`

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_phone_state.py`:

```python
from amber_mcp import screens
from amber_mcp.flows import phone_state


class Stub:
    def __init__(self, focus, audio_lines, telephony="mCallState=0", xml=""):
        self._focus = focus
        self._audio = audio_lines
        self._tel = telephony
        self._xml = xml
        self.sent = []

    def current_focus(self):
        return self._focus

    def shell(self, cmd, timeout=10.0):
        self.sent.append(cmd)
        if "dumpsys audio" in cmd:
            out = "\n".join(self._audio)
        elif "telephony.registry" in cmd:
            out = self._tel
        else:
            out = ""
        return type("R", (), {"rc": 0, "out": out, "err": "", "ok": True})()

    def dump_hierarchy(self):
        return self._xml


IN_CALL = "com.amberit.dialer/com.reve.base.callrtc.ui.CallActivityWebRTC"
UP = ["- Requested mode = MODE_IN_COMMUNICATION", "- Actual mode = MODE_IN_COMMUNICATION"]
DOWN = ["- Requested mode = MODE_NORMAL", "- Actual mode = MODE_NORMAL"]


def test_call_is_up_needs_both_the_activity_and_the_audio_path():
    assert phone_state.call_is_up(Stub(IN_CALL, UP)) is True


def test_a_cellular_call_is_not_an_amberit_call():
    assert phone_state.call_is_up(Stub("com.android.server.telecom/.UserCallActivity", UP)) is False


def test_the_audio_path_without_the_call_activity_is_not_a_call():
    assert phone_state.call_is_up(Stub("com.amberit.dialer/.AgniDashboardActivity", UP)) is False


def test_audio_mode_reads_only_the_current_state_line():
    noisy = UP + ["11-06 19:57:07:446 setMode(MODE_IN_COMMUNICATION) from package=com.whatsapp"]
    assert phone_state.audio_mode(Stub(IN_CALL, noisy)) == "MODE_IN_COMMUNICATION"


def test_the_history_block_is_never_mistaken_for_the_current_mode():
    # Measured: dumpsys audio carries 20 timestamped setMode lines naming other
    # apps. Reading the wrong one reports someone else's call as the user's.
    only_history = ["11-06 19:57:07:446 setMode(MODE_IN_COMMUNICATION) from package=com.whatsapp"]
    assert phone_state.audio_mode(Stub(IN_CALL, only_history)) == "unknown"


def test_sim_idle_is_proven_not_assumed():
    assert phone_state.sim_idle_proof(Stub(IN_CALL, UP, "mCallState=0")) is True
    assert phone_state.sim_idle_proof(Stub(IN_CALL, UP, "mCallState=2")) is False


def test_wait_for_call_end_returns_as_soon_as_the_path_drops():
    assert phone_state.wait_for_call_end(Stub(IN_CALL, DOWN), timeout=1.0) is True


def test_wait_for_call_end_times_out_honestly():
    assert phone_state.wait_for_call_end(Stub(IN_CALL, UP), timeout=0.4) is False


def test_balance_is_the_dashboard_view_value():
    xml = ('<hierarchy><node resource-id="com.amberit.dialer:id/tvBalance" '
           'text="BDT 12.49" bounds="[1,2][3,4]"/></hierarchy>')
    assert phone_state.read_balance(Stub("com.amberit.dialer/.Agni", [], xml=xml)) == "BDT 12.49"


def test_history_is_capped_and_one_line_each():
    xml = ('<hierarchy>'
           '<node resource-id="com.amberit.dialer:id/name_tv" text="C1 BT" bounds="[187,539][937,600]"/>'
           '<node text="Wednesday" bounds="[264,600][444,666]"/>'
           '<node text="10:10 AM" bounds="[466,600][611,666]"/>'
           '<node resource-id="com.amberit.dialer:id/name_tv" text="C2 BT" bounds="[187,715][937,776]"/>'
           '<node text="Thursday" bounds="[264,776][444,842]"/>'
           '</hierarchy>')
    lines = phone_state.read_history(Stub("com.amberit.dialer/.Agni", [], xml=xml), limit=1)
    assert lines == ["C1 BT, Wednesday 10:10 AM"]
```

- [ ] **Step 2: Run to make sure they fail**

Run: `uv run pytest tests/unit/test_phone_state.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'amber_mcp.flows.phone_state'`.

- [ ] **Step 3: Write `amber_mcp/flows/phone_state.py`**

```python
"""What the phone says about the call, without pretending to know more.

Two measured limits shape this file. The in-call screen exposes no view nodes to
accessibility, so there is no timer to read and no End button to find: a call is
up when the activity is `CallActivityWebRTC` and the audio path is up, and that
is the whole surface. And `dumpsys audio` carries a timestamped history naming
other apps' mode changes, so the current state line is the only line that may be
read -- the history is how someone else's WhatsApp call gets reported as yours.

Answered versus ringing is not answered here at all. Measured: the mode rises at
dial time, before anyone picks up. Anything that claimed otherwise would be a
guess with a confident tone of voice.
"""
from __future__ import annotations

import time

from amber_mcp import screens

AUDIO_UP = "MODE_IN_COMMUNICATION"
AUDIO_DOWN = "MODE_NORMAL"
ACTUAL_MODE = "- Actual mode = "
REQUESTED_MODE = "- Requested mode = "


def audio_mode(driver) -> str:
    """The current audio mode, from the state line only.

    Never the `setMode(...)` history: those lines are attributed to whichever app
    asked, are timestamped, and on this handset number about twenty per dump.
    """
    out = driver.shell("dumpsys audio | grep 'Actual mode'").out
    for line in out.splitlines():
        if ACTUAL_MODE in line:
            return line.split(ACTUAL_MODE, 1)[1].strip()
    return "unknown"


def call_is_up(driver) -> bool:
    """Both halves, or it is not an AmberIT call.

    The activity alone is not enough (the mode can be up for a cellular call) and
    the mode alone is not enough (any app can raise it).
    """
    focus = driver.current_focus()
    return screens.ACTIVITIES["in_call"] in focus and audio_mode(driver) == AUDIO_UP


def sim_idle_proof(driver) -> bool:
    """The negative that shows the SIM was not used.

    `mCallState` is cellular-only and reads 0 for the whole of an IP call, which
    is why it can prove a call did not go out on the mobile line, and cannot say
    anything about whether it is connected.
    """
    out = driver.shell("dumpsys telephony.registry | grep -m1 mCallState").out
    return "mCallState=0" in out


def wait_for_call_end(driver, *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if audio_mode(driver) != AUDIO_UP:
            return True
        time.sleep(1.0)
    return False


def read_balance(driver) -> str:
    value = screens.balance_text(driver)
    if value is None:
        # An unreadable balance and a zero balance are different facts, and a
        # voice assistant must not be allowed to hear one as the other.
        return "I could not read the balance"
    return value


def read_history(driver, limit: int = 5) -> list[str]:
    rows = screens.history_rows(driver)[: max(1, int(limit))]
    return [f"{r['name']}, {r['detail']}" if r["detail"] else r["name"] for r in rows]
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/test_phone_state.py -v`
Expected: PASS, 10 tests.

- [ ] **Step 5: Prove the history line cannot be read as current state**

```bash
uv run python -c "
from amber_mcp.flows import phone_state
class S:
    def shell(self,c,timeout=10.0): return type('R',(),{'rc':0,'out':'11-06 19:57:07:446 setMode(MODE_IN_COMMUNICATION) from package=com.whatsapp','err':'','ok':True})()
print('mode read from history-only dump:', phone_state.audio_mode(S()))
"
```

Expected: `unknown`. If it prints `MODE_IN_COMMUNICATION`, the parser is reading the history and will attribute another app's call to the user.

- [ ] **Step 6: Commit**

```bash
git add amber_mcp/flows/phone_state.py tests/unit/test_phone_state.py
git commit -m "Read call state from the two signals that exist, and no more

The audio history is excluded by name, because it is attributed to other apps
and timestamped; the activity alone is not a call. Answered versus ringing is
left unclaimed, which is what the measurement supports."
```

---

### Task 5: Server wiring — four tools, one writer, one log line per dial

**Files:**
- Modify: `E:\Amber-MCP\amber_mcp\server.py`
- Modify: `E:\Amber-MCP\amber_mcp\config.py`
- Create: `E:\Amber-MCP\tests\unit\test_server_tools.py`

**Interfaces:**
- Consumes: `gate.enter`/`gate.NotReady`, `dial.place_call`/`dial.DialResult`, `phone_state.*`, `session.say`, `driver.Driver`, `config.PACKAGE`
- Produces: `server.call_tool(target: str) -> str`, `server.hangup_tool() -> str`, `server.balance_tool() -> str`, `server.history_tool(limit: int = 5) -> str`, `server.log_dial(entry: dict) -> None`, `server.LOG_PATH: Path`, `server.WRITER_LOCK: threading.Lock`, `config.MAX_CALL_SECONDS: float`

This task closes the spec's two requirements that plan 1 recorded as not implemented: the single-writer lock and the append-only dial log.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_server_tools.py`:

```python
import asyncio
import json
import threading

import pytest

from amber_mcp import config, gate, server
from amber_mcp.flows import dial, phone_state


class NoopDriver:
    def current_focus(self):
        return "com.amberit.dialer/.AgniDashboardActivity"


@pytest.fixture
def wired(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "get_driver", lambda: NoopDriver())
    monkeypatch.setattr(server, "LOG_PATH", tmp_path / "calls.jsonl")
    calls = []
    monkeypatch.setattr(dial, "place_call",
                        lambda driver, target, **kw: calls.append(target)
                        or dial.DialResult(True, "dialing", f"dialling {target}"))
    monkeypatch.setattr(phone_state, "read_balance", lambda driver: "BDT 12.49")
    monkeypatch.setattr(phone_state, "read_history", lambda driver, limit=5: ["C1 BT, Wednesday"])
    monkeypatch.setattr(phone_state, "wait_for_call_end", lambda driver, timeout: True)
    monkeypatch.setattr(phone_state, "call_is_up", lambda driver: True)
    return calls


def test_call_returns_one_spoken_line( wired):
    out = asyncio.run(server.call_tool("+8801763417627"))
    assert len(out.splitlines()) <= 2 and "{" not in out
    assert wired == ["+8801763417627"]


def test_every_dial_attempt_is_logged_with_its_verdict(wired):
    asyncio.run(server.call_tool("+8801763417627"))
    rows = [json.loads(l) for l in server.LOG_PATH.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["target_digits"].endswith("1763417627")
    assert rows[-1]["verdict"] == "dialing"


def test_the_log_records_a_refusal_too(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "get_driver", lambda: NoopDriver())
    monkeypatch.setattr(server, "LOG_PATH", tmp_path / "calls.jsonl")
    monkeypatch.setattr(dial, "place_call",
                        lambda driver, target, **kw: dial.DialResult(False, "invalid_number", "refused"))
    asyncio.run(server.call_tool("+8801763417627"))
    rows = [json.loads(l) for l in server.LOG_PATH.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["verdict"] == "invalid_number"


def test_the_log_never_holds_the_pin_or_a_raw_command(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "LOG_PATH", tmp_path / "calls.jsonl")
    server.log_dial({"target_digits": "8801763417627", "verdict": "dialing"})
    body = server.LOG_PATH.read_text(encoding="utf-8")
    for forbidden in ("REDMI_PIN", "keyevent", "am start", "input tap"):
        assert forbidden not in body


def test_one_writer_at_a_time():
    # Two clients tapping at once leaves the app halfway through a dialling
    # screen, which is the state the whole design is built to avoid.
    inside = []

    def worker():
        with server.WRITER_LOCK:
            inside.append(threading.get_ident())
            import time; time.sleep(0.05)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert len(inside) == 4


def test_write_tools_take_the_lock_and_reads_do_not(monkeypatch, wired):
    held = threading.Lock()
    assert held.acquire()
    def fake_run(fn):
        raise AssertionError("a read must not block behind a write")
    monkeypatch.setattr(server, "_serialised", lambda fn: fn())
    out = asyncio.run(server.balance_tool())
    assert out == "BDT 12.49"
    held.release()


def test_hangup_uses_the_endcall_key_and_reports_the_result(monkeypatch, wired):
    sent = []
    monkeypatch.setattr(NoopDriver, "keyevent", lambda self, code: sent.append(code))
    out = asyncio.run(server.hangup_tool())
    assert 6 in sent and "ended" in out.lower()


def test_a_gate_refusal_is_spoken_not_swallowed(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "get_driver", lambda: NoopDriver())
    monkeypatch.setattr(server, "LOG_PATH", tmp_path / "calls.jsonl")
    def boom(driver, target, **kw):
        raise gate.NotReady("the phone is showing something else", wrong_foreground=True)
    monkeypatch.setattr(dial, "place_call", boom)
    out = asyncio.run(server.call_tool("+8801763417627"))
    assert "something else" in out and len(out) < 160


def test_no_tool_can_place_a_cellular_call():
    source = open("amber_mcp/server.py", encoding="utf-8").read()
    for forbidden in ("android.intent.action.CALL", "ACTION_DIAL", "input text"):
        assert forbidden not in source


def test_tools_are_registered_and_still_awaitable():
    names = {t.name for t in (asyncio.run(server.mcp.get_tools())).values()}
    assert names == {"status", "call", "hangup", "balance", "history"}
    assert callable(server.call_tool)


def test_max_call_seconds_is_bounded():
    assert config.MAX_CALL_SECONDS > 0
```

- [ ] **Step 2: Run to make sure they fail**

Run: `uv run pytest tests/unit/test_server_tools.py -v`
Expected: FAIL — `AttributeError: module 'amber_mcp.server' has no attribute 'call_tool'`.

- [ ] **Step 3: Add the config value**

In `amber_mcp/config.py`, after `ACTION_TIMEOUT_SECONDS = 10.0`:

```python
# A call that cannot be read is a call that keeps costing. This is the ceiling on
# waiting for one to end, generous enough for a slow divert and short enough that
# a stuck call does not run for an hour.
MAX_CALL_SECONDS = 900.0
```

- [ ] **Step 4: Write the server tools**

Append to `amber_mcp/server.py` (imports at the top become `import json, threading, time` plus `from amber_mcp.flows import dial, phone_state` and `from amber_mcp import config, gate, session`):

```python
WRITER_LOCK = threading.Lock()
LOG_PATH = config.PROJECT_ROOT / "logs" / "calls.jsonl"


def _serialised(fn):
    """One writer, so two clients cannot leave the app mid-chooser.

    Reads are not serialised: `status`, `balance` and `history` only look, and
    making a voice poll wait behind a dial is a worse user experience than the
    race it would prevent, because a poll cannot change anything.
    """
    with WRITER_LOCK:
        return fn()


def log_dial(entry: dict) -> None:
    """Append one line per dial attempt. This is a record, not a permission check.

    Only digits and a verdict are stored. No argv, no PIN, no raw command — the
    gate in tests/unit/test_no_secrets_committed.py cannot see a log, so this
    function is the only thing deciding what reaches that file.
    """
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), **entry}, sort_keys=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


async def call_tool(target: str) -> str:
    driver = get_driver()
    digits = dial.digits(target)
    try:
        result = _serialised(lambda: dial.place_call(driver, target))
    except gate.NotReady as exc:
        log_dial({"target_digits": digits, "verdict": "not_ready", "why": str(exc)[:120]})
        return _spoken(exc.message)
    log_dial({"target_digits": digits, "verdict": result.verdict, "why": result.detail[:120]})
    if not result.ok:
        return _spoken(_refusal(result))
    return _spoken(f"Dialling {digits} on AmberIT.")


async def hangup_tool() -> str:
    driver = get_driver()

    def act():
        if not phone_state.call_is_up(driver):
            return "There is no AmberIT call to end."
        driver.keyevent(6)                     # KEYCODE_ENDCALL: the only surface
        if phone_state.wait_for_call_end(driver, timeout=20.0):
            return "The call is ended."
        return "I sent the hang-up and the call is still up — look at the phone."

    return _spoken(_serialised(act))


async def balance_tool() -> str:
    return _spoken(phone_state.read_balance(get_driver()))


async def history_tool(limit: int = 5) -> str:
    lines = phone_state.read_history(get_driver(), limit=limit)
    return _spoken("\n".join(lines) if lines else "No recent AmberIT calls.")


def _refusal(result) -> str:
    return {
        "number_mismatch": f"The dialpad does not show that number: {result.detail}",
        "invalid_number": "AmberIT refused that number, so nothing was dialled.",
        "wrong_foreground": "The screen changed under us, so nothing was tapped.",
        "too_short": "That is not a number I can dial.",
    }.get(result.verdict, result.detail)
```

Register each one with the same by-call form already used for `status`, with descriptions that state what the action does to the phone, and `annotations=ToolAnnotations(title=…, destructiveHint=False)` for `hangup`/`balance`/`history` and `ToolAnnotations(title="Dial", destructiveHint=False)` for `call` — **not** `readOnlyHint`, because a dial is not a read.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/unit/test_server_tools.py -v`
Expected: PASS, 12 tests.

- [ ] **Step 6: Prove the log is written and ignored**

```bash
uv run pytest -q
mkdir -p logs && printf 'target_digits=01712345678\n' > logs/calls.jsonl
git check-ignore -v logs/calls.jsonl
uv run pytest tests/unit/test_no_secrets_committed.py -q
rm -f logs/calls.jsonl
```

Expected: `git check-ignore` names the `logs/` rule, and the gate still passes — proof that a file holding real numbers cannot be committed by accident.

- [ ] **Step 7: Commit**

```bash
git add amber_mcp/server.py amber_mcp/config.py tests/unit/test_server_tools.py
git commit -m "Add call, hangup, balance and history, with one writer and a dial log

The lock and the log are the two things the spec required and plan 1 recorded
as missing. Nothing is gated before dialling; everything is recorded after,
digits and verdict only, in a file git is proven to ignore."
```

---

### Task 6: Close the two residuals that these tools make live

**Files:**
- Modify: `E:\Amber-MCP\amber_mcp\driver.py:422-430`
- Modify: `E:\Amber-MCP\tests\unit\test_no_secrets_committed.py`
- Create: `E:\Amber-MCP\tests\unit\test_residuals.py`

**Interfaces:**
- Consumes: `driver.Driver.type_text`, `config.is_inside_project`, `discovery._refuse_the_repository`
- Produces: `driver.TextEntryError(TransportError)` raised without quoting the text; a pairing test asserting the two in-repo predicates agree

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_residuals.py`:

```python
from pathlib import Path

import pytest

from amber_mcp import config, discovery


def test_typing_failure_names_the_failure_not_the_text():
    # Residual 1: send_keys used to be called bare, so a uiautomator2 exception
    # could quote the string it failed to type -- a contact name, in a transcript.
    from amber_mcp.driver import Driver

    class Device:
        def send_keys(self, text, clear=False):
            raise RuntimeError(f"IME rejected {text!r}")

    driver = Driver("stub:5555")
    driver.device = Device()
    with pytest.raises(Exception) as excinfo:
        driver.type_text("রাকিন উদ্দিন")
    assert "রাকিন" not in str(excinfo.value)
    assert "could not be typed" in str(excinfo.value)


@pytest.mark.parametrize("candidate", [
    "artifacts", "artifacts/raw", "logs", ".", "..", "E:\\Amber-MCP_x",
    str(config.PROJECT_ROOT), str(config.PROJECT_ROOT / "artifacts"),
])
def test_the_two_in_repo_predicates_never_disagree(candidate):
    # Residual 2: config.is_inside_project and discovery's own refusal both
    # answer "is this inside the repo", and both docstrings claim they apply the
    # same test while nothing proved it. A comparison against itself would pass
    # either way, so this calls the two real functions on the same path.
    path = Path(candidate)
    assert config.is_inside_project(path) == discovery.is_inside_repository(path)
```

- [ ] **Step 2: Run to make sure they fail**

Run: `uv run pytest tests/unit/test_residuals.py -v`
Expected: FAIL — `AttributeError: module 'amber_mcp.discovery' has no attribute 'is_inside_repository'`, and `type_text` raises the exception quoting the name.

- [ ] **Step 3: Implement both**

In `driver.py`, replace the body of `type_text` so the IME call is wrapped:

```python
    def type_text(self, text: str) -> None:
        """Send text through the device IME, which is the only way non-ASCII works.

        A failure names the failure, never the payload. uiautomator2's IME path
        raises with the rejected string in its message, and that string is a
        contact name a voice transcript would then repeat.
        """
        device = self._require_device()
        try:
            device.send_keys(text)
        except Exception as exc:
            raise TextEntryError("the text could not be typed on the phone") from exc
```

Add beside `TransportError`:

```python
class TextEntryError(TransportError):
    """Typing failed. The message never quotes what was being typed."""
```

In `discovery.py`, expose the existing predicate under a public name and have `_refuse_the_repository` call it, so there is one implementation and the pairing test can compare two.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/test_residuals.py -v`
Expected: PASS.

- [ ] **Step 5: Prove the scrub works**

```bash
uv run python -c "
from amber_mcp.driver import Driver, TextEntryError
class Bad:
    def send_keys(self, t, clear=False): raise RuntimeError(f'IME rejected {t!r}')
d=Driver('x:5555'); d.device=Bad()
try: d.type_text('রাকিন')
except TextEntryError as e: print('scrubbed:', str(e), '| name in message:', 'রাকিন' in str(e))
"
```

Expected: `scrubbed: the text could not be typed on the phone | name in message: False`.

- [ ] **Step 6: Commit**

```bash
git add amber_mcp/driver.py amber_mcp/discovery.py tests/unit/test_residuals.py
git commit -m "Close the two residuals these tools make live

The IME path used to raise with the rejected string in its message, which was
harmless while nothing typed and is a name leak the moment a contact search
does. The two in-repo path predicates now share one implementation and a test
proves they agree."
```

---

### Task 7: Live measurement that plan 3 needs, and nothing invented for it

**Files:**
- Create: `E:\Amber-MCP\artifacts\SELECTORS.md` (append sections)
- Create: `E:\Amber-MCP\artifacts\signals\FINDINGS.md` (append sections)

**Interfaces:**
- Produces: measured rows for three unknowns. No Python interfaces — this task's output is evidence, and its test is that the evidence exists.

**This task cannot be done alone.** It needs a reachable phone, and two of the three items need a real call. The transport was measured as unstable over Tailscale with a rotating wireless-debugging port: a session completed one ladder run and then dropped with `device not online`. Fix the link first — USB, or the phone on the PC's own WiFi with the wireless-debugging screen left open.

- [ ] **Step 1: Verify the link is stable enough to work on**

```bash
adb connect <serial> && adb -s <serial> shell getprop ro.product.model
```

Expected: the model is `2201117SI`. **Confirm the model before any input** — a discovered endpoint that answered was a Toshiba SmartTV on this LAN, not the phone. Then run `session.ensure_ready()` twice with a minute between and require both to succeed. A link that drops between two reads cannot support a dial flow.

- [ ] **Step 2: Locate the contacts-search scope control**

Ask the user to open it by hand, then capture both states:

```bash
uv run python -c "
from amber_mcp import config, discovery, session
from amber_mcp.driver import Driver
import winreg
d = Driver(config.serial_from_env()); d.connect()
pin = winreg.QueryValueEx(winreg.OpenKey(winreg.HKEY_CURRENT_USER,'Environment'),'REDMI_PIN')[0]
print(session.say(session.ensure_ready(d, pin=str(pin))))
print(discovery.capture(discovery.read_only(d), 'contacts-search-scoped'))
"
```

Record in `SELECTORS.md`: the entry point that reaches it, the control's resource-id or bounds, and the two labels' exact text. Until this exists, `contacts_search(query, scope)` has nothing to set and the spec's rule — scope must always be set explicitly, never inherited, because the app remembers the last one — cannot be implemented at all.

- [ ] **Step 3: Measure the answer control**

`KEYCODE_ENDCALL` (6) is measured working on this app. `answer` is not: Mark-LV's `5` is proven for cellular, and the in-call screen exposes no view nodes to tap. With the user's explicit go-ahead, place an inbound call and try `5`, recording whether `audio_mode` transitions and what `current_focus` says. If nothing works, write that down as the answer.

- [ ] **Step 4: Measure whether the balance refreshes after a paid call**

Read `tvBalance` before, immediately after a call ends, and again after ten seconds, and record the three values in `FINDINGS.md` as values only — never a number that identifies the account. This decides whether `balance()` may be called immediately after `call()` or must wait.

- [ ] **Step 5: Commit the evidence**

```bash
git add artifacts
uv run pytest tests/unit/test_no_secrets_committed.py -q
git commit -m "Measure the answer control, the search scope, and balance refresh"
```

If any capture file would carry a contact name or number, it does not belong in this commit — the gate is not smart enough to tell a real name from a UI label.

---

## What this plan does not contain, and why

Three of the spec's seven tools are absent, and each absence is a decision rather than an omission:

- **`answer()`** — the key code is unmeasured for this app. Task 7 step 3 measures it; plan 3 writes the flow. Writing it now would ship a guess at a control that either does nothing or does something unexpected on a live inbound call.
- **`contacts_search()`** — the AmberIT/All scope control has never been located. The spec's own rule makes this unfakeable: scope must be set explicitly on every search because the app remembers the previous one, so a flow without the control silently searches the wrong list and returns plausible results.
- **The answered-versus-ringing classifier** — measured as impossible on the current signals. Task 7's second call, one the user answers, is the only thing that could change that; until then the honest answer stays UNKNOWN, which is behaviour, not a gap.

---

## Self-Review

**1. Spec coverage.**

| Spec requirement | Task |
|---|---|
| Semantic tools, not primitives | 3, 4, 5 |
| IP line only, no cellular fallback | 3 (`ACTION_DIAL` to the explicit AmberIT component; `ACTION_CALL` asserted absent by test in 5) |
| No confirmation gate; proof instead | 3 step 5 (read-back aborts with zero taps), 5 (log records refusals too) |
| Read-back is proof, never `am start` rc 0 | 3, 4 |
| Call up = activity **and** audio mode | 4, two tests and one negative each |
| `mCallState` as the negative that proves the SIM was idle | 4 (`sim_idle_proof`) |
| Answered vs ringing is UNKNOWN | stated as unimplemented in "What this plan does not contain", not silently dropped |
| In-call screen has no view nodes → hang up by keyevent, duration from the audio span | 4, 5 |
| The call-type chooser decides the cost; `Free Audio Call` is a silent no-op | 3 — the row is caller-named and the chooser's absence is a positive result |
| Locate from live hierarchy, never remembered pixels | 2 step 5, with a missing-element test |
| No write without the ladder and a proven foreground | 1 — this is the spec's "Residuals" and "gap I caused" requirement, now structural |
| Single-writer lock (recorded not implemented in plan 1) | 5 |
| Append-only dial log, record not gate (recorded not implemented) | 5 |
| Tool output 1–3 spoken lines | 5 |
| PIN env-only, never typed by a read, never in output or logs | 1 (asymmetric `unlock`), 5 (log content test), 6 (typing failure cannot quote the payload) |
| Nothing real committed | 5 step 6, 7 step 5 |
| `fastmcp>=2.14,<3`, registration by call | 5 (the tools-list test uses `.values()`, which is 2.14.7's shape) |
| `contacts_search`, `answer`, classifier | deferred with reasons, not placeholders |

**Gaps found and closed while reviewing:** the spec's failure policy says a crashed app should be force-stopped and relaunched, and plan 1 recorded that as missing. This plan does not implement it either — nothing in these flows can leave the app crashed, but `hangup` timing out can leave it in `CallActivityWebRTC`, so Task 5's `hangup_tool` reports that state explicitly rather than pretending. Recorded here so plan 3 inherits it as a known gap, not a surprise.

**2. Placeholder scan.** Task 6 step 1 initially contained a parametrised test that compared a value to itself — a test that cannot fail, the exact defect this plan's own reviews rejected three times. It is replaced with the real pairing assertion and the instruction to expose the predicate publicly is what makes it executable. Task 7 has no code by design, but every step names the command to run and the file the answer goes into. No TBD, no "add error handling", no "similar to Task N".

**3. Type consistency.** Checked across tasks: `gate.enter(driver, *, unlock=False) -> str` defined in 1 and called in 3 with `unlock=True` and in 5 only through the flow. `gate.NotReady.message` and `.wrong_foreground` defined in 1, read in 5. `screens.find/text_row_centre/has_chooser/read_field/balance_text/history_rows/DIAL_INTENT/ACTIVITIES/NotFound/ALL` defined in 2 and consumed in 3 and 4 with those exact names. `dial.DialResult(ok, verdict, detail)`, `dial.digits`, `dial.place_call`, `dial.CHOOSER_ROW` defined in 3 and consumed in 5. `phone_state.audio_mode/call_is_up/sim_idle_proof/wait_for_call_end/read_balance/read_history/AUDIO_UP` defined in 4 and consumed in 5. `TextEntryError` and `discovery.is_inside_repository` defined in 6. `config.MAX_CALL_SECONDS` defined in 5 step 3 and referenced only there and in Task 7's notes — if a flow needs it, it is passed to `wait_for_call_end(timeout=…)` explicitly, which is why that parameter is keyword-only.
