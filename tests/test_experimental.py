"""Tests for the experimental settings group, T1-3 Agentic Vision, and T1-4 Gemini Live.

These four capabilities are user-togglable, which raises the stakes on two properties:
they must every one default OFF, and enabling any of them must never be able to break the
normal pipeline. Both are asserted here.
"""

import pytest


# --- Experimental settings group ---------------------------------------------

class TestExperimentalToggleDefinitions:
    """The toggles are data, so their shape and honesty are testable."""

    EXPECTED = {"CODE_EXECUTION", "SEARCH_GROUNDING", "AGENTIC_VISION", "GEMINI_LIVE"}

    def test_all_four_toggles_present(self):
        from settings_dialog import _EXPERIMENTAL_TOGGLES
        assert {s for s, _, _ in _EXPERIMENTAL_TOGGLES} == self.EXPECTED

    def test_every_toggle_has_a_label_and_tooltip(self):
        from settings_dialog import _EXPERIMENTAL_TOGGLES
        for setting, label, tooltip in _EXPERIMENTAL_TOGGLES:
            assert label.strip(), f"{setting} has no label"
            assert len(tooltip) > 80, f"{setting} tooltip is too thin to be useful"

    def test_tooltips_state_what_the_feature_costs(self):
        """A settings dialog that only lists upsides pushes users into choices they
        would not have made informed. Every tooltip must name a trade-off."""
        from settings_dialog import _EXPERIMENTAL_TOGGLES
        cost_words = ("cost", "latency", "slower", "not recommended", "unmeasured",
                      "untested", "least tested", "trade")
        for setting, _, tooltip in _EXPERIMENTAL_TOGGLES:
            lowered = tooltip.lower()
            assert any(w in lowered for w in cost_words), (
                f"{setting} tooltip does not state a downside"
            )

    def test_search_grounding_tooltip_carries_the_measured_warning(self):
        """T1-5 measured WORSE under Nimbus's own prompt. Hiding that would be
        dishonest, so the tooltip must say so."""
        from settings_dialog import _EXPERIMENTAL_TOGGLES
        tooltip = next(t for s, _, t in _EXPERIMENTAL_TOGGLES if s == "SEARCH_GROUNDING")
        assert "NOT RECOMMENDED" in tooltip
        assert "3.12" in tooltip and "3.14" in tooltip, "should cite the wrong answer"

    def test_tooltips_name_their_provider_requirement(self):
        from settings_dialog import _EXPERIMENTAL_TOGGLES
        for setting, _, tooltip in _EXPERIMENTAL_TOGGLES:
            assert "Requires" in tooltip, f"{setting} does not state its requirement"

    def test_all_experimental_settings_default_off(self, first_run_config):
        """The load-bearing property of this whole group.

        Uses the first-run fixture rather than reading the live module: the settings
        persist to the keyring once toggled, so asserting the imported value would test
        this machine's configuration instead of the declared default.
        """
        for name in ("CODE_EXECUTION", "SEARCH_GROUNDING", "AGENTIC_VISION"):
            assert getattr(first_run_config, name) == "off", f"{name} must default off"

    def test_gemini_live_defaults_off(self, first_run_config):
        """GEMINI_LIVE has no module-level constant -- app.py resolves it inline -- so
        it is checked through resolve_setting with the same default app.py passes."""
        assert first_run_config.resolve_setting("GEMINI_LIVE", default="off") == "off"

    def test_toggles_are_cleared_by_clear_local_data(self):
        """'Clear all Nimbus local data' must really return to a first-run state."""
        from settings_dialog import _LOCAL_KEYRING_ENTRIES
        for setting in self.EXPECTED:
            assert setting in _LOCAL_KEYRING_ENTRIES, f"{setting} would survive a wipe"


# --- T1-3 Agentic Vision -----------------------------------------------------

