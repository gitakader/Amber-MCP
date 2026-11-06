import tempfile
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


def test_pin_is_never_derived_from_a_file(monkeypatch):
    # The spec forbids the PIN on disk, so this asserts two things that can
    # actually fail: the module exposes no path-shaped name, and REDMI_PIN is
    # the only key the reader ever looks at.
    path_shaped = [
        name
        for name in dir(config)
        if "pin" in name.lower()
        and any(token in name.lower() for token in ("file", "path", "dir"))
    ]
    assert path_shaped == []
    for forbidden in ("pin_file", "PIN_FILE", "secrets_path"):
        assert not hasattr(config, forbidden)

    # Set the environment first: monkeypatch itself looks keys up in os.environ,
    # and that bookkeeping must not land in the recorded query list.
    monkeypatch.setenv("REDMI_PIN", "123456")
    monkeypatch.setenv("AMBER_PHONE_PIN", "999999")

    queried = []
    real_get = config.os.environ.get

    def spy(key, default=None):
        queried.append(key)
        return real_get(key, default)

    monkeypatch.setattr(config.os.environ, "get", spy)

    assert config.pin_from_env() == "123456"
    assert queried == ["REDMI_PIN"]

    # A silent `REDMI_PIN or SOMETHING_ELSE` fallback hides behind the short
    # circuit above, so probe the absence case too.
    monkeypatch.delenv("REDMI_PIN")
    assert config.pin_from_env() is None


def test_raw_dir_is_inside_artifacts_and_not_committed():
    assert config.raw_dir() == config.artifacts_dir() / "raw"
    assert isinstance(config.artifacts_dir(), Path)


# ---------------------------------------------------------------------------
# R2: where a capture goes when nobody says.
#
# `artifacts/` is inside the repository, and a raw capture is the user's real
# call log. The scratch directory exists so that "somewhere to put it" and
# "somewhere git could publish" stop being the same answer.
# ---------------------------------------------------------------------------

_EXPECTED_SYSTEM_FALLBACK = Path(tempfile.gettempdir()) / "amber-captures"


def test_scratch_dir_lands_outside_the_repository(monkeypatch):
    monkeypatch.delenv("AMBER_CAPTURE_DIR", raising=False)
    scratch = config.scratch_dir()
    assert config.PROJECT_ROOT not in scratch.parents, (
        f"the default capture directory {scratch} is inside the repo")
    assert scratch == _EXPECTED_SYSTEM_FALLBACK


def test_scratch_dir_honours_the_environment_override(tmp_path, monkeypatch):
    monkeypatch.setenv("AMBER_CAPTURE_DIR", str(tmp_path / "captures"))
    assert config.scratch_dir() == tmp_path / "captures" / "amber-captures"


def test_a_blank_scratch_override_falls_back_to_the_temp_dir(monkeypatch):
    # Whitespace is truthy, so a bare `env or gettempdir()` would build
    # Path("   ") -- a relative path resolved against the current working
    # directory, which is the repo. Blank means unset here, as it does for every
    # other environment read in this module.
    monkeypatch.setenv("AMBER_CAPTURE_DIR", "   ")
    scratch = config.scratch_dir()
    assert scratch == _EXPECTED_SYSTEM_FALLBACK
    assert scratch.is_absolute()


def test_scratch_dir_creates_itself_on_demand(tmp_path, monkeypatch):
    monkeypatch.setenv("AMBER_CAPTURE_DIR", str(tmp_path / "not" / "made" / "yet"))
    assert config.scratch_dir().is_dir()
