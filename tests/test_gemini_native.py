"""Unit tests for the native Gemini client (T1-1, T1-2, T1-7, T1-9).

All mock-based: the SDK client is injected via ``client_factory``, so nothing here
touches the network or needs a key. Mirrors the DI conventions in ``test_stt.py`` and
``test_realtime.py``.

The properties under test are the ones the live end-to-end run proved matter:
geometry never reaches the speech channel, the speech call declares no tools (or the
model goes silent), and every geometry failure degrades to speech-only rather than
failing the interaction.
"""

import pytest


# --- Fakes mirroring the google-genai response shape -------------------------

class _FakePart:
    def __init__(self, text=None, function_call=None):
        self.text = text
        self.function_call = function_call


class _FakeCall:
    def __init__(self, name, args):
        self.name = name
        self.args = args


class _FakeContent:
    def __init__(self, parts):
        self.parts = parts


class _FakeCandidate:
    def __init__(self, parts):
        self.content = _FakeContent(parts)


class _FakeChunk:
    def __init__(self, parts):
        self.candidates = [_FakeCandidate(parts)]


class _FakeResponse:
    def __init__(self, parts):
        self.candidates = [_FakeCandidate(parts)]


class _FakeModels:
    """Records requests so tests can assert on config, then replays canned output."""

    def __init__(self, stream_chunks=None, geometry_parts=None, raise_on_stream=None):
        self._stream_chunks = stream_chunks or []
        self._geometry_parts = geometry_parts or []
        self._raise_on_stream = raise_on_stream
        self.stream_calls = []
        self.generate_calls = []

    def generate_content_stream(self, model, contents, config=None):
        self.stream_calls.append({"model": model, "contents": contents, "config": config})
        if self._raise_on_stream:
            raise self._raise_on_stream
        return iter(self._stream_chunks)

    def generate_content(self, model, contents, config=None):
        self.generate_calls.append({"model": model, "contents": contents, "config": config})
        return _FakeResponse(self._geometry_parts)


class _FakeClient:
    def __init__(self, **kwargs):
        self.models = kwargs.pop("models", _FakeModels())


def _make_client(models, **overrides):
    from gemini_native import GeminiNativeClient

    def factory(api_key=None):
        client = _FakeClient(models=models)
        return client

    kwargs = dict(
        api_key="AQ.fake-key-for-testing",
        model_id="gemini-3-flash-preview",
        client_factory=factory,
    )
    kwargs.update(overrides)
    return GeminiNativeClient(**kwargs)


def _images():
    from PIL import Image
    return [(Image.new("RGB", (1000, 500), "black"), "primary focus (1000x500)")]


# --- T1-2: normalised coordinate conversion ---------------------------------

class TestNormalisedPointToSpaceC:
    """T1-2: Gemini returns 0-1000 normalised coords with **y first**.

    A transposed axis order produces plausible-looking wrong coordinates, which is the
    worst possible failure mode, so the helper takes y and x as named arguments and
    these tests use deliberately asymmetric values to catch a swap.
    """

    def test_centre_maps_to_centre(self):
        from ai import normalised_point_to_space_c
        assert normalised_point_to_space_c(500, 500, 1000, 500) == (500, 250)

    def test_axes_are_not_transposed(self):
        """Asymmetric input on an asymmetric canvas: a swap changes the answer."""
        from ai import normalised_point_to_space_c
        # y=100 of 500 -> 50 ; x=800 of 1000 -> 800
        assert normalised_point_to_space_c(norm_y=100, norm_x=800,
                                           target_w=1000, target_h=500) == (800, 50)

    def test_origin(self):
        from ai import normalised_point_to_space_c
        assert normalised_point_to_space_c(0, 0, 1920, 1080) == (0, 0)

    def test_max_clamps_inside_bounds(self):
        from ai import normalised_point_to_space_c
        assert normalised_point_to_space_c(1000, 1000, 1920, 1080) == (1919, 1079)

    def test_out_of_range_is_clamped(self):
        """Normalised input is dimensionless, so an out-of-range value has no valid
        pixel meaning and is clamped here rather than passed on."""
        from ai import normalised_point_to_space_c
        assert normalised_point_to_space_c(5000, -300, 1920, 1080) == (0, 1079)

    def test_zero_dimension_rejected(self):
        from ai import normalised_point_to_space_c
        with pytest.raises(ValueError):
            normalised_point_to_space_c(500, 500, 0, 100)