class TestAgenticVision:
    """T1-3: replaces Nimbus's crop-and-recheck pass with the model inspecting the
    screenshot itself. Opt-in because it is genuinely unmeasured."""

    def _client(self, agentic: bool):
        from gemini_native import GeminiNativeClient
        return GeminiNativeClient(
            api_key="AQ.fake", model_id="gemini-3-flash-preview",
            client_factory=lambda api_key=None: object(),
            enable_agentic_vision=agentic,
        )

    def test_off_by_default(self):
        from gemini_native import GeminiNativeClient
        client = GeminiNativeClient(api_key="AQ.fake", model_id="m",
                                    client_factory=lambda api_key=None: object())
        assert client.supports_agentic_refinement() is False

    def test_reports_capability_when_enabled(self):
        assert self._client(True).supports_agentic_refinement() is True

    def test_geometry_call_gets_the_inspection_instruction(self):
        client = self._client(True)
        cfg = client._build_config(
            "BASE PROMPT", 1024, "where is the save button",
            annotation_mode=False, with_tools=True,
        )
        assert "zoom in" in cfg.system_instruction.lower()
        assert cfg.system_instruction.startswith("BASE PROMPT")

    def test_speech_call_does_not_get_the_instruction(self):
        """The user never hears grounding guidance, so it must not reach the speech
        prompt where it would waste tokens and risk being spoken."""
        client = self._client(True)
        cfg = client._build_config(
            "BASE PROMPT", 1024, "where is the save button",
            annotation_mode=False, with_tools=False,
        )
        assert "zoom in" not in (cfg.system_instruction or "").lower()

    def test_agentic_raises_the_geometry_thinking_budget(self):
        """Self-inspection IS reasoning, so the minimal budget the geometry call
        normally uses would leave no room for it."""
        client = self._client(True)
        plain = self._client(False)
        agentic_cfg = client._build_config(
            "P", 1024, "where is x", annotation_mode=False, with_tools=True)
        plain_cfg = plain._build_config(
            "P", 1024, "where is x", annotation_mode=False, with_tools=True,
            force_minimal_budget=True)
        assert (agentic_cfg.thinking_config.thinking_budget
                > plain_cfg.thinking_config.thinking_budget)

    def test_disabled_client_config_is_unchanged(self):
        """Regression gate: leaving the toggle off must reproduce today's behaviour."""
        cfg = self._client(False)._build_config(
            "BASE", 1024, "where is x", annotation_mode=False, with_tools=True)
        assert cfg.system_instruction == "BASE"


class TestGroundingRefinementModes:
    def test_default_is_crop(self, first_run_config):
        """Default must stay the existing provider-agnostic crop pass, so enabling
        nothing reproduces today's behaviour."""
        assert first_run_config.GROUNDING_REFINEMENT == "crop"

    def test_other_providers_do_not_claim_agentic_support(self, mocker):
        """So the app's silent fallback to crop actually triggers for them."""
        from ai import AnthropicClient, OllamaClient, OpenAIVisionClient
        mocker.patch("ai.Anthropic")
        mocker.patch("ai.OpenAI")
        for client in (
            AnthropicClient(api_key="k", model_id="anthropic/claude-sonnet-4-6"),
            OpenAIVisionClient(api_key="k", model_id="openai/gpt-4o"),
            OllamaClient(host="http://localhost:11434", model_id="llava:7b"),
        ):
            assert not getattr(client, "supports_agentic_refinement", lambda: False)()


# --- T1-4 Gemini Live --------------------------------------------------------

class _FakeSession:
    """Stands in for the async bridge. Records what was sent, replays canned messages."""

    def __init__(self, messages=None, raise_on_send=False):
        self.messages = messages or []
        self.audio_sent, self.images_sent = [], []
        self.committed = False
        self.closed = False
        self.connected = False
        self._raise_on_send = raise_on_send

    def connect(self):
        self.connected = True

    def send_audio(self, pcm):
        if self._raise_on_send:
            raise RuntimeError("socket closed")
        self.audio_sent.append(pcm)

    def send_image(self, b64):
        self.images_sent.append(b64)

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True

    def __iter__(self):
        return iter(self.messages)


