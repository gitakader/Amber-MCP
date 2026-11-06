"""Tests for the discovery harness.

The contract these tests hold is a security one as much as a functional one: the
redacted copy is what the repo may carry, and the un-redacted copy may only ever
land inside the directory git is proven to ignore. A `.parent` slipped into the
plan's draft of `capture()` would have put `raw/` *beside* `artifacts/`, i.e.
outside the ignore rule, so the exact location of the raw copy is asserted here
rather than assumed.
"""
import xml.etree.ElementTree as ET

import pytest

from amber_mcp import config, discovery, redact

SAMPLE = """<hierarchy>
  <node text="Dial" resource-id="com.amberit.dialer:id/tab_dial" class="android.widget.TextView"
        bounds="[90,100][240,180]" clickable="true"/>
  <node text="01712345678" resource-id="com.amberit.dialer:id/number" bounds="[0,50][100,90]"
        clickable="false"/>
  <node content-desc="Call" class="android.widget.ImageButton" bounds="[400,600][500,700]"
        clickable="true"/>
  <node text="" resource-id="com.amberit.dialer:id/container" bounds="[0,0][1080,2400]"/>
</hierarchy>"""

# A clickable node that carries no label at all: no text, no content-desc, no
# resource-id. It is a hit-box on a screen nobody has named yet, so it must not
# reach plan 2's selector table as a tap target nobody can identify.
UNLABELLED = """<hierarchy>
  <node bounds="[0,0][100,100]" clickable="true"/>
  <node resource-id="com.amberit.dialer:id/ok" bounds="[0,0][100,100]" clickable="true"/>
</hierarchy>"""

SECRET = "01712345678"


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


def test_a_clickable_thing_with_no_label_is_not_offered_as_a_target():
    assert [d for d, _x, _y in discovery.interactive(UNLABELLED)] == [
        "com.amberit.dialer:id/ok"]


def test_a_screen_label_is_taken_from_the_activity_not_from_translated_text():
    assert discovery.screen_names(SAMPLE) != []
    # Focus-derived naming is checked in the capture test below.


def test_capture_writes_the_committed_trio_under_the_screen_name(tmp_path):
    # Four artifacts in the plan's Step 1 draft; Step 5 then withdrew the
    # screenshot from the committed copy, so the committed set is the three
    # files a pattern can read and a human can redact. The fourth (screen.png)
    # is asserted on its own, below.
    driver = StubDriver()
    path = discovery.capture(driver, "dashboard", out_dir=tmp_path)
    assert path.name == "dashboard"
    written = {p.name for p in path.iterdir()}
    assert written == {"hierarchy.xml", "focus.txt", "signals.txt"}


def test_committed_copy_is_redacted_and_the_raw_copy_is_kept_separate(tmp_path):
    driver = StubDriver()
    discovery.capture(driver, "contacts", out_dir=tmp_path, secrets=[SECRET])
    clean = (tmp_path / "contacts" / "hierarchy.xml").read_text(encoding="utf-8")
    raw = (tmp_path / "raw" / "contacts" / "hierarchy.xml").read_text(encoding="utf-8")
    assert SECRET not in clean
    assert SECRET in raw, "raw must keep reality for debugging"
    assert "id/number" in clean, "selectors must survive redaction intact"


def test_nothing_unredacted_is_written_outside_the_raw_directory(tmp_path):
    # The one mistake that turns this harness into a leak: `raw/` landing next to
    # `artifacts/` instead of inside it, where the ignore rule cannot reach it.
    discovery.capture(StubDriver(), "contacts", out_dir=tmp_path, secrets=[SECRET])
    leaking = sorted(
        p.relative_to(tmp_path).as_posix()
        for p in tmp_path.rglob("*")
        if p.is_file() and SECRET.encode() in p.read_bytes()
    )
    assert leaking == ["raw/contacts/hierarchy.xml"]


def test_capture_fails_loudly_when_the_hierarchy_read_is_broken(tmp_path):
    class Broken(StubDriver):
        def dump_hierarchy(self):
            raise RuntimeError("hierarchy came back empty")
    with pytest.raises(RuntimeError):
        discovery.capture(Broken(), "x", out_dir=tmp_path)
    assert not (tmp_path / "x" / "hierarchy.xml").exists(), (
        "a broken read must not stand in for a blank screen")


def test_signals_are_sampled_from_the_exact_commands_the_spec_named(tmp_path):
    driver = StubDriver()
    discovery.capture(driver, "dashboard", out_dir=tmp_path)
    commands = " ".join(driver.files.get("shell", []))
    assert "dumpsys audio" in commands
    assert "dumpsys notification" in commands
    assert "dumpsys power" in commands
    assert "telephony.registry" in commands
    assert "content query --uri content://call_log/calls" in commands


def test_no_screenshot_is_committed_by_default(tmp_path):
    discovery.capture(StubDriver(), "contacts", out_dir=tmp_path)
    assert not (tmp_path / "contacts" / "screen.png").exists()
    assert (tmp_path / "raw" / "contacts" / "screen.png").exists()


