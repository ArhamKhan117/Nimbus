"""Tests for Esc-to-cancel (T2-2).

All the cancellation machinery already existed -- `_cancel_event`, 11 checkpoints in the
pipeline worker, `tts.stop()` with an epoch counter. The only missing piece was a way for
the user to reach it. So these tests focus on the two risks that come with wiring a very
common key into a global listener:

* **False positives.** Esc is among the most-pressed keys on a keyboard. If it fires when
  Nimbus is idle, Nimbus interferes with every dialog dismissal and vim escape in the
  session. Most of TestEscGating exists for this.
* **Collateral damage to push-to-talk.** Esc must be provably unable to disturb the
  IDLE/RECORDING state machine.
"""

import threading

import pytest


class _FakeKey:
    """Stand-in for a pynput key that is not Esc and not part of any combo."""

    def __init__(self, name):
        self.char = name


def _hotkey(on_cancel=None, is_in_flight=None, **kw):
    from unittest.mock import MagicMock
    from hotkey import PushToTalkHotkey
    return PushToTalkHotkey(
        on_press=kw.pop("on_press", lambda: None),
        on_release=kw.pop("on_release", lambda: None),
        listener_class=MagicMock(),
        on_cancel=on_cancel,
        is_in_flight=is_in_flight,
        **kw,
    )


def _esc():
    from pynput import keyboard
    return keyboard.Key.esc


class TestEscGating:
    def test_esc_fires_cancel_when_in_flight(self):
        fired = []
        hk = _hotkey(on_cancel=lambda: fired.append(1), is_in_flight=lambda: True)
        hk._handle_press(_esc())
        assert fired == [1]

    def test_esc_ignored_when_idle(self):
        """Must not interfere with every other Esc press in the session."""
        fired = []
        hk = _hotkey(on_cancel=lambda: fired.append(1), is_in_flight=lambda: False)
        hk._handle_press(_esc())
        assert fired == []

    def test_cancel_callback_optional(self):
        """Backward-compat: existing construction without on_cancel still works."""
        hk = _hotkey()
        assert hk._handle_press(_esc()) is None  # no crash, no callback

    def test_esc_ignored_when_hotkey_disabled(self):
        """Paused from the tray means paused for cancel too."""
        fired = []
        hk = _hotkey(on_cancel=lambda: fired.append(1), is_in_flight=lambda: True)
        hk._enabled = False
        hk._handle_press(_esc())
        assert fired == []

    def test_missing_predicate_defaults_to_firing(self):
        """A caller that supplies on_cancel but no predicate gets an always-on cancel
        rather than a silently dead one."""
        fired = []
        hk = _hotkey(on_cancel=lambda: fired.append(1))
        hk._handle_press(_esc())
        assert fired == [1]

    def test_predicate_exception_does_not_kill_the_listener(self):
        """An exception escaping to pynput kills the listener thread, and the hotkey stops
        working for the rest of the session."""
        def boom():
            raise RuntimeError("predicate exploded")
        hk = _hotkey(on_cancel=lambda: None, is_in_flight=boom)
        assert hk._handle_press(_esc()) is None

    def test_cancel_callback_exception_does_not_kill_the_listener(self):
        def boom():
            raise RuntimeError("cancel exploded")
        hk = _hotkey(on_cancel=boom, is_in_flight=lambda: True)
        assert hk._handle_press(_esc()) is None

    def test_predicate_not_consulted_when_no_callback(self):
        """No point asking the app whether it is busy if nothing will act on it."""
        asked = []
        hk = _hotkey(is_in_flight=lambda: asked.append(1) or True)
        hk._handle_press(_esc())
        assert asked == []


class TestEscDoesNotDisturbPushToTalk:
    def test_esc_does_not_affect_ptt_state_machine(self):
        """Esc must not corrupt the IDLE/RECORDING transitions."""
        from hotkey import HotkeyState
        hk = _hotkey(on_cancel=lambda: None, is_in_flight=lambda: True)
        hk._handle_press(_esc())
        assert hk.state is HotkeyState.IDLE
        assert hk._down_modifiers == set()
        assert hk._trigger_down is False

    def test_esc_during_recording_does_not_fire_release(self):
        from pynput import keyboard
        from hotkey import HotkeyState
        released = []
        hk = _hotkey(on_release=lambda: released.append(1),
                     on_cancel=lambda: None, is_in_flight=lambda: True)
        hk._handle_press(keyboard.Key.ctrl_l)
        hk._handle_press(keyboard.Key.alt_l)
        hk._handle_press(keyboard.Key.space)
        assert hk.state is HotkeyState.RECORDING
        hk._handle_press(_esc())
        assert released == [], "Esc must not end the recording"
        assert hk.state is HotkeyState.RECORDING

    def test_ptt_still_works_after_a_cancel(self):
        from pynput import keyboard
        from hotkey import HotkeyState
        pressed = []
        hk = _hotkey(on_press=lambda: pressed.append(1),
                     on_cancel=lambda: None, is_in_flight=lambda: True)
        hk._handle_press(_esc())
        hk._handle_press(keyboard.Key.ctrl_l)
        hk._handle_press(keyboard.Key.alt_l)
        hk._handle_press(keyboard.Key.space)
        assert pressed == [1]
        assert hk.state is HotkeyState.RECORDING

    def test_other_keys_are_untouched(self):
        fired = []
        hk = _hotkey(on_cancel=lambda: fired.append(1), is_in_flight=lambda: True)
        hk._handle_press(_FakeKey("a"))
        assert fired == []

    def test_esc_is_not_routed_through_parse_hotkey(self):
        """parse_hotkey deliberately rejects modifier-free chords so a Settings typo cannot
        turn typing into push-to-talk. Cancel must not weaken that guard."""
        from hotkey import parse_hotkey
        with pytest.raises(ValueError):
            parse_hotkey("esc")


