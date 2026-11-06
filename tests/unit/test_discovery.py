"""Tests for the discovery harness.

The contract these tests hold is a security one as much as a functional one: the
redacted copy is what the repo may carry, and the un-redacted copy may only ever
land inside the directory git is proven to ignore. A `.parent` slipped into the
plan's draft of `capture()` would have put `raw/` *beside* `artifacts/`, i.e.
outside the ignore rule, so the exact location of the raw copy is asserted here
rather than assumed.
"""
import shutil
import xml.etree.ElementTree as ET

import pytest

from amber_mcp import config, discovery, redact, session
from amber_mcp.driver import Driver

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
    # under test is in signals.txt, not just in the hierarchy. The fixture plants
    # it on an allowlisted classifier line, so this bites on the committed copy
    # and not only on the fact that the rest of the dump was filtered away.
    class Leaky(StubDriver):
        def shell(self, cmd, timeout=20.0):
            self.files.setdefault("shell", []).append(cmd)
            return type("R", (), {"ok": True, "rc": 0, "out": f"mCallState={SECRET}",
                                  "err": ""})()
    discovery.capture(Leaky(), "dashboard", out_dir=tmp_path, secrets=[SECRET])
    clean = (tmp_path / "dashboard" / "signals.txt").read_text(encoding="utf-8")
    raw = (tmp_path / "raw" / "dashboard" / "signals.txt").read_text(encoding="utf-8")
    assert SECRET not in clean
    assert SECRET in raw
    assert f"mCallState={redact.NUMBER_TOKEN}" in clean, "the classifier line must survive"


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


def test_capture_defaults_its_output_to_the_scratch_dir(tmp_path, monkeypatch):
    # R2: the default is `config.scratch_dir()` and from nowhere else. Two proofs
    # composed: this one says the default *is* the scratch directory, and
    # `test_scratch_dir_lands_outside_the_repository` in test_config.py says the
    # scratch directory is not the repo. `config.artifacts_dir()` used to be the
    # default, and it is inside the repo -- which is how a raw capture of the
    # user's whole call log used to land where git could publish it.
    monkeypatch.setattr(config, "scratch_dir", lambda: tmp_path / "amber-captures")
    path = discovery.capture(StubDriver(), "dashboard")
    assert path == tmp_path / "amber-captures" / "dashboard"
    assert path.is_dir()
    assert (tmp_path / "amber-captures" / "raw" / "dashboard" / "hierarchy.xml").is_file()
    assert config.PROJECT_ROOT not in path.parents


def test_the_environment_named_capture_dir_is_used_when_no_out_dir_is_given(
        tmp_path, monkeypatch, repo_scratch):
    # End to end through the real `config.scratch_dir()`. `repo_scratch` cleans
    # the probe-named paths either way, so a run against un-fixed code writes
    # into a directory that is deleted again rather than staying in the tree.
    monkeypatch.setenv("AMBER_CAPTURE_DIR", str(tmp_path))
    path = discovery.capture(StubDriver(), PROBE_SCREEN)
    assert path == tmp_path / "amber-captures" / PROBE_SCREEN


def test_the_committed_hierarchy_goes_through_redact_xml(tmp_path, monkeypatch):
    # R3 moved the attribute-escaping into `redact.xml`; escaping is no longer
    # this module's to remember. This asserts the wiring, not the escaping, so it
    # fails the moment the hierarchy is written from `redact.text` again.
    seen = []
    real_xml = redact.xml

    def spy(value, secrets=()):
        seen.append(value)
        return real_xml(value, secrets)

    monkeypatch.setattr(redact, "xml", spy)
    discovery.capture(StubDriver(), "dashboard", out_dir=tmp_path, secrets=[SECRET])
    assert seen and any(SECRET in dumped for dumped in seen), (
        "the raw hierarchy was not handed to redact.xml")
    assert not hasattr(discovery, "_xml_safe"), "the escaping belongs to redact now"
    assert "&lt;number&gt;" in (tmp_path / "dashboard" / "hierarchy.xml").read_text(
        encoding="utf-8")


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


# ---------------------------------------------------------------------------
# R2: the repository is never a capture directory.
# ---------------------------------------------------------------------------