def test_signals_are_redacted_in_the_committed_copy_too(tmp_path):
    # The call-log query is one of the spec's own signal sources, so the number
    # under test is in signals.txt, not just in the hierarchy.
    class Leaky(StubDriver):
        def shell(self, cmd, timeout=20.0):
            self.files.setdefault("shell", []).append(cmd)
            return type("R", (), {"ok": True, "out": f"number={SECRET}", "err": ""})()
    discovery.capture(Leaky(), "dashboard", out_dir=tmp_path, secrets=[SECRET])
    clean = (tmp_path / "dashboard" / "signals.txt").read_text(encoding="utf-8")
    raw = (tmp_path / "raw" / "dashboard" / "signals.txt").read_text(encoding="utf-8")
    assert SECRET not in clean
    assert SECRET in raw


def test_the_screenshot_override_is_per_screen_and_deletes_a_stale_image(tmp_path):
    # `include=False` has to remove an image a previous run committed, or a
    # screen re-captured without the override keeps publishing the old pixels.
    discovery.capture(StubDriver(), "contacts", out_dir=tmp_path, include_png=True)
    assert (tmp_path / "contacts" / "screen.png").is_file()
    discovery.capture(StubDriver(), "contacts", out_dir=tmp_path)
    assert not (tmp_path / "contacts" / "screen.png").exists()


def test_a_signal_that_did_not_run_is_recorded_as_not_running(tmp_path):
    # Silent absence and an empty answer are different facts, and plan 2's
    # classifier can only tell them apart if the capture says which happened.
    class Refusing(StubDriver):
        def shell(self, cmd, timeout=20.0):
            self.files.setdefault("shell", []).append(cmd)
            return type("R", (), {"ok": False, "rc": 127, "out": "", "err": "adb: not found"})()
    discovery.capture(Refusing(), "dashboard", out_dir=tmp_path)
    clean = (tmp_path / "dashboard" / "signals.txt").read_text(encoding="utf-8")
    assert "did not run" in clean
    assert "$ dumpsys audio" in clean


def test_the_committed_hierarchy_is_still_parseable_xml_after_redaction(tmp_path):
    # `redact.text` substitutes a bare `<number>`, which is malformed inside an
    # XML attribute. A hierarchy nobody can parse hands plan 2 nothing, so the
    # token has to arrive escaped.
    discovery.capture(StubDriver(), "dashboard", out_dir=tmp_path, secrets=[SECRET])
    clean = (tmp_path / "dashboard" / "hierarchy.xml").read_text(encoding="utf-8")
    root = ET.fromstring(clean)
    assert "&lt;number&gt;" in clean, "the token must be entity-escaped on disk"
    number_node = [n for n in root.iter("node")
                   if n.get("resource-id") == "com.amberit.dialer:id/number"][0]
    assert number_node.get("text") == redact.NUMBER_TOKEN
    assert SECRET not in clean


def test_summarise_reads_the_committed_copy_back(tmp_path):
    discovery.capture(StubDriver(), "dashboard", out_dir=tmp_path)
    screen = discovery.summarise(tmp_path / "dashboard")
    assert screen.name == "dashboard"
    # The identity of the screen is the activity that owns it, never a translated
    # label on it: AmberIT's text changes with the device language, the activity
    # name does not.
    assert screen.focus == "com.amberit.dialer/.Dashboard"
    assert "com.amberit.dialer:id/tab_dial" in screen.resource_ids
    assert screen.interactive == ["Dial @ 165,140", "Call @ 450,650"]


def test_capture_defaults_its_output_to_the_configured_artifacts_dir(tmp_path, monkeypatch):
    # R2: the default comes from `config.artifacts_dir()` and from nowhere else.
    # A path derived from `__file__` would point inside site-packages under a
    # real wheel install, silently splitting the repo's artifacts in two.
    monkeypatch.setattr(config, "artifacts_dir", lambda: tmp_path)
    path = discovery.capture(StubDriver(), "dashboard")
    assert path == tmp_path / "dashboard"
    assert (tmp_path / "raw" / "dashboard" / "hierarchy.xml").is_file()


def test_selectors_doc_is_named_at_the_repo_artifacts_dir():
    assert discovery.SELECTORS_DOC.parts[-2:] == ("artifacts", "SELECTORS.md")


def test_a_screen_holds_what_plan_two_needs_to_write_a_flow():
    screen = discovery.Screen(name="x", focus="f")
    assert screen.resource_ids == [] and screen.texts == [] and screen.interactive == []
    assert discovery.Screen("x", "f").focus == "f"


def test_interactive_survives_a_malformed_bounds_attribute():
    broken = '<hierarchy><node text="Dial" bounds="[0,0]" clickable="true"/></hierarchy>'
    assert discovery.interactive(broken) == []
    assert ET.fromstring(broken) is not None  # the XML itself was fine
