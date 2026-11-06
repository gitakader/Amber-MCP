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


def test_search_dirs_covers_the_windows_sdk_location_specifically(tmp_path, monkeypatch):
    # adb here lives in %LOCALAPPDATA%\Android\Sdk\platform-tools, which is not on
    # PATH. Asserting only that "some" platform-tools dir appears would pass on a
    # mac too, so pin LOCALAPPDATA and require the entry that comes from it.
    fake_local = tmp_path / "AppData"
    (fake_local / "Android" / "Sdk" / "platform-tools").mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(fake_local))
    expected = os.path.join(str(fake_local), "Android", "Sdk", "platform-tools")
    assert expected in adb.search_dirs()


def test_no_adb_anywhere_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("AMBER_MCP_ADB_PATH", raising=False)
    monkeypatch.delenv("ADBUTILS_ADB_PATH", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("ANDROID_HOME", str(tmp_path))
    monkeypatch.delenv("ANDROID_SDK_ROOT", raising=False)
    with pytest.raises(adb.AdbNotFoundError):
        adb.adb_path()
