import os
import re
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Real content that must never be committed. The device address is redacted in
# the spec, and PINs must exist only in the environment.
#
# Each rule is (rule id, pattern, label). The id is what the exemption below keys
# on and the label only ever reaches the failure message: an exemption decided by
# comparing labels would move silently onto another rule the first time someone
# reworded one, and a security exemption that widens without anyone choosing it
# is worse than no exemption at all.
PHONE_LABEL = "a phone number"
FORBIDDEN = [
    ("lan-address", re.compile(r"192\.168\.\d{1,3}\.\d{1,3}"), "a LAN address"),
    ("bd-phone-number", re.compile(r"(?<!\d)(?:\+?880|0)1[3-9]\d{8}(?!\d)"), PHONE_LABEL),
    ("device-pin", re.compile(r"REDMI_PIN\s*=\s*\S"), "a PIN value in a file"),
]

# The fourth rule, and the only one that is not a pattern over a whole file.
#
# A captured UI tree is the one artifact this repo publishes *because* it was
# redacted, and redaction only substitutes what it is handed: `capture()` takes
# `secrets=()`, so `<node text="Rabin Bose"/>` passed through byte-for-byte. A
# name matches none of the three rules above, so the gate stayed green over a
# published contact list while `artifacts/SELECTORS.md` promised the opposite in
# prose. Names cannot be pattern-matched -- a name-shaped string is also a button
# label, and the balance of the tree depends on keeping the labels. So the rule is
# structural: a committed capture XML carries *no* literal UI text at all, only
# the tokens `redact` emits. Anything else is the finding.
UI_VALUE_RULE_ID = "unredacted-ui-value"
UI_VALUE_LABEL = "an unredacted UI value in a committed capture"
# Deliberately spelled out rather than imported from `amber_mcp.redact`: a gate
# that reads its own vocabulary out of the module it polices widens the moment
# that module renames a token. `test_the_gate_names_the_redactors_own_tokens`
# below is what keeps the two lists honest instead.
REDACTION_TOKENS = ("<number>", "<balance>", "<name>")
_UI_VALUE = re.compile(r'\b(?:text|content-desc)="([^"]*)"')

SKIP_DIRS = {".venv", ".git", "__pycache__", "node_modules"}
SCAN_SUFFIXES = {".py", ".md", ".toml", ".xml", ".txt", ".json", ".yaml", ".yml"}
# Where Task 6 puts what it captures.
CAPTURE_DIR = "artifacts/"
# The one capture subdirectory that holds un-redacted reality on purpose.
RAW_CAPTURE_DIR = "artifacts/raw/"
UNREADABLE_CAPTURE_LABEL = "a binary capture that no pattern can read"

# `artifacts/raw/` is exempt from the unreadable-capture rule and from nothing
# else, and the exemption is not a sentence this file asserts — it is bought from
# git at run time (see `_ignored_paths`). The reason for the tradeoff:
# `.gitignore:9` sanctions `artifacts/raw/` as the drawer for captures that
# cannot be redacted (screenshots) and must not be lost (a hierarchy exactly as
# the phone wrote it). Treating those files as offenders would keep `pytest` red
# for the whole duration of capture work, and a permanently red suite teaches
# people to bypass the gate, which is the failure mode this repo cannot survive.
#
# What is being accepted, stated plainly: the ignored files in that directory
# hold the user's real account — contacts, numbers, balance. `git add -f` /
# `git add -A` on them publishes all of it, and no rule in this file can stop a
# force-add. So the exemption covers *git cannot see it* and never *this tree is
# safe to commit*: the pattern rules below still apply to every readable file in
# `raw/`, and a capture is deleted, not committed, when discovery finishes.
#
# Task 6's fix round went further than this exemption and made it much less
# important: `capture()` now refuses an `out_dir` inside the repository at all,
# so raw data has somewhere else to be and never arrives here to be argued about.
# The exemption stays as defence in depth for a capture somebody did put here.
RAW_EXEMPT_RULE_IDS = frozenset({"unreadable-capture"})


