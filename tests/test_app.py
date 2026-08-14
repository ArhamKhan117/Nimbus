"""Unit tests for app.py.

All tests are mock-based. Zero real-hardware or real-API dependency.
Covers: flush_sentences, get_foreground_app, NimbusApp signal wiring.
"""

import pytest


def test_app_module_importable():
    import app  # noqa: F401


# --- flush_sentences ----------------------------------------------------------

class TestFlushSentences:
    """Tests for app.flush_sentences — regex sentence splitter."""

    def test_single_sentence_with_trailing_text(self):
        from app import flush_sentences
        sentences, remaining = flush_sentences("hello world. more text")
        assert sentences == ["hello world."]
        assert remaining == "more text"

    def test_multiple_sentences(self):
        from app import flush_sentences
        sentences, remaining = flush_sentences(
            "first sentence. second one! third? leftover"
        )
        assert len(sentences) == 3
        assert sentences[0] == "first sentence."
        assert sentences[1] == "second one!"
        assert sentences[2] == "third?"
        assert remaining == "leftover"

    def test_no_boundary_returns_empty_list(self):
        from app import flush_sentences
        sentences, remaining = flush_sentences("no boundary here")
        assert sentences == []
        assert remaining == "no boundary here"

    def test_empty_string(self):
        from app import flush_sentences
        sentences, remaining = flush_sentences("")
        assert sentences == []
        assert remaining == ""

    def test_sentence_ending_at_buffer_end_without_space(self):
        from app import flush_sentences
        sentences, remaining = flush_sentences("hello world.")
        assert sentences == []
        assert remaining == "hello world."

    def test_exclamation_and_question_marks(self):
        from app import flush_sentences
        sentences, remaining = flush_sentences("wow! really? yes. done")
        assert len(sentences) == 3
        assert remaining == "done"


# --- get_foreground_app -------------------------------------------------------

class TestGetForegroundApp:
    """Tests for app.get_foreground_app — ctypes Win32 wrapper."""

    def test_returns_tuple_of_two_strings(self):
        from app import get_foreground_app
        result = get_foreground_app()
        assert isinstance(result, tuple)
        assert len(result) == 2
        app_name, window_title = result
        assert isinstance(app_name, str)
        assert isinstance(window_title, str)
        assert len(app_name) > 0

    def test_app_name_is_exe_basename(self):
        from app import get_foreground_app
        app_name, _ = get_foreground_app()
        # Real desktop apps come back as basenames like "excel.exe" or
        # "chrome.exe". On headless CI runners (no GUI foreground), the
        # name might be something like "hosted-compute-agent" with no
        # extension. Both are valid — what we actually care about is that
        # we got a clean basename without path separators.
        assert app_name
        assert "/" not in app_name
        assert "\\" not in app_name


# --- NimbusApp ---------------------------------------------------------------

