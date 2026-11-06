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
