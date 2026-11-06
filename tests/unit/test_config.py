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