class TestNimbusApp:
    """Tests for NimbusApp orchestrator with fully mocked services."""

    def _make_app(self, mocker):
        from app import NimbusApp
        return NimbusApp(
            ai_client=mocker.MagicMock(),
            stt_client=mocker.MagicMock(),
            tts_client=mocker.MagicMock(),
            memory_store=mocker.MagicMock(),
            overlay_controller=mocker.MagicMock(),
            hotkey_instance=mocker.MagicMock(),
        )

    def test_construction_with_mocks(self, mocker):
        app = self._make_app(mocker)
        assert app._history == []
        assert app._current_app == "unknown"

    def test_export_session_history_writes_conversation_and_recent_memory(
        self, mocker, tmp_path
    ):
        """Exports current in-memory turns plus MemoryStore's bounded recall."""
        app = self._make_app(mocker)
        app._current_app = "EXCEL.EXE"
        app._current_title = "Budget - Excel"
        app._history = [
            {"role": "user", "content": [{"type": "text", "text": "freeze panes"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "open View."}]},
        ]
        app._memory.recall.return_value = "## Earlier turn\nResponse: Use Freeze Panes."

        path = app._export_session_history(tmp_path)

        assert path.parent == tmp_path
        assert path.name.startswith("nimbus-session-")
        assert path.suffix == ".md"
        exported = path.read_text(encoding="utf-8")
        assert "# Nimbus session history" in exported
        assert "Current app: EXCEL.EXE" in exported
        assert "### User" in exported
        assert "freeze panes" in exported
        assert "### Assistant" in exported
        assert "open View." in exported
        assert "## Recent per-app memory (EXCEL.EXE)" in exported
        assert "Response: Use Freeze Panes." in exported
        app._memory.recall.assert_called_once_with("EXCEL.EXE")

    def test_export_signal_dispatches_to_main_thread_slot(self, mocker, tmp_path):
        """The tray-facing signal, rather than the tray callback, owns export."""
        app = self._make_app(mocker)
        export_mock = mocker.patch.object(app, "_export_session_history")
        export_mock.return_value = tmp_path / "nimbus-session.md"

        app.sig_export_session_history.emit()

        export_mock.assert_called_once_with()

    def test_export_uses_fallback_when_documents_is_not_writable(
        self, mocker, tmp_path
    ):
        """Managed Windows profiles can deny Documents even when it exists."""
        from pathlib import Path

        app = self._make_app(mocker)
        app._memory.recall.return_value = ""
        blocked_dir = tmp_path / "blocked-documents"
        fallback_dir = tmp_path / "fallback-exports"
        mocker.patch("app.SESSION_EXPORT_DIR", blocked_dir)
        mocker.patch("app.SESSION_EXPORT_FALLBACK_DIR", fallback_dir)
        real_write_text = Path.write_text

        def write_text(path, *args, **kwargs):
            if path.parent == blocked_dir:
                raise FileNotFoundError("Documents is unavailable")
            return real_write_text(path, *args, **kwargs)

        mocker.patch.object(Path, "write_text", new=write_text)

        path = app._export_session_history()

        assert path.parent == fallback_dir
        assert path.is_file()

    def test_handle_press_starts_recording(self, mocker):
        app = self._make_app(mocker)
        mocker.patch("app.get_foreground_app", return_value=("EXCEL.EXE", "Sheet1"))
        app._handle_press()
        app._stt.start_recording.assert_called_once()
        app._tts.stop.assert_called_once()
        assert app._current_app == "EXCEL.EXE"
        assert app._current_title == "Sheet1"

    def test_handle_release_spawns_worker(self, mocker):
        app = self._make_app(mocker)
        app._stt.stop_recording.return_value = ""
        app._handle_release()
        assert app._worker_thread is not None
        assert app._worker_thread.daemon is True
        app._worker_thread.join(timeout=2)

    def test_handle_press_sets_tts_grace_on_stt(self, mocker):
        """_handle_press calls tts.stop() then sets a ~200ms STT grace window
        so speaker decay doesn't leak into the transcription.
        """
        import time as _t
        app = self._make_app(mocker)
        mocker.patch("app.get_foreground_app", return_value=("EXCEL.EXE", "Sheet1"))

        t_before = _t.time()
        app._handle_press()

        app._stt.set_tts_grace_until.assert_called_once()
        grace_ts = app._stt.set_tts_grace_until.call_args.args[0]
        # Should be ~200ms in the future (give ±50ms slack for test timing)
        assert grace_ts >= t_before + 0.150, (
            f"Grace ts {grace_ts} should be ~200ms after t_before {t_before}"
        )
        assert grace_ts <= t_before + 0.300, (
            f"Grace ts {grace_ts} should not be more than 300ms in future"
        )

    def test_handle_release_sets_tts_grace_when_cancelling_worker(self, mocker):
        """On a re-press mid-response, _handle_release kills in-flight TTS + sets
        grace so the new PTT doesn't pick up the aborted TTS's decay.
        """
        import threading
        import time as _t
        app = self._make_app(mocker)
        app._stt.stop_recording.return_value = ""

        # Fake an in-flight worker thread so the cancel branch runs.
        fake_worker = mocker.MagicMock()
        fake_worker.is_alive.return_value = True
        app._worker_thread = fake_worker

        t_before = _t.time()
        app._handle_release()

        app._tts.stop.assert_called()
        app._stt.set_tts_grace_until.assert_called()
        grace_ts = app._stt.set_tts_grace_until.call_args.args[0]
        assert grace_ts >= t_before + 0.150

        # Let the spawned worker thread exit cleanly so pytest teardown is clean.
        if app._worker_thread is not None and app._worker_thread is not fake_worker:
            if hasattr(app._worker_thread, "join"):
                try:
                    app._worker_thread.join(timeout=2)
                except Exception:
                    pass

    def test_stop_sets_cancel_event(self, mocker):
        app = self._make_app(mocker)
        app.stop()
        assert app._cancel_event.is_set()
        app._hotkey.stop.assert_called_once()
        app._tts.stop.assert_called_once()

    def test_press_handler_shows_waveform_at_cursor(self, mocker):
        """_handle_press emits sig_show_waveform with the cursor
        position + the containing monitor → OverlayController routes to the
        right screen + hides cursor polygon + shows the 5-bar waveform."""
        app = self._make_app(mocker)
        mocker.patch("app.get_foreground_app", return_value=("EXCEL.EXE", "Sheet1"))
        mocker.patch("app.get_cursor_position", return_value=(500, 600))
        mocker.patch("app.capture_all_screens", return_value=[mocker.MagicMock()])
        # Ensure list_monitors + monitor_containing return a usable mon dict.
        mon = {"left": 0, "top": 0, "width": 1920, "height": 1080}
        mocker.patch("app.list_monitors", return_value=[mon])
        mocker.patch("app.monitor_containing", return_value=mon)

        app._handle_press()
        if app._capture_thread is not None:
            app._capture_thread.join(timeout=2.0)

        app._overlay.show_waveform.assert_called_once()
        call_args = app._overlay.show_waveform.call_args
        assert call_args.args[0] == 500, "x coordinate should be cursor x"
        assert call_args.args[1] == 600, "y coordinate should be cursor y"
        assert call_args.args[2] == mon, "monitor dict should be the containing monitor"

    def test_release_handler_hides_waveform(self, mocker):
        """_handle_release must fire hide_waveform so the bars disappear once
        the user lets go of the hotkey."""
        app = self._make_app(mocker)
        app._stt.stop_recording.return_value = ""  # empty transcript → fast exit
        app._handle_release()
        app._overlay.hide_waveform.assert_called()

    def test_audio_level_slot_forwards_to_overlay(self, mocker):
        """RMS level from stt → pyqtSignal → Qt main thread slot →
        overlay.set_audio_level. Test the slot directly since pytest has no
        Qt event loop to marshal the signal.emit() → slot_handler hop."""
        app = self._make_app(mocker)
        app._on_audio_level(0.42)
        app._overlay.set_audio_level.assert_called_once_with(0.42)

    def test_release_emits_show_spinner(self, mocker):
        """On RELEASE, the THINKING spinner must appear at the cursor position."""
        app = self._make_app(mocker)
        app._stt.stop_recording.return_value = ""  # fast-exit pipeline
        mocker.patch("app.get_cursor_position", return_value=(500, 600))
        mon = {"left": 0, "top": 0, "width": 1920, "height": 1080}
        mocker.patch("app.list_monitors", return_value=[mon])
        mocker.patch("app.monitor_containing", return_value=mon)

        app._handle_release()

        app._overlay.show_spinner.assert_called_once()
        args = app._overlay.show_spinner.call_args.args
        assert args[0] == 500 and args[1] == 600

    def test_press_emits_hide_spinner_to_clear_stale(self, mocker):
        """PRESS must clear any stale spinner from a prior interaction."""
        app = self._make_app(mocker)
        mocker.patch("app.get_foreground_app", return_value=("EXCEL.EXE", "Sheet1"))
        mocker.patch("app.get_cursor_position", return_value=(100, 100))
        mocker.patch("app.capture_all_screens", return_value=[mocker.MagicMock()])

        app._handle_press()
        if app._capture_thread is not None:
            app._capture_thread.join(timeout=2.0)

        # Any hide_spinner call proves the defensive clear fired.
        app._overlay.hide_spinner.assert_called()

    def test_hide_spinner_slot_forwards_to_overlay(self, mocker):
        """sig_hide_spinner slot must delegate to overlay.hide_spinner."""
        app = self._make_app(mocker)
        app._on_hide_spinner()
        app._overlay.hide_spinner.assert_called_once()

    def test_press_clears_stale_annotations(self, mocker):
        """PRESS must clear any stale teaching annotations from a prior
        turn so old shapes never survive a no-speech/cancelled/errored turn."""
        app = self._make_app(mocker)
        mocker.patch("app.get_foreground_app", return_value=("EXCEL.EXE", "Sheet1"))
        mocker.patch("app.get_cursor_position", return_value=(100, 100))
        mocker.patch("app.capture_all_screens", return_value=[mocker.MagicMock()])

        app._handle_press()
        if app._capture_thread is not None:
            app._capture_thread.join(timeout=2.0)

        app._overlay.clear_all_annotations.assert_called()

    def test_clear_annotations_slot_forwards_to_overlay(self, mocker):
        """sig_clear_annotations slot must delegate to overlay.clear_all_annotations."""
        app = self._make_app(mocker)
        app._on_clear_annotations()
        app._overlay.clear_all_annotations.assert_called_once()

    def test_press_handler_plays_listening_tone(self, mocker):
        """_handle_press plays a short chime the moment the
        hotkey goes down so the user has immediate feedback 'mic is hot, keep
        talking'. Must be non-blocking (0ms pipeline latency)."""
        import app as app_module
        play_spy = mocker.patch.object(app_module, "_play_feedback_tone_async")
        mocker.patch("app.get_foreground_app", return_value=("EXCEL.EXE", "Sheet1"))
        mocker.patch("app.get_cursor_position", return_value=(100, 100))
        mocker.patch("app.capture_all_screens", return_value=[mocker.MagicMock()])

        app = self._make_app(mocker)
        app._handle_press()
        if app._capture_thread is not None:
            app._capture_thread.join(timeout=2.0)

        play_spy.assert_called_once_with("listening")

    def test_press_handler_kicks_off_capture_in_background(self, mocker):
        """_handle_press starts capture_all_screens + memory.recall on a
        background thread so the work overlaps with the user speaking.

        Saves ~250ms post-release wall-clock (the full capture stage — hide
        overlay + 50ms wait + mss.grab + PIL resize + show overlay).
        """
        app = self._make_app(mocker)
        mocker.patch("app.get_foreground_app", return_value=("EXCEL.EXE", "Sheet1"))
        mocker.patch("app.get_cursor_position", return_value=(100, 200))

        fake_capture = mocker.MagicMock()
        mocker.patch("app.capture_all_screens", return_value=[fake_capture])
        app._memory.recall.return_value = "prior interaction"

        app._handle_press()

        # Background thread should have been spawned.
        assert app._capture_thread is not None, (
            "_handle_press should spawn a background capture thread"
        )
        app._capture_thread.join(timeout=2.0)

        assert app._press_captures == [fake_capture]
        assert app._press_memory == "prior interaction"
        assert app._press_cursor_pos == (100, 200)
        app._memory.recall.assert_called_once_with("EXCEL.EXE")

    def test_release_capture_worker_reuses_when_cursor_still(self, mocker):
        """If cursor moved ~0px between press and release, worker must
        reuse the press-time captures without calling capture_all_screens
        (no flicker, no wasted work on cursor-still sessions).

        Post-Commit-2: decision logic moved from _pipeline_worker to
        _release_capture_worker. This test targets the worker directly."""
        import queue as _queue
        app = self._make_app(mocker)

        fake_capture = mocker.MagicMock()
        fake_capture.image = mocker.MagicMock()
        fake_capture.label = "screen 1 of 1"
        fake_capture.scale_x = 1.0
        fake_capture.scale_y = 1.0
        fake_capture.monitor = {"left": 0, "top": 0, "width": 1920, "height": 1080}
        fake_capture.target_width = 1280
        fake_capture.target_height = 800
        fake_capture.is_cursor_screen = True

        app._press_captures = [fake_capture]
        app._press_memory = "prior memory"
        app._press_cursor_pos = (100, 100)

        capture_fn = mocker.patch("app.capture_all_screens")

        result_queue: _queue.Queue = _queue.Queue(maxsize=1)
        # Cursor moved only 18px (sqrt(15²+10²)) — well within 150px threshold
        app._release_capture_worker(
            release_cursor=(115, 110),
            app_name="EXCEL.EXE",
            result_queue=result_queue,
        )

        assert not capture_fn.called, (
            "Expected worker to reuse press-time captures when cursor is still"
        )
        captures, memory_context, reason = result_queue.get_nowait()
        assert captures == [fake_capture]
        assert memory_context == "prior memory"
        assert "reusing" in reason

    def test_pipeline_streams_sentences_during_model_generation(self, mocker):
        """Pipeline must call tts.speak_sentence for each .!? boundary in the
        Nimbus stream (not batch tts.speak() at the end). The tag-start character
        '[' must freeze flushing so the POINT tag is never spoken aloud.

        Biggest latency win: first audible word happens when sentence-1
        is ready (~1200ms after Nimbus TTFT) instead of when sentence-N is done
        (~3700ms).

        Post-Commit-2: `_pipeline_worker` takes a pre-populated capture queue
        (normally filled in parallel by `_release_capture_worker`). Tests pass
        a manually-populated queue."""
        import queue as _queue
        import threading
        from PIL import Image
        app = self._make_app(mocker)

        # Prime press-time captures so the pipeline takes the fast path.
        fake_cap = mocker.MagicMock()
        fake_cap.image = Image.new("RGB", (1280, 800))
        fake_cap.label = "screen 1 of 1"
        fake_cap.scale_x = 1.0; fake_cap.scale_y = 1.0
        fake_cap.monitor = {"left": 0, "top": 0, "width": 1920, "height": 1080}
        fake_cap.target_width = 1280; fake_cap.target_height = 800
        fake_cap.is_cursor_screen = True

        app._press_captures = [fake_cap]
        app._press_memory = ""
        app._press_cursor_pos = (100, 100)
        app._stt.stop_recording.return_value = "how do I make my repo public"

        # Stream that yields sentences one delta at a time + a [POINT:...] tag at the end.
        def fake_deltas():
            yield "you "; yield "want "; yield "the settings tab. "
            yield "scroll "; yield "down to the bottom. "
            yield "click 'change visibility'. "
            yield "[POINT:721,215:settings tab]"

        fake_stream = mocker.MagicMock()
        fake_stream.text_deltas.return_value = iter(fake_deltas())
        fake_stream.final_result.return_value = mocker.MagicMock(
            spoken_text=(
                "you want the settings tab. "
                "scroll down to the bottom. "
                "click 'change visibility'."
            ),
            coordinate=(721, 215),
            element_label="settings tab",
            screen_number=None,
        )
        app._ai.ask_stream.return_value.__enter__ = mocker.MagicMock(return_value=fake_stream)
        app._ai.ask_stream.return_value.__exit__ = mocker.MagicMock(return_value=False)

        # Pre-populate capture queue simulating the parallel worker's output.
        capture_queue: _queue.Queue = _queue.Queue(maxsize=1)
        capture_queue.put(([fake_cap], "", "test: reusing press-time captures"))

        cancel = threading.Event()
        app._pipeline_worker("TEST.EXE", "TestWindow", cancel, capture_queue)

        sentence_calls = [c.args[0] for c in app._tts.speak_sentence.call_args_list]

        # Sentence-level streaming during the Nimbus generation.
        assert any("settings tab." in s for s in sentence_calls), (
            "First sentence should have been flushed during streaming, "
            f"got speak_sentence calls: {sentence_calls}"
        )
        assert any("to the bottom." in s for s in sentence_calls), (
            "Second sentence should have been flushed during streaming"
        )

        # POINT tag must NEVER appear in anything sent to TTS.
        for s in sentence_calls:
            assert "[POINT" not in s, (
                f"POINT tag must not be spoken aloud, but got: {s!r}"
            )

        # The batch speak() path must be gone (replaced by sentence-level).
        assert not app._tts.speak.called, (
            "Batch tts.speak() should be replaced with sentence-level streaming"
        )

    def test_release_capture_worker_reuses_on_medium_cursor_move(self, mocker):
        """Commit 1 threshold (50 → 150px) verified at the worker level.
        Cursor moves 106px — within 'target hover' intent, not 'user
        repositioned' — so worker reuses press-time captures. Pre-Commit-1
        this would have re-captured (106 > 50); post-Commit-1 it reuses
        (106 <= 150)."""
        import queue as _queue
        app = self._make_app(mocker)

        fake_capture = mocker.MagicMock()
        fake_capture.image = mocker.MagicMock()
        fake_capture.label = "screen 1 of 1"
        fake_capture.scale_x = 1.0
        fake_capture.scale_y = 1.0
        fake_capture.monitor = {"left": 0, "top": 0, "width": 1920, "height": 1080}
        fake_capture.target_width = 1280
        fake_capture.target_height = 800
        fake_capture.is_cursor_screen = True

        app._press_captures = [fake_capture]
        app._press_memory = "prior memory"
        app._press_cursor_pos = (100, 100)

        capture_fn = mocker.patch("app.capture_all_screens")

        result_queue: _queue.Queue = _queue.Queue(maxsize=1)
        # Cursor moved 106px (sqrt(80² + 70²)) — above old 50px threshold,
        # below new 150px threshold. Should reuse.
        app._release_capture_worker(
            release_cursor=(180, 170),
            app_name="EXCEL.EXE",
            result_queue=result_queue,
        )

        assert not capture_fn.called, (
            "Expected worker to reuse press-time captures at 106px "
            "(within new 150px threshold), but capture_all_screens was called"
        )
        captures, _, reason = result_queue.get_nowait()
        assert captures == [fake_capture]
        assert "reusing" in reason and "106px" in reason

    def test_release_capture_worker_recaptures_on_large_cursor_move(self, mocker):
        """If cursor moved past the reuse threshold, worker re-captures on
        release (safeguard against stale screenshots when user actively
        repositioned mid-utterance).

        Post-Commit-2: decision logic lives in _release_capture_worker."""
        import queue as _queue
        from PIL import Image
        app = self._make_app(mocker)

        stale_capture = mocker.MagicMock()
        stale_capture.image = Image.new("RGB", (1280, 800))
        stale_capture.label = "stale"
        stale_capture.scale_x = 1.0; stale_capture.scale_y = 1.0
        stale_capture.monitor = {"left": 0, "top": 0, "width": 1920, "height": 1080}
        stale_capture.target_width = 1280; stale_capture.target_height = 800
        stale_capture.is_cursor_screen = True

        fresh_capture = mocker.MagicMock()
        fresh_capture.image = Image.new("RGB", (1280, 800))
        fresh_capture.label = "fresh"
        fresh_capture.scale_x = 1.0; fresh_capture.scale_y = 1.0
        fresh_capture.monitor = {"left": 0, "top": 0, "width": 1920, "height": 1080}
        fresh_capture.target_width = 1280; fresh_capture.target_height = 800
        fresh_capture.is_cursor_screen = True

        app._press_captures = [stale_capture]
        app._press_memory = "prior"
        app._press_cursor_pos = (100, 100)
        app._memory.recall.return_value = "fresh memory"

        capture_fn = mocker.patch("app.capture_all_screens", return_value=[fresh_capture])

        result_queue: _queue.Queue = _queue.Queue(maxsize=1)
        # Cursor moved 283px (sqrt(200²+200²)) — well past 150px threshold
        app._release_capture_worker(
            release_cursor=(300, 300),
            app_name="EXCEL.EXE",
            result_queue=result_queue,
        )

        assert capture_fn.called, (
            "Expected re-capture when cursor moved >150px between press and release"
        )
        captures, memory_context, reason = result_queue.get_nowait()
        assert captures == [fresh_capture]
        assert memory_context == "fresh memory"
        assert "re-capturing" in reason

    def test_default_ai_client_comes_from_factory(self, mocker):
        """When no ai_client passed, NimbusApp calls create_ai_client(MODEL_ID, ...)."""
        mock_factory = mocker.patch("app.create_ai_client")
        mock_factory.return_value = mocker.MagicMock(name="ai_client_returned")
        from app import NimbusApp
        nimbus = NimbusApp(
            stt_client=mocker.MagicMock(),
            tts_client=mocker.MagicMock(),
            memory_store=mocker.MagicMock(),
            overlay_controller=mocker.MagicMock(),
            hotkey_instance=mocker.MagicMock(),
        )
        mock_factory.assert_called_once()
        kwargs = mock_factory.call_args.kwargs
        assert "model_id" in kwargs
        assert "api_key" in kwargs
        assert nimbus._ai is mock_factory.return_value

    # --- Commit 2: parallel release capture -----------------

    def test_release_capture_runs_in_parallel_with_stt(self, mocker):
        """Commit 2: release-time capture worker must start BEFORE
        stt.stop_recording returns. Event handshake proves parallelism:
        STT blocks in stop_recording until the test releases it; capture
        is instrumented to signal when it's called. If capture_started
        fires WHILE stt_release is still unset, parallelism is proven.
        If the refactor regressed to serial, capture_started never fires
        until after stt_release — test times out.

        Pattern mirrors test_tts.py::test_prefetch_fires_before_previous_playback_completes.
        """
        import threading as _t
        from PIL import Image

        app = self._make_app(mocker)
        stt_release = _t.Event()
        capture_started = _t.Event()

        def blocking_stop_recording():
            if not stt_release.wait(timeout=2.0):
                raise AssertionError("stt_release never set")
            return "hello world"
        app._stt.stop_recording.side_effect = blocking_stop_recording

        fake_cap = mocker.MagicMock()
        fake_cap.image = Image.new("RGB", (1280, 800))
        fake_cap.label = "screen 1 of 1"
        fake_cap.scale_x = fake_cap.scale_y = 1.0
        fake_cap.monitor = {"left": 0, "top": 0, "width": 1920, "height": 1080}
        fake_cap.target_width = 1280
        fake_cap.target_height = 800
        fake_cap.is_cursor_screen = True

        def signal_capture():
            capture_started.set()
            return [fake_cap]
        mocker.patch("app.capture_all_screens", side_effect=signal_capture)
        mocker.patch("app.get_cursor_position", return_value=(500, 500))
        mocker.patch("app.list_monitors", return_value=[fake_cap.monitor])
        mocker.patch("app.monitor_containing", return_value=fake_cap.monitor)
        app._memory.recall.return_value = ""
        # Force re-capture path (no press-time capture available).
        app._press_captures = None
        app._press_cursor_pos = (0, 0)

        # Short-circuit Nimbus.
        fake_stream = mocker.MagicMock()
        fake_stream.text_deltas.return_value = iter([])
        fake_stream.final_result.return_value = mocker.MagicMock(
            spoken_text="ok", coordinate=None, element_label=None, screen_number=None,
        )
        app._ai.ask_stream.return_value.__enter__ = mocker.MagicMock(return_value=fake_stream)
        app._ai.ask_stream.return_value.__exit__ = mocker.MagicMock(return_value=False)

        app._handle_release()

        # PROOF OF PARALLELISM: capture must fire while STT is still blocked.
        assert capture_started.wait(timeout=2.0), (
            "release capture worker didn't fire capture_all_screens within 2s. "
            "Either the worker wasn't launched, or it's waiting on STT (regressed to serial)."
        )
        assert not stt_release.is_set(), (
            "capture_started fired AFTER stt_release — shouldn't happen but indicates test bug."
        )

        # Now release STT and let the pipeline finish cleanly.
        stt_release.set()
        if app._worker_thread is not None:
            app._worker_thread.join(timeout=3.0)

    def test_release_no_speech_bails_without_hanging(self, mocker):
        """Commit 2: if stt.stop_recording returns empty, pipeline_worker
        must bail out without hanging on capture_queue.get(). The worker
        thread finishes in the background as a daemon thread."""
        app = self._make_app(mocker)
        app._stt.stop_recording.return_value = ""  # no speech
        mocker.patch("app.get_cursor_position", return_value=(100, 100))
        mon = {"left": 0, "top": 0, "width": 1920, "height": 1080}
        mocker.patch("app.list_monitors", return_value=[mon])
        mocker.patch("app.monitor_containing", return_value=mon)
        mocker.patch("app.capture_all_screens", return_value=[mocker.MagicMock()])
        app._memory.recall.return_value = ""

        app._handle_release()

        # Pipeline worker should exit cleanly (not hang on queue.get).
        if app._worker_thread is not None:
            app._worker_thread.join(timeout=3.0)
            assert not app._worker_thread.is_alive(), (
                "pipeline_worker hung after no-speech bail — likely blocked on "
                "capture_queue.get() without a timeout or fallback"
            )
        # Nimbus must not have been called (no speech → no request).
        app._ai.ask_stream.assert_not_called()

    def test_release_capture_worker_hides_overlay_before_grab(self, mocker):
        """Invariant #3: overlay.hide_for_capture() MUST fire BEFORE every
        mss.grab(). If Nimbus sees our blue cursor in its input screenshot
        it tries to point at itself (infinite feedback loop).

        Test strategy: call _release_capture_worker DIRECTLY on the test
        thread (bypassing _handle_release) so the pyqtSignal slot dispatch
        fires synchronously. Cross-thread pyqtSignal.emit requires a
        QApplication event loop to fire the slot; calling the worker
        synchronously avoids that dependency.

        The worker's sig_hide_overlay.emit() → _on_hide_overlay slot →
        app._overlay.hide_for_capture() gives us the observable call on
        the mock overlay. Compare ordering vs capture_all_screens.
        """
        import queue as _queue

        app = self._make_app(mocker)
        app._memory.recall.return_value = ""
        app._press_captures = None  # force re-capture path
        app._press_cursor_pos = (0, 0)

        events = []
        mon = {"left": 0, "top": 0, "width": 1920, "height": 1080}

        app._overlay.hide_for_capture.side_effect = lambda: events.append("hide")

        def record_capture():
            events.append("capture")
            cap = mocker.MagicMock()
            cap.image = mocker.MagicMock()
            cap.label = "s"
            cap.scale_x = cap.scale_y = 1.0
            cap.monitor = mon
            cap.target_width = 1280
            cap.target_height = 800
            cap.is_cursor_screen = True
            return [cap]

        mocker.patch("app.capture_all_screens", side_effect=record_capture)

        # Call worker directly on this thread — slot dispatch is synchronous.
        result_queue: _queue.Queue = _queue.Queue(maxsize=1)
        app._release_capture_worker(
            release_cursor=(500, 500),
            app_name="TEST.EXE",
            result_queue=result_queue,
        )

        assert "hide" in events and "capture" in events, (
            f"Expected both hide+capture events, got {events}"
        )
        assert events.index("hide") < events.index("capture"), (
            f"INVARIANT #3 VIOLATION: hide must fire before capture, got order {events}"
        )
        # Worker must always push a result to the queue (never hang).
        assert not result_queue.empty(), "worker did not push result to queue"


