"""Tests for the live transcript caption (T4-5).

The capability was already wired before this change -- `on_partial_transcript` fired and
printed to stdout, which a windowed build does not have. So the work was not "build a
feature" but "route a proven callback to a widget", and the risks concentrate accordingly:

* **Thread safety.** Partials arrive on the AssemblyAI WebSocket thread. Touching a QWidget
  from there is the §1.6 invariant whose violation produces intermittent crashes rather than
  clean failures, so the path MUST go through a signal.
* **Never breaking an interaction.** A caption is decoration. Every failure mode here has to
  degrade to "no caption", never "no answer".
* **Not lingering.** A stale transcript from a previous turn is worse than none, because it
  reads as current.
"""

import pytest


@pytest.fixture(scope="module")
def qt_app():
    from PyQt6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


class TestElideCaption:
    """Pure function, so the caption's only real logic needs no Qt at all."""

    def test_short_text_unchanged(self):
        from overlay import elide_caption
        assert elide_caption("where is the save button") == "where is the save button"

    def test_whitespace_is_collapsed(self):
        from overlay import elide_caption
        assert elide_caption("  where   is\nthe  button ") == "where is the button"

    def test_long_text_is_elided_from_the_FRONT(self):
        """Front-elision is the whole point and the opposite of normal truncation: speech
        arrives incrementally, so the newest, still-unverified words are at the end."""
        from overlay import elide_caption
        text = "".join(f"word{i} " for i in range(200))
        result = elide_caption(text, max_chars=40)
        assert len(result) == 40
        assert result.startswith("\u2026")
        assert result.endswith("word199")

    def test_exact_length_is_not_elided(self):
        from overlay import elide_caption
        assert elide_caption("x" * 40, max_chars=40) == "x" * 40

    @pytest.mark.parametrize("value", ["", None, "   ", "\n\t"])
    def test_empty_inputs_return_empty(self, value):
        from overlay import elide_caption
        assert elide_caption(value) == ""

    def test_default_limit_is_generous_enough_for_a_sentence(self):
        from overlay import CAPTION_MAX_CHARS
        assert CAPTION_MAX_CHARS >= 120