PROBE_SCREEN = "_amber_r2_probe_screen"


@pytest.fixture
def repo_scratch():
    """Cleans the probe-named capture paths a not-yet-refusing `capture()` wrote.

    These tests ask for a refusal that does not exist while they are RED, so
    without this the run itself would drop raw account data into the working
    tree and hand it to the secret gate. Only paths carrying `PROBE_SCREEN` are
    ever removed, so a real capture can never be deleted by accident.
    """
    root = config.PROJECT_ROOT
    candidates = [
        root / "artifacts" / PROBE_SCREEN,
        root / "artifacts" / "raw" / PROBE_SCREEN,
        root / PROBE_SCREEN,
        root / "raw" / PROBE_SCREEN,
        root / "artifacts" / "_amber_r2_probe_dir",
        root / "artifacts" / "_amber_r2_probe_dir" / "raw",
    ]

    def _clean():
        for path in candidates:
            shutil.rmtree(path, ignore_errors=True)

    _clean()
    yield root / "artifacts" / "_amber_r2_probe_dir"
    _clean()


def test_an_out_dir_inside_the_repository_is_refused_and_writes_nothing(repo_scratch):
    for bad in (repo_scratch, config.PROJECT_ROOT / "artifacts", config.PROJECT_ROOT):
        with pytest.raises(ValueError, match="public"):
            discovery.capture(StubDriver(), PROBE_SCREEN, out_dir=bad)
        for made in (bad / PROBE_SCREEN, bad / "raw" / PROBE_SCREEN):
            assert not made.exists(), f"a refused capture still wrote into {made}"
    assert not repo_scratch.exists(), (
        "the refusal came after the mkdir, leaving a directory in the tree")


# ---------------------------------------------------------------------------
# R1: what a committed signals.txt may contain.
#
# The commands themselves stay (Task 7 exists to find out what each one says
# about an IP call), and `raw/` keeps every line of what they answer. What must
# not survive is the dump: the measured call-log query alone produced 52,130
# lines of `date=<epoch>`, and after redaction that is still a publishable record
# of who was called, when and how often -- which no rule in the gate can see,
# because timestamps and prose match nothing.
# ---------------------------------------------------------------------------

FOCUS_CLASSIFIER = ("mCurrentFocus=Window{a1b2c3d u0 com.amberit.dialer/"
                    "com.revesoft.itelmobiledialer.ui2.dashboard.AgniDashboardActivity}")
WAKEFULNESS_CLASSIFIER = "mWakefulness=Awake"
CALL_STATE_CLASSIFIER = "mCallState=CALL_STATE_DIALING"
AUDIO_REQUESTED_CLASSIFIER = "- Requested mode = MODE_IN_COMMUNICATION"
AUDIO_MODE_CLASSIFIER = "- Actual mode = MODE_IN_COMMUNICATION"

# Measured on the handset 2026-11-06: `dumpsys audio` carries a timestamped
# setMode history naming which app asked for each mode. Publishing it would
# publish the user's WhatsApp and cellular call times, so it is the exact leak
# this allowlist exists to stop -- and it survived the first live capture.
AUDIO_HISTORY_LINE = ("11-06 19:57:07:446 setMode(MODE_IN_COMMUNICATION) from package=com.whatsapp pid=29275 selected mode=MODE_IN_COMMUNICATION by pid=0")

# The call log as the handset answers it: one number and one millisecond
# timestamp per call, for every call it remembers.
CALL_LOG_ROWS = [
    f"Row: 0 number={SECRET}, date=1760000000000",
    f"Row: 1 number={SECRET}, date=1760086400000",
    "Row: 2 number=01911223344, date=1760172800000",
    "Row: 3 number=01799887766, date=1735689600000",
    f"Row: 4 number={SECRET}, date=1770624000000",
]

# Message prose, which `redact.text` has no model of at all: its digit and money
# passes only fire inside `text=`/`content-desc=` attributes.
NOTIFICATION_MESSAGE = "Payment of 4,250.75 received from Rabin Bose"
AUDIO_FILLER = [f"- stream={i % 10} volume={i % 15} max=15 enabled={i}" for i in range(300)]
AUDIO_FILLER_LINE = AUDIO_FILLER[7]