class _FakeStream:
    def __init__(self, callback=None):
        self.callback = callback
        self.started = self.stopped = self.closed = False
        self.written = []

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True

    def write(self, samples):
        self.written.append(samples)

    def abort(self):
        self.written.clear()


def _live(messages=None, **kw):
    from gemini_live import GeminiLiveSession
    session = _FakeSession(messages or [])
    mic, speaker = _FakeStream(), _FakeStream()
    live = GeminiLiveSession(
        api_key="AQ.fake",
        connection_factory=lambda: session,
        mic_stream_factory=lambda cb: mic,
        speaker_factory=lambda: speaker,
        **kw,
    )
    return live, session, mic, speaker


class TestGeminiLiveSession:
    """T1-4: mirrors realtime.py's surface so app.py needs no new branching."""

    def test_audio_rates_match_the_live_api_not_realtime_py(self):
        """The input rate differs from realtime.py's 24 kHz. Copying that value would
        make the model hear the user at the wrong pitch."""
        from gemini_live import LIVE_INPUT_SAMPLE_RATE, LIVE_OUTPUT_SAMPLE_RATE
        assert LIVE_INPUT_SAMPLE_RATE == 16_000
        assert LIVE_OUTPUT_SAMPLE_RATE == 24_000

    def test_connect_opens_session_and_speaker(self):
        live, session, _, speaker = _live()
        live.connect()
        assert session.connected and live._speaker is speaker

    def test_start_turn_opens_the_mic(self):
        live, _, mic, _ = _live()
        live.connect()
        live.start_turn()
        assert mic.started and live._recording is True

    def test_start_turn_is_idempotent(self):
        live, _, _, _ = _live()
        live.connect()
        live.start_turn()
        first = live._mic
        live.start_turn()
        assert live._mic is first

    def test_respond_closes_mic_sends_image_and_commits(self):
        live, session, mic, _ = _live()
        live.connect()
        live.start_turn()
        live.respond("ZmFrZQ==")
        assert mic.stopped
        assert session.images_sent == ["ZmFrZQ=="]
        assert session.committed is True
        assert live._recording is False

    def test_mic_chunks_forwarded_only_while_recording(self):
        live, session, _, _ = _live()
        live.connect()
        live._on_mic_chunk(b"\x01\x00", 1, None, None)   # before start_turn
        assert session.audio_sent == []
        live.start_turn()
        live._on_mic_chunk(b"\x01\x00", 1, None, None)
        assert len(session.audio_sent) == 1

    def test_mic_callback_never_raises(self):
        """It runs on the portaudio thread; an exception there kills audio for the
        whole session."""
        live, _, mic, _ = _live()
        live._session = _FakeSession(raise_on_send=True)
        live._recording = True
        live._on_mic_chunk(b"\x01\x00", 1, None, None)  # must not raise

    def test_audio_message_is_played(self):
        import numpy as np
        pcm = np.array([1000, -1000], dtype=np.int16).tobytes()
        live, _, _, speaker = _live([{"audio": pcm}])
        live.connect()
        live._recv_thread.join(timeout=2.0)
        assert speaker.written, "audio should have reached the speaker"

    def test_audio_start_callback_fires_once(self):
        import numpy as np
        pcm = np.array([500], dtype=np.int16).tobytes()
        calls = []
        live, _, _, _ = _live([{"audio": pcm}, {"audio": pcm}],
                              on_audio_start=lambda: calls.append(1))
        live.connect()
        live._recv_thread.join(timeout=2.0)
        assert len(calls) == 1, "spinner must be hidden once, not per chunk"

    def test_point_at_delivers_normalised_coordinates(self):
        """Conversion to Space C belongs to app.py, which alone knows the capture size."""
        got = []
        live, _, _, _ = _live(
            [{"tool_calls": [{"name": "point_at",
                              "args": {"y": 100, "x": 800, "label": "save"}}]}],
            on_coordinate=lambda y, x, label: got.append((y, x, label)),
        )
        live.connect()
        live._recv_thread.join(timeout=2.0)
        assert got == [(100, 800, "save")]

    def test_malformed_tool_args_are_ignored(self):
        got = []
        live, _, _, _ = _live(
            [{"tool_calls": [{"name": "point_at", "args": {"y": "nope"}}]}],
            on_coordinate=lambda *a: got.append(a),
        )
        live.connect()
        live._recv_thread.join(timeout=2.0)
        assert got == []

    def test_unknown_tool_is_ignored(self):
        got = []
        live, _, _, _ = _live(
            [{"tool_calls": [{"name": "something_else", "args": {}}]}],
            on_coordinate=lambda *a: got.append(a),
        )
        live.connect()
        live._recv_thread.join(timeout=2.0)
        assert got == []

    def test_transcript_callback_receives_text(self):
        seen = []
        live, _, _, _ = _live([{"text": "hello there"}],
                              on_transcript=seen.append)
        live.connect()
        live._recv_thread.join(timeout=2.0)
        assert seen == ["hello there"]

    def test_close_releases_everything(self):
        live, session, mic, speaker = _live()
        live.connect()
        live.start_turn()
        live.close()
        assert mic.stopped and speaker.stopped and session.closed

    def test_close_is_safe_before_connect(self):
        live, _, _, _ = _live()
        live.close()  # must not raise

    def test_stop_aborts_playback_without_closing(self):
        live, session, _, speaker = _live()
        live.connect()
        live.stop()
        assert not session.closed

    def test_pcm16_conversion_is_normalised(self):
        import numpy as np
        from gemini_live import pcm16_to_float32
        out = pcm16_to_float32(np.array([32767, -32768, 0], dtype=np.int16).tobytes())
        assert out[2] == 0.0
        assert 0.99 < out[0] <= 1.0
        assert -1.0 <= out[1] < -0.99

    def test_receive_loop_survives_a_broken_message(self):
        """One malformed frame must not end the session."""
        live, _, _, _ = _live([{"unexpected": "shape"}, {"text": "still here"}])
        seen = []
        live._on_transcript = seen.append
        live.connect()
        live._recv_thread.join(timeout=2.0)
        assert seen == ["still here"]