# --- single-instance mutex ---------------------------------------

class TestSingleInstanceMutex:
    """Tests for app._acquire_single_instance_mutex.

    Bug fixed: clicking the installed Nimbus shortcut multiple times spawned
    multiple Nimbus processes. Each had its own pynput Listener, so one
    Ctrl+Alt+Space press fired N independent STT->Nimbus->TTS pipelines and
    the user heard N overlapping voice responses.

    Fix: Win32 named mutex acquired before QApplication construction. First
    process gets the mutex; second sees ERROR_ALREADY_EXISTS=183 and exits
    with a MessageBox telling the user to look in the system tray.
    """

    def test_first_instance_returns_handle(self, mocker):
        """First launch: CreateMutexW returns a handle, GetLastError is 0.
        Function returns the handle for module-global retention (kernel
        auto-releases on process exit)."""
        mock_kernel32 = mocker.MagicMock(name="kernel32")
        mock_kernel32.CreateMutexW.return_value = 12345  # fake HANDLE
        mock_kernel32.GetLastError.return_value = 0
        from app import _acquire_single_instance_mutex
        result = _acquire_single_instance_mutex(kernel32=mock_kernel32)
        assert result == 12345
        # CloseHandle MUST NOT be called — we keep the mutex alive.
        mock_kernel32.CloseHandle.assert_not_called()

    def test_second_instance_returns_none_and_closes_handle(self, mocker):
        """Second launch: CreateMutexW returns valid handle but GetLastError
        is ERROR_ALREADY_EXISTS=183. Function closes its handle (so we don't
        leak a kernel object) and returns None so caller can show messagebox
        and exit cleanly."""
        mock_kernel32 = mocker.MagicMock(name="kernel32")
        mock_kernel32.CreateMutexW.return_value = 67890
        mock_kernel32.GetLastError.return_value = 183
        from app import _acquire_single_instance_mutex
        result = _acquire_single_instance_mutex(kernel32=mock_kernel32)
        assert result is None
        mock_kernel32.CloseHandle.assert_called_once_with(67890)

    def test_create_mutex_failure_returns_fail_open_for_none(self, mocker):
        """If CreateMutexW genuinely fails it returns NULL — and ctypes maps
        c_void_p NULL to Python None (NOT integer 0) when restype is HANDLE.
        Fail open: don't block startup, accept the small risk of duplicates
        over leaving the user with a broken installer."""
        mock_kernel32 = mocker.MagicMock(name="kernel32")
        mock_kernel32.CreateMutexW.return_value = None  # ctypes NULL → None
        from app import _acquire_single_instance_mutex
        result = _acquire_single_instance_mutex(kernel32=mock_kernel32)
        assert result == "fail-open"
        # GetLastError must NOT be checked when CreateMutexW failed —
        # the handle is invalid, no point asking why.
        mock_kernel32.GetLastError.assert_not_called()

    def test_create_mutex_failure_returns_fail_open_for_zero(self, mocker):
        """Defensive: even if a future ctypes change or a different DI mock
        passes integer 0 instead of None for NULL, the `not handle` check
        must still trip the fail-open branch. Belt-and-suspenders against
        contributor confusion about which falsy NULL representation to use."""
        mock_kernel32 = mocker.MagicMock(name="kernel32")
        mock_kernel32.CreateMutexW.return_value = 0
        from app import _acquire_single_instance_mutex
        result = _acquire_single_instance_mutex(kernel32=mock_kernel32)
        assert result == "fail-open"
        mock_kernel32.GetLastError.assert_not_called()


