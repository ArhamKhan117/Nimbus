"""Tests for per-app prompt addenda (T2-5, Code Mode).

The feature is a dictionary lookup, so most of the risk is not in the lookup -- it is in the
two ways an addendum can quietly break something else:

1. Replacing the base prompt instead of appending to it would destroy the persona, the
   write-for-the-ear contract and the pointing rules in one go.
2. Changing the prompt text at all breaks any code that identifies Nimbus's own prompt by
   equality. The native Gemini client did exactly that, and an appended addendum would have
   disabled structured geometry *and* the geometry call -- Code Mode would have stopped
   Nimbus pointing at anything. Guarded by TestAddendumDoesNotBreakStructuredGeometry.
"""

import pytest


class TestAppPromptAddenda:
    @pytest.mark.parametrize("exe", [
        "Code.exe", "code.exe", "Cursor.exe", "idea64.exe",
        # Verified installed on the development machine, not guessed.
        "Kiro.exe", "notepad++.exe", "devenv.exe",
    ])
    def test_known_editors_get_code_addendum(self, exe):
        from prompts import addendum_for_app
        assert "code editor" in addendum_for_app(exe)

    def test_unknown_app_gets_no_addendum(self):
        from prompts import addendum_for_app
        assert addendum_for_app("SomeRandomApp.exe") == ""

    def test_lookup_is_case_insensitive(self):
        from prompts import addendum_for_app
        assert addendum_for_app("KIRO.EXE") == addendum_for_app("kiro.exe") != ""

    def test_returns_empty_string_not_none(self):
        """Callers concatenate unconditionally; None would raise."""
        from prompts import addendum_for_app
        assert addendum_for_app("nope.exe") == ""
        assert isinstance(addendum_for_app("nope.exe"), str)

    @pytest.mark.parametrize("bad", ["", "unknown", "   "])
    def test_detection_failure_sentinels_are_safe(self, bad):
        """get_foreground_app() returns ('unknown', '') when detection fails, and a hiccup
        must never break the prompt."""
        from prompts import addendum_for_app
        assert addendum_for_app(bad) == ""

    def test_browsers_and_terminals_get_their_own_guidance(self):
        from prompts import addendum_for_app
        assert "browser" in addendum_for_app("chrome.exe")
        assert "terminal" in addendum_for_app("powershell.exe")

    def test_addendum_appended_not_replacing_base_prompt(self):
        """The persona must survive -- this is an addendum, not a substitution."""
        from ai import _NIMBUS_SYSTEM_PROMPT
        from prompts import apply_app_addendum
        result = apply_app_addendum(_NIMBUS_SYSTEM_PROMPT, "Kiro.exe")
        assert result.startswith(_NIMBUS_SYSTEM_PROMPT)
        assert len(result) > len(_NIMBUS_SYSTEM_PROMPT)

    def test_apply_is_identity_for_unknown_apps(self):
        from ai import _NIMBUS_SYSTEM_PROMPT
        from prompts import apply_app_addendum
        assert apply_app_addendum(
            _NIMBUS_SYSTEM_PROMPT, "Unknown.exe") == _NIMBUS_SYSTEM_PROMPT

    def test_sanitisation_matches_memory_module(self):
        """Drift guard: keys must match memory._sanitize_app_name output, so the keys line
        up with the folder names users already see in ~/.nimbus/memory/."""
        from memory import _sanitize_app_name
        from prompts import APP_PROMPT_ADDENDA
        for key in APP_PROMPT_ADDENDA:
            assert _sanitize_app_name(key) == key, (
                f"{key!r} is not in sanitised form; it could never match"
            )

    def test_addenda_are_lowercase_like_the_base_prompt(self):
        """The persona is written all-lowercase; an addendum in sentence case would fight
        it and show up as inconsistent narration."""
        from prompts import APP_PROMPT_ADDENDA
        for key, text in APP_PROMPT_ADDENDA.items():
            stripped = text.strip()
            assert stripped[:1].islower(), f"{key} addendum starts uppercase"

    def test_no_addendum_mentions_reading_things_aloud_verbatim(self):
        """Every addendum must respect the write-for-the-ear contract rather than quietly
        undoing it."""
        from prompts import APP_PROMPT_ADDENDA
        for key, text in APP_PROMPT_ADDENDA.items():
            assert "heard" in text or "aloud" in text or "slowly" in text, (
                f"{key} addendum gives no spoken-output guidance"
            )