class TestGeminiLiveFailSafe:
    """The property that makes this safe to ship as a toggle: a Live failure must leave
    the normal push-to-talk pipeline running."""

    def test_setup_failure_leaves_realtime_none(self, mocker):
        from app import NimbusApp
        mocker.patch("gemini_live.GeminiLiveSession.connect",
                     side_effect=RuntimeError("no network"))
        app = NimbusApp(
            ai_client=mocker.MagicMock(), stt_client=mocker.MagicMock(),
            tts_client=mocker.MagicMock(), memory_store=mocker.MagicMock(),
            overlay_controller=mocker.MagicMock(), hotkey_instance=mocker.MagicMock(),
        )
        app._setup_gemini_live()
        assert app._realtime is None, "must fall back to the normal pipeline"

    def test_live_disabled_by_default_does_not_construct_a_session(self, mocker):
        import config
        assert config.resolve_setting("GEMINI_LIVE", "off") in ("off", "on")
        spy = mocker.patch("app.NimbusApp._setup_gemini_live")
        mocker.patch("app.resolve_setting", return_value="off")
        from app import NimbusApp
        NimbusApp(
            ai_client=mocker.MagicMock(), stt_client=mocker.MagicMock(),
            tts_client=mocker.MagicMock(), memory_store=mocker.MagicMock(),
            overlay_controller=mocker.MagicMock(), hotkey_instance=mocker.MagicMock(),
        )
        spy.assert_not_called()

    def test_two_mic_guard_still_applies(self):
        """_should_connect_stt exists so the Live mic and the STT mic never both grab
        the input device. It keys off _realtime being set, which Live also sets."""
        from app import _should_connect_stt
        assert _should_connect_stt(None) is True
        assert _should_connect_stt(object()) is False


