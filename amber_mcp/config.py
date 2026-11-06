"""The handful of facts about this phone and this app that everything else reads.

Values here are measured, not assumed. The activity names come from
`cmd package resolve-activity` and `dumpsys window` on the device, 2026-11-06.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

PACKAGE = "com.amberit.dialer"

# The per-action ceiling: how long one adb action (a `get-state`, a focus read,
# an `input` write) may hang before the driver calls it a failure. The driver's
# reconnect (`host_adb connect`) keeps its own generous timeout; this value is
# what turns two hung reads at 30s each -- a minute a voice assistant cannot
# spend -- into a sentence inside twenty.
ACTION_TIMEOUT_SECONDS = 10.0
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


def artifacts_dir() -> Path:
    return PROJECT_ROOT / "artifacts"


def raw_dir() -> Path:
    return artifacts_dir() / "raw"
