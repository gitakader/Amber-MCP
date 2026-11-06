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
SKIP_DIRS = {".venv", ".git", "__pycache__", "node_modules"}
SCAN_SUFFIXES = {".py", ".md", ".toml", ".xml", ".txt", ".json", ".yaml", ".yml"}
# Where Task 6 puts what it captures.
CAPTURE_DIR = "artifacts/"
UNREADABLE_CAPTURE_LABEL = "a binary capture that no pattern can read"

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


def _offenders(forbidden=None) -> list[str]:
    """Every "file: reason" the gate objects to.

    `forbidden` defaults to the module rules *at call time*, so a test can hand in
    a variant of the rule table without editing this file on disk.
    """
    if forbidden is None:
        forbidden = FORBIDDEN
    hits = set()
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
                hits.add((relative, "unreadable-capture", UNREADABLE_CAPTURE_LABEL))
            # A dotfile is read regardless of suffix: `.env` has none, which is
            # how the old filter lost the PIN.
            if not path.name.startswith("."):
                continue
        body = path.read_text(encoding="utf-8", errors="ignore")
        for rule_id, pattern, label in forbidden:
            if pattern.search(body):
                if rule_id in EXEMPT_RULE_IDS and relative.startswith(NUMBER_RULE_EXEMPT):
                    continue
                hits.add((relative, rule_id, label))
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
