"""Unit tests for config.resolve_api_key — the env→keyring resolver.

The function is the load-bearing piece of 's BYOK migration:
on launch with .env present, keys auto-write to keyring as backup;
on launch without .env, keys read from keyring transparently.

Tests mock keyring's get_password / set_password so we don't touch
the real Windows Credential Manager during CI.
"""
from __future__ import annotations

import pytest


KEY = "ANTHROPIC_API_KEY"  # any of the three keys; semantics identical


@pytest.fixture
def fake_keyring(monkeypatch):
    """In-memory dict-backed mock for keyring's three functions.

    Yields the dict so tests can pre-populate or assert on what got
    written. Mock is scoped to this test only — module-level keyring
    import in config.py is patched on the keyring module itself.

    ``delete_password`` was missing here, and its absence was not harmless: code under test that
    deleted a setting reached the **real Windows Credential Manager**, so the fake store still held
    the value, the assertion failed for the wrong reason, and the developer's own vault was edited by
    a test run. It raises ``PasswordDeleteError`` on a missing entry, as the real one does, so code
    that swallows that is exercised honestly rather than against a forgiving stub.
    """
    store: dict[tuple[str, str], str] = {}

    def fake_get(service, name):
        return store.get((service, name))

    def fake_set(service, name, value):
        store[(service, name)] = value

    def fake_delete(service, name):
        import keyring.errors

        if (service, name) not in store:
            raise keyring.errors.PasswordDeleteError(name)
        del store[(service, name)]

    import config
    monkeypatch.setattr(config.keyring, "get_password", fake_get)
    monkeypatch.setattr(config.keyring, "set_password", fake_set)
    monkeypatch.setattr(config.keyring, "delete_password", fake_delete)
    yield store


class TestResolveApiKey:
    """resolve_api_key returns env first, keyring second, None last —
    and on env-present, ALSO migrates the value into keyring."""

    def test_returns_env_value_when_present(self, monkeypatch, fake_keyring):
        monkeypatch.setenv(KEY, "sk-from-env")
        from config import resolve_api_key
        assert resolve_api_key(KEY) == "sk-from-env"

    def test_migrates_env_to_keyring_on_resolve(
        self, monkeypatch, fake_keyring
    ):
        """When env is present, the value MUST also land in keyring.
        This is the one-shot migration so the user can later delete
        .env without losing the key."""
        monkeypatch.setenv(KEY, "sk-migrate-me")
        from config import resolve_api_key, KEYRING_SERVICE
        resolve_api_key(KEY)
        assert fake_keyring[(KEYRING_SERVICE, KEY)] == "sk-migrate-me"

    def test_falls_back_to_keyring_when_env_absent(
        self, monkeypatch, fake_keyring
    ):
        """No env var → read from keyring."""
        monkeypatch.delenv(KEY, raising=False)
        from config import resolve_api_key, KEYRING_SERVICE
        fake_keyring[(KEYRING_SERVICE, KEY)] = "sk-from-keyring"
        assert resolve_api_key(KEY) == "sk-from-keyring"

    def test_returns_none_when_neither_source_has_value(
        self, monkeypatch, fake_keyring
    ):
        """First-launch state: no env, empty keyring → None.
        The settings dialog gate uses this to decide whether to show."""
        monkeypatch.delenv(KEY, raising=False)
        from config import resolve_api_key
        assert resolve_api_key(KEY) is None

    def test_keyring_set_failure_does_not_block_env_path(
        self, monkeypatch
    ):
        """If keyring backend is unavailable (vault locked, no service
        registered, etc.), set_password raising must NOT prevent the
        env-var path from returning the user's value. The user has a
        valid .env — credential-store glitches shouldn't fail startup."""
        monkeypatch.setenv(KEY, "sk-env-survives")

        def boom(*_args, **_kwargs):
            raise RuntimeError("simulated keyring failure")

        import config
        monkeypatch.setattr(config.keyring, "set_password", boom)
        from config import resolve_api_key
        assert resolve_api_key(KEY) == "sk-env-survives"

    def test_keyring_get_failure_returns_none_no_raise(
        self, monkeypatch
    ):
        """Keyring read errors swallowed → caller sees None and shows
        the settings dialog. No traceback up to main."""
        monkeypatch.delenv(KEY, raising=False)

        def boom(*_args, **_kwargs):
            raise RuntimeError("simulated keyring read failure")

        import config
        monkeypatch.setattr(config.keyring, "get_password", boom)
        from config import resolve_api_key
        assert resolve_api_key(KEY) is None


