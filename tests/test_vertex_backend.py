"""Vertex AI backend selection for the native Gemini path.

One SDK reaches two Google backends: the Gemini API with an AI Studio key, and Vertex AI
with a Google Cloud project on Application Default Credentials. Nimbus treats that as a
setting rather than a second implementation, because every capability it depends on
(structured function calling, thinking budgets, explicit caching, streaming, the Live
API) is identical across both.

These tests pin the three things that could silently go wrong:

1. **The default must not move.** An individual with a pasted key has to keep working
   exactly as before, so an unset project means the Gemini API and an API key.
2. **A configured project must win over a leftover key.** An institution that has
   deliberately pointed Nimbus at its own Cloud project must not be silently downgraded
   to a personal key that happens to still be in the keyring, which would bill and audit
   in the wrong place and defeat the reason they configured Vertex.
3. **No key is passed to Vertex.** Vertex authenticates with ADC. Passing a key
   alongside a project is the kind of thing that appears to work until it does not.

The SDK is never imported here: the client factory is injected, so this file needs no
credentials, no project and no network.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def clean_google_env(monkeypatch):
    """Neutralise ambient Google configuration.

    A developer machine with a real project exported would otherwise make the
    default-path assertions pass or fail depending on whose machine ran them.
    """
    for name in ("GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION"):
        monkeypatch.delenv(name, raising=False)
    import config
    monkeypatch.setattr(
        config, "resolve_setting", lambda name, default: default, raising=True,
    )
    return monkeypatch


class TestVertexSettings:
    def test_no_project_configured_reports_empty(self, clean_google_env):
        from ai import vertex_settings

        project, location = vertex_settings()
        assert project == ""
        assert location == "global"

    def test_project_and_location_are_read_from_settings(self, monkeypatch):
        import config
        from ai import vertex_settings

        values = {
            "GOOGLE_CLOUD_PROJECT": "nimbus-hackathon",
            "GOOGLE_CLOUD_LOCATION": "us-central1",
        }
        monkeypatch.setattr(
            config, "resolve_setting",
            lambda name, default: values.get(name, default),
            raising=True,
        )
        assert vertex_settings() == ("nimbus-hackathon", "us-central1")

    def test_blank_location_falls_back_to_global(self, monkeypatch):
        """The SDK rejects an empty region with an error naming neither the setting
        nor the fix, so a blank value is coerced rather than forwarded."""
        import config
        from ai import vertex_settings

        values = {"GOOGLE_CLOUD_PROJECT": "p", "GOOGLE_CLOUD_LOCATION": "   "}
        monkeypatch.setattr(
            config, "resolve_setting",
            lambda name, default: values.get(name, default),
            raising=True,
        )
        assert vertex_settings() == ("p", "global")


class TestClientConstruction:
    @staticmethod
    def _record_factory(calls: list[dict]):
        def factory(**kwargs):
            calls.append(kwargs)
            return object()
        return factory

    def test_without_a_project_the_factory_receives_only_the_api_key(self):
        """Backward compatibility. Factories injected before Vertex existed are
        declared as ``lambda api_key=None: ...``, and widening the call
        unconditionally would break them for no behavioural gain."""
        from gemini_native import GeminiNativeClient

        calls: list[dict] = []
        client = GeminiNativeClient(
            api_key="AQ.fake",
            model_id="gemini-3-flash-preview",
            client_factory=self._record_factory(calls),
        )
        client._get_client()

        assert calls == [{"api_key": "AQ.fake"}]
        assert client.uses_vertex() is False

    def test_with_a_project_the_factory_receives_the_vertex_arguments(self):
        from gemini_native import GeminiNativeClient

        calls: list[dict] = []
        client = GeminiNativeClient(
            api_key="AQ.fake",
            model_id="gemini-3-flash-preview",
            client_factory=self._record_factory(calls),
            vertex_project="nimbus-hackathon",
            vertex_location="us-central1",
        )
        client._get_client()

        assert calls == [{
            "api_key": "AQ.fake",
            "vertex_project": "nimbus-hackathon",
            "vertex_location": "us-central1",
        }]
        assert client.uses_vertex() is True

    def test_the_client_is_built_once_and_reused(self):
        """Construction is lazy so an offline start stays cheap, and cached so a
        Vertex credential exchange happens once per session rather than per turn."""
        from gemini_native import GeminiNativeClient

        calls: list[dict] = []
        client = GeminiNativeClient(
            api_key="AQ.fake",
            model_id="gemini-3-flash-preview",
            client_factory=self._record_factory(calls),
            vertex_project="p",
        )
        first = client._get_client()
        second = client._get_client()

        assert first is second
        assert len(calls) == 1

    def test_a_blank_location_is_normalised_at_construction(self):
        from gemini_native import GeminiNativeClient

        client = GeminiNativeClient(
            api_key="AQ.fake",
            model_id="gemini-3-flash-preview",
            vertex_project="p",
            vertex_location="",
        )
        assert client._vertex_location == "global"


class TestFactoryRouting:
    def test_a_configured_project_selects_the_native_client_without_any_key(self):
        """Vertex uses Application Default Credentials, so there is no key to paste.
        A deployment with a project and no key must still reach the native path,
        which is the whole point of supporting Vertex for institutions."""
        import config
        from ai import create_ai_client
        from gemini_native import GeminiNativeClient

        values = {"GOOGLE_CLOUD_PROJECT": "nimbus-hackathon"}
        original = config.resolve_setting
        config.resolve_setting = lambda name, default: values.get(name, default)
        try:
            client = create_ai_client(
                model_id="gemini-3-flash-preview", api_key="",
            )
        finally:
            config.resolve_setting = original

        assert isinstance(client, GeminiNativeClient)
        assert client.uses_vertex() is True

    def test_a_project_wins_over_a_leftover_openrouter_key(self):
        """An institution that configured Vertex must not be silently downgraded to
        whichever key happens to remain in the keyring: billing, audit and data
        residency would all land in the wrong account."""
        import config
        from ai import create_ai_client
        from gemini_native import GeminiNativeClient

        values = {"GOOGLE_CLOUD_PROJECT": "nimbus-hackathon"}
        original = config.resolve_setting
        config.resolve_setting = lambda name, default: values.get(name, default)
        try:
            client = create_ai_client(
                model_id="google/gemini-3-flash-preview",
                api_key="sk-or-v1-leftover",
            )
        finally:
            config.resolve_setting = original

        assert isinstance(client, GeminiNativeClient)
        assert client.uses_vertex() is True


class TestLiveSessionBackend:
    def test_live_defaults_to_the_gemini_api(self):
        from gemini_live import GeminiLiveSession

        session = GeminiLiveSession(api_key="AQ.fake")
        assert session._vertex_project == ""
        assert session._vertex_location == "global"

    def test_live_accepts_a_vertex_project(self):
        """Text and voice must share one backend. Routing questions through an
        institution's Cloud project while voice goes to a personal key is a data
        residency hole nobody would notice until an audit."""
        from gemini_live import GeminiLiveSession

        session = GeminiLiveSession(
            api_key="", vertex_project="nimbus-hackathon", vertex_location="us-central1",
        )
        assert session._vertex_project == "nimbus-hackathon"
        assert session._vertex_location == "us-central1"