# --- the startup licence gate ------------------------------------


class TestStartupLicenceGate:
    """The gate must actually run.

    It did not. For the life of the product, ``__main__``'s gate raised ``NameError: name 'theme' is
    not defined`` -- ``theme`` is imported roughly 200 lines below the gate that used it -- and the
    caller's ``except Exception`` swallowed it so that a licence bug could never lock out a paying
    customer. Correct policy, and it meant every shipped build started unlicensed and never showed
    the activation screen. The windowed EXE has no stdout, so the line saying "gate skipped" was
    invisible on every affected machine.

    These tests execute the real function body, so an unbound name is a failure here rather than a
    silence in production. That is the whole reason the block became a function.
    """

    def _qt_app(self, mocker):
        """A stand-in for QApplication that records the stylesheet it was handed."""
        return mocker.MagicMock(name="qt_app")

    def test_an_unlicensed_machine_is_asked_to_activate(self, mocker):
        """The case that was broken. Nothing is mocked inside the gate except the dialog itself, so
        the imports and the stylesheet call are genuinely executed."""
        import app

        flow = mocker.patch("activation_dialog.run_activation_flow", return_value=True)
        qt_app = self._qt_app(mocker)

        assert app._run_startup_licence_gate(qt_app) is True
        flow.assert_called_once()
        qt_app.setStyleSheet.assert_called_once()
        assert qt_app.setStyleSheet.call_args[0][0], "the gate must be styled, not stock Qt"

    def test_declining_reports_false_so_startup_can_stop(self, mocker):
        """`False` is what stops Nimbus before the hotkey installs and the mic opens."""
        import app

        mocker.patch("activation_dialog.run_activation_flow", return_value=False)

        assert app._run_startup_licence_gate(self._qt_app(mocker)) is False

    def test_the_gate_needs_no_name_from_main(self):
        """Its imports are local to it, so ``__main__``'s line order cannot break it again.

        Reads the source rather than the behaviour on purpose: the bug was a name resolved from an
        enclosing scope that happened to be populated later, and no amount of calling the function
        with mocks proves the next contributor will not reintroduce that.
        """
        import inspect

        import app

        body = inspect.getsource(app._run_startup_licence_gate)
        for name in ("import theme", "import licensing", "run_activation_flow"):
            assert name in body, f"the gate must import {name!r} itself"

    def test_a_skipped_gate_is_written_somewhere_readable(self, tmp_path, monkeypatch):
        """A windowed build has no stdout, which is why this bug survived.

        ``_log`` alone was not enough to notice a gate that never ran, so the failure also lands in a
        file that someone can be asked to send.
        """
        import app

        monkeypatch.setattr(app.os.path, "expanduser", lambda _path: str(tmp_path))
        app._record_licence_gate_failure(NameError("name 'theme' is not defined"))

        log = tmp_path / ".nimbus" / "licence-gate-error.log"
        assert log.is_file()
        assert "NameError" in log.read_text(encoding="utf-8")

    def test_recording_a_failure_never_raises(self, mocker):
        """It runs on the path where something already went wrong; it must not add to it."""
        import app

        mocker.patch.object(app.os.path, "expanduser", side_effect=RuntimeError("no home"))
        app._record_licence_gate_failure(RuntimeError("boom"))  # must not raise


# --- raising the running instance --------------------------------