def _ignored_paths(paths: list[str]) -> frozenset[str]:
    """Which of these paths git really ignores, asked of git itself.

    Fail-closed by construction, in both ways it can fail. `check-ignore` exits
    0 when at least one path is ignored, 1 when none is, and 128 when it could
    not answer at all (no repo); and when git is not on PATH at all there is no
    exit code -- `subprocess.run` raises `FileNotFoundError` before one exists,
    which is the ordinary state of a GUI-launched host. Both paths return the
    empty set, so a broken or absent git hands the raw captures back to the
    unreadable-capture rule instead of quietly releasing them. Anything here that
    ever reads "no findings" because git was unwell is a gate that went blind on
    a Sunday.

    `-z` and bytes, deliberately. Passing text with `text=True` makes Python
    rewrite every LF as CRLF on Windows, git echoes the CR back inside the path,
    and every line but the last stops matching -- which reads as "not ignored"
    and turns the exemption off for the second screen of a real capture run. Measured
    before the fix: `{'"artifacts/raw/probe-dashboard/screen.png\\\\r"', 'artifacts/raw/_gate_probe/shot.png'}`.
    """
    if not paths:
        return frozenset()
    payload = b"\0".join(p.encode("utf-8", "surrogateescape") for p in paths) + b"\0"
    try:
        done = subprocess.run(
            ["git", "check-ignore", "--stdin", "-z"],
            cwd=ROOT, input=payload, capture_output=True,
        )
    except FileNotFoundError:
        return frozenset()
    if done.returncode not in (0, 1):
        return frozenset()
    ignored = {p.decode("utf-8", "replace") for p in (done.stdout or b"").split(b"\0") if p}
    # `-z` prints a NUL after each path, so the tail is empty; keep only answers
    # that name one of the paths we asked about, never a path git invented.
    return frozenset(ignored) & frozenset(paths)


def _check_ignore_reports(relative: str) -> tuple[int, str]:
    """rc and rule line from `git check-ignore -v`, for the proof test."""
    done = subprocess.run(
        ["git", "check-ignore", "-v", relative], cwd=ROOT, capture_output=True, text=True,
    )
    return done.returncode, (done.stdout or "").strip()

# Tests must contain number-shaped strings to prove the redactor works, and this
# plan quotes those tests inside docs/. Both are illustrative, so the number rule
# cannot apply to them. The gate exists to catch captured DEVICE data, which only
# ever lands in artifacts/ and amber_mcp/ — that is where it looks hard.
# The exemption is for ONE rule, chosen by id, and for the LAN address and the
# PIN there is no exemption anywhere in the tree.
# Residual risk, stated rather than hidden: a real number pasted into a markdown
# page is not caught. Task 7 step 6 re-runs this gate on every committed capture,
# which is the actual protection for the data that gets produced.
NUMBER_RULE_EXEMPT = ("tests/", "docs/")
EXEMPT_RULE_IDS = frozenset({"bd-phone-number"})


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


def _files_to_scan() -> list[Path]:
    """Git's list, plus the two files it is structurally unable to name.

    `.env` is gitignored, so `git ls-files` never reports it -- which is exactly
    why it needs reading: a dotfile at the repo root is where a human writes the
    PIN down. Binary captures are picked up the same way, because a screenshot
    lands in artifacts/ untracked and long predates any `git add`.
    """
    found = list(_tracked_files())
    for dir_path, dir_names, file_names in os.walk(ROOT):
        dir_names[:] = sorted(d for d in dir_names if d not in SKIP_DIRS)
        relative_dir = Path(dir_path).relative_to(ROOT).as_posix()
        under_capture = relative_dir == CAPTURE_DIR.rstrip("/") or (
            relative_dir.startswith(CAPTURE_DIR))
        for name in file_names:
            if name.startswith(".") or under_capture:
                found.append(Path(dir_path) / name)
    return found


