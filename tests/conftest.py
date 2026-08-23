"""Shared fixtures.

Two jobs, both about keeping the developer's own machine out of the assertions: let tests read what a
setting *defaults to*, and stop any test writing where the real settings live.
"""

import importlib

import pytest


@pytest.fixture(autouse=True)
def isolated_setting_fallback(tmp_path, monkeypatch):
    """Point ``config``'s fallback file somewhere disposable, for **every** test.

    Autouse, and not optional. ``store_setting`` writes to a file when the credential vault cannot be
    verified, and a test that stubs the vault produces exactly that condition -- so the moment the
    fallback existed, the suite began writing fake provider keys and toggle values into the real
    ``~/.nimbus/settings.dat``. It did: a full run left twenty-five entries there, including
    ``OPENAI_API_KEY``, and three unrelated shell tests then failed because ``NAV_SIDE`` had changed
    underneath them.

    That is worse than a red test. A suite that edits the machine's own configuration corrupts the
    thing it is meant to be checking, and the failures surface somewhere else entirely.

    Redirected through the environment rather than by patching ``config._fallback_path``, because
    ``first_run_config`` reloads ``config`` and a reload restores every attribute on the module. A
    patched function therefore reverted halfway through those tests and the writes went to the real
    file anyway, which is how this was still happening after the first attempt at fixing it.
    """
    monkeypatch.setenv("NIMBUS_SETTINGS_FALLBACK", str(tmp_path / "settings.dat"))
    yield


EXPERIMENTAL_SETTING_NAMES = (
    "CODE_EXECUTION", "SEARCH_GROUNDING", "AGENTIC_VISION", "GEMINI_LIVE",
    "GROUNDING_REFINEMENT", "KB_CACHE",
)


@pytest.fixture
def first_run_config(monkeypatch):
    """``config`` as a first-run machine sees it: no env vars, empty keyring.

    Reading ``config.AGENTIC_VISION`` directly tests the *machine*, not the code. Every
    experimental setting resolves env -> keyring -> default once at import, and
    ``resolve_setting`` writes any env value it finds back into the keyring. So the
    moment a user flips a toggle in the Settings dialog -- which is the entire point of
    the experimental group -- the value persists and a naive `== "off"` assertion turns
    the suite red on a perfectly healthy build. That happened, which is why this exists.

    Reloading under a cleared environment asserts the *declared* default instead. The
    teardown reload restores the real resolved values so no later test in the session
    inherits a stubbed config.
    """
    import config as config_module

    for name in EXPERIMENTAL_SETTING_NAMES:
        monkeypatch.delenv(name, raising=False)
    # Patch on the keyring module itself: config.py holds a module reference, so
    # patching the attribute there would not cover other readers during the reload.
    monkeypatch.setattr("keyring.get_password", lambda *a, **k: None)
    monkeypatch.setattr("keyring.set_password", lambda *a, **k: None)

    yield importlib.reload(config_module)

    # Undo before reloading so the restoring import sees the real keyring again.
    monkeypatch.undo()
    importlib.reload(config_module)