class TestShowWindowOnSecondLaunch:
    """The other half of single-instance: a second launch should *show* Nimbus.

    Stopping the duplicate process was correct and left the launch itself doing nothing useful --
    the user double-clicked the shortcut and got a dialog explaining that Nimbus was already
    running and that a tray icon existed somewhere. Every tray application raises its window
    instead, because clicking a shortcut is the request to see the app.

    Mechanism: a named auto-reset event. The first instance creates it and waits on it; a later
    launch opens it, sets it, and exits silently. Same DI-a-mock-kernel32 pattern as the mutex.
    """

    def test_setting_the_event_reports_success(self, mocker):
        mock_kernel32 = mocker.MagicMock(name="kernel32")
        mock_kernel32.OpenEventW.return_value = 4242
        mock_kernel32.SetEvent.return_value = 1
        from app import _signal_existing_instance

        assert _signal_existing_instance(kernel32=mock_kernel32) is True
        mock_kernel32.SetEvent.assert_called_once_with(4242)
        # The handle is ours, not the kernel object's only reference: closing it must not be skipped.
        mock_kernel32.CloseHandle.assert_called_once_with(4242)

    def test_no_event_reports_failure_so_the_caller_can_explain_itself(self, mocker):
        """An older Nimbus in the tray has no event to open.

        `False` must not be silent-exit: after a double-click, a process that vanishes without a
        window or a message is indistinguishable from a crash, so the caller falls back to the
        messagebox.
        """
        mock_kernel32 = mocker.MagicMock(name="kernel32")
        mock_kernel32.OpenEventW.return_value = None
        from app import _signal_existing_instance

        assert _signal_existing_instance(kernel32=mock_kernel32) is False
        mock_kernel32.SetEvent.assert_not_called()

    def test_a_refused_set_reports_failure_rather_than_pretending(self, mocker):
        mock_kernel32 = mocker.MagicMock(name="kernel32")
        mock_kernel32.OpenEventW.return_value = 7
        mock_kernel32.SetEvent.return_value = 0
        from app import _signal_existing_instance

        assert _signal_existing_instance(kernel32=mock_kernel32) is False
        mock_kernel32.CloseHandle.assert_called_once_with(7)

    def test_the_watcher_creates_an_auto_reset_event_that_starts_unsignalled(self, mocker):
        """Both flags matter.

        Manual-reset would fire once and then stay hot, showing the window forever. An initial state
        of signalled would raise the window at startup for nobody.
        """
        mock_kernel32 = mocker.MagicMock(name="kernel32")
        mock_kernel32.CreateEventW.return_value = 99
        mock_kernel32.WaitForSingleObject.return_value = 0x102  # WAIT_TIMEOUT
        from app import _watch_for_show_requests

        assert _watch_for_show_requests(
            lambda: None, kernel32=mock_kernel32, should_stop=lambda: True) is True

        _security, manual_reset, initial_state, name = mock_kernel32.CreateEventW.call_args[0]
        assert manual_reset is False
        assert initial_state is False
        assert name == "Local\\NimbusWindows-ShowWindow-v1"

    def test_a_signalled_event_asks_for_the_window(self, mocker):
        mock_kernel32 = mocker.MagicMock(name="kernel32")
        mock_kernel32.CreateEventW.return_value = 5
        mock_kernel32.WaitForSingleObject.return_value = 0  # WAIT_OBJECT_0
        asked = []
        started = []
        mocker.patch("app.threading.Thread",
                     side_effect=lambda **kwargs: started.append(kwargs) or mocker.MagicMock())
        from app import _watch_for_show_requests

        _watch_for_show_requests(lambda: asked.append(True), kernel32=mock_kernel32,
                                 should_stop=lambda: len(asked) >= 1)
        started[0]["target"]()

        assert asked == [True]
        assert started[0]["daemon"] is True, "must not keep Nimbus alive at exit"

    def test_a_failure_to_create_the_event_is_not_fatal(self, mocker):
        """It costs the convenience of raising the window and nothing else. The tray icon still
        works, so refusing to start over this would be the wrong trade."""
        mock_kernel32 = mocker.MagicMock(name="kernel32")
        mock_kernel32.CreateEventW.return_value = None
        from app import _watch_for_show_requests

        assert _watch_for_show_requests(lambda: None, kernel32=mock_kernel32) is False

    def test_a_raising_callback_never_escapes_the_wait_thread(self, mocker):
        """An exception out of a bare thread is an unhandled-exception dialog and a dead watcher."""
        mock_kernel32 = mocker.MagicMock(name="kernel32")
        mock_kernel32.CreateEventW.return_value = 5
        mock_kernel32.WaitForSingleObject.return_value = 0
        started = []
        mocker.patch("app.threading.Thread",
                     side_effect=lambda **kwargs: started.append(kwargs) or mocker.MagicMock())
        calls = []

        def boom():
            calls.append(True)
            raise RuntimeError("window gone")

        from app import _watch_for_show_requests

        _watch_for_show_requests(boom, kernel32=mock_kernel32,
                                 should_stop=lambda: len(calls) >= 1)
        started[0]["target"]()  # must not raise

        assert calls == [True]


# --- TTS factory dispatch -----------------------------------------


class TestTTSProviderDispatch:
    """The main block must construct the right TTS subclass based on
    config.TTS_PROVIDER. This test mocks the helper's dependencies +
    verifies it returns the right (provider, api_key) tuple.

    The main block is gated by ``if __name__ == "__main__"`` so we test
    the helper (a small extracted function) directly, not the full
    main-block flow."""

    def test_resolve_tts_credentials_for_cartesia(self, mocker):
        """Helper returns (provider, api_key) tuple — Cartesia path."""
        mocker.patch("app.resolve_setting", return_value="cartesia")
        mocker.patch("app.resolve_api_key", side_effect=lambda name: {
            "CARTESIA_API_KEY": "sk_car_test",
            "ELEVENLABS_API_KEY": None,
        }[name])
        from app import _resolve_tts_credentials
        provider, api_key = _resolve_tts_credentials()
        assert provider == "cartesia"
        assert api_key == "sk_car_test"

    def test_resolve_tts_credentials_for_elevenlabs(self, mocker):
        """Helper returns (provider, api_key) tuple — ElevenLabs path."""
        mocker.patch("app.resolve_setting", return_value="elevenlabs")
        mocker.patch("app.resolve_api_key", side_effect=lambda name: {
            "CARTESIA_API_KEY": None,
            "ELEVENLABS_API_KEY": "eleven_test",
        }[name])
        from app import _resolve_tts_credentials
        provider, api_key = _resolve_tts_credentials()
        assert provider == "elevenlabs"
        assert api_key == "eleven_test"


# --- Grid-locator fallback (Ollama pixel-pointing) --------------------

class TestLooksDirectional:
    """Tests for app._looks_directional — cheap pre-filter before firing grid-locator."""

    def test_returns_true_for_where_question(self):
        from app import _looks_directional
        assert _looks_directional("where is the save button") is True

    def test_returns_true_for_click_command(self):
        from app import _looks_directional
        assert _looks_directional("click on settings") is True

    def test_returns_true_for_show_me(self):
        from app import _looks_directional
        assert _looks_directional("show me how to open the inspector") is True

    def test_returns_true_for_find(self):
        from app import _looks_directional
        assert _looks_directional("find the user dropdown") is True

    def test_returns_false_for_conceptual_question(self):
        from app import _looks_directional
        assert _looks_directional("what is HTML") is False
        assert _looks_directional("explain async iterators") is False

    def test_returns_false_for_empty_or_none(self):
        from app import _looks_directional
        assert _looks_directional("") is False
        assert _looks_directional(None) is False

    def test_case_insensitive(self):
        from app import _looks_directional
        assert _looks_directional("WHERE is the menu") is True
        assert _looks_directional("Click The Button") is True


class TestGroundingRefinement:
    def _capture(self):
        from PIL import Image
        cap = type("Capture", (), {})()
        cap.image = Image.new("RGB", (1000, 500), "white")
        cap.source_image = Image.new("RGB", (2000, 1000), "white")
        cap.target_width, cap.target_height = 1000, 500
        cap.monitor = {"left": 100, "top": 50, "width": 2000, "height": 1000}
        cap.cursor_physical = (1100, 550)
        return cap

    def test_cursor_reference_uses_captured_mouse_as_refinement_seed(self, mocker):
        from app import _refine_model_coordinate
        cap = self._capture()
        refine = mocker.patch("app.refine_point_via_crop", return_value=(1200, 600))
        result = _refine_model_coordinate(
            ai_client=mocker.MagicMock(), capture=cap, model_x=100, model_y=100,
            target="button", query="what is this near my cursor?",
        )
        assert result == (600, 300)
        assert refine.call_args.kwargs["seed_x"] == 1000
        assert refine.call_args.kwargs["seed_y"] == 500

    def test_failed_refinement_keeps_primary_result(self, mocker):
        from app import _refine_model_coordinate
        mocker.patch("app.refine_point_via_crop", return_value=None)
        assert _refine_model_coordinate(
            ai_client=mocker.MagicMock(), capture=self._capture(), model_x=100,
            model_y=100, target="button", query="where is the button",
        ) is None

    def test_tutor_circle_and_arrow_head_are_refined(self, mocker):
        from app import _refine_annotations
        from annotations import Arrow, Circle
        cap = self._capture()
        refine = mocker.patch("app._refine_model_coordinate", side_effect=[(11, 12), (21, 22)])
        annotations = [Circle(1, 2, 8, "save"), Arrow(3, 4, 5, 6)]
        assert _refine_annotations(mocker.MagicMock(), annotations, cap, "where is save") == [
            Circle(11, 12, 8, "save"), Arrow(3, 4, 21, 22),
        ]
        assert refine.call_count == 2