def _is_committed_capture_xml(relative: str) -> bool:
    """Is this an XML the repository could publish, outside the raw drawer?

    `raw/` is handled by its own predicate below rather than by
    `RAW_EXEMPT_RULE_IDS`, which still names only `unreadable-capture`: every
    exemption already in this file is left exactly as it was, and a fourth rule
    does not travel on the back of a list it was never added to.
    """
    return (relative.startswith(CAPTURE_DIR)
            and not relative.startswith(RAW_CAPTURE_DIR)
            and relative.lower().endswith(".xml"))


def _is_raw_capture_xml(relative: str) -> bool:
    """The same shape inside the drawer, where un-redacted reality belongs.

    The raw exemption for this rule is *bought*, not asserted: like the
    unreadable-capture rule, a raw file becomes a finding unless git proves the
    path cannot be committed (see `_offenders`' batched `check-ignore`).
    """
    return relative.startswith(RAW_CAPTURE_DIR) and relative.lower().endswith(".xml")


def _unredacted_ui_values(body: str) -> list[str]:
    """Every `text=`/`content-desc=` value that is not made of redaction tokens.

    A token may appear escaped (`&lt;number&gt;`, which is how `redact.xml` writes
    one into an attribute) or bare, mixed with untouched characters. Stripping
    the tokens and what is left is whitespace is the only pass.
    """
    offenders = []
    for value in _UI_VALUE.findall(body):
        residue = value
        for token in REDACTION_TOKENS:
            residue = residue.replace(token, "").replace(
                token.replace("<", "&lt;").replace(">", "&gt;"), "")
        if residue.strip():
            offenders.append(value)
    return offenders


def _offenders(forbidden=None) -> list[str]:
    """Every "file: reason" the gate objects to.

    `forbidden` defaults to the module rules *at call time*, so a test can hand in
    a variant of the rule table without editing this file on disk.
    """
    if forbidden is None:
        forbidden = FORBIDDEN
    hits = set()
    # Raw captures that only the ignore rule can excuse, held back until git has
    # actually been asked (one batched call, after the walk).
    pending_raw: list[str] = []
    # Raw hierarchies holding literal UI text are the same bargain: the drawer is
    # where un-redacted reality is *supposed* to sit, so long as git cannot see it.
    pending_raw_ui: list[str] = []
    for path in _files_to_scan():
        if not path.is_file():
            continue
        if set(path.parts) & SKIP_DIRS:
            continue
        relative = path.relative_to(ROOT).as_posix()
        scannable = path.suffix.lower() in SCAN_SUFFIXES
        if not scannable:
            # A screenshot of a logged-in home screen cannot be redacted, so its
            # mere presence under artifacts/ is the finding. Over-reporting here
            # is correct: the alternative is a committed pixel dump of a balance.
            if relative.startswith(CAPTURE_DIR):
                if "unreadable-capture" in RAW_EXEMPT_RULE_IDS and relative.startswith(RAW_CAPTURE_DIR):
                    # Held back, not forgiven: it becomes a finding unless git
                    # proves it cannot be committed.
                    pending_raw.append(relative)
                else:
                    hits.add((relative, "unreadable-capture", UNREADABLE_CAPTURE_LABEL))
            # A dotfile is read regardless of suffix: `.env` has none, which is
            # how the old filter lost the PIN.
            if not path.name.startswith("."):
                continue
        body = path.read_text(encoding="utf-8", errors="ignore")
        if _unredacted_ui_values(body):
            if _is_committed_capture_xml(relative):
                hits.add((relative, UI_VALUE_RULE_ID, UI_VALUE_LABEL))
            elif _is_raw_capture_xml(relative):
                # Held back on the same evidence as a raw screenshot: git's own
                # answer, asked once below, not this file's assurance that the
                # drawer is ignored.
                pending_raw_ui.append(relative)
        for rule_id, pattern, label in forbidden:
            if pattern.search(body):
                if rule_id in EXEMPT_RULE_IDS and relative.startswith(NUMBER_RULE_EXEMPT):
                    continue
                hits.add((relative, rule_id, label))
    # One batched call, here at the end -- where the comment above has always
    # said it belonged. Asking per file costs a git subprocess per pending raw
    # capture, and on a real run of several screens that is the whole drawer.
    pending = sorted(set(pending_raw) | set(pending_raw_ui))
    ignored = _ignored_paths(pending)
    for relative in set(pending_raw) - ignored:
        hits.add((relative, "unreadable-capture", UNREADABLE_CAPTURE_LABEL))
    for relative in set(pending_raw_ui) - ignored:
        hits.add((relative, UI_VALUE_RULE_ID, UI_VALUE_LABEL))
    return [f"{where}: {why}" for where, _, why in sorted(hits)]