class TestInFlightPredicate:
    """The app-side predicate. Both phases of a response must count as in flight."""

    def _app(self, worker_alive=False, tts_alive=False):
        from app import NimbusApp

        class _Thread:
            def __init__(self, alive):
                self._alive = alive
            def is_alive(self):
                return self._alive

        class _Tts:
            _current_thread = _Thread(tts_alive) if tts_alive else None

        app = NimbusApp.__new__(NimbusApp)  # no Qt / audio construction
        app._worker_thread = _Thread(worker_alive) if worker_alive else None
        app._tts = _Tts()
        return app

    def test_idle_is_not_in_flight(self):
        assert self._app()._is_response_in_flight() is False

    def test_worker_running_is_in_flight(self):
        """The thinking / streaming phase."""
        assert self._app(worker_alive=True)._is_response_in_flight() is True

    def test_tts_speaking_is_in_flight(self):
        """TTS outlives the worker, and the user still perceives Nimbus as busy."""
        assert self._app(tts_alive=True)._is_response_in_flight() is True

    def test_finished_worker_is_not_in_flight(self):
        app = self._app()
        app._worker_thread = type("T", (), {"is_alive": lambda self: False})()
        assert app._is_response_in_flight() is False

    def test_predicate_is_cheap_and_total(self):
        """Runs on the listener thread for every Esc press anywhere in Windows, so it must
        never raise even with unexpected TTS internals."""
        app = self._app()
        app._tts = object()  # no _current_thread attribute at all
        assert app._is_response_in_flight() is False


class TestCancelSlot:
    """The Qt-main-thread slot. Mirrors _handle_press's abandon sequence."""

    def _app(self):
        from app import NimbusApp

        class _Sig:
            def __init__(self):
                self.emitted = 0
            def emit(self, *a):
                self.emitted += 1

        class _Tts:
            def __init__(self):
                self.stopped = 0
            def stop(self):
                self.stopped += 1

        class _Stt:
            def __init__(self):
                self.grace_until = None
            def set_tts_grace_until(self, t):
                self.grace_until = t

        app = NimbusApp.__new__(NimbusApp)
        app._cancel_event = threading.Event()
        app._tts = _Tts()
        app._stt = _Stt()
        app._realtime = None
        app.__dict__["sig_hide_spinner"] = _Sig()
        app.__dict__["sig_clear_annotations"] = _Sig()
        # T4-5: cancel also clears the live caption, since an abandoned turn's transcript
        # is stale and leaving it up implies Nimbus is still working on it.
        app.__dict__["sig_caption"] = _Sig()
        # SHELL_AND_CHAT.md §4: and it returns the chat panel's state strip to idle, for the
        # same reason -- an abandoned turn that still reads "thinking" is a lie.
        app.__dict__["sig_chat_state"] = _Sig()
        return app

    def test_cancel_sets_the_cancel_event(self):
        """The pipeline worker's 11 checkpoints all read this."""
        app = self._app()
        app._on_cancel()
        assert app._cancel_event.is_set()

    def test_cancel_stops_tts(self):
        app = self._app()
        app._on_cancel()
        assert app._tts.stopped == 1

    def test_cancel_clears_overlay_and_spinner(self):
        app = self._app()
        app._on_cancel()
        assert app.__dict__["sig_hide_spinner"].emitted == 1
        assert app.__dict__["sig_clear_annotations"].emitted == 1

    def test_cancel_clears_the_live_caption(self):
        """T4-5: a cancelled turn's transcript must not linger as if still in progress."""
        app = self._app()
        app._on_cancel()
        assert app.__dict__["sig_caption"].emitted == 1

    def test_cancel_sets_tts_grace_window(self):
        """Otherwise the aborted TTS tail contaminates the next transcript."""
        import time
        app = self._app()
        app._on_cancel()
        assert app._stt.grace_until is not None
        assert app._stt.grace_until > time.time()

    def test_cancel_does_not_record_memory(self):
        """An aborted turn must not be written to per-app memory."""
        app = self._app()
        recorded = []
        app._memory = type("M", (), {"record": lambda self, *a, **k: recorded.append(a)})()
        app._on_cancel()
        assert recorded == []

    def test_cancel_stops_a_realtime_session_when_present(self):
        app = self._app()
        stopped = []
        app._realtime = type("R", (), {"stop": lambda self: stopped.append(1)})()
        app._on_cancel()
        assert stopped == [1]

    def test_realtime_stop_failure_does_not_break_cancel(self):
        app = self._app()
        def boom(self):
            raise RuntimeError("nope")
        app._realtime = type("R", (), {"stop": boom})()
        app._on_cancel()
        assert app._cancel_event.is_set()
        assert app._tts.stopped == 1
