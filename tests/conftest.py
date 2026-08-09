"""Shared fixtures.

Currently one job: let tests assert what a setting *defaults to* without accidentally
asserting what the developer's own machine happens to be configured for.
"""

import importlib

import pytest


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