def test_no_tracked_file_holds_a_number_address_or_pin():
    offenders = _offenders()
    assert not offenders, "commit these would publish real account data:\n" + "\n".join(offenders)


# ---------------------------------------------------------------------------
# Teeth. A gate that cannot fail on the file most likely to hold the secret is
# decoration, so each test below plants a real offender on disk, reads what the
# gate reports, and restores the working tree afterwards.
#
# The LAN address and the PIN rule have no exemption in any directory, so the
# shapes planted below are assembled from pieces: this file is in tests/ and a
# complete PIN assignment written in it would be flagged by the very gate it is
# testing. That is the rule working, not a bypass -- a real leaked PIN sitting in
# a test fixture would still be caught, because nothing about the pattern knows
# that this file is a test.
# ---------------------------------------------------------------------------

PHONE_SHAPE = "01712" + "345678"
LAN_SHAPE = "192.168." + "12.34"
PIN_SHAPE = 'REDMI_PIN' + "=" + "12345"

# An ordinary file at the repo root: outside the exempt tree, with a suffix the
# gate already reads. `docs/_gate_probe.md` is the same shapes *inside* the tree
# the phone rule is exempted in.
ROOT_PROBE = "gate_leak_probe.txt"
EXEMPT_PROBE = "docs/_gate_probe.md"


@contextmanager
def _planted(relative: str, body):
    """Put `body` at `relative`, then put the working tree back as it was."""
    path = ROOT / relative
    existed = path.is_file()
    backup = path.read_bytes() if existed else None
    made_dirs = []
    parent = path.parent
    while parent != ROOT and not parent.exists():
        made_dirs.append(parent)
        parent = parent.parent
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body if isinstance(body, bytes) else body.encode("utf-8"))
    try:
        yield path
    finally:
        if existed:
            path.write_bytes(backup)
        else:
            path.unlink()
        for directory in reversed(made_dirs):
            if not any(directory.iterdir()):
                directory.rmdir()


def _hits_for(offenders: list[str], relative: str) -> list[str]:
    return [o for o in offenders if o.startswith(f"{relative}:")]


def _rules_relabelled_to(new_label: str) -> list:
    """Every rule, same order and same patterns, with its human label replaced.

    A label is documentation. If the exemption decision reads labels, relabelling
    the PIN rule as "a phone number" smuggles PINs into the exempt tree.
    """
    return [(*rule[:-1], new_label) for rule in FORBIDDEN]


def test_the_gate_fails_on_a_planted_number_address_and_pin():
    # Every rule has to bite, so all three are planted in one ordinary file.
    with _planted(ROOT_PROBE, f"contact {PHONE_SHAPE} device {LAN_SHAPE} {PIN_SHAPE}\n"):
        offenders = _hits_for(_offenders(), ROOT_PROBE)
        assert len(offenders) == 3, f"the gate stayed green over a planted leak: {offenders}"