class TestCaptionWidget:
    def _widget(self, qt_app):
        """Build a parented CaptionWidget.

        The parent is stashed on the instance deliberately. Letting it fall out of scope
        gets it garbage-collected, and Qt then deletes the child -- every assertion fails
        with "wrapped C/C++ object has been deleted", which looks like a widget bug and is
        not one.
        """
        from PyQt6.QtWidgets import QWidget
        from overlay import CaptionWidget
        self._parent = QWidget()
        self._parent.resize(1920, 1080)
        return CaptionWidget(self._parent)

    def test_starts_hidden(self, qt_app):
        assert self._widget(qt_app).isHidden() is True

    def test_is_click_through(self, qt_app):
        """Non-negotiable: the overlay is click-through. A caption that ate clicks would
        make the screen underneath unusable while visible."""
        from PyQt6.QtCore import Qt
        widget = self._widget(qt_app)
        assert widget.testAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents) is True

    def test_set_caption_stores_elided_text(self, qt_app):
        widget = self._widget(qt_app)
        widget.set_caption("  hello   world  ")
        assert widget._text == "hello world"

    def test_empty_caption_hides_immediately(self, qt_app):
        widget = self._widget(qt_app)
        widget.set_caption("something")
        widget.set_caption("")
        assert widget.isHidden() is True

    def test_clear_caption_hides_and_stops_timer(self, qt_app):
        widget = self._widget(qt_app)
        widget.set_caption("something")
        widget.clear_caption()
        assert widget.isHidden() is True
        assert widget._timer.isActive() is False
        assert widget._text == ""

    def test_update_restarts_the_hide_timer(self, qt_app):
        """A pause mid-sentence must not clear text the user is still reading."""
        widget = self._widget(qt_app)
        widget.set_caption("first")
        widget.set_caption("first second")
        assert widget._timer.isActive() is True

    def test_positioned_within_the_parent(self, qt_app):
        widget = self._widget(qt_app)
        widget.set_caption("hello")
        parent = widget.parentWidget()
        assert 0 <= widget.x() and widget.x() + widget.width() <= parent.width()
        assert 0 <= widget.y() and widget.y() + widget.height() <= parent.height()

    def test_horizontally_centred(self, qt_app):
        widget = self._widget(qt_app)
        widget.set_caption("hello")
        parent = widget.parentWidget()
        centre_offset = abs(
            (widget.x() + widget.width() // 2) - parent.width() // 2)
        assert centre_offset <= 1

    def test_paint_does_not_raise(self, qt_app):
        """Rendering runs on the Qt paint path; an exception there spams the event loop."""
        from PyQt6.QtGui import QPixmap
        widget = self._widget(qt_app)
        widget.set_caption("where is the save button")
        widget.resize(600, 74)
        widget.render(QPixmap(widget.size()))


class TestOverlayControllerRouting:
    def _controller(self):
        from overlay import OverlayController

        class _FakeOverlay:
            def __init__(self, name):
                self.name = name
                self.captions = []
                self.cleared = 0
            def set_caption(self, text):
                self.captions.append(text)
            def clear_caption(self):
                self.cleared += 1

        controller = OverlayController.__new__(OverlayController)
        controller.overlays = [_FakeOverlay("a"), _FakeOverlay("b")]
        controller._pick_overlay_for_point = lambda x, y, m: controller.overlays[0]
        return controller

    def test_caption_goes_to_one_monitor_only(self):
        """A sentence duplicated across three screens is noise, and the user is looking at
        the screen they asked about."""
        controller = self._controller()
        controller.set_caption("hello", 10, 10, {})
        assert controller.overlays[0].captions == ["hello"]
        assert controller.overlays[1].captions == []

    def test_other_monitors_are_cleared_first(self):
        """Guards a mid-session monitor switch leaving two captions on screen."""
        controller = self._controller()
        controller.set_caption("hello", 10, 10, {})
        assert controller.overlays[1].cleared == 1
        assert controller.overlays[0].cleared == 0

    def test_clear_captions_clears_every_overlay(self):
        controller = self._controller()
        controller.clear_captions()
        assert all(o.cleared == 1 for o in controller.overlays)

    def test_no_target_overlay_does_not_raise(self):
        controller = self._controller()
        controller._pick_overlay_for_point = lambda x, y, m: None
        controller.set_caption("hello", 0, 0, {})  # must not raise

    def test_overlay_without_caption_support_is_skipped(self):
        """Defensive: mirrors how set_audio_level guards with hasattr."""
        controller = self._controller()
        controller.overlays.append(object())
        controller.clear_captions()  # must not raise


class TestAppWiring:
    def _app(self, overlay=None, enabled=True, press_cursor=(100, 200), hud=None):
        import app as app_module
        from app import NimbusApp

        instance = NimbusApp.__new__(NimbusApp)
        instance._overlay = overlay
        instance._read_press_state = lambda: (None, "", press_cursor)
        # SHELL_AND_CHAT.md §6.1: the caption stands down while the chat panel is showing the
        # same transcript. ``None`` is the no-HUD case, which is every test below except the
        # two that name it.
        instance._hud = hud
        return instance, app_module

    def test_caption_is_a_signal_not_a_direct_call(self):
        """Partials arrive on the WebSocket thread; touching Qt from there is the §1.6
        invariant that produces intermittent crashes instead of clean failures."""
        from PyQt6.QtCore import pyqtSignal
        from app import NimbusApp
        assert isinstance(NimbusApp.sig_caption, type(pyqtSignal(str)))

    def test_stt_callback_is_wired_to_the_signal_emit(self):
        """Regression gate: this previously printed to a console a windowed build lacks."""
        import inspect
        import app as app_module
        source = inspect.getsource(app_module)
        assert "on_partial_transcript(nimbus.sig_caption.emit)" in source
        assert 'print(f"[stt partial]' not in source, "old console print still present"

    def test_no_overlay_is_a_noop(self, mocker):
        instance, _ = self._app(overlay=None)
        instance._on_caption("hello")  # must not raise

    def test_disabled_setting_suppresses_captions(self, mocker):
        overlay = mocker.MagicMock()
        instance, app_module = self._app(overlay=overlay)
        mocker.patch.object(app_module, "CAPTIONS_ENABLED", False)
        instance._on_caption("hello")
        overlay.set_caption.assert_not_called()

    def test_blank_text_clears_instead_of_showing(self, mocker):
        overlay = mocker.MagicMock()
        instance, app_module = self._app(overlay=overlay)
        mocker.patch.object(app_module, "CAPTIONS_ENABLED", True)
        instance._on_caption("   ")
        overlay.clear_captions.assert_called_once()
        overlay.set_caption.assert_not_called()

    def test_uses_press_time_cursor_not_the_live_one(self, mocker):
        """The caption belongs on the screen the user was asking about, not wherever the
        mouse has drifted since."""
        overlay = mocker.MagicMock()
        instance, app_module = self._app(overlay=overlay, press_cursor=(4242, 77))
        mocker.patch.object(app_module, "CAPTIONS_ENABLED", True)
        mocker.patch.object(app_module, "monitor_containing", return_value={"m": 1})
        live = mocker.patch.object(app_module, "get_cursor_position")
        instance._on_caption("hello")
        live.assert_not_called()
        args = overlay.set_caption.call_args.args
        assert args[0] == "hello" and args[1] == 4242 and args[2] == 77

    def test_falls_back_to_live_cursor_when_no_press_recorded(self, mocker):
        overlay = mocker.MagicMock()
        instance, app_module = self._app(overlay=overlay, press_cursor=None)
        mocker.patch.object(app_module, "CAPTIONS_ENABLED", True)
        mocker.patch.object(app_module, "monitor_containing", return_value={})
        mocker.patch.object(
            app_module, "get_cursor_position", return_value=(7, 9))
        instance._on_caption("hello")
        args = overlay.set_caption.call_args.args
        assert args[1] == 7 and args[2] == 9

    def test_overlay_failure_never_breaks_the_interaction(self, mocker):
        """A caption is decoration; it must degrade to "no caption", never "no answer"."""
        overlay = mocker.MagicMock()
        overlay.set_caption.side_effect = RuntimeError("paint exploded")
        instance, app_module = self._app(overlay=overlay)
        mocker.patch.object(app_module, "CAPTIONS_ENABLED", True)
        mocker.patch.object(app_module, "monitor_containing", return_value={})
        instance._on_caption("hello")  # must not raise


class TestDefaults:
    def test_captions_default_on(self, first_run_config):
        """Sanctioned §1.3 exception: the old behaviour printed to a console a windowed
        build does not have, so nobody was relying on it."""
        assert first_run_config.CAPTIONS == "on"

    def test_enabled_flag_is_cached_at_import(self):
        """resolve_setting writes to the keyring when the value came from the environment,
        and partials arrive many times per second -- that must not touch Credential
        Manager on the hot path."""
        import app
        assert isinstance(app.CAPTIONS_ENABLED, bool)


class TestProviderLivenessIsHonest:
    """The caption is only genuinely 'live' on a streaming provider. Pinned so nobody
    later 'fixes' a non-bug when faster-whisper shows text at release instead."""

    def test_assemblyai_fires_partials_during_recording(self):
        import inspect
        import stt
        source = inspect.getsource(stt.AssemblyAIStreamingSTT)
        assert "_partial_cb" in source

    def test_faster_whisper_is_batch_and_fires_once_at_stop(self):
        import inspect
        import stt
        source = inspect.getsource(stt.FasterWhisperSTT.stop_recording)
        assert "_partial_cb" in source, (
            "faster-whisper delivers its transcript from stop_recording; if this moves, "
            "the caption's liveness characteristics changed and the docs need updating"
        )