class TestMaybeLocateViaGrid:
    """Tests for app._maybe_locate_via_grid — only fires for Ollama + directional + no coord."""

    def _mock_capture(self, mocker):
        """Build a fake capture.LabeledCapture with a 200x100 white screenshot."""
        from PIL import Image
        capture = mocker.MagicMock()
        capture.image = Image.new("RGB", (200, 100), color="white")
        capture.target_width = 200
        capture.target_height = 100
        capture.monitor = {"left": 0, "top": 0, "width": 200, "height": 100}
        capture.scale_x = 1.0
        capture.scale_y = 1.0
        return capture

    def _mock_result(self, coordinate):
        """Build a fake PointParseResult with given coordinate (None or (x,y))."""
        from ai import PointParseResult
        return PointParseResult(
            spoken_text="some response",
            coordinate=coordinate,
            element_label=None,
            screen_number=None,
        )

    def test_skipped_when_ai_client_is_not_ollama(self, mocker):
        """Anthropic / Gemini paths should never trigger grid-locator — they have
        their own native [POINT:x,y] tag emission."""
        from app import _maybe_locate_via_grid
        from ai import AnthropicClient
        mock_locator = mocker.patch("app.locate_via_grid")

        mock_anthropic = mocker.MagicMock(spec=AnthropicClient)
        capture = self._mock_capture(mocker)
        result = self._mock_result(coordinate=None)

        out = _maybe_locate_via_grid(
            ai_client=mock_anthropic,
            result=result,
            cursor_capture=capture,
            query="where is the save button",
        )
        assert out is None
        mock_locator.assert_not_called()

    def test_fires_for_openai_vision_client(self, mocker):
        """GPT-4o is weak at raw pixel coords (like Ollama), so the
        grid-locator must fire for OpenAIVisionClient too — directional query,
        no coordinate in the result."""
        from app import _maybe_locate_via_grid
        from ai import OpenAIVisionClient
        mocker.patch("ai.OpenAI")  # don't build a real OpenAI client
        mock_locator = mocker.patch("app.locate_via_grid", return_value=(123, 45))

        openai_client = OpenAIVisionClient(api_key="sk-proj-test", model_id="openai/gpt-4o")
        capture = self._mock_capture(mocker)
        result = self._mock_result(coordinate=None)

        out = _maybe_locate_via_grid(
            ai_client=openai_client,
            result=result,
            cursor_capture=capture,
            query="where is the save button",
        )
        assert out == (123, 45)
        mock_locator.assert_called_once()

    def test_skipped_when_result_already_has_coordinate(self, mocker):
        """If Nimbus returned a [POINT:x,y] tag, the locator is unnecessary."""
        from app import _maybe_locate_via_grid
        from ai import OllamaClient
        mock_locator = mocker.patch("app.locate_via_grid")

        mock_ollama = OllamaClient(host="http://localhost:11434", model_id="ollama/llama3.2-vision")
        capture = self._mock_capture(mocker)
        result = self._mock_result(coordinate=(640, 400))

        out = _maybe_locate_via_grid(
            ai_client=mock_ollama,
            result=result,
            cursor_capture=capture,
            query="where is the save button",
        )
        assert out is None
        mock_locator.assert_not_called()

    def test_skipped_when_query_not_directional(self, mocker):
        """Conceptual questions ('what is HTML') should skip grid-locator —
        no UI element to point at, would waste 2 LLM calls."""
        from app import _maybe_locate_via_grid
        from ai import OllamaClient
        mock_locator = mocker.patch("app.locate_via_grid")

        mock_ollama = OllamaClient(host="http://localhost:11434", model_id="ollama/llama3.2-vision")
        capture = self._mock_capture(mocker)
        result = self._mock_result(coordinate=None)

        out = _maybe_locate_via_grid(
            ai_client=mock_ollama,
            result=result,
            cursor_capture=capture,
            query="what is HTML",
        )
        assert out is None
        mock_locator.assert_not_called()

    def test_fires_for_ollama_no_coord_directional_query(self, mocker):
        """Happy path: Ollama + no coord + directional query → locator runs."""
        from app import _maybe_locate_via_grid
        from ai import OllamaClient
        mock_locator = mocker.patch("app.locate_via_grid", return_value=(450, 300))

        mock_ollama = OllamaClient(host="http://localhost:11434", model_id="ollama/llama3.2-vision")
        capture = self._mock_capture(mocker)
        result = self._mock_result(coordinate=None)

        out = _maybe_locate_via_grid(
            ai_client=mock_ollama,
            result=result,
            cursor_capture=capture,
            query="where is the save button",
        )
        assert out == (450, 300)
        mock_locator.assert_called_once()
        kwargs = mock_locator.call_args.kwargs
        # Caller uses physical-coords convention (dpi_scale=1.0)
        assert kwargs["dpi_scale"] == 1.0
        assert kwargs["llm_client"] is mock_ollama
        assert kwargs["query"] == "where is the save button"

    def test_passes_correct_physical_size_and_origin(self, mocker):
        """Locator receives monitor's physical_size + physical_origin from capture."""
        from app import _maybe_locate_via_grid
        from ai import OllamaClient
        mock_locator = mocker.patch("app.locate_via_grid", return_value=(100, 50))

        mock_ollama = OllamaClient(host="http://localhost:11434", model_id="ollama/llama3.2-vision")
        capture = self._mock_capture(mocker)
        # Override to simulate a secondary monitor
        capture.monitor = {"left": 1920, "top": 0, "width": 2560, "height": 1440}
        result = self._mock_result(coordinate=None)

        _maybe_locate_via_grid(
            ai_client=mock_ollama,
            result=result,
            cursor_capture=capture,
            query="click the menu",
        )
        kwargs = mock_locator.call_args.kwargs
        assert kwargs["physical_size"] == (2560, 1440)
        assert kwargs["physical_origin"] == (1920, 0)

    def test_logs_skip_reason_when_query_not_directional(self, mocker):
        """When skipped due to non-directional query, dbg.log is called with reason."""
        from app import _maybe_locate_via_grid
        from ai import OllamaClient
        mocker.patch("app.locate_via_grid")

        mock_ollama = OllamaClient(host="http://localhost:11434", model_id="ollama/llama3.2-vision")
        capture = self._mock_capture(mocker)
        result = self._mock_result(coordinate=None)
        mock_dbg = mocker.MagicMock()

        _maybe_locate_via_grid(
            ai_client=mock_ollama,
            result=result,
            cursor_capture=capture,
            query="what is HTML",
            dbg=mock_dbg,
        )
        log_messages = [c.args[0] for c in mock_dbg.log.call_args_list]
        assert any("GRID-LOCATOR" in m and "not directional" in m for m in log_messages)

    def test_logs_hit_when_locator_returns_coords(self, mocker):
        """When grid-locator returns coords, dbg.log records the hit with coords."""
        from app import _maybe_locate_via_grid
        from ai import OllamaClient
        mocker.patch("app.locate_via_grid", return_value=(450, 300))

        mock_ollama = OllamaClient(host="http://localhost:11434", model_id="ollama/llama3.2-vision")
        capture = self._mock_capture(mocker)
        result = self._mock_result(coordinate=None)
        mock_dbg = mocker.MagicMock()

        _maybe_locate_via_grid(
            ai_client=mock_ollama,
            result=result,
            cursor_capture=capture,
            query="where is save",
            dbg=mock_dbg,
        )
        log_messages = [c.args[0] for c in mock_dbg.log.call_args_list]
        assert any("GRID-LOCATOR" in m and "hit" in m and "450" in m for m in log_messages)

    def test_passes_debug_log_to_locator_for_structured_logging(self, mocker):
        """_maybe_locate_via_grid must thread dbg.log into
        locate_via_grid so transport failures are distinguishable from
        model uncertainty in the debug log."""
        from app import _maybe_locate_via_grid
        from ai import OllamaClient
        mock_locator = mocker.patch("app.locate_via_grid", return_value=(100, 50))

        mock_ollama = OllamaClient(host="http://localhost:11434", model_id="ollama/llama3.2-vision")
        capture = self._mock_capture(mocker)
        result = self._mock_result(coordinate=None)
        mock_dbg = mocker.MagicMock()

        _maybe_locate_via_grid(
            ai_client=mock_ollama,
            result=result,
            cursor_capture=capture,
            query="click thing",
            dbg=mock_dbg,
        )
        # locate_via_grid received debug_log kwarg pointing at dbg.log
        kwargs = mock_locator.call_args.kwargs
        assert kwargs.get("debug_log") is mock_dbg.log


# --- Regression: LLM_PROVIDER routing -------------------------

class TestResolveLLMCredentials:
    """Regression tests for an earlier bug: Settings dropdown
    persisted LLM_PROVIDER=ollama but startup ignored it and constructed
    AnthropicClient anyway. The Settings dropdown was cosmetic."""

    def test_defaults_to_anthropic_path(self, mocker):
        """No LLM_PROVIDER + no MODEL_ID override → defaults to
        anthropic/model-sonnet-4-6. resolve_setting is keyed (LLM_PROVIDER vs
        ANTHROPIC_MODEL), and MODEL_ID env is forced empty so the test exercises
        the default model-resolution path regardless of the local .env."""
        from app import _resolve_llm_credentials

        mocker.patch(
            "app.resolve_setting",
            side_effect=lambda name, default=None: {"LLM_PROVIDER": "anthropic"}.get(name, default),
        )
        mocker.patch("app.os.getenv", return_value=None)  # no MODEL_ID env override
        mocker.patch("app.ANTHROPIC_API_KEY", "sk-ant-test")

        model_id, api_key = _resolve_llm_credentials()
        # T0-1: was asserted as the literal "anthropic/model-sonnet-4-6" — a
        # placeholder left behind when vendor model names were scrubbed, which
        # no endpoint could resolve, so selecting Anthropic failed every time.
        # Now asserted against the config constant so the test tracks the
        # shipped default instead of duplicating it.
        import config
        assert model_id == f"anthropic/{config.DEFAULT_ANTHROPIC_MODEL}"
        assert "model-sonnet" not in model_id
        assert api_key == "sk-ant-test"

    def test_routes_to_ollama_when_provider_is_ollama(self, mocker):
        """LLM_PROVIDER=ollama → returns ('ollama/<vision-model>', '').
        Without the fix, this returned MODEL_ID / ANTHROPIC_API_KEY
        and the Settings dropdown was silently ignored."""
        from app import _resolve_llm_credentials

        mocker.patch("app.resolve_setting", return_value="ollama")
        mocker.patch("app.OLLAMA_MODEL_VISION", "llama3.2-vision")

        model_id, api_key = _resolve_llm_credentials()
        assert model_id == "ollama/llama3.2-vision"
        assert api_key == "", "Ollama path must return empty key (unauthenticated local)"

    def test_routes_to_openai_when_provider_is_openai(self, mocker):
        """LLM_PROVIDER=openai → returns ('openai/<vision-model>',
        OPENAI_API_KEY). create_ai_client routes the openai/ prefix to
        OpenAIVisionClient."""
        from app import _resolve_llm_credentials

        mocker.patch("app.resolve_setting", return_value="openai")
        mocker.patch("app.OPENAI_MODEL_VISION", "gpt-4o")
        mocker.patch("app.OPENAI_API_KEY", "sk-proj-test")

        model_id, api_key = _resolve_llm_credentials()
        assert model_id == "openai/gpt-4o"
        assert api_key == "sk-proj-test"

    def test_openai_path_handles_none_api_key(self, mocker):
        """OPENAI_API_KEY None + no cached OpenRouter key anywhere → empty
        string, not None (create_ai_client wants str)."""
        from app import _resolve_llm_credentials

        mocker.patch("app.resolve_setting", return_value="openai")
        mocker.patch("app.OPENAI_MODEL_VISION", "gpt-4o")
        mocker.patch("app.OPENAI_API_KEY", None)
        mocker.patch("app.ANTHROPIC_API_KEY", None)
        mocker.patch("app.GEMINI_API_KEY", None)

        model_id, api_key = _resolve_llm_credentials()
        assert api_key == ""

    def test_openai_reuses_cached_openrouter_key(self, mocker):
        """OpenAI slot empty but an sk-or- key cached (here in the
        Anthropic slot) → OpenAI reuses it. One OpenRouter key serves all."""
        from app import _resolve_llm_credentials

        mocker.patch("app.resolve_setting", return_value="openai")
        mocker.patch("app.OPENAI_MODEL_VISION", "gpt-5.4")
        mocker.patch("app.OPENAI_API_KEY", None)
        mocker.patch("app.ANTHROPIC_API_KEY", "sk-or-v1-test")
        mocker.patch("app.GEMINI_API_KEY", None)

        model_id, api_key = _resolve_llm_credentials()
        assert model_id == "openai/gpt-5.4"
        assert api_key == "sk-or-v1-test"

    def test_direct_key_wins_over_cached_openrouter(self, mocker):
        """A direct key in the provider's own slot beats a cached OpenRouter key."""
        from app import _resolve_llm_credentials

        mocker.patch("app.resolve_setting", return_value="openai")
        mocker.patch("app.OPENAI_MODEL_VISION", "gpt-5.4")
        mocker.patch("app.OPENAI_API_KEY", "sk-proj-direct")
        mocker.patch("app.ANTHROPIC_API_KEY", "sk-or-v1-test")
        mocker.patch("app.GEMINI_API_KEY", None)

        _, api_key = _resolve_llm_credentials()
        assert api_key == "sk-proj-direct"

    def test_openai_realtime_resolves_to_gpt4o_vision_client(self, mocker):
        """LLM_PROVIDER=openai-realtime still builds a valid GPT-4o
        vision client as the main ai_client (used by the realtime path's
        grid-locator refinement). The realtime speech-to-speech session runs
        as a parallel pipeline (_setup_realtime), not via this resolver."""
        from app import _resolve_llm_credentials

        mocker.patch("app.resolve_setting", return_value="openai-realtime")
        mocker.patch("app.OPENAI_MODEL_VISION", "gpt-4o")
        mocker.patch("app.OPENAI_API_KEY", "sk-proj-test")

        model_id, api_key = _resolve_llm_credentials()
        assert model_id == "openai/gpt-4o"
        assert api_key == "sk-proj-test"

    def test_anthropic_path_handles_none_api_key(self, mocker):
        """If ANTHROPIC_API_KEY is None (resolve_api_key returned nothing),
        we must return empty string (not None) — create_ai_client expects str."""
        from app import _resolve_llm_credentials

        mocker.patch("app.resolve_setting", return_value="anthropic")
        mocker.patch("app.MODEL_ID", "anthropic/model-sonnet-4-6")
        mocker.patch("app.ANTHROPIC_API_KEY", None)
        mocker.patch("app.OPENAI_API_KEY", None)
        mocker.patch("app.GEMINI_API_KEY", None)

        model_id, api_key = _resolve_llm_credentials()
        assert api_key == ""

    def test_unknown_provider_falls_back_to_anthropic(self, mocker):
        """If LLM_PROVIDER is a string we don't recognize (forward-compat
        for some future provider that gets removed), fall back to Anthropic
        path. Don't crash."""
        from app import _resolve_llm_credentials

        mocker.patch(
            "app.resolve_setting",
            side_effect=lambda name, default=None: {"LLM_PROVIDER": "bogus-provider"}.get(name, default),
        )
        mocker.patch("app.os.getenv", return_value=None)  # no MODEL_ID env override
        mocker.patch("app.ANTHROPIC_API_KEY", "sk-ant-test")

        model_id, api_key = _resolve_llm_credentials()
        # T0-1: was "anthropic/model-sonnet-4-6", a scrubbed placeholder that no
        # endpoint could resolve. Asserts against the config constant so this
        # test cannot drift from the shipped default.
        import config
        assert model_id == f"anthropic/{config.DEFAULT_ANTHROPIC_MODEL}"
        assert "model-sonnet" not in model_id
        # Original intent preserved: an unknown provider falls back to the
        # anthropic branch rather than raising.
        assert api_key == "sk-ant-test"
        assert api_key == "sk-ant-test"