def test_the_gate_fails_on_the_dotfile_git_cannot_see():
    # A gitignored dotfile is invisible to `git ls-files -co --exclude-standard`,
    # and `Path(".env").suffix` is empty, so the old suffix filter lost it twice.
    # A PIN written there is the likeliest leak in this repo.
    with _planted(".env", f"AMBER_PHONE_SERIAL=abc\n{PIN_SHAPE}\n"):
        offenders = _offenders()
        assert ".env: a PIN value in a file" in offenders, (
            f"a dotfile holding the PIN read as clean: {offenders}")


def test_the_gate_fails_on_a_binary_capture_it_cannot_read():
    # A screenshot of a logged-in home screen cannot be redacted, so its presence
    # under artifacts/ is itself the finding -- over-reporting there is correct.
    with _planted("artifacts/whatever.png", b"\x89PNG\r\n\x1a\n\x00\xff\x01unreadable"):
        offenders = _offenders()
        assert "artifacts/whatever.png: a binary capture that no pattern can read" in offenders, (
            f"a binary capture under artifacts/ read as clean: {offenders}")


def test_renaming_a_rule_label_cannot_widen_the_exemption(monkeypatch):
    # The documented exemption covers the phone rule only, in tests/ and docs/,
    # because illustrative fixtures have to carry number-shaped strings. It keys
    # on rule identity, so relabelling every rule "a phone number" must leave the
    # PIN and the LAN address in the exempt tree reported anyway.
    monkeypatch.setattr(
        sys.modules[__name__], "FORBIDDEN", _rules_relabelled_to(PHONE_LABEL))
    with _planted(EXEMPT_PROBE, f"{PIN_SHAPE} on {LAN_SHAPE}\n"):
        offenders = _hits_for(_offenders(), EXEMPT_PROBE)
        assert len(offenders) == 2, (
            "a renamed label moved the phone rule's exemption onto the PIN and "
            f"LAN rules, which read as clean: {offenders}")


# ---------------------------------------------------------------------------
# R1: the raw-capture exemption, and the proof that pays for it.
#
# `artifacts/raw/` is where Task 6 keeps captures that cannot be redacted. The
# unreadable-capture rule would otherwise flag every one of them, and the suite
# would stay red for the length of the discovery work. The exemption is granted
# only for as long as git is demonstrated to ignore the exact path, so it is
# conditional on evidence rather than on this file's say-so.
# ---------------------------------------------------------------------------

RAW_PROBE = "artifacts/raw/_gate_probe/shot.png"
# The same shape one directory up: outside the sanctioned drawer, so the ordinary
# capture rule still owns it.
OUTSIDE_RAW_PROBE = "artifacts/_gate_probe/shot.png"


def test_git_actually_ignores_the_raw_capture_drawer():
    # The exemption's whole justification is `.gitignore`'s `artifacts/raw/`
    # rule, so this asserts the rule bites on a path inside it and names the
    # line that does the work. Delete that line and this test fails, and the
    # exemption below stops applying two tests later -- it never goes silent.
    rc, report = _check_ignore_reports("artifacts/raw/anything/hierarchy.xml")
    assert rc == 0, (
        "git does not ignore artifacts/raw/, so the raw-capture exemption in this "
        "file is unsupported: un-redacted captures are committable.")
    assert "artifacts/raw/" in report, f"unexpected ignore rule cited: {report}"


def test_the_raw_drawer_is_exempt_from_the_unreadable_capture_rule_only():
    with _planted(RAW_PROBE, b"\x89PNG\r\n\x1a\n\x00\xff\x01unredacted pixels"):
        offenders = _hits_for(_offenders(), RAW_PROBE)
        assert offenders == [], f"a raw capture was reported despite the ignore rule: {offenders}"
    # ...and a byte-identical file outside the drawer is still a finding.
    with _planted(OUTSIDE_RAW_PROBE, b"\x89PNG\r\n\x1a\n\x00\xff\x01unredacted pixels"):
        offenders = _hits_for(_offenders(), OUTSIDE_RAW_PROBE)
        assert offenders == [f"{OUTSIDE_RAW_PROBE}: {UNREADABLE_CAPTURE_LABEL}"], (
            f"the raw exemption reached outside artifacts/raw/: {offenders}")