# --- resolve_setting (env→keyring→default for non-secret config) ---


class TestResolveSetting:
    """resolve_setting is a sibling to resolve_api_key for non-secret
    config. Same env→keyring semantics, plus a default fallback when
    neither env nor keyring has a value (since settings always have a
    sensible default, unlike API keys which require explicit entry)."""

    def test_returns_env_value_when_present(self, monkeypatch, fake_keyring):
        monkeypatch.setenv("TTS_PROVIDER", "elevenlabs")
        from config import resolve_setting
        assert resolve_setting("TTS_PROVIDER", default="cartesia") == "elevenlabs"

    def test_migrates_env_to_keyring_on_resolve(self, monkeypatch, fake_keyring):
        """When env is present, the value MUST also land in keyring so the
        user can later delete .env without losing the choice."""
        monkeypatch.setenv("TTS_PROVIDER", "elevenlabs")
        from config import resolve_setting, KEYRING_SERVICE
        resolve_setting("TTS_PROVIDER", default="cartesia")
        assert fake_keyring[(KEYRING_SERVICE, "TTS_PROVIDER")] == "elevenlabs"

    def test_falls_back_to_keyring_when_env_absent(self, monkeypatch, fake_keyring):
        monkeypatch.delenv("TTS_PROVIDER", raising=False)
        from config import resolve_setting, KEYRING_SERVICE
        fake_keyring[(KEYRING_SERVICE, "TTS_PROVIDER")] = "elevenlabs"
        assert resolve_setting("TTS_PROVIDER", default="cartesia") == "elevenlabs"

    def test_returns_default_when_neither_source_has_value(self, monkeypatch, fake_keyring):
        """First-launch state: no env, empty keyring → default. Distinct from
        resolve_api_key which returns None (settings always have a default)."""
        monkeypatch.delenv("TTS_PROVIDER", raising=False)
        from config import resolve_setting
        assert resolve_setting("TTS_PROVIDER", default="cartesia") == "cartesia"

    def test_keyring_failures_do_not_block_env_path(self, monkeypatch):
        """Keyring backend errors swallowed — env value still returned + default
        still works as final fallback."""
        monkeypatch.setenv("TTS_PROVIDER", "elevenlabs")

        def boom(*_args, **_kwargs):
            raise RuntimeError("simulated keyring failure")

        import config
        monkeypatch.setattr(config.keyring, "set_password", boom)
        from config import resolve_setting
        assert resolve_setting("TTS_PROVIDER", default="cartesia") == "elevenlabs"


class TestBoundedIntegerSettings:
    def test_invalid_retention_value_falls_back_without_crashing(self, monkeypatch, fake_keyring):
        monkeypatch.delenv("DIAGNOSTIC_RETENTION_DAYS", raising=False)
        fake_keyring[("nimbus", "DIAGNOSTIC_RETENTION_DAYS")] = "not-a-number"
        from config import resolve_bounded_int_setting
        assert resolve_bounded_int_setting("DIAGNOSTIC_RETENTION_DAYS", 7, 1, 365) == 7

    def test_retention_value_is_clamped(self, monkeypatch, fake_keyring):
        monkeypatch.setenv("DIAGNOSTIC_RETENTION_DAYS", "9999")
        from config import resolve_bounded_int_setting
        assert resolve_bounded_int_setting("DIAGNOSTIC_RETENTION_DAYS", 7, 1, 365) == 365


class TestOnboardingFlag:
    def test_onboarding_seen_reads_false_then_true_from_keyring(self, monkeypatch, fake_keyring):
        from config import KEYRING_SERVICE, ONBOARDING_SEEN_KEY, onboarding_seen
        monkeypatch.delenv(ONBOARDING_SEEN_KEY, raising=False)
        assert onboarding_seen() is False
        fake_keyring[(KEYRING_SERVICE, ONBOARDING_SEEN_KEY)] = "1"
        assert onboarding_seen() is True

    def test_mark_onboarding_seen_persists_flag(self, fake_keyring):
        from config import KEYRING_SERVICE, ONBOARDING_SEEN_KEY, mark_onboarding_seen
        assert mark_onboarding_seen() is True
        assert fake_keyring[(KEYRING_SERVICE, ONBOARDING_SEEN_KEY)] == "1"