# --- Model-picker persistence regression -------------------------------------

class TestModelPickerPersistsIds:
    """Regression: the Settings model picker persisted the DISPLAY LABEL, not the id.

    The native Gemini provider is `models_editable=True` (Google ships new preview names
    faster than Nimbus releases, so typing a custom id must stay possible). But
    `_selected_model_id` read `currentText()` unconditionally for editable combos, and the
    items are labelled for humans -- "Gemini 3 Flash (default, fastest)" displaying
    `gemini-3-flash-preview`.

    Result: choosing a model in Settings stored the label. Verified from the real keyring,
    which held `'Gemini 3 Flash (default, fastest)'`. Downstream that is either ignored (if
    .env pins the setting) or sent to the API as a model name and 404s. Either way the
    picker silently does not work.
    """

    def test_every_native_gemini_choice_is_a_bare_model_id(self):
        """Labels and ids must be distinguishable, or this bug is untestable."""
        from settings_dialog import _GEMINI_NATIVE_MODEL_CHOICES
        for display, model_id in _GEMINI_NATIVE_MODEL_CHOICES:
            assert " " not in model_id, f"{model_id!r} looks like a label, not an id"
            assert model_id.startswith("gemini-"), model_id
            assert display != model_id, (
                f"{display!r} is indistinguishable from its id, so a label/id mix-up "
                f"could not be detected"
            )

    def _combo(self, items, current_text):
        """Minimal stand-in for QComboBox: only the four methods under test."""
        class _Combo:
            def __init__(self):
                self._items = list(items)
            def currentText(self):
                return current_text
            def findText(self, text):
                for i, (display, _) in enumerate(self._items):
                    if display == text:
                        return i
                return -1
            def itemData(self, index):
                return self._items[index][1]
            def currentData(self):
                index = self.findText(current_text)
                return self._items[index][1] if index >= 0 else None
        return _Combo()

    def _selected(self, items, current_text, editable=True):
        from settings_dialog import SettingsDialog, _Provider, _ProviderCategory
        provider = _Provider(
            provider_id="gemini-native", display_name="G",
            api_key_env_var="GEMINI_API_KEY",
            signup_url="https://aistudio.google.com/apikey",
            models_editable=editable,
        )
        category = _ProviderCategory(
            category_key="LLM", label="LLM", providers=(provider,), default_index=0,
        )
        dialog = SettingsDialog.__new__(SettingsDialog)  # no Qt construction
        dialog._model_combos = {"LLM": self._combo(items, current_text)}
        return SettingsDialog._selected_model_id(dialog, category, provider)

    def test_selecting_a_labelled_choice_yields_the_model_id(self):
        from settings_dialog import _GEMINI_NATIVE_MODEL_CHOICES
        for display, model_id in _GEMINI_NATIVE_MODEL_CHOICES:
            assert self._selected(_GEMINI_NATIVE_MODEL_CHOICES, display) == model_id

    def test_a_typed_custom_model_name_is_still_honoured(self):
        """The reason the combo is editable at all -- must not regress."""
        from settings_dialog import _GEMINI_NATIVE_MODEL_CHOICES
        assert self._selected(
            _GEMINI_NATIVE_MODEL_CHOICES, "gemini-9-experimental"
        ) == "gemini-9-experimental"

    def test_no_display_label_can_ever_be_returned(self):
        """The invariant, stated directly."""
        from settings_dialog import _GEMINI_NATIVE_MODEL_CHOICES
        labels = {d for d, _ in _GEMINI_NATIVE_MODEL_CHOICES}
        for display, _ in _GEMINI_NATIVE_MODEL_CHOICES:
            assert self._selected(_GEMINI_NATIVE_MODEL_CHOICES, display) not in labels