def test_the_raw_exemption_holds_for_every_capture_in_the_drawer_not_just_one():
    # A batched `git check-ignore` that mangles all but the last path -- which is
    # what a CRLF-translated --stdin does on Windows -- exempts one screen and
    # reports the rest, so the suite goes red in the middle of real capture work
    # and the rule it trips is the one that was ruled away. Two files, both
    # branches of the drawer, must be silent.
    second = "artifacts/raw/_gate_probe_two/shot.png"
    with _planted(RAW_PROBE, b"\x89PNG\r\n\x1a\n\x00\xff\x01unredacted pixels"):
        with _planted(second, b"\x89PNG\r\n\x1a\n\x00\xff\x01unredacted pixels"):
            offenders = _offenders()
            reported = [o for o in offenders
                        if o.startswith(f"{RAW_PROBE}:") or o.startswith(f"{second}:")]
            assert reported == [], (
                f"the raw exemption only covered part of the batch: {reported}")


def test_the_raw_exemption_is_withdrawn_the_moment_git_stops_ignoring_it(monkeypatch):
    # This is the difference between an exemption and an assertion. With git
    # answering "nothing here is ignored" -- a deleted rule, a rewritten
    # .gitignore, a checkout outside a repo -- the same file must be reported.
    monkeypatch.setattr(sys.modules[__name__], "_ignored_paths", lambda paths: frozenset())
    with _planted(RAW_PROBE, b"\x89PNG\r\n\x1a\n\x00\xff\x01unredacted pixels"):
        offenders = _hits_for(_offenders(), RAW_PROBE)
        assert offenders == [f"{RAW_PROBE}: {UNREADABLE_CAPTURE_LABEL}"], (
            f"raw captures read as clean without git's proof: {offenders}")


def test_the_raw_drawer_is_not_exempt_from_reading_for_numbers():
    # R1 buys silence about *unreadable* files. A raw hierarchy.xml is readable,
    # so the account rules apply to it exactly as they apply anywhere else in the
    # tree. Stating it as a test is what keeps the exemption from being read as
    # a general pardon for anything under artifacts/raw/.
    relative = "artifacts/raw/_gate_probe/hierarchy.xml"
    with _planted(relative, f'<node text="{PHONE_SHAPE}"/>\n'):
        offenders = _hits_for(_offenders(), relative)
        assert offenders == [f"{relative}: {PHONE_LABEL}"], (
            f"a readable raw capture evaded the number rule: {offenders}")


# ---------------------------------------------------------------------------
# Minors folded into fix round 1.
# ---------------------------------------------------------------------------


def test_the_ignore_check_is_one_batched_call_and_not_one_per_file(monkeypatch):
    # The comment above `_offenders` promises a single batched call, and a git
    # subprocess per pending raw file is what the loop actually did: with two
    # captures outstanding that is two spawns of the same question.
    calls = []

    def spy(paths):
        calls.append(sorted(paths))
        return frozenset(paths)

    monkeypatch.setattr(sys.modules[__name__], "_ignored_paths", spy)
    second = "artifacts/raw/_gate_probe_two/shot.png"
    with _planted(RAW_PROBE, b"\x89PNG\r\n\x01unredacted pixels"):
        with _planted(second, b"\x89PNG\r\n\x02unredacted pixels"):
            assert _offenders() == []
    assert len(calls) == 1, f"git was asked once per file, not once: {calls}"
    assert calls[0] == sorted([RAW_PROBE, second]), (
        f"the batched call did not carry the whole drawer: {calls}")