class TestNormalisedBoxToSpaceC:
    """T1-2: box_2d is ``[ymin, xmin, ymax, xmax]`` - y first, again."""

    def test_wide_short_box_stays_wide_and_short(self):
        """A search bar is wide and short. A transposed order would make it tall and
        narrow, so this asserts the shape survives the conversion."""
        from ai import normalised_box_to_space_c
        rect = normalised_box_to_space_c([100, 200, 150, 800], 1000, 1000)
        assert rect is not None
        assert rect.w > rect.h, "wide box must stay wide - axis order is transposed"
        assert (rect.x, rect.y, rect.w, rect.h) == (200, 100, 600, 50)

    def test_inverted_edges_are_normalised(self):
        from ai import normalised_box_to_space_c
        rect = normalised_box_to_space_c([150, 800, 100, 200], 1000, 1000)
        assert rect is not None and rect.w > 0 and rect.h > 0

    def test_centre_property(self):
        from ai import normalised_box_to_space_c
        rect = normalised_box_to_space_c([0, 0, 200, 400], 1000, 1000)
        assert rect.center == (200, 100)

    @pytest.mark.parametrize("bad", [None, [], [1, 2, 3], [1, 2, 3, 4, 5],
                                     ["a", "b", "c", "d"], [100, 200, 100, 200]])
    def test_malformed_box_returns_none_not_raises(self, bad):
        """A bad box from the model must never break the pipeline - same contract as
        parse_annotations dropping malformed shape tags."""
        from ai import normalised_box_to_space_c
        assert normalised_box_to_space_c(bad, 1000, 1000) is None


# --- T1-7: query classification ---------------------------------------------

class TestClassifyQuery:
    """T1-7: drives thinking-budget tiering. Pure function, table-driven."""

    @pytest.mark.parametrize("query,expected", [
        ("where is the save button", "locate"),
        ("show me the color panel", "locate"),
        ("click the export option", "locate"),
        ("find the settings menu", "locate"),
        ("what is a pivot table", "conceptual"),
        ("explain how promises work", "conceptual"),
        ("why is my answer wrong", "diagnostic"),
        ("this is broken, what happened", "diagnostic"),
        ("why doesn't this work", "diagnostic"),
        ("there is an error in my code", "diagnostic"),
    ])
    def test_classification(self, query, expected):
        from ai import classify_query
        assert classify_query(query) == expected

    def test_diagnostic_wins_over_locate(self):
        """'why is this button greyed out' contains a directional word but needs real
        reasoning. Classifying it as a cheap lookup would degrade the answer, so
        diagnostic is tested first in the implementation."""
        from ai import classify_query
        assert classify_query("why is the save button greyed out") == "diagnostic"

    @pytest.mark.parametrize("empty", ["", "   ", None])
    def test_empty_does_not_crash(self, empty):
        from ai import classify_query
        assert classify_query(empty) == "conceptual"

    def test_directional_word_list_matches_app(self):
        """Drift guard: ai and app keep separate copies to avoid an import cycle, so
        they must not diverge."""
        import ai
        import app
        assert set(app._DIRECTIONAL_QUERY_WORDS).issubset(
            set(ai._DIRECTIONAL_QUERY_WORDS)
        )