class TestAddendumDoesNotBreakStructuredGeometry:
    """T2-5 x T1-2 interaction. This is the test that matters most in this file.

    `GeminiNativeClient` identifies Nimbus's own prompts to decide two things: whether to
    swap in the structured (tool-based) prompt, and whether to fire the geometry call at
    all. Both checks were equality-based. Appending "this is a code editor..." would have
    made a Nimbus prompt look fully custom, so on the native path Code Mode would have
    silently stopped Nimbus pointing -- while still answering perfectly, which is exactly
    the kind of regression nobody notices until a user reports "it stopped pointing".
    """

    def _client(self):
        from gemini_native import GeminiNativeClient
        return GeminiNativeClient(
            api_key="AQ.fake", model_id="gemini-3-flash-preview",
            client_factory=lambda api_key=None: object(),
        )

    def test_prompt_with_addendum_still_selects_structured_prompt(self):
        from ai import _NIMBUS_STRUCTURED_SYSTEM_PROMPT, _NIMBUS_SYSTEM_PROMPT
        from prompts import addendum_for_app
        requested = _NIMBUS_SYSTEM_PROMPT + addendum_for_app("Kiro.exe")
        selected = self._client()._select_system_prompt(requested, annotation_mode=False)
        assert selected.startswith(_NIMBUS_STRUCTURED_SYSTEM_PROMPT)

    def test_addendum_survives_the_prompt_swap(self):
        """Swapping the base must not discard the app guidance."""
        from ai import _NIMBUS_SYSTEM_PROMPT
        from prompts import addendum_for_app
        addendum = addendum_for_app("Kiro.exe")
        selected = self._client()._select_system_prompt(
            _NIMBUS_SYSTEM_PROMPT + addendum, annotation_mode=False)
        assert selected.endswith(addendum)

    def test_annotation_prompt_with_addendum_selects_structured_annotation(self):
        from ai import (
            _NIMBUS_ANNOTATION_SYSTEM_PROMPT, _NIMBUS_STRUCTURED_ANNOTATION_PROMPT,
        )
        from prompts import addendum_for_app
        requested = _NIMBUS_ANNOTATION_SYSTEM_PROMPT + addendum_for_app("Kiro.exe")
        selected = self._client()._select_system_prompt(requested, annotation_mode=True)
        assert selected.startswith(_NIMBUS_STRUCTURED_ANNOTATION_PROMPT)

    def test_prompt_with_addendum_is_still_recognised_as_ours(self):
        """The geometry-call decision. If this returns False the pointer never fires."""
        from ai import _NIMBUS_STRUCTURED_SYSTEM_PROMPT
        from prompts import addendum_for_app
        client = self._client()
        with_addendum = _NIMBUS_STRUCTURED_SYSTEM_PROMPT + addendum_for_app("Kiro.exe")
        assert client._is_structured_nimbus_prompt(with_addendum) is True

    def test_genuinely_custom_prompt_is_still_passed_through(self):
        """locator.py's refinement prompt must keep working -- it wants a [POINT] tag in
        text and would be broken by a structured swap."""
        import locator
        client = self._client()
        selected = client._select_system_prompt(
            locator._REFINEMENT_SYSTEM_PROMPT, annotation_mode=False)
        assert selected == locator._REFINEMENT_SYSTEM_PROMPT
        assert client._is_structured_nimbus_prompt(selected) is False

    def test_geometry_call_still_fires_with_an_addendum(self):
        """End to end through ask_stream: the observable behaviour that matters."""
        from PIL import Image
        from tests.test_gemini_native import (
            _FakeCall, _FakeChunk, _FakeModels, _FakePart, _make_client,
        )
        from ai import _NIMBUS_SYSTEM_PROMPT
        from prompts import addendum_for_app

        models = _FakeModels(
            stream_chunks=[_FakeChunk([_FakePart(text="that variable is unused.")])],
            geometry_parts=[_FakePart(function_call=_FakeCall(
                "point_at", {"y": 500, "x": 500, "label": "line 42"}))],
        )
        client = _make_client(models)
        with client.ask_stream(
            [(Image.new("RGB", (1000, 500), "black"), "primary focus (1000x500)")],
            "where is the unused variable",
            [],
            system_prompt=_NIMBUS_SYSTEM_PROMPT + addendum_for_app("Kiro.exe"),
        ) as stream:
            list(stream.text_deltas())
            result = stream.final_result()
        assert len(models.generate_calls) == 1, "geometry call did not fire"
        assert result.coordinate == (500, 250)

    def test_addendum_reaches_the_geometry_call_prompt_too(self):
        """Both calls should share the app context; the geometry call benefits from knowing
        it is looking at code."""
        from PIL import Image
        from tests.test_gemini_native import (
            _FakeChunk, _FakeModels, _FakePart, _make_client,
        )
        from ai import _NIMBUS_SYSTEM_PROMPT
        from prompts import addendum_for_app

        models = _FakeModels(stream_chunks=[_FakeChunk([_FakePart(text="hi")])])
        client = _make_client(models)
        with client.ask_stream(
            [(Image.new("RGB", (1000, 500), "black"), "primary focus (1000x500)")],
            "where is the save button",
            [],
            system_prompt=_NIMBUS_SYSTEM_PROMPT + addendum_for_app("Kiro.exe"),
        ) as stream:
            list(stream.text_deltas())
            stream.final_result()
        geometry_cfg = models.generate_calls[0]["config"]
        assert "code editor" in geometry_cfg.system_instruction