# --- T1-4 Live model identity ------------------------------------------------

class TestLiveModelDefault:
    """Regression: the Live default was `gemini-live-2.5-flash-preview`, which does not
    exist. It failed at connect with "not found for API version v1beta, or is not
    supported for bidiGenerateContent".

    The Live API serves a SEPARATE and much smaller model set than `generateContent`, and
    the two do not overlap -- so a name that looks right is not evidence. Verified against
    `models.list()`, and the replacement was confirmed to accept Nimbus's exact session
    config (AUDIO modality plus the point_at declaration).
    """

    def test_default_is_a_verified_live_model(self):
        from gemini_live import DEFAULT_LIVE_MODEL
        assert DEFAULT_LIVE_MODEL in (
            "gemini-3.1-flash-live-preview",
            "gemini-2.5-flash-native-audio-latest",
        ), f"{DEFAULT_LIVE_MODEL!r} was not verified against models.list()"

    def test_default_is_not_the_nonexistent_model(self):
        from gemini_live import DEFAULT_LIVE_MODEL
        assert DEFAULT_LIVE_MODEL != "gemini-live-2.5-flash-preview"

    def test_default_is_not_a_plain_chat_model(self):
        """A normal chat model does not serve the Live protocol at all. This is the
        mistake most likely to be made when 'fixing' the model name by hand."""
        from gemini_live import DEFAULT_LIVE_MODEL
        assert ("live" in DEFAULT_LIVE_MODEL
                or "native-audio" in DEFAULT_LIVE_MODEL), DEFAULT_LIVE_MODEL

    def test_session_uses_the_default_when_no_model_given(self):
        from gemini_live import DEFAULT_LIVE_MODEL, GeminiLiveSession
        session = GeminiLiveSession(api_key="AQ.fake")
        assert session._model == DEFAULT_LIVE_MODEL

    def test_explicit_model_overrides_the_default(self):
        from gemini_live import GeminiLiveSession
        session = GeminiLiveSession(
            api_key="AQ.fake", model="gemini-2.5-flash-native-audio-latest")
        assert session._model == "gemini-2.5-flash-native-audio-latest"


class TestEnvPinnedSettingWarning:
    """A setting present in .env silently overrides the Settings dialog AND overwrites the
    dialog's stored choice on every launch. That is deliberate, but it looked exactly like
    the dialog being broken, so it is now reported at startup."""

    def test_experimental_toggles_are_covered(self):
        from app import _SETTINGS_ALSO_IN_SETTINGS_DIALOG
        from settings_dialog import _EXPERIMENTAL_TOGGLES
        for setting, _, _ in _EXPERIMENTAL_TOGGLES:
            assert setting in _SETTINGS_ALSO_IN_SETTINGS_DIALOG, setting

    def test_the_setting_that_caused_the_confusion_is_covered(self):
        from app import _SETTINGS_ALSO_IN_SETTINGS_DIALOG
        assert "GEMINI_NATIVE_MODEL" in _SETTINGS_ALSO_IN_SETTINGS_DIALOG

    def test_logs_nothing_when_no_setting_is_pinned(self, monkeypatch, capsys):
        import app
        for name in app._SETTINGS_ALSO_IN_SETTINGS_DIALOG:
            monkeypatch.delenv(name, raising=False)
        logged = []
        monkeypatch.setattr(app, "_log", logged.append)
        app._log_env_pinned_settings()
        assert logged == []

    def test_names_the_pinned_setting(self, monkeypatch):
        import app
        for name in app._SETTINGS_ALSO_IN_SETTINGS_DIALOG:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("GEMINI_NATIVE_MODEL", "gemini-3.1-pro-preview")
        logged = []
        monkeypatch.setattr(app, "_log", logged.append)
        app._log_env_pinned_settings()
        assert logged, "a pinned setting must be reported"
        assert "GEMINI_NATIVE_MODEL" in logged[0]