class TestThinkingBudget:
    """T1-7: verified live - budget 0 gave TTFB 1.18s vs 3.97s at default, but `pro`
    models reject 0 outright with 400 'Budget 0 is invalid'."""

    def test_locate_gets_minimal_budget(self):
        from gemini_native import THINKING_BUDGET_BY_CLASS
        assert THINKING_BUDGET_BY_CLASS["locate"] == 0

    def test_diagnostic_gets_most_budget(self):
        from gemini_native import THINKING_BUDGET_BY_CLASS
        b = THINKING_BUDGET_BY_CLASS
        assert b["diagnostic"] > b["conceptual"] > b["locate"]

    def test_pro_model_zero_budget_is_raised_to_floor(self):
        from gemini_native import _clamp_thinking_budget
        assert _clamp_thinking_budget(0, "gemini-3.1-pro-preview") > 0

    def test_flash_model_keeps_zero_budget(self):
        from gemini_native import _clamp_thinking_budget
        assert _clamp_thinking_budget(0, "gemini-3-flash-preview") == 0

    def test_nonzero_budget_passes_through_unchanged(self):
        from gemini_native import _clamp_thinking_budget
        assert _clamp_thinking_budget(4096, "gemini-3.1-pro-preview") == 4096


# --- T1-2: speech-channel hygiene -------------------------------------------

class TestStripNonSpeech:
    """T1-2: every character of spoken_text is read aloud, so nothing machine-shaped
    may survive. Observed live: a model told about tools but given none writes the
    call it would have made as a markdown block."""

    def test_python_fence_removed(self):
        from ai import strip_non_speech
        out = strip_non_speech("here it is.\n```python\ndraw_box(ymin=47)\n```")
        assert "```" not in out and "draw_box" not in out
        assert out.startswith("here it is.")

    def test_unterminated_fence_removed(self):
        from ai import strip_non_speech
        assert "```" not in strip_non_speech("look.\n```python\npoint_at(y=1")

    def test_bare_tool_call_removed(self):
        from ai import strip_non_speech
        assert "point_at" not in strip_non_speech("it is here point_at(y=10, x=20)")

    def test_ordinary_prose_untouched(self):
        from ai import strip_non_speech
        text = "the address bar is at the top of your browser window."
        assert strip_non_speech(text) == text

    def test_whitespace_collapsed(self):
        from ai import strip_non_speech
        assert strip_non_speech("a  \n\n  b") == "a b"


# --- T1-1 / T1-9: client behaviour ------------------------------------------

class TestGeminiNativeClientCapabilities:
    def test_declares_structured_geometry(self):
        assert _make_client(_FakeModels()).supports_structured_geometry() is True

    def test_declares_thinking_budget(self):
        assert _make_client(_FakeModels()).supports_thinking_budget() is True

    def test_namespaced_slug_is_stripped(self):
        """The native SDK rejects a 'google/' prefix; OpenRouter requires it."""
        client = _make_client(_FakeModels(), model_id="google/gemini-3-flash-preview")
        assert client.model_id == "gemini-3-flash-preview"

    def test_bare_model_id_unchanged(self):
        assert _make_client(_FakeModels()).model_id == "gemini-3-flash-preview"

    def test_construction_does_no_io(self):
        """Client build is lazy so construction stays cheap and offline."""
        from gemini_native import GeminiNativeClient
        c = GeminiNativeClient(api_key="AQ.x", model_id="m",
                               client_factory=lambda api_key=None: 1 / 0)
        assert c._client is None  # factory not yet invoked