SIGNAL_DUMPS = {
    "dumpsys window | grep mCurrentFocus": (
        f"  {FOCUS_CLASSIFIER}\n"
        "  mFocusedApp=ActivityRecord{ffcfc0e u0 com.miui.securitycenter/"
        "com.miui.cleanmaster.LowMemoryIntentDispatchActivity} t343}"),
    "dumpsys audio": "\n".join(
        ["AudioService state:", "- zen mode:ZEN_MODE_OFF",
         f"  {AUDIO_REQUESTED_CLASSIFIER}", f"  {AUDIO_MODE_CLASSIFIER}",
         "mScoAudioMode: SCO_MODE_VIRTUAL_CALL", AUDIO_HISTORY_LINE]
        + [AUDIO_FILLER_LINE.format(i=i) for i in range(300)]),
    "dumpsys notification --noredact": (
        "Notification records:\n"
        "  NotificationRecord(0x0099aabb: pkg=com.amberit.dialer user=0 id=0)\n"
        "    android.title=AmberIT\n"
        f"    android.text={NOTIFICATION_MESSAGE} on {SECRET}\n"
        "    mEffectiveNotification=0x112233 flags=0x12"),
    "dumpsys power | grep mWakefulness": (
        f"  {WAKEFULNESS_CLASSIFIER}\n"
        "  mWakefulnessChanging=false\n"
        "  mIsPowered=true\n"
        "  mStayOn=false"),
    "dumpsys telephony.registry | grep mCallState": (
        f"  {CALL_STATE_CLASSIFIER}\n"
        "  mCallAidlBundle=Bundle[{key=1}]\n"
        "  mCallForwardingEnabled=false"),
    "content query --uri content://call_log/calls --projection number:date "
    "--sort 'date DESC'": "\n".join(CALL_LOG_ROWS),
}

# row count, and the span of days the rows cover -- as days, not timestamps.
CALL_LOG_SUMMARY = "call-log: rows=5 oldest_day=2025-01-01 newest_day=2026-02-09 (utc)"
EXPECTED_COMMITTED_BODIES = [
    FOCUS_CLASSIFIER, WAKEFULNESS_CLASSIFIER, CALL_STATE_CLASSIFIER,
    AUDIO_REQUESTED_CLASSIFIER, AUDIO_MODE_CLASSIFIER, CALL_LOG_SUMMARY,
]


class HandsetStub(StubDriver):
    """Answers each signal command the way the measured handset answered it."""

    def shell(self, cmd, timeout=20.0):
        self.files.setdefault("shell", []).append(cmd)
        return type("R", (), {"ok": True, "rc": 0, "out": SIGNAL_DUMPS[cmd], "err": ""})()


def committed_signals(tmp_path, name="dashboard"):
    return (tmp_path / name / "signals.txt").read_text(encoding="utf-8")


def test_the_handset_fixture_answers_every_signal_command_the_spec_named():
    assert set(SIGNAL_DUMPS) == set(discovery.SIGNAL_COMMANDS)


def test_planted_call_log_rows_and_notification_prose_reach_the_raw_copy(tmp_path):
    # The raw copy is unchanged by R1: Task 7 has to be able to read what each
    # command really said, in full.
    discovery.capture(HandsetStub(), "dashboard", out_dir=tmp_path)
    raw = (tmp_path / "raw" / "dashboard" / "signals.txt").read_text(encoding="utf-8")
    for row in CALL_LOG_ROWS:
        assert row in raw, "raw must keep every row of the query"
    assert NOTIFICATION_MESSAGE in raw
    assert SECRET in raw
    assert AUDIO_FILLER_LINE in raw
    assert AUDIO_HISTORY_LINE in raw, "raw must keep the audio history for Task 7"
    assert raw.count("\n") > 300, "raw must be the whole dump, not the classifier lines"