class TestShellStartupDefaultMigration:
    """Reversing a default is not enough when the old one was written down.

    ``resolve_setting`` is env -> keyring -> default, and the Settings dialog saves every checkbox,
    so anyone who ever pressed Save while the old default was "off" has "off" stored -- a recorded
    choice they never made. Measured on a real machine: the window did not open after the default
    was flipped, because Credential Manager said "off".
    """

    def test_an_inherited_off_is_retired_once(self, fake_keyring):
        from config import (KEYRING_SERVICE, SHELL_STARTUP_REVISION,
                            SHELL_STARTUP_REVISION_KEY, migrate_shell_startup_default)

        fake_keyring[(KEYRING_SERVICE, "SHELL_ON_STARTUP")] = "off"

        assert migrate_shell_startup_default() is True
        assert (KEYRING_SERVICE, "SHELL_ON_STARTUP") not in fake_keyring
        assert fake_keyring[(KEYRING_SERVICE, SHELL_STARTUP_REVISION_KEY)] == SHELL_STARTUP_REVISION

    def test_it_does_not_run_twice_so_a_re_chosen_off_survives(self, fake_keyring):
        """The point of the revision marker. Without it, every launch would overrule the user."""
        from config import KEYRING_SERVICE, migrate_shell_startup_default

        fake_keyring[(KEYRING_SERVICE, "SHELL_ON_STARTUP")] = "off"
        assert migrate_shell_startup_default() is True

        # The user deliberately turns it off again.
        fake_keyring[(KEYRING_SERVICE, "SHELL_ON_STARTUP")] = "off"
        assert migrate_shell_startup_default() is False
        assert fake_keyring[(KEYRING_SERVICE, "SHELL_ON_STARTUP")] == "off"

    def test_an_explicit_on_is_left_alone(self, fake_keyring):
        from config import KEYRING_SERVICE, migrate_shell_startup_default

        fake_keyring[(KEYRING_SERVICE, "SHELL_ON_STARTUP")] = "on"

        assert migrate_shell_startup_default() is False
        assert fake_keyring[(KEYRING_SERVICE, "SHELL_ON_STARTUP")] == "on"

    def test_a_fresh_install_has_nothing_to_migrate_but_is_still_marked(self, fake_keyring):
        """Marked either way, so the one chance is spent and cannot reappear later."""
        from config import (KEYRING_SERVICE, SHELL_STARTUP_REVISION,
                            SHELL_STARTUP_REVISION_KEY, migrate_shell_startup_default)

        assert migrate_shell_startup_default() is False
        assert fake_keyring[(KEYRING_SERVICE, SHELL_STARTUP_REVISION_KEY)] == SHELL_STARTUP_REVISION

    def test_an_unreadable_keyring_is_not_fatal(self, mocker):
        """`should_open_on_startup` already falls back to opening the window, so there is nothing
        to rescue and nothing to crash over."""
        import config

        mocker.patch.object(config.keyring, "get_password", side_effect=RuntimeError("locked"))

        assert config.migrate_shell_startup_default() is False


def test_resolve_kb_dir_falls_back_when_documents_cannot_create_child(tmp_path):
    """A broken/managed Documents path must not break the tray KB shortcut."""
    from config import _resolve_kb_dir

    blocker = tmp_path / "Documents"
    blocker.write_text("not a directory", encoding="utf-8")
    fallback = tmp_path / "Nimbus Wiki"

    assert _resolve_kb_dir(blocker / "Nimbus Wiki", fallback) == fallback
    assert fallback.is_dir()


# --- T0-2 / T0-4: provider + model default integrity -------------------------