def test_an_absent_git_binary_answers_the_ignore_check_with_nothing(monkeypatch):
    # The docstring has always claimed that an absent git fails closed. `git` is
    # not on PATH in a GUI-launched host, where subprocess.run raises
    # FileNotFoundError before any return code exists, and that is not a 128.
    real_run = subprocess.run

    def no_git(argv, *args, **kwargs):
        raise FileNotFoundError(2, "The system cannot find the file specified", argv[0])

    monkeypatch.setattr(subprocess, "run", no_git)
    assert _ignored_paths([RAW_PROBE]) == frozenset()


def test_an_absent_git_binary_withdraws_the_raw_exemption(monkeypatch):
    # Failing closed has to mean the file is reported, not that the suite errors.
    real_run = subprocess.run

    def check_ignore_is_unavailable(argv, *args, **kwargs):
        if argv[:2] == ["git", "check-ignore"]:
            raise FileNotFoundError(2, "The system cannot find the file specified", argv[0])
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", check_ignore_is_unavailable)
    with _planted(RAW_PROBE, b"\x89PNG\r\n\x03unredacted pixels"):
        offenders = _hits_for(_offenders(), RAW_PROBE)
        assert offenders == [f"{RAW_PROBE}: {UNREADABLE_CAPTURE_LABEL}"], (
            f"a missing git read as an exemption: {offenders}")


def test_the_ignore_rule_the_comments_cite_is_the_line_that_does_the_work():
    # Both comments in this file point at a `.gitignore` line number. Pin the
    # citation so the comment cannot rot into a plausible wrong answer again.
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert lines[8].strip() == "artifacts/raw/", "the rule is on line 9, not line 8"


# ---------------------------------------------------------------------------
# The fourth rule: a committed capture XML carries no literal UI text.
#
# `capture()` withholds the hierarchy by default now, but a default is only a
# default: one `include_hierarchy=True`, or one XML saved by hand from a raw
# capture, and the contact names are back in the public tree -- where the three
# pattern rules cannot see them, because a name is not a number, an address or a
# PIN. So the promise `artifacts/SELECTORS.md` makes in prose is decided here.
# ---------------------------------------------------------------------------

LEAKY_XML = ('<hierarchy>\n'
             '  <node text="Some One" resource-id="com.amberit.dialer:id/name_tv"/>\n'
             '</hierarchy>\n')
# What a redacted capture that *was* given its secrets looks like, escaped the way
# `redact.xml` writes it: the same shape, only tokens, and clean.
CLEAN_XML = ('<hierarchy>\n'
             '  <node text="&lt;number&gt;" content-desc="&lt;name&gt;"/>\n'
             '  <node text="" resource-id="com.amberit.dialer:id/name_tv"/>\n'
             '</hierarchy>\n')

LEAKY_PROBE = "artifacts/leaky.xml"
LEAKY_RAW_PROBE = "artifacts/raw/_gate_probe/leaky.xml"


def test_the_gate_fails_on_a_name_in_a_committed_capture_xml():
    with _planted(LEAKY_PROBE, LEAKY_XML):
        offenders = _hits_for(_offenders(), LEAKY_PROBE)
        assert offenders == [f"{LEAKY_PROBE}: {UI_VALUE_LABEL}"], (
            f"a committed hierarchy holding a name read as clean: {offenders}")


def test_a_token_only_committed_capture_is_clean():
    # The rule forbids literal UI text, not `text=` attributes: a redacted capture
    # whose values are the tokens `redact` emits is the artifact this repo exists
    # to publish, and ids, classes and bounds survive it untouched.
    with _planted(LEAKY_PROBE, CLEAN_XML):
        offenders = _hits_for(_offenders(), LEAKY_PROBE)
        assert offenders == [], f"a fully tokenised capture was reported: {offenders}"