def test_the_committed_signals_file_carries_exactly_the_four_classifier_lines(tmp_path):
    discovery.capture(HandsetStub(), "dashboard", out_dir=tmp_path)
    clean = committed_signals(tmp_path)
    for line in (FOCUS_CLASSIFIER, WAKEFULNESS_CLASSIFIER, CALL_STATE_CLASSIFIER,
                 AUDIO_REQUESTED_CLASSIFIER, AUDIO_MODE_CLASSIFIER):
        assert line in clean, f"{line} is a classifier line and must be committed"
    bodies = [ln.strip() for ln in clean.splitlines()
              if ln.strip() and not ln.startswith("$ ")]
    assert sorted(bodies) == sorted(EXPECTED_COMMITTED_BODIES), (
        f"the committed copy carries more than the allowlist: {bodies}")


def test_neither_the_rows_nor_their_redacted_shadow_reach_the_committed_copy(tmp_path):
    discovery.capture(HandsetStub(), "dashboard", out_dir=tmp_path, secrets=[SECRET])
    clean = committed_signals(tmp_path)
    for row in CALL_LOG_ROWS:
        assert row not in clean
    for forbidden in (
            "number=", "date=", "Row:", NOTIFICATION_MESSAGE, "Rabin Bose",
            "android.text", "NotificationRecord", "mFocusedApp", AUDIO_FILLER_LINE,
            "setMode(", "com.whatsapp", "11-06 19:57:07:446",
            redact.NUMBER_TOKEN, redact.BALANCE_TOKEN, redact.NAME_TOKEN):
        assert forbidden not in clean, (
            f"{forbidden!r} reached the committed signals.txt in some form")
    assert "1735689600000" not in clean, "a day is allowed, an epoch timestamp is not"
    # The command is still recorded as having run, so Task 7 can see it answered.
    assert "$ dumpsys notification --noredact" in clean


def test_the_committed_call_log_block_is_a_two_value_summary(tmp_path):
    discovery.capture(HandsetStub(), "dashboard", out_dir=tmp_path)
    clean = committed_signals(tmp_path)
    assert CALL_LOG_SUMMARY in clean
    assert "rows=5" in clean
    assert "2025-01-01" in clean and "2026-02-09" in clean


def test_the_committed_signal_allowlist_is_data_and_covers_exactly_the_spec_commands():
    # R1 asked for an allowlist a test can read, not regex soup in the middle of
    # a join. Keying it per command is what keeps `MODE_` from reaching a dump it
    # happens to mention, and an empty tuple is what drops a block whole.
    keys = discovery.COMMITTED_SIGNAL_KEYS
    assert set(keys) == set(discovery.SIGNAL_COMMANDS), (
        "the allowlist and the command list have drifted apart")
    assert keys["dumpsys window | grep mCurrentFocus"] == ("mCurrentFocus=",)
    assert keys["dumpsys power | grep mWakefulness"] == ("mWakefulness=",)
    assert keys["dumpsys telephony.registry | grep mCallState"] == ("mCallState=",)
    assert keys["dumpsys audio"] == ("Requested mode", "Actual mode"), (
        "only the current audio state may be published, never the history")
    assert keys["dumpsys notification --noredact"] == (), (
        "the committed copy must hold no notification content at all")
    call_log = ("content query --uri content://call_log/calls "
                "--projection number:date --sort 'date DESC'")
    assert keys[call_log] == (), "call-log rows are summarised, never transcribed"
    assert tuple(discovery.SUMMARISED_COMMANDS) == (call_log,)


# ---------------------------------------------------------------------------
# R4: the harness is an observer, and now refuses anything else.
#
# `capture()` deliberately does not call `session.ensure_ready()` and does not
# use `config.raw_dir`: a look must not wake, unlock or launch a handset, and
# promoting the harness to a driver would make the stray `input tap` class of
# accident permanent. The refusal below is what turns that reasoning into a rule.
# ---------------------------------------------------------------------------


def test_capture_refuses_a_driver_that_exposes_write_capability(tmp_path):
    issued = []

    def runner(argv):
        issued.append(argv)
        raise AssertionError("capture() issued a command after it should have refused")

    with pytest.raises(TypeError) as excinfo:
        discovery.capture(Driver("stub-serial", runner=runner), "dashboard",
                          out_dir=tmp_path)
    for name in discovery.DRIVER_WRITE_METHODS:
        assert name in str(excinfo.value), "refusal must name what it objected to"
    assert issued == [], "the refusal must come before a single command is issued"
    assert not (tmp_path / "dashboard").exists(), "a refused driver writes nothing"