class TestAnnotationsToPhysical:
    """map screenshot-space annotation coords to physical
    virtual-desktop coords via the SAME unscale_model_coords transform the
    [POINT] cursor uses (proven correct by the 253,52->569,117 marker)."""

    def _cap(self, mocker, scale=2.25):
        cap = mocker.MagicMock()
        cap.scale_x = scale
        cap.scale_y = scale
        cap.monitor = {"left": 0, "top": 0, "width": 2880, "height": 1800}
        cap.target_width = 1280
        cap.target_height = 800
        return cap

    def test_circle_uses_proven_marker_transform(self, mocker):
        from app import _annotations_to_physical
        from annotations import Circle
        out = _annotations_to_physical([Circle(253, 52, 10, "save")], self._cap(mocker))
        # 253*2.25=569, 52*2.25=117, r 10*2.25=22 — the exact proven numbers
        assert out == [Circle(569, 117, 22, "save")]

    def test_arrow_both_endpoints(self, mocker):
        from app import _annotations_to_physical
        from annotations import Arrow
        cap = self._cap(mocker, scale=2.0)
        out = _annotations_to_physical([Arrow(10, 20, 30, 40)], cap)
        assert out == [Arrow(20, 40, 60, 80)]

    def test_underline_width_scaled(self, mocker):
        from app import _annotations_to_physical
        from annotations import Underline
        cap = self._cap(mocker, scale=2.0)
        out = _annotations_to_physical([Underline(10, 20, 50)], cap)
        assert out == [Underline(20, 40, 100)]

    def test_label_coords_mapped_text_preserved(self, mocker):
        from app import _annotations_to_physical
        from annotations import Label
        cap = self._cap(mocker, scale=2.0)
        out = _annotations_to_physical([Label(10, 20, "use the chain rule")], cap)
        assert out == [Label(20, 40, "use the chain rule")]

    def test_respects_monitor_origin_offset(self, mocker):
        from app import _annotations_to_physical
        from annotations import Circle
        cap = self._cap(mocker, scale=2.0)
        cap.monitor = {"left": 2560, "top": 100, "width": 1920, "height": 1080}
        out = _annotations_to_physical([Circle(10, 20, 5, "x")], cap)
        # 2560 + 10*2 = 2580 ; 100 + 20*2 = 140 ; r 5*2 = 10
        assert out == [Circle(2580, 140, 10, "x")]

    def test_empty_returns_empty(self, mocker):
        from app import _annotations_to_physical
        assert _annotations_to_physical([], mocker.MagicMock()) == []


class TestShouldConnectStt:
    """Two-mic fix: skip the AssemblyAI STT mic in realtime mode (GPT-Realtime
    owns the 24kHz mic; opening the 16kHz STT mic too = no audio)."""

    def test_connects_stt_in_normal_mode(self):
        from app import _should_connect_stt
        assert _should_connect_stt(None) is True

    def test_skips_stt_in_realtime_mode(self, mocker):
        from app import _should_connect_stt
        fake_realtime = mocker.MagicMock()  # a live RealtimeSession
        assert _should_connect_stt(fake_realtime) is False


class TestRealtimeDrawShapes:
    """B: realtime mode does an ACCURATE vision pass (gpt-5.4) — draws
    shapes when draw mode is on, else points the cursor. No grid-locator."""

    def _app(self, mocker):
        from app import NimbusApp
        return NimbusApp(
            ai_client=mocker.MagicMock(),
            stt_client=mocker.MagicMock(),
            tts_client=mocker.MagicMock(),
            memory_store=mocker.MagicMock(),
            overlay_controller=mocker.MagicMock(),
            hotkey_instance=mocker.MagicMock(),
        )

    def _cap(self, mocker):
        cap = mocker.MagicMock()
        cap.image = mocker.MagicMock()  # PIL image (not used by the mocked client)
        cap.scale_x = 2.25
        cap.scale_y = 2.25
        cap.monitor = {"left": 0, "top": 0, "width": 2880, "height": 1800}
        cap.target_width = 1280
        cap.target_height = 800
        return cap

    def test_render_annotations_parses_shapes_and_transforms(self, mocker):
        from annotations import Circle
        app = self._app(mocker)
        cap = self._cap(mocker)

        class _FakeStream:
            def __enter__(self_):
                return self_
            def __exit__(self_, *a):
                return False
            def text_deltas(self_):
                return iter(["circle it [CIRCLE:253,52,30:save]"])
            def final_result(self_):
                r = mocker.MagicMock()
                r.spoken_text = "circle it [CIRCLE:253,52,30:save]"
                return r

        vision = mocker.MagicMock()
        vision.ask_stream.return_value = _FakeStream()
        shapes = app._realtime_render_annotations("save", cap, vision)
        # 253*2.25=569, 52*2.25=117, r 30*2.25=67 — the proven ×scale transform
        assert shapes == [Circle(569, 117, 67, "save")]
        # annotation prompt was used (system_prompt kwarg)
        _, kwargs = vision.ask_stream.call_args
        from ai import _NIMBUS_ANNOTATION_SYSTEM_PROMPT
        assert kwargs["system_prompt"] == _NIMBUS_ANNOTATION_SYSTEM_PROMPT

    def test_locate_point_uses_ask_and_unscales(self, mocker):
        app = self._app(mocker)
        cap = self._cap(mocker)
        vision = mocker.MagicMock()
        vision.ask.return_value = {
            "text": "the save button",
            "points": [{"x": 253, "y": 52, "label": "save"}],
        }
        phys = app._realtime_locate_point("save", cap, vision)
        assert phys == (569, 117)

    def test_locate_point_returns_none_when_no_points(self, mocker):
        app = self._app(mocker)
        cap = self._cap(mocker)
        vision = mocker.MagicMock()
        vision.ask.return_value = {"text": "conceptual", "points": []}
        assert app._realtime_locate_point("x", cap, vision) is None


class TestResolveSTTCredentials:
    def test_defaults_to_assemblyai(self, mocker):
        from app import _resolve_stt_credentials
        mocker.patch("app.resolve_setting", return_value="assemblyai")
        mocker.patch("app.resolve_api_key", return_value="aai-key")
        provider, key = _resolve_stt_credentials()
        assert provider == "assemblyai"
        assert key == "aai-key"

    def test_faster_whisper_has_no_key(self, mocker):
        from app import _resolve_stt_credentials
        mocker.patch("app.resolve_setting", return_value="faster-whisper")
        provider, key = _resolve_stt_credentials()
        assert provider == "faster-whisper"
        assert key == ""


class TestResolveTTSKokoro:
    def test_kokoro_has_no_key(self, mocker):
        from app import _resolve_tts_credentials
        mocker.patch("app.resolve_setting", return_value="kokoro")
        provider, key = _resolve_tts_credentials()
        assert provider == "kokoro"
        assert key == ""