class TestGeminiNativeStreaming:
    def test_text_deltas_stream_incrementally(self):
        models = _FakeModels(stream_chunks=[
            _FakeChunk([_FakePart(text="hello ")]),
            _FakeChunk([_FakePart(text="world")]),
        ])
        client = _make_client(models)
        with client.ask_stream(_images(), "what is http", []) as stream:
            deltas = list(stream.text_deltas())
        assert deltas == ["hello ", "world"]

    def test_conceptual_query_makes_no_geometry_call(self):
        """T1-9: a conceptual turn must cost exactly one request."""
        models = _FakeModels(stream_chunks=[_FakeChunk([_FakePart(text="http is...")])])
        client = _make_client(models)
        with client.ask_stream(_images(), "what is http", []) as stream:
            list(stream.text_deltas())
            stream.final_result()
        assert len(models.stream_calls) == 1
        assert models.generate_calls == []

    def test_locate_query_fires_parallel_geometry_call(self):
        """T1-9: forced by a measured API property - Gemini returns prose OR a tool
        call, never both, so the two roles must be separate concurrent requests."""
        models = _FakeModels(
            stream_chunks=[_FakeChunk([_FakePart(text="it is up top.")])],
            geometry_parts=[_FakePart(function_call=_FakeCall(
                "point_at", {"y": 100, "x": 800, "label": "address bar"}))],
        )
        client = _make_client(models)
        with client.ask_stream(_images(), "where is the address bar", []) as stream:
            list(stream.text_deltas())
            result = stream.final_result()
        assert len(models.stream_calls) == 1
        assert len(models.generate_calls) == 1
        assert result.coordinate == (800, 50)  # 1000x500 canvas
        assert result.element_label == "address bar"
        assert result.spoken_text == "it is up top."

    def test_speech_call_declares_no_tools(self):
        """The load-bearing detail: declaring tools on the speech call silences prose
        entirely. Measured at budgets 0, 64, 128, 256 and 512."""
        models = _FakeModels(stream_chunks=[_FakeChunk([_FakePart(text="hi")])])
        client = _make_client(models)
        with client.ask_stream(_images(), "where is the button", []) as stream:
            list(stream.text_deltas())
            stream.final_result()
        speech_cfg = models.stream_calls[0]["config"]
        assert not getattr(speech_cfg, "tools", None), "speech call must have no tools"
        geometry_cfg = models.generate_calls[0]["config"]
        assert getattr(geometry_cfg, "tools", None), "geometry call must have tools"

    def test_geometry_never_enters_spoken_text(self):
        """T1-2 HARD INVARIANT, the structural version of T0-3's guarantee."""
        models = _FakeModels(
            stream_chunks=[_FakeChunk([_FakePart(text="the save button is up here.")])],
            geometry_parts=[_FakePart(function_call=_FakeCall(
                "point_at", {"y": 500, "x": 500, "label": "save"}))],
        )
        client = _make_client(models)
        with client.ask_stream(_images(), "where is save", []) as stream:
            list(stream.text_deltas())
            result = stream.final_result()
        spoken = result.spoken_text.lower()
        for marker in ("[point", "point_at", "500", "```"):
            assert marker not in spoken, f"{marker!r} leaked into speech"
        assert result.malformed_tags == ()

    def test_geometry_failure_still_speaks(self):
        """T1-9 safety property: speech must never depend on geometry succeeding."""
        class _Exploding(_FakeModels):
            def generate_content(self, model, contents, config=None):
                raise RuntimeError("geometry backend down")

        models = _Exploding(stream_chunks=[_FakeChunk([_FakePart(text="it is up top.")])])
        client = _make_client(models)
        with client.ask_stream(_images(), "where is the button", []) as stream:
            list(stream.text_deltas())
            result = stream.final_result()
        assert result.spoken_text == "it is up top."
        assert result.coordinate is None

    def test_malformed_tool_args_drop_the_pointer_not_the_answer(self):
        models = _FakeModels(
            stream_chunks=[_FakeChunk([_FakePart(text="here.")])],
            geometry_parts=[_FakePart(function_call=_FakeCall(
                "point_at", {"y": "not-a-number", "x": 5, "label": "x"}))],
        )
        client = _make_client(models)
        with client.ask_stream(_images(), "where is it", []) as stream:
            list(stream.text_deltas())
            result = stream.final_result()
        assert result.spoken_text == "here."
        assert result.coordinate is None

    def test_stream_error_raises_actionable_message(self):
        models = _FakeModels(raise_on_stream=RuntimeError("401 unauthorized"))
        client = _make_client(models)
        with pytest.raises(RuntimeError, match="Gemini native request failed"):
            client.ask_stream(_images(), "hello", [])

    def test_mid_stream_failure_still_returns_accumulated_text(self):
        """Parity with _OllamaStreamingResponse: final_result must not re-raise."""
        def exploding():
            yield _FakeChunk([_FakePart(text="partial answer")])
            raise RuntimeError("socket dropped")

        models = _FakeModels()
        models.generate_content_stream = lambda model, contents, config=None: exploding()
        client = _make_client(models)
        with client.ask_stream(_images(), "what is http", []) as stream:
            with pytest.raises(RuntimeError):
                list(stream.text_deltas())
            result = stream.final_result()
        assert "partial answer" in result.spoken_text

    def test_draw_box_produces_rect_geometry(self):
        from annotations import Rect
        models = _FakeModels(
            stream_chunks=[_FakeChunk([_FakePart(text="i boxed it.")])],
            geometry_parts=[_FakePart(function_call=_FakeCall(
                "draw_box", {"box_2d": [100, 200, 150, 800], "label": "search bar"}))],
        )
        client = _make_client(models)
        with client.ask_stream(_images(), "circle the search bar", [],
                               annotation_mode=True) as stream:
            list(stream.text_deltas())
            shapes = stream.geometry()
        assert len(shapes) == 1 and isinstance(shapes[0], Rect)
        assert shapes[0].w > shapes[0].h

    def test_geometry_is_collected_only_once(self):
        """final_result() and geometry() may both be called, in either order."""
        models = _FakeModels(
            stream_chunks=[_FakeChunk([_FakePart(text="ok")])],
            geometry_parts=[_FakePart(function_call=_FakeCall(
                "point_at", {"y": 10, "x": 10, "label": "a"}))],
        )
        client = _make_client(models)
        with client.ask_stream(_images(), "where is a", []) as stream:
            list(stream.text_deltas())
            stream.final_result()
            shapes_a = stream.geometry()
            shapes_b = stream.geometry()
        assert len(shapes_a) == len(shapes_b) == 1

    def test_history_roles_map_assistant_to_model(self):
        """Gemini's assistant role is 'model'."""
        models = _FakeModels(stream_chunks=[_FakeChunk([_FakePart(text="x")])])
        client = _make_client(models)
        history = [
            {"role": "user", "content": [{"type": "text", "text": "hi"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
        ]
        with client.ask_stream(_images(), "what is http", history) as stream:
            list(stream.text_deltas())
        roles = [c.role for c in models.stream_calls[0]["contents"]]
        assert roles == ["user", "model", "user"]

    def test_empty_history_turns_are_skipped(self):
        """Parity with GeminiClient/OllamaClient: the API rejects empty content."""
        models = _FakeModels(stream_chunks=[_FakeChunk([_FakePart(text="x")])])
        client = _make_client(models)
        history = [{"role": "user", "content": [{"type": "image", "data": "..."}]}]
        with client.ask_stream(_images(), "what is http", history) as stream:
            list(stream.text_deltas())
        assert len(models.stream_calls[0]["contents"]) == 1  # only the live turn


class TestTextTagCoordinatesAreNormalised:
    """Regression: Gemini emits NORMALISED 0-1000 coords in a [POINT] tag, not pixels.

    This module previously assumed the opposite, with a comment asserting that text-path
    coordinates were "already in Space C". They are not, and the mistake had teeth: it
    silently degraded pointing accuracy through the one code path that exists to IMPROVE
    accuracy, `locator.refine_point_via_crop`.

    Measured at four image sizes (900x900, 1920x1080, 600x400, 400x1200): a dead-centre
    target returned `[POINT:500,500]` every single time. Pixels would have been (450,450),
    (960,540), (300,200) and (200,600). The prompt asks for pixels and states the
    dimensions; the model ignores that, because the convention is trained in.
    """

    def _refinement_result(self, text, size=(900, 900)):
        from PIL import Image
        models = _FakeModels(stream_chunks=[_FakeChunk([_FakePart(text=text)])])
        client = _make_client(models)
        # A CUSTOM prompt, as locator.py passes: this bypasses the tool path entirely and
        # forces the text-tag fallback, which is where the bug lived.
        with client.ask_stream(
            [(Image.new("RGB", size, "black"), f"crop ({size[0]}x{size[1]})")],
            "find the exact center of: the gear icon",
            [],
            system_prompt="custom verifier prompt asking for a [POINT] tag",
        ) as stream:
            list(stream.text_deltas())
            return stream.final_result()

    def test_centre_tag_maps_to_pixel_centre(self):
        """500,500 means the middle of the image, whatever its pixel dimensions."""
        result = self._refinement_result("[POINT:500,500:gear]", size=(900, 900))
        assert result.coordinate == (450, 450)

    def test_conversion_is_not_a_no_op_on_non_square_images(self):
        """The axes scale independently, so a square-image test alone could pass while
        the mapping was still wrong."""
        result = self._refinement_result("[POINT:500,500:gear]", size=(1920, 1080))
        assert result.coordinate == (960, 540)

    def test_the_exact_regression_case(self):
        """The measured live values that exposed the bug.

        900x900 refinement crop, gear icon whose true centre is crop-pixel (450, 70).
        The model returned `[POINT:500,78]`. Read as pixels that is (500, 78) -- 50px off
        in x and outside a 32px icon. Read correctly it is (450, 70): exact.
        """
        result = self._refinement_result("[POINT:500,78:gear]", size=(900, 900))
        assert result.coordinate == (450, 70)

    def test_out_of_range_values_are_clamped_not_wrapped(self):
        """normalised_point_to_space_c clamps, so a model error cannot point off-image."""
        result = self._refinement_result("[POINT:1400,-200:gear]", size=(900, 900))
        x, y = result.coordinate
        assert 0 <= x < 900 and 0 <= y < 900

    def test_no_tag_still_returns_no_coordinate(self):
        """Conversion must not invent a coordinate where the model gave none."""
        result = self._refinement_result("the gear icon is not visible here.")
        assert result.coordinate is None

    def test_tool_path_is_unaffected(self):
        """The structured path already converted correctly; it must not double-convert."""
        models = _FakeModels(
            stream_chunks=[_FakeChunk([_FakePart(text="up top.")])],
            geometry_parts=[_FakePart(function_call=_FakeCall(
                "point_at", {"y": 500, "x": 500, "label": "save"}))],
        )
        client = _make_client(models)
        with client.ask_stream(_images(), "where is save", []) as stream:
            list(stream.text_deltas())
            result = stream.final_result()
        assert result.coordinate == (500, 250)  # 1000x500 canvas, converted once


class TestSystemPromptSelection:
    """T1-2: a tag-based prompt actively breaks the structured path - the model obeys
    'append [POINT:x,y]' instead of using the tool channel."""

    def test_default_prompt_swapped_for_structured(self):
        from ai import _NIMBUS_STRUCTURED_SYSTEM_PROMPT, _NIMBUS_SYSTEM_PROMPT
        from gemini_native import GeminiNativeClient
        assert GeminiNativeClient._select_system_prompt(
            _NIMBUS_SYSTEM_PROMPT, False) is _NIMBUS_STRUCTURED_SYSTEM_PROMPT

    def test_annotation_prompt_swapped_for_structured(self):
        from ai import _NIMBUS_ANNOTATION_SYSTEM_PROMPT, _NIMBUS_STRUCTURED_ANNOTATION_PROMPT
        from gemini_native import GeminiNativeClient
        assert GeminiNativeClient._select_system_prompt(
            _NIMBUS_ANNOTATION_SYSTEM_PROMPT, True
        ) is _NIMBUS_STRUCTURED_ANNOTATION_PROMPT

    def test_custom_prompt_passed_through(self):
        """locator.py's crop-refinement prompt genuinely wants a [POINT] tag back, so
        it must not be swapped or the refinement path breaks."""
        from gemini_native import GeminiNativeClient
        custom = "You are a grounding verifier. Reply with [POINT:x,y:target]."
        assert GeminiNativeClient._select_system_prompt(custom, False) == custom

    def test_structured_prompts_mention_no_tool_names(self):
        """The speech prompt must not name functions: with no tools declared, a model
        told about them writes the call out as markdown, which TTS reads aloud."""
        from ai import _NIMBUS_STRUCTURED_ANNOTATION_PROMPT, _NIMBUS_STRUCTURED_SYSTEM_PROMPT
        for prompt in (_NIMBUS_STRUCTURED_SYSTEM_PROMPT,
                       _NIMBUS_STRUCTURED_ANNOTATION_PROMPT):
            lowered = prompt.lower()
            assert "point_at" not in lowered
            assert "draw_box" not in lowered
            assert "[point" not in lowered

    def test_structured_prompt_preserves_persona(self):
        """Voice must be identical across providers - only the geometry channel differs."""
        from ai import _NIMBUS_STRUCTURED_SYSTEM_PROMPT
        lowered = _NIMBUS_STRUCTURED_SYSTEM_PROMPT.lower()
        assert "all lowercase" in lowered
        assert "write for the ear" in lowered


class TestToolDeclarations:
    """T1-2: y and x are separate named integer fields, not an array, so the model
    cannot transpose them and the wire format is self-documenting."""

    def test_point_at_uses_named_axes(self):
        from gemini_native import GeminiNativeClient
        tools = GeminiNativeClient._build_tools(annotation_mode=False)
        decl = tools[0].function_declarations[0]
        assert decl.name == "point_at"
        assert set(decl.parameters.properties) == {"y", "x", "label"}

    def test_draw_box_only_declared_in_annotation_mode(self):
        from gemini_native import GeminiNativeClient
        without = GeminiNativeClient._build_tools(annotation_mode=False)
        with_ann = GeminiNativeClient._build_tools(annotation_mode=True)
        assert [d.name for d in without[0].function_declarations] == ["point_at"]
        assert "draw_box" in [d.name for d in with_ann[0].function_declarations]


# --- T1-1: factory routing ---------------------------------------------------

class TestDirectGoogleKeyDetection:
    """T1-1: the audit assumed only AIza existed. A real working key uses AQ., so
    detecting one prefix would silently mis-route every AQ. key to OpenRouter."""

    @pytest.mark.parametrize("key", ["AIzaSyExample", "AQ.Ab8RN6JuExample"])
    def test_direct_keys_detected(self, key):
        from ai import is_direct_google_key
        assert is_direct_google_key(key) is True

    @pytest.mark.parametrize("key", ["sk-or-v1-abc", "sk-ant-abc", "sk-proj-abc",
                                      "", None])
    def test_non_direct_keys_rejected(self, key):
        from ai import is_direct_google_key
        assert is_direct_google_key(key) is False


class TestBackwardCompatibility:
    """The regression gate for this whole tier: every existing provider must be
    completely unaffected by the additions."""

    def test_other_providers_report_no_structured_geometry(self, mocker):
        from ai import AnthropicClient, GeminiClient, OllamaClient, OpenAIVisionClient
        mocker.patch("ai.Anthropic")
        mocker.patch("ai.OpenAI")
        for client in (
            AnthropicClient(api_key="k", model_id="anthropic/claude-sonnet-4-6"),
            GeminiClient(api_key="k", model_id="google/gemini-3.1-pro-preview"),
            OpenAIVisionClient(api_key="k", model_id="openai/gpt-4o"),
            OllamaClient(host="http://localhost:11434", model_id="llava:7b"),
        ):
            assert client.supports_structured_geometry() is False
            assert client.supports_thinking_budget() is False

    def test_openrouter_gemini_path_untouched(self):
        """T1-1 must be additive: an sk-or- key still gets the shim client."""
        from ai import GeminiClient, create_ai_client
        client = create_ai_client(
            model_id="google/gemini-3.1-pro-preview", api_key="sk-or-v1-x")
        assert isinstance(client, GeminiClient)
        assert client.supports_structured_geometry() is False

    def test_new_settings_default_to_off(self, first_run_config):
        """Backward-compat rule: a new setting must reproduce current behaviour.

        Reads through the first-run fixture, not the imported module: these settings
        persist to the keyring once toggled in Settings, so the live value reflects the
        machine rather than the declared default.
        """
        assert first_run_config.SEARCH_GROUNDING == "off"
        assert first_run_config.AGENTIC_VISION == "off"
        assert first_run_config.GROUNDING_REFINEMENT == "crop"