def test_a_read_only_driver_is_accepted(tmp_path):
    class ReadOnly:
        serial = "stub"

        def current_focus(self):
            return "com.amberit.dialer/.Dashboard"

        def dump_hierarchy(self):
            return SAMPLE

        def screenshot(self):
            return b"\x89PNG-fake"

        def shell(self, cmd, timeout=20.0):
            return type("R", (), {"ok": True, "rc": 0, "out": FOCUS_CLASSIFIER,
                                  "err": ""})()

    path = discovery.capture(ReadOnly(), "dashboard", out_dir=tmp_path)
    assert {p.name for p in path.iterdir()} == {"hierarchy.xml", "focus.txt",
                                                "signals.txt"}


def test_any_single_write_method_on_the_driver_is_enough_to_be_refused(tmp_path):
    assert set(discovery.DRIVER_WRITE_METHODS) == {
        "tap_xy", "keyevent", "type_text", "type_digits"}
    for name in discovery.DRIVER_WRITE_METHODS:
        stub = type("OneWrite", (StubDriver,), {name: lambda self, *a, **k: None})()
        with pytest.raises(TypeError, match=name):
            discovery.capture(stub, "dashboard", out_dir=tmp_path)
        assert not (tmp_path / "dashboard").exists()


def test_the_harness_never_reaches_for_the_readiness_ladder(tmp_path, monkeypatch):
    # Ratified deviation, recorded as a test rather than as a sentence: importing
    # the ladder at all is how a look becomes a wake-unlock-launch.
    def boom(*args, **kwargs):
        raise AssertionError("capture() promoted itself from observer to driver")

    monkeypatch.setattr(session, "ensure_ready", boom)
    assert not hasattr(discovery, "session")
    discovery.capture(StubDriver(), "dashboard", out_dir=tmp_path)


def test_read_only_exposes_the_four_reads_and_refuses_everything_else():
    class Full:
        def current_focus(self): return "x/y"
        def dump_hierarchy(self): return "<hierarchy/>"
        def screenshot(self): return b"png"
        def shell(self, cmd, timeout=20.0): return type("R", (), {"ok": True, "out": "", "err": ""})()
        def tap_xy(self, x, y): raise AssertionError("capture must never reach this")
        def keyevent(self, code): raise AssertionError("capture must never reach this")
        def type_text(self, t): raise AssertionError("capture must never reach this")
        def type_digits(self, d): raise AssertionError("capture must never reach this")

    view = discovery.read_only(Full())
    assert view.current_focus() == "x/y"
    assert view.dump_hierarchy() == "<hierarchy/>"
    for forbidden in ("tap_xy", "keyevent", "type_text", "type_digits"):
        try:
            getattr(view, forbidden)
        except AttributeError:
            continue
        raise AssertionError(f"{forbidden} leaked through the read-only view")


def test_a_read_only_view_is_the_way_a_real_capture_is_supplied(tmp_path):
    from amber_mcp.driver import Driver, ShellResult

    def runner(argv):
        cmd = " ".join(argv)
        if "mCurrentFocus" in cmd:
            return ShellResult(0, "  mCurrentFocus=Window{1 u0 com.amberit.dialer/.Agni}\n", "")
        return ShellResult(0, "mCallState=0", "")

    driver = Driver("stub:5555", runner=runner)
    driver._hierarchy_override = '<node text="Dial" resource-id="a:id/d" bounds="[0,0][2,2]" clickable="true"/>'
    import io

    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buf, format="PNG")
    driver._screenshot_override = buf.getvalue()
    # A real Driver validates the PNG tail, so this payload must be a real image:
    # the StubDriver path does not, and a fake here would test the stub.
    try:
        discovery.capture(driver, "x", out_dir=tmp_path)
        raise AssertionError("the full driver was accepted by a read-only capture")
    except TypeError:
        pass
    path = discovery.capture(discovery.read_only(driver), "wrapped", out_dir=tmp_path)
    assert path.name == "wrapped"