class TestResolveLLMGemini:
    def test_routes_to_gemini(self, mocker):
        from app import _resolve_llm_credentials
        mocker.patch("app.resolve_setting", return_value="gemini")
        mocker.patch("app.GEMINI_MODEL_VISION", "google/gemini-3.5-flash")
        mocker.patch("app.GEMINI_API_KEY", "sk-or-test")
        model_id, api_key = _resolve_llm_credentials()
        assert model_id == "google/gemini-3.5-flash"
        assert api_key == "sk-or-test"


# --- T0-5: interaction audio cues --------------------------------------------

class TestFeedbackTones:
    """T0-5: audio cues are UX-only and must never raise into the pipeline.

    `_play_chime_async` used to sit here as a backward-compatible alias whose
    body was unreachable dead code after an early `return`. It has been removed;
    `_play_feedback_tone_async` is the single implementation. These tests pin the
    behaviour that made the deletion safe.
    """

    def test_feedback_tone_spec_returns_known_kinds(self):
        from app import _feedback_tone_spec
        for kind in ("listening", "done", "error"):
            freqs, duration = _feedback_tone_spec(kind)
            assert isinstance(freqs, tuple) and freqs, f"{kind} has no frequencies"
            assert all(f > 0 for f in freqs), f"{kind} has a non-positive frequency"
            assert 0 < duration < 1.0, f"{kind} duration {duration} is not a short cue"

    def test_distinct_kinds_are_distinguishable(self):
        """The three cues must actually differ, or they convey nothing."""
        from app import _feedback_tone_spec
        specs = {k: _feedback_tone_spec(k) for k in ("listening", "done", "error")}
        assert len(set(specs.values())) == 3

    def test_unknown_kind_falls_back_to_listening(self):
        from app import _feedback_tone_spec
        assert _feedback_tone_spec("not-a-real-cue") == _feedback_tone_spec("listening")

    def test_play_feedback_tone_swallows_device_errors(self, mocker):
        """The deliberate error-swallowing that keeps a missing, busy, or
        misconfigured audio device from breaking push-to-talk."""
        import sounddevice
        mocker.patch.object(
            sounddevice, "play", side_effect=RuntimeError("no output device")
        )
        from app import _play_feedback_tone_async
        _play_feedback_tone_async("listening")  # must not raise

    def test_play_feedback_tone_is_non_blocking(self, mocker):
        """sounddevice.play() returns immediately; the press handler must not
        wait on audio."""
        import sounddevice
        play = mocker.patch.object(sounddevice, "play")
        from app import _play_feedback_tone_async
        _play_feedback_tone_async("done")
        play.assert_called_once()

    def test_removed_chime_alias_is_gone(self):
        """T0-5 drift guard: the dead alias must not be reintroduced."""
        import app as app_module
        assert not hasattr(app_module, "_play_chime_async")
        assert not hasattr(app_module, "_CHIME_SAMPLES")

    def test_chime_sample_rate_retained(self):
        """_CHIME_SAMPLE_RATE is still live inside _play_feedback_tone_async, so
        it must survive the deletion of the dead code around it."""
        import app as app_module
        assert app_module._CHIME_SAMPLE_RATE == 44100


# --- T0-7: press-state thread safety -----------------------------------------

class TestPressStateThreadSafety:
    """T0-7: `_press_captures`, `_press_memory` and `_press_cursor_pos` are
    written by nimbus-press-capture and read by nimbus-release-capture.

    There was no lock. `_release_capture_worker` joins the press thread first,
    which usually orders things, but on join timeout it read fields the other
    thread could still be writing - so a caller could observe a fresh captures
    list paired with a stale memory string. All access now goes through
    `_read_press_state` / `_write_press_state`.
    """

    def _make_app(self, mocker):
        from app import NimbusApp
        return NimbusApp(
            ai_client=mocker.MagicMock(),
            stt_client=mocker.MagicMock(),
            tts_client=mocker.MagicMock(),
            memory_store=mocker.MagicMock(),
            overlay_controller=mocker.MagicMock(),
            hotkey_instance=mocker.MagicMock(),
        )

    def test_lock_exists(self, mocker):
        import threading
        app = self._make_app(mocker)
        assert isinstance(app._press_lock, type(threading.Lock()))

    def test_write_then_read_roundtrip(self, mocker):
        app = self._make_app(mocker)
        app._write_press_state(["cap"], "mem", (10, 20))
        assert app._read_press_state() == (["cap"], "mem", (10, 20))

    def test_read_returns_a_consistent_triple(self, mocker):
        """The point of the lock: captures and memory always belong together."""
        app = self._make_app(mocker)
        app._write_press_state(["a"], "memory-for-a", (1, 2))
        captures, memory, cursor = app._read_press_state()
        assert (captures, memory, cursor) == (["a"], "memory-for-a", (1, 2))

    def test_concurrent_writers_never_produce_a_mixed_triple(self, mocker):
        """Hammer the accessors from several threads. Every observed snapshot
        must be one of the whole values written, never a blend of two."""
        import threading
        app = self._make_app(mocker)
        valid = {("a", "mem-a"), ("b", "mem-b"), ("c", "mem-c")}
        observed = []
        stop = threading.Event()

        def writer(tag):
            while not stop.is_set():
                app._write_press_state([tag], f"mem-{tag}", (1, 2))

        def reader():
            while not stop.is_set():
                captures, memory, _ = app._read_press_state()
                if captures is not None:
                    observed.append((captures[0], memory))

        threads = [threading.Thread(target=writer, args=(t,), daemon=True)
                   for t in ("a", "b", "c")]
        threads.append(threading.Thread(target=reader, daemon=True))
        for t in threads:
            t.start()
        threading.Event().wait(0.25)
        stop.set()
        for t in threads:
            t.join(timeout=2.0)

        assert observed, "reader thread never observed a published value"
        bad = [pair for pair in observed if pair not in valid]
        assert not bad, f"observed torn press state: {bad[:5]}"

    def test_press_handler_records_cursor_atomically(self, mocker):
        """_handle_press must publish the press cursor through the accessor."""
        mocker.patch("app.get_foreground_app", return_value=("EXCEL.EXE", "Sheet1"))
        mocker.patch("app.get_cursor_position", return_value=(321, 654))
        mocker.patch("app.capture_all_screens", return_value=[mocker.MagicMock()])
        mocker.patch("app.monitor_containing", return_value={"left": 0, "top": 0})
        mocker.patch("app.list_monitors", return_value=[{"left": 0, "top": 0}])
        app = self._make_app(mocker)
        app._handle_press()
        if app._capture_thread is not None:
            app._capture_thread.join(timeout=2.0)
        _, _, cursor = app._read_press_state()
        assert cursor == (321, 654)

    def test_press_capture_failure_clears_captures_but_keeps_cursor(self, mocker):
        """On capture failure the release path must fall back to re-capture, and
        the cursor recorded at press time must not be lost."""
        mocker.patch("app.capture_all_screens", side_effect=RuntimeError("mss down"))
        app = self._make_app(mocker)
        app._write_press_state(None, "", (11, 22))
        app._press_time_capture("EXCEL.EXE")
        captures, memory, cursor = app._read_press_state()
        assert captures is None, "failed capture must not publish a captures list"
        assert memory == ""
        assert cursor == (11, 22), "press cursor must survive a capture failure"

    def test_cursor_still_reuses_press_captures(self, mocker):
        """T0-7 regression: movement within the threshold must still REUSE, so
        the no-second-flicker property is preserved. Previously uncovered."""
        import queue
        from app import _REUSE_THRESHOLD_PX
        app = self._make_app(mocker)
        sentinel = [mocker.MagicMock()]
        app._write_press_state(sentinel, "press-memory", (100, 100))
        recapture = mocker.patch("app.capture_all_screens")
        result_queue: queue.Queue = queue.Queue(maxsize=1)

        app._release_capture_worker(
            (100 + _REUSE_THRESHOLD_PX - 1, 100), "EXCEL.EXE", result_queue
        )

        captures, memory, reason = result_queue.get(timeout=2.0)
        assert captures is sentinel
        assert memory == "press-memory"
        assert "reusing" in reason
        recapture.assert_not_called()

    def test_cursor_moved_triggers_recapture(self, mocker):
        """T0-7 regression: movement past the threshold must RE-capture."""
        import queue
        from app import _REUSE_THRESHOLD_PX
        app = self._make_app(mocker)
        app._write_press_state([mocker.MagicMock()], "press-memory", (100, 100))
        fresh = [mocker.MagicMock()]
        recapture = mocker.patch("app.capture_all_screens", return_value=fresh)
        app._memory.recall.return_value = "fresh-memory"
        result_queue: queue.Queue = queue.Queue(maxsize=1)

        app._release_capture_worker(
            (100 + _REUSE_THRESHOLD_PX + 50, 100), "EXCEL.EXE", result_queue
        )

        captures, memory, reason = result_queue.get(timeout=2.0)
        assert captures is fresh
        assert memory == "fresh-memory"
        assert "re-capturing" in reason
        recapture.assert_called_once()

    def test_no_press_capture_forces_recapture(self, mocker):
        import queue
        app = self._make_app(mocker)
        app._write_press_state(None, "", (100, 100))
        fresh = [mocker.MagicMock()]
        mocker.patch("app.capture_all_screens", return_value=fresh)
        app._memory.recall.return_value = "m"
        result_queue: queue.Queue = queue.Queue(maxsize=1)

        app._release_capture_worker((100, 100), "EXCEL.EXE", result_queue)

        captures, _, reason = result_queue.get(timeout=2.0)
        assert captures is fresh
        assert "no press-time capture available" in reason

    def test_release_worker_always_publishes_even_on_error(self, mocker):
        """The pipeline worker blocks on this queue. If the capture worker can
        raise without publishing, the pipeline stalls until its 5s timeout."""
        import queue
        app = self._make_app(mocker)
        app._write_press_state(None, "", (0, 0))
        mocker.patch("app.capture_all_screens", side_effect=RuntimeError("boom"))
        result_queue: queue.Queue = queue.Queue(maxsize=1)

        app._release_capture_worker((500, 500), "EXCEL.EXE", result_queue)

        captures, memory, reason = result_queue.get(timeout=2.0)
        assert captures is None
        assert "error" in reason.lower()