class TestProviderDefaultConsistency:
    """T0-2: three call sites resolved LLM_PROVIDER and they did not agree.

    config.py defaulted to "openai" while both app.py sites defaulted to
    "anthropic". A populated .env or a completed first-run dialog hid the
    divergence, but a *cancelled* dialog on a clean keyring silently selected a
    different provider than Settings displayed - and landed on the broken
    Anthropic model default (T0-1).
    """

    def test_the_default_provider_is_the_native_gemini_api(self):
        """Not merely a default: the Gemini API must be what a default install actually calls.

        `gemini-native` is the only provider string that reaches `google.genai` and the Gemini API.
        Plain `gemini` routes through OpenRouter's compatibility endpoint unless the key happens to be
        a direct Google one, so asserting `startswith("gemini")` would pass while the requirement
        quietly failed."""
        import config

        assert config.DEFAULT_LLM_PROVIDER == "gemini-native"

    def test_default_llm_provider_constant_exists(self):
        import config
        assert isinstance(config.DEFAULT_LLM_PROVIDER, str)
        assert config.DEFAULT_LLM_PROVIDER

    def test_app_imports_the_shared_default(self):
        """app.py must consume the constant, not repeat a literal."""
        import app
        import config

        # `==`, not `is`. The identity check only ever passed by accident: CPython interns short
        # identifier-like literals, so `"openai"` was the same object in both modules for free. It
        # stopped being true the moment the value contained a hyphen -- and it was never testing the
        # real invariant anyway, because the `first_run_config` fixture reloads `config`, which rebinds
        # the constant to a fresh object while `app` still holds the original.
        #
        # The invariant that matters is "app does not repeat a literal", and it is pinned by value here
        # plus `test_every_llm_provider_call_site_uses_the_constant` reading app.py's source below.
        assert app.DEFAULT_LLM_PROVIDER == config.DEFAULT_LLM_PROVIDER

    def test_no_hardcoded_anthropic_provider_default_in_app(self):
        """T0-2 drift guard, in the style of test_overlay.py's bit-pattern
        assertion: read the source and prove the divergent literal is gone so it
        cannot silently return in a future edit."""
        from pathlib import Path
        source = Path(app_module_path()).read_text(encoding="utf-8")
        assert 'resolve_setting("LLM_PROVIDER", default="anthropic")' not in source
        assert "default=DEFAULT_LLM_PROVIDER" in source

    def test_every_llm_provider_call_site_uses_the_constant(self):
        """Counts call sites so a newly added one cannot reintroduce a literal.
        The audit originally found two; verification found a third."""
        import re
        from pathlib import Path
        source = Path(app_module_path()).read_text(encoding="utf-8")
        sites = re.findall(r'resolve_setting\(\s*\n?\s*"LLM_PROVIDER",\s*default=(\w+)', source)
        assert sites, "expected at least one LLM_PROVIDER call site in app.py"
        assert set(sites) == {"DEFAULT_LLM_PROVIDER"}, (
            f"a call site is not using the shared default: {sites}"
        )


class TestModelDefaultIntegrity:
    """T0-4: guard the model defaults against known-bad placeholder values.

    Verification against the live OpenAI model list and OpenRouter's model list
    showed the OpenAI and Gemini defaults were already VALID - the audit's claim
    that they were fictional was wrong. The one genuinely broken default was
    Anthropic's, fixed in T0-1. These tests pin the placeholder pattern that was
    real so it cannot come back; they deliberately do not try to validate that a
    model exists, which needs a network call and does not belong in unit tests.
    """

    def test_anthropic_default_is_not_the_scrubbed_placeholder(self):
        import config
        assert "model-sonnet" not in config.DEFAULT_ANTHROPIC_MODEL
        assert config.DEFAULT_ANTHROPIC_MODEL.startswith("claude-")

    def test_anthropic_default_is_native_dash_versioned(self):
        """Native api.anthropic.com uses dashes; OpenRouter uses a dot. The
        canonical stored form is native, converted per-endpoint in ai.py."""
        import config
        assert "." not in config.DEFAULT_ANTHROPIC_MODEL
        assert "/" not in config.DEFAULT_ANTHROPIC_MODEL

    def test_model_defaults_are_non_empty(self):
        import config
        for name in (
            "OPENAI_MODEL_VISION",
            "OPENAI_REALTIME_MODEL",
            "GEMINI_MODEL_VISION",
            "DEFAULT_ANTHROPIC_MODEL",
            "OLLAMA_MODEL_VISION",
        ):
            value = getattr(config, name)
            assert isinstance(value, str) and value.strip(), f"{name} is empty"

    def test_gemini_default_keeps_openrouter_namespace(self):
        """GeminiClient strips the namespace for native endpoints, so the stored
        value must carry it or OpenRouter routing breaks."""
        import config
        assert config.GEMINI_MODEL_VISION.startswith("google/")


def app_module_path() -> str:
    """Path to app.py, for the source-reading drift guards above."""
    import app
    return app.__file__
