"""Process-env isolation for every test in this directory.

`adb.publish()` writes real process environment: it sets ``ADBUTILS_ADB_PATH``
with a plain ``os.environ`` assignment, and Task 2's suite calls it through
``adb.adb_path()`` while pointing an override at a ``tmp_path`` binary. pytest
undoes only the changes a test makes through its own ``monkeypatch``; an env
write the code performs on its own survives the teardown and stays set for the
rest of the pytest process -- and the tmp directory is deleted when that test
ends. Measured without this fixture:

    uv run pytest tests/unit/test_adb.py tests/unit/test_zz_probe.py
    PROBE ADBUTILS_ADB_PATH = '...pytest-9\\test_publishes_the_same_binary0\\adb.exe'

That is a path that no longer exists, inherited by whichever test runs next. A
driver test that then resolves the adb binary would read this value and pass for
a reason unrelated to what it asserts -- the worst outcome available to a test of
a silent-failure guard. So both knobs are cleared before every test, in every
file, regardless of order.
"""

import pytest

ADB_ENV_VARS = ("ADBUTILS_ADB_PATH", "AMBER_MCP_ADB_PATH")


@pytest.fixture(autouse=True)
def _isolated_adb_env(monkeypatch):
    """Clear the adb-resolution knobs so no test can inherit another's answer."""
    for name in ADB_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