def test_a_committed_hierarchy_with_a_stripped_leading_plus_still_bites():
    # Measured in SELECTORS.md: the deep link arrives with the `+` removed, and a
    # reviewer's eye slides off `text="+8801712345678"` as a number the phone rule
    # owns. Inside artifacts/ it is also a literal UI value, so both rules fire on
    # it and neither one can be the excuse for the other.
    with _planted(LEAKY_PROBE, f'<node text="{PHONE_SHAPE}"/>\n'):
        offenders = _hits_for(_offenders(), LEAKY_PROBE)
        assert f"{LEAKY_PROBE}: {UI_VALUE_LABEL}" in offenders, offenders
        assert len(offenders) == 2, f"only one of the two rules fired: {offenders}"


def test_the_raw_drawer_is_not_a_committed_capture_for_this_rule_either():
    # The same bytes, in the drawer git is proven to ignore, are the point of the
    # drawer: raw must keep reality or Task 7 cannot read the selectors out of it.
    # The raw exemption is bought from `git check-ignore`, exactly as it is for the
    # unreadable-capture rule, so if git stops ignoring the path the file becomes
    # a finding for this rule too -- checked in the test below.
    with _planted(LEAKY_RAW_PROBE, LEAKY_XML):
        offenders = _hits_for(_offenders(), LEAKY_RAW_PROBE)
        assert offenders == [], f"the raw copy was reported for holding names: {offenders}"


def test_the_ui_value_rule_is_withdrawn_from_raw_only_by_git_proof(monkeypatch):
    # An exemption that survives the loss of its evidence is a hole. With git
    # answering "nothing here is ignored", the raw hierarchy is a committable file
    # holding a contact name, and it must be named.
    monkeypatch.setattr(sys.modules[__name__], "_ignored_paths", lambda paths: frozenset())
    with _planted(LEAKY_RAW_PROBE, LEAKY_XML):
        offenders = _hits_for(_offenders(), LEAKY_RAW_PROBE)
        assert f"{LEAKY_RAW_PROBE}: {UI_VALUE_LABEL}" in offenders, (
            f"raw kept its exemption without git's proof: {offenders}")


def test_the_gate_names_the_redactors_own_tokens():
    # The token list above is copied on purpose (see `REDACTION_TOKENS`), so the
    # copy is pinned: rename a token in `redact` and this fails, rather than the
    # gate quietly accepting a value nothing emits any more.
    from amber_mcp import redact

    assert set(REDACTION_TOKENS) == {redact.NUMBER_TOKEN, redact.BALANCE_TOKEN,
                                     redact.NAME_TOKEN}


def test_the_number_rule_is_exempt_in_exactly_two_trees_and_for_exactly_one_rule():
    # `NUMBER_RULE_EXEMPT` and `EXEMPT_RULE_IDS` are the widest thing in this
    # file, and until now nothing pinned their values: adding `"artifacts/"` to
    # the first, or `"lan-address"` to the second, is a one-line diff that makes
    # the gate blind where captured data actually lands, and every test in this
    # file would stay green doing it.
    assert NUMBER_RULE_EXEMPT == ("tests/", "docs/"), (
        "the number exemption's reach changed; it exists for illustrative fixtures "
        "in tests/ and the plan text that quotes them, and for nothing else")
    assert EXEMPT_RULE_IDS == frozenset({"bd-phone-number"}), (
        "the LAN address and the PIN have no exemption anywhere in the tree")
    assert CAPTURE_DIR not in NUMBER_RULE_EXEMPT and RAW_CAPTURE_DIR not in NUMBER_RULE_EXEMPT


def test_a_number_in_the_capture_tree_is_reported():
    # The consequence the pin above guards: `artifacts/` is where captures land,
    # so it must not sit inside the number exemption. Widen the tuple and this is
    # the test that goes red.
    relative = "artifacts/_gate_probe/notes.md"
    with _planted(relative, f"called {PHONE_SHAPE} on the dashboard\n"):
        offenders = _hits_for(_offenders(), relative)
        assert offenders == [f"{relative}: {PHONE_LABEL}"], (
            f"the number exemption reached the capture tree: {offenders}")
