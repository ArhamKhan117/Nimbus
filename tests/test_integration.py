"""The §9.1 integration pass: app.py <-> shell, chat HUD, tray, config, nimbus.spec.

One file rather than spreading these across ``test_app.py``, ``test_shell.py`` and
``test_chat_hud.py`` because what is under test is the **seam**, not a module. Agent A and
Agent B each delivered a self-contained surface with a documented integration surface; these
tests are what turn "the surfaces match the spec" into "the surfaces are actually connected".

The thing worth being most careful about here is `S-3`, the single source of truth for
push-to-talk. Three views (window toggle, tray checkmark, tray icon) read one piece of state,
and the failure mode is not a crash -- it is a checkmark quietly disagreeing with reality.
``TestPowerConvergence`` drives a real ``PushToTalkHotkey`` for exactly that reason.
"""

import pytest


@pytest.fixture(scope="module")
def qt_app():
    """One QApplication for the module. Qt requires it before any QWidget exists."""
    from PyQt6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


@pytest.fixture
def nimbus(mocker):
    """A ``NimbusApp`` with every service mocked. Same pattern as ``test_app.py``."""
    from app import NimbusApp

    ai = mocker.MagicMock()
    # A real client's model_id is a string, and the window puts it in a QLabel. Left as a
    # MagicMock it exercised a genuine fragility in build_main_window rather than the wiring
    # under test -- which is how that bug was found, so the coercion there stays.
    ai.model_id = "openai/gpt-5.4"
    return NimbusApp(
        ai_client=ai,
        stt_client=mocker.MagicMock(),
        tts_client=mocker.MagicMock(),
        memory_store=mocker.MagicMock(),
        overlay_controller=mocker.MagicMock(),
        hotkey_instance=mocker.MagicMock(),
    )


def stub_form_class():
    """``SettingsForm``'s host contract with no keyring access. Mirrors test_shell.py's."""
    from PyQt6.QtCore import pyqtSignal
    from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

    class StubForm(QWidget):
        sig_validity_changed = pyqtSignal(bool)
        sig_local_data_cleared = pyqtSignal()
        sig_saved = pyqtSignal()

        def __init__(self, parent=None):
            super().__init__(parent)
            QVBoxLayout(self).addWidget(QLabel("stub settings"))

        def is_valid(self):
            return True

        def save(self):
            return True

        @property
        def local_data_cleared(self):
            return False

    return StubForm


@pytest.fixture
def window(qt_app, nimbus, mocker, tmp_path):
    """A real ``MainWindow`` wired to ``nimbus`` through ``build_main_window``.

    The settings form and KB folder are stubbed for the reasons ``test_shell.py`` documents:
    the real form reads the keyring, and the real KB folder is the developer's own.
    """
    import app as app_module
    import shell.window as shell_window

    real_main_window = shell_window.MainWindow

    def patched(**kwargs):
        kwargs.setdefault("settings_form_factory", stub_form_class())
        kwargs.setdefault("kb_dir", tmp_path / "kb")
        return real_main_window(**kwargs)

    mocker.patch("shell.window.MainWindow", side_effect=patched)
    built = app_module.build_main_window(nimbus)
    yield built
    if built is not None:
        built.hide()
        built.deleteLater()


# --- config declarations (§10.1) --------------------------------------------


class TestNewSettings:
    def test_all_seven_are_declared_with_the_specified_defaults(self, first_run_config):
        """Declared in ``config.py`` so they appear in Settings; both modules already read
        them through ``resolve_setting``, which is why this could land after the fact."""
        config = first_run_config

        assert config.CHAT_HUD == "on"
        assert config.CHAT_HUD_AUTOHIDE_SECONDS == 45
        assert config.CHAT_STORE_SCREENSHOTS == "off"
        assert config.CHAT_RETENTION_DAYS == 14
        # Flipped from "off". Nothing starts Nimbus at login, so every launch is a deliberate
        # double-click and the window should appear -- see ``config.SHELL_ON_STARTUP``.
        assert config.SHELL_ON_STARTUP == "on"
        assert config.NAV_SIDE == "left"
        assert config.REDUCE_MOTION == "auto"

    def test_storing_screenshots_is_an_explicit_opt_in(self, first_run_config):
        """The one in the group that is a privacy commitment rather than a preference.

        Screen contents on disk is a bigger undertaking than a transcript, so it must never be
        inherited from having enabled the HUD.
        """
        assert first_run_config.CHAT_HUD == "on"
        assert first_run_config.CHAT_STORE_SCREENSHOTS == "off"

    def test_integer_settings_survive_a_corrupt_stored_value(self, mocker):
        """``resolve_bounded_int_setting`` exists so a bad keyring value cannot stop startup."""
        import config

        mocker.patch("config.resolve_setting", return_value="not-a-number")
        assert config.resolve_bounded_int_setting(
            "CHAT_RETENTION_DAYS", default=14, minimum=1, maximum=365) == 14

    def test_all_seven_are_marked_restart_required(self):
        """Each is read once at startup, so the ``↻`` marker is the honest label (T4-7)."""
        from settings_dialog import RESTART_REQUIRED_SETTINGS, restart_marker_for

        for setting in ("CHAT_HUD", "CHAT_HUD_AUTOHIDE_SECONDS", "CHAT_STORE_SCREENSHOTS",
                        "CHAT_RETENTION_DAYS", "SHELL_ON_STARTUP", "NAV_SIDE",
                        "REDUCE_MOTION"):
            assert setting in RESTART_REQUIRED_SETTINGS
            assert restart_marker_for(setting)

    def test_the_settings_form_exposes_and_persists_them(self, qt_app, mocker):
        """A declared setting nobody can reach is not a setting."""
        from settings_dialog import KEYRING_SERVICE, SettingsForm

        written = {}
        mocker.patch("settings_dialog.keyring.set_password",
                     side_effect=lambda service, name, value: written.__setitem__(name, value))
        mocker.patch("settings_dialog.keyring.get_password", return_value=None)

        form = SettingsForm()
        try:
            assert form._chat_hud_checkbox is not None
            assert form._nav_side_combo is not None

            form._chat_hud_checkbox.setChecked(False)
            form._shell_startup_checkbox.setChecked(True)
            form._chat_autohide_seconds.setValue(90)
            form._chat_retention_days.setValue(30)
            form._nav_side_combo.setCurrentIndex(1)  # right
            form._reduce_motion_combo.setCurrentIndex(1)  # reduce motion on

            assert form.save() is True

            assert written["CHAT_HUD"] == "off"
            assert written["SHELL_ON_STARTUP"] == "on"
            assert written["CHAT_HUD_AUTOHIDE_SECONDS"] == "90"
            assert written["CHAT_RETENTION_DAYS"] == "30"
            assert written["NAV_SIDE"] == "right"
            assert written["REDUCE_MOTION"] == "on"
            assert written["CHAT_STORE_SCREENSHOTS"] == "off"
            assert KEYRING_SERVICE == "nimbus"
        finally:
            form.deleteLater()

    def test_turning_the_panel_off_disables_its_own_settings(self, qt_app, mocker):
        """A live spinbox beside a cleared checkbox reads as "this still applies"."""
        from settings_dialog import SettingsForm

        mocker.patch("settings_dialog.keyring.get_password", return_value=None)
        form = SettingsForm()
        try:
            form._chat_hud_checkbox.setChecked(False)
            assert not form._chat_autohide_seconds.isEnabled()
            assert not form._chat_retention_days.isEnabled()
            assert not form._chat_screenshots_checkbox.isEnabled()

            form._chat_hud_checkbox.setChecked(True)
            assert form._chat_autohide_seconds.isEnabled()
        finally:
            form.deleteLater()


# --- frozen build (§9.1 item 7/9) -------------------------------------------


class TestFrozenBuild:
    """PyInstaller's static graph cannot see any of this, which is how it gets missed."""

    NEW_MODULES = (
        "chat_hud", "sessions", "shell", "shell.window", "shell.nav", "shell.titlebar",
        "shell.widgets", "shell.pages", "shell.pages.home", "shell.pages.knowledge",
        "shell.pages.journal", "shell.pages.settings", "shell.pages.account",
    )

    def test_every_new_module_is_in_hiddenimports(self):
        from pathlib import Path

        import app

        spec = (Path(app.__file__).resolve().parent / "nimbus.spec").read_text(
            encoding="utf-8")
        for module in self.NEW_MODULES:
            assert f'"{module}"' in spec, f"{module} missing from nimbus.spec hiddenimports"

    def test_every_new_module_is_in_the_selftest(self):
        """The selftest is what makes a missing hiddenimport fail on the build machine rather
        than at a user's first click."""
        import inspect

        import app

        source = inspect.getsource(app._run_selftest)
        for module in self.NEW_MODULES:
            assert f'"{module}"' in source, f"{module} missing from runtime_modules"

    def test_the_selftest_actually_imports_them(self):
        """Naming a module in a list proves nothing if the list is never walked."""
        import importlib

        for module in self.NEW_MODULES:
            importlib.import_module(module)


# --- the chat HUD builder (§4) ----------------------------------------------


class TestChatHudBuilder:
    def test_disabled_still_builds_the_panel_but_hidden(self, nimbus, mocker, qt_app):
        """``CHAT_HUD=off`` now means "does not show itself", not "does not exist".

        It used to gate construction, and that made the rail's switch a lie in one direction:
        starting with the setting off left nothing to show, so turning the switch on could only
        answer "not until you restart" -- reported, fairly, as the switch being broken. A hidden
        panel costs a widget and a SQLite handle; a switch that does nothing costs trust.

        What must still be true is that it stays out of the way: hidden, and not revealing itself
        when an interaction starts.
        """
        import app as app_module

        mocker.patch.object(app_module, "CHAT_HUD_ENABLED", False)
        mocker.patch("sessions.SessionStore", return_value=mocker.MagicMock())
        mocker.patch("sessions.start_new_session", return_value=1)
        hud = mocker.MagicMock()
        mocker.patch("chat_hud.ChatHud", return_value=hud)

        assert app_module.build_chat_hud(nimbus) is hud
        assert nimbus._hud is hud
        hud.set_auto_reveal.assert_called_once_with(False)
        hud.show.assert_not_called()
        hud.reveal.assert_not_called()

    def test_enabled_lets_the_panel_reveal_itself(self, nimbus, mocker, qt_app):
        import app as app_module

        mocker.patch.object(app_module, "CHAT_HUD_ENABLED", True)
        mocker.patch("sessions.SessionStore", return_value=mocker.MagicMock())
        mocker.patch("sessions.start_new_session", return_value=1)
        hud = mocker.MagicMock()
        mocker.patch("chat_hud.ChatHud", return_value=hud)

        app_module.build_chat_hud(nimbus)
        hud.set_auto_reveal.assert_called_once_with(True)

    def test_a_failure_costs_the_panel_and_nothing_else(self, nimbus, mocker):
        """Invariant 10: the HUD is downstream of the answer and must never affect it."""
        import app as app_module
        import sessions

        mocker.patch.object(app_module, "CHAT_HUD_ENABLED", True)
        mocker.patch.object(sessions, "SessionStore", side_effect=OSError("db locked"))

        assert app_module.build_chat_hud(nimbus) is None
        assert nimbus._hud is None

    def test_a_failing_prune_does_not_stop_the_panel_opening(self, nimbus, mocker, qt_app):
        """Retention is housekeeping; the panel is the feature."""
        import app as app_module

        store = mocker.MagicMock()
        store.prune.side_effect = OSError("read-only")
        mocker.patch.object(app_module, "CHAT_HUD_ENABLED", True)
        mocker.patch("sessions.SessionStore", return_value=store)
        hud = mocker.MagicMock()
        mocker.patch("chat_hud.ChatHud", return_value=hud)

        assert app_module.build_chat_hud(nimbus) is hud
        assert nimbus._hud is hud

    def test_the_three_inbound_signals_reach_the_huds_own_slots(self, nimbus, mocker, qt_app):
        """A real signal-to-signal chain, not a mock that would accept anything.

        The HUD's own signals are the documented entry point precisely because they are
        already connected internally to ``append`` / ``stream_delta`` / ``set_state``. This
        builds a stand-in with genuine ``pyqtSignal``s wired the same way, so the test proves
        content emitted by ``NimbusApp`` actually arrives at a slot -- which a ``MagicMock``
        cannot show, because PyQt treats a mock as a plain callable and the second hop never
        happens.
        """
        import app as app_module
        from PyQt6.QtCore import QObject, pyqtSignal

        class FakeHud(QObject):
            sig_message = pyqtSignal(object)
            sig_delta = pyqtSignal(str)
            sig_state = pyqtSignal(str)

            def __init__(self):
                super().__init__()
                self.messages, self.deltas, self.states = [], [], []
                self.sig_message.connect(self.append)
                self.sig_delta.connect(self.stream_delta)
                self.sig_state.connect(self.set_state)
                self.sig_replay = pyqtSignal
                self.sessions = []

            def append(self, message):
                self.messages.append(message)

            def stream_delta(self, text):
                self.deltas.append(text)

            def set_state(self, state):
                self.states.append(state)

            def set_session(self, session_id, title):
                self.sessions.append((session_id, title))

            def needs_hide_for_capture(self):
                return False

            def set_auto_reveal(self, enabled):
                self.auto_reveal = bool(enabled)

        hud = FakeHud()
        # The outbound connections need real signals, which FakeHud has no business faking;
        # they are covered by the ChatHud tests. Patch them out for this one.
        mocker.patch.object(app_module, "CHAT_HUD_ENABLED", True)
        mocker.patch("sessions.SessionStore", return_value=mocker.MagicMock())
        mocker.patch("sessions.start_new_session", return_value=1)
        mocker.patch("chat_hud.ChatHud", return_value=hud)
        for name in ("sig_replay", "sig_repoint", "sig_retry", "sig_new_session",
                     "sig_open_session"):
            setattr(hud, name, mocker.MagicMock())

        assert app_module.build_chat_hud(nimbus) is hud

        marker = object()
        nimbus.sig_chat_message.emit(marker)
        nimbus.sig_chat_delta.emit("partial")
        nimbus.sig_chat_state.emit("thinking")

        assert hud.messages == [marker]
        assert hud.deltas == ["partial"]
        assert hud.states == ["thinking"]

    def test_the_outbound_actions_are_connected(self, nimbus, mocker, qt_app):
        """Each one is something only ``NimbusApp`` can do."""
        import app as app_module

        hud = mocker.MagicMock()
        hud.needs_hide_for_capture.return_value = False
        mocker.patch.object(app_module, "CHAT_HUD_ENABLED", True)
        mocker.patch("sessions.SessionStore", return_value=mocker.MagicMock())
        mocker.patch("sessions.start_new_session", return_value=1)
        mocker.patch("chat_hud.ChatHud", return_value=hud)

        app_module.build_chat_hud(nimbus)

        assert hud.sig_replay.connect.call_args.args[0] == nimbus._tts.speak
        assert hud.sig_repoint.connect.call_args.args[0] == nimbus.repoint_at
        assert hud.sig_retry.connect.call_args.args[0] == nimbus.retry_transcript
        assert hud.sig_open_session.connect.call_args.args[0] == nimbus.open_chat_session

    def test_a_new_session_is_started_so_the_panel_has_somewhere_to_write(
            self, nimbus, mocker, qt_app):
        import app as app_module

        hud = mocker.MagicMock()
        mocker.patch.object(app_module, "CHAT_HUD_ENABLED", True)
        mocker.patch("sessions.SessionStore", return_value=mocker.MagicMock())
        mocker.patch("chat_hud.ChatHud", return_value=hud)
        start = mocker.patch("sessions.start_new_session", return_value=7)

        app_module.build_chat_hud(nimbus)

        start.assert_called_once()
        assert nimbus._session_id == 7
        hud.set_session.assert_called_with(7, "")


# --- the window builder (§3) ------------------------------------------------


class TestWindowBuilder:
    def test_a_failure_leaves_the_app_running_as_a_tray_utility(self, nimbus, mocker):
        import app as app_module

        mocker.patch("shell.window.MainWindow", side_effect=RuntimeError("no display"))
        assert app_module.build_main_window(nimbus) is None
        assert nimbus._window is None

    def test_home_is_fed_from_nimbus_not_from_defaults(self, window, nimbus):
        from datetime import datetime

        nimbus._question_times = [datetime.now(), datetime.now()]
        nimbus._privacy_skips = [datetime.now()]
        nimbus._recent_turns = [{
            "question": "where is the render queue?",
            "app": "orionflow.exe",
            "when": datetime.now(),
            "target": "Render",
        }]

        window.refresh()

        assert window.home.week_count.text() == "2"
        assert "1" in window.home.privacy_count.text()
        # Sentence-cased for display only; the stored question is untouched.
        assert window.home.recent.item(0, 0).text() == "Where is the render queue?"
        assert nimbus.recent_turns()[0]["question"] == "where is the render queue?"
        assert window.home.recent.item(0, 1).text() == "orionflow.exe"

    def test_unwired_numbers_are_never_invented(self, window, nimbus):
        """With nothing measured Home shows an em dash, not a zero -- except these *are*
        measured, and a measured zero is a different claim, so it shows 0."""
        nimbus._question_times = []
        nimbus._privacy_skips = []
        window.refresh()

        assert window.home.week_count.text() == "0"

    def test_the_journal_page_is_only_given_a_queue_when_the_journal_is_on(
            self, nimbus, mocker, qt_app, tmp_path):
        """Handing over a queue the user disabled would create the table they opted out of."""
        import app as app_module

        mocker.patch.object(app_module, "JOURNAL_ENABLED", False)
        captured = {}

        def capture(**kwargs):
            captured.update(kwargs)
            raise RuntimeError("stop here, the kwargs are the point")

        mocker.patch("shell.window.MainWindow", side_effect=capture)
        app_module.build_main_window(nimbus)

        assert captured["review_queue_provider"] is None

    def test_export_and_memory_folder_reached_their_new_home(
            self, window, nimbus, mocker, tmp_path):
        """`S-5`: the tray gave these up, so the window must genuinely service them.

        Asserted all the way to ``os.startfile`` rather than by patching the bound method --
        the builder connected the real one before a patch could land, and a test that patches
        after the connection proves only that patching works.
        """
        emitted = []
        nimbus.sig_export_session_history.connect(lambda: emitted.append("export"))
        folder = tmp_path / "memory"
        mocker.patch("config.MEMORY_DIR", folder)
        startfile = mocker.patch("app.os.startfile")

        window.home.export_button.click()
        window.home.memory_button.click()

        assert emitted == ["export"]
        startfile.assert_called_once_with(str(folder))

    def test_the_sidebar_reports_cloud_versus_local_honestly(self, nimbus, mocker):
        """All three providers must be local, not just the model.

        A local LLM with cloud STT still sends the user's voice off the machine, and a dot
        claiming "local only" on that basis would be worse than no dot.
        """
        import app as app_module

        def providers(mapping):
            return lambda name, default=None: mapping.get(name, default)

        mocker.patch("app.resolve_setting", side_effect=providers({
            "LLM_PROVIDER": "ollama", "STT_PROVIDER": "faster-whisper",
            "TTS_PROVIDER": "kokoro"}))
        assert app_module._is_fully_local() is True

        mocker.patch("app.resolve_setting", side_effect=providers({
            "LLM_PROVIDER": "ollama", "STT_PROVIDER": "assemblyai",
            "TTS_PROVIDER": "kokoro"}))
        assert app_module._is_fully_local() is False


# --- S-3: one source of truth, three views ----------------------------------


class TestPowerConvergence:
    """The failure mode is a checkmark that quietly disagrees with reality, so this uses a
    real ``PushToTalkHotkey`` rather than a mock that would accept anything."""

    def _hotkey(self, mocker):
        import hotkey

        listener = mocker.MagicMock()
        hk = hotkey.PushToTalkHotkey(
            on_press=lambda: None,
            on_release=lambda: None,
            listener_class=lambda **kwargs: listener,
        )
        hk.start()
        return hk, listener

    def test_nimbus_app_is_the_only_writer(self, nimbus, mocker):
        hk, listener = self._hotkey(mocker)
        nimbus._hotkey = hk

        nimbus.set_listening(False)
        assert hk.enabled is False
        assert nimbus.is_listening is False

        nimbus.set_listening(True)
        assert hk.enabled is True
        assert nimbus.is_listening is True

        # Instant, with the hook left installed -- so pausing needs no restart.
        assert listener.stop.called is False

    def test_it_reports_the_state_achieved_not_the_state_requested(self, nimbus):
        """With no hotkey installed nothing changed, and saying otherwise leaves every view
        displaying something that is not true."""
        nimbus._hotkey = None
        seen = []
        nimbus.sig_listening_changed.connect(seen.append)

        nimbus.set_listening(True)

        assert seen == [False]

    def test_pausing_also_silences_what_is_in_flight(self, nimbus, mocker):
        hk, _listener = self._hotkey(mocker)
        nimbus._hotkey = hk

        nimbus.set_listening(False)

        nimbus._tts.stop.assert_called()

    def test_window_tray_and_hotkey_all_converge(self, window, nimbus, mocker, qt_app):
        """The whole point of `S-3`, end to end: three views, one state, one notification."""
        from PyQt6.QtGui import QAction

        from tray import NimbusTray

        mocker.patch("tray.config.onboarding_seen", return_value=True)
        hk, _listener = self._hotkey(mocker)
        nimbus._hotkey = hk

        tray = NimbusTray(
            on_quit=mocker.MagicMock(),
            on_show_window=mocker.MagicMock(),
            on_pause_changed=lambda paused: nimbus.set_listening(not paused),
        )
        nimbus.sig_listening_changed.connect(lambda on: tray.set_paused(not on))
        try:
            assert isinstance(tray._pause_action, QAction)

            # 1. The user clicks the window's toggle.
            window.home.toggle.click()
            assert hk.enabled is False
            assert window.home.toggle.isChecked() is False
            assert tray._pause_action.isChecked() is True

            # 2. The user unchecks Pause in the tray.
            tray._pause_action.trigger()
            assert hk.enabled is True
            assert window.home.toggle.isChecked() is True
            assert tray._pause_action.isChecked() is False

            # 3. Something else changes it -- both views still follow.
            nimbus.set_listening(False)
            assert window.home.toggle.isChecked() is False
            assert tray._pause_action.isChecked() is True
        finally:
            tray._icon.hide()

    def test_no_view_keeps_its_own_copy(self):
        """A source guard. The behavioural tests above catch a copy that is read; this catches
        one that is only written, which is how the two drift apart to begin with."""
        import re
        from pathlib import Path

        import tray

        source = Path(tray.__file__).read_text(encoding="utf-8")
        # The tray may set the checkmark, but it must not cache the boolean.
        assert not re.search(r"self\._paused\s*=", source)
        assert not re.search(r"self\._is_paused\s*=", source)


# --- the chat feed (§4) -----------------------------------------------------


class TestChatFeed:
    def test_no_hud_means_no_cost(self, nimbus, mocker):
        """The pipeline must pay nothing for a panel that is not there."""
        import sessions

        nimbus._hud = None
        message = mocker.patch.object(sessions, "ChatMessage")
        seen = []
        nimbus.sig_chat_message.connect(seen.append)

        nimbus._emit_chat_message("user", "hello")

        assert seen == []
        message.assert_not_called()

    def test_a_message_carries_role_text_and_coordinate(self, nimbus, mocker):
        nimbus._hud = mocker.MagicMock()
        seen = []
        nimbus.sig_chat_message.connect(seen.append)

        nimbus._emit_chat_message("nimbus", "top right", coordinate=(120, 340))

        assert len(seen) == 1
        assert seen[0].role == "nimbus"
        assert seen[0].text == "top right"
        assert seen[0].coordinate == (120, 340)

    def test_privacy_skipped_is_passed_through_not_reinvented(self, nimbus, mocker):
        """Invariant 6. ``add_message`` treats the flag as a hard stop, so it must be the
        *same* boolean the Guard produced -- deciding it again here is how the guarantee
        becomes decorative."""
        nimbus._hud = mocker.MagicMock()
        seen = []
        nimbus.sig_chat_message.connect(seen.append)

        nimbus._emit_chat_message("nimbus", "answered blind", image=object(),
                                  privacy_skipped=True)

        assert seen[0].privacy_skipped is True

    def test_a_broken_message_never_reaches_the_pipeline(self, nimbus, mocker):
        nimbus._hud = mocker.MagicMock()
        mocker.patch("sessions.ChatMessage", side_effect=TypeError("bad field"))

        nimbus._emit_chat_message("user", "hello")  # must not raise

    def test_the_capture_fallback_hides_the_panel_around_a_grab(self, nimbus, mocker):
        """`S-7`: on pre-19041 Windows there is no exclusion flag, so the panel has to go the
        old way or the model sees Nimbus's own previous answer (Invariant 1)."""
        hud = mocker.MagicMock()
        nimbus._hud = hud

        nimbus._on_hide_overlay()
        nimbus._on_show_overlay()

        hud.hide_for_capture.assert_called_once()
        hud.show_after_capture.assert_called_once()

    def test_a_hud_that_throws_does_not_break_the_capture_cycle(self, nimbus, mocker):
        """The overlay hide/show cycle is Invariant 3. Nothing optional may break it."""
        hud = mocker.MagicMock()
        hud.hide_for_capture.side_effect = RuntimeError("gone")
        nimbus._hud = hud

        nimbus._on_hide_overlay()  # must not raise
        nimbus._overlay.hide_for_capture.assert_called_once()

    def test_the_caption_stands_down_while_the_panel_shows_the_transcript(
            self, nimbus, mocker):
        """§6.1: two copies of the same words on one screen is noise, and the caption is the
        one with nowhere to go."""
        import app as app_module

        mocker.patch.object(app_module, "CAPTIONS_ENABLED", True)
        hud = mocker.MagicMock()
        hud.is_showing_transcript.return_value = True
        nimbus._hud = hud

        nimbus._on_caption("where is the export button")

        nimbus._overlay.clear_captions.assert_called_once()
        nimbus._overlay.set_caption.assert_not_called()

    def test_the_caption_still_shows_when_the_panel_is_not(self, nimbus, mocker):
        import app as app_module

        mocker.patch.object(app_module, "CAPTIONS_ENABLED", True)
        hud = mocker.MagicMock()
        hud.is_showing_transcript.return_value = False
        nimbus._hud = hud

        nimbus._on_caption("where is the export button")

        nimbus._overlay.set_caption.assert_called_once()

    def test_a_hud_that_cannot_answer_does_not_suppress_the_caption(self, nimbus, mocker):
        import app as app_module

        mocker.patch.object(app_module, "CAPTIONS_ENABLED", True)
        hud = mocker.MagicMock()
        hud.is_showing_transcript.side_effect = RuntimeError("gone")
        nimbus._hud = hud

        nimbus._on_caption("hello")

        nimbus._overlay.set_caption.assert_called_once()


# --- sessions and _history (Invariant 7) ------------------------------------


class TestSessionsAndHistory:
    def test_a_new_chat_clears_history_in_place(self, nimbus, mocker):
        """In place, because the pipeline worker holds the same list object. Rebinding would
        leave it writing to the old one."""
        nimbus._sessions = mocker.MagicMock()
        nimbus._hud = mocker.MagicMock()
        mocker.patch("sessions.start_new_session", return_value=3)
        original = nimbus._history
        original.append({"role": "user", "content": [{"type": "text", "text": "old"}]})

        nimbus.start_new_chat()

        assert nimbus._history is original, "the pipeline still holds this object"
        assert nimbus._session_id == 3

    def test_a_new_chat_that_starts_a_fresh_thread_must_not_keep_sending_the_old_one(
            self, nimbus, tmp_path):
        """The real helper, not a mock: the clear is the part that matters and it lives there."""
        import sessions

        store = sessions.SessionStore(index_db_path=tmp_path / "index.db")
        nimbus._sessions = store
        nimbus._history.append({"role": "user", "content": [{"type": "text", "text": "old"}]})

        nimbus.start_new_chat()

        assert nimbus._history == []

    def test_opening_a_session_rebuilds_history_in_place(self, nimbus, mocker):
        nimbus._sessions = mocker.MagicMock()
        nimbus._hud = mocker.MagicMock()
        switch = mocker.patch("sessions.switch_session")
        original = nimbus._history

        nimbus.open_chat_session(11)

        switch.assert_called_once()
        assert switch.call_args.args[2] is original
        assert nimbus._session_id == 11

    def test_session_operations_are_no_ops_with_no_store(self, nimbus):
        nimbus._sessions = None
        nimbus.start_new_chat()
        nimbus.open_chat_session(4)
        assert nimbus._session_id == 0

    def test_a_failing_session_switch_does_not_raise(self, nimbus, mocker):
        nimbus._sessions = mocker.MagicMock()
        mocker.patch("sessions.switch_session", side_effect=OSError("db gone"))
        nimbus.open_chat_session(2)  # must not raise


# --- retry and repoint (§4 S-6b) --------------------------------------------


class TestRetryAndRepoint:
    def test_retry_refuses_while_a_turn_is_in_flight(self, nimbus, mocker):
        """Two pipeline workers writing the same ``_history`` and moving the same cursor is a
        race; the honest answer is to say so."""
        mocker.patch.object(nimbus, "_is_response_in_flight", return_value=True)
        toasts = []
        nimbus.sig_show_toast.connect(lambda text, kind: toasts.append(text))

        nimbus.retry_transcript("try again")

        assert toasts and "still working" in toasts[0]

    def test_retry_ignores_an_empty_transcript(self, nimbus, mocker):
        worker = mocker.patch.object(nimbus, "_retry_worker")
        nimbus.retry_transcript("   ")
        worker.assert_not_called()

    def test_retry_substitutes_the_transcript_and_restores_stt(self, nimbus, mocker):
        """A failed retry must not leave Nimbus permanently deaf."""
        original = nimbus._stt.stop_recording
        seen = {}

        def fake_pipeline(app_name, title, cancel, capture_queue):
            seen["transcript"] = nimbus._stt.stop_recording()

        mocker.patch.object(nimbus, "_pipeline_worker", side_effect=fake_pipeline)
        import queue as queue_module

        nimbus._retry_worker("where is the export button", queue_module.Queue())

        assert seen["transcript"] == "where is the export button"
        assert nimbus._stt.stop_recording is original

    def test_stt_is_restored_even_when_the_pipeline_raises(self, nimbus, mocker):
        import queue as queue_module

        original = nimbus._stt.stop_recording
        mocker.patch.object(nimbus, "_pipeline_worker", side_effect=RuntimeError("boom"))

        nimbus._retry_worker("hello", queue_module.Queue())

        assert nimbus._stt.stop_recording is original

    def test_repoint_recomputes_from_a_fresh_capture(self, nimbus, mocker):
        """A stored physical coordinate is only valid for the monitor layout that produced it.
        Space C plus a fresh capture survives the user moving a window or docking a laptop."""
        capture = mocker.MagicMock()
        capture.is_cursor_screen = True
        capture.scale_x = capture.scale_y = 2.0
        capture.monitor = {"left": 0, "top": 0, "width": 3840, "height": 2160}
        capture.target_width, capture.target_height = 1920, 1080
        mocker.patch.object(nimbus, "_capture_screens_guarded", return_value=[capture])
        pointed = []
        nimbus.sig_point_at.connect(lambda x, y, monitor: pointed.append((x, y)))

        nimbus.repoint_at(100, 200)

        assert pointed == [(200, 400)]

    def test_repoint_respects_the_privacy_guard(self, nimbus, mocker):
        """Re-pointing must not become a way to photograph a password manager."""
        mocker.patch.object(nimbus, "_capture_screens_guarded", return_value=[])
        pointed = []
        nimbus.sig_point_at.connect(lambda x, y, monitor: pointed.append((x, y)))
        toasts = []
        nimbus.sig_show_toast.connect(lambda text, kind: toasts.append(text))

        nimbus.repoint_at(100, 200)

        assert pointed == []
        assert toasts and "see the screen" in toasts[0]

    def test_repoint_never_raises(self, nimbus, mocker):
        mocker.patch.object(nimbus, "_capture_screens_guarded",
                            side_effect=RuntimeError("no screens"))
        nimbus.repoint_at(1, 1)  # must not raise


# --- Home's numbers (§3 S-2) ------------------------------------------------


class TestHomeNumbers:
    def test_a_completed_turn_is_recorded_newest_first(self, nimbus):
        nimbus._record_turn("excel.exe", "what is a pivot table?", "a summary")
        nimbus._record_turn("orionflow.exe", "where is render?", "view menu", target="Render")

        recent = nimbus.recent_turns()
        assert recent[0]["question"] == "where is render?"
        assert recent[0]["app"] == "orionflow.exe"
        assert recent[0]["target"] == "Render"
        assert nimbus.questions_this_week() == 2

    def test_the_recent_list_is_capped(self, nimbus):
        import app as app_module

        for index in range(app_module._MAX_RECENT_TURNS + 5):
            nimbus._record_turn("a.exe", f"q{index}", "answer")

        assert len(nimbus.recent_turns()) == app_module._MAX_RECENT_TURNS

    def test_only_the_last_seven_days_count(self, nimbus):
        from datetime import datetime, timedelta

        nimbus._question_times = [
            datetime.now() - timedelta(days=1),
            datetime.now() - timedelta(days=30),
        ]
        assert nimbus.questions_this_week() == 1

    def test_recording_a_turn_never_breaks_one(self, nimbus, mocker):
        """This runs after the user already has their answer."""
        mocker.patch.object(nimbus, "_within_window", side_effect=RuntimeError("clock"))
        nimbus._record_turn("a.exe", "q", "a")  # must not raise

    def test_the_returned_list_is_a_copy(self, nimbus):
        """A caller mutating Home's data would be editing the app's state."""
        nimbus._record_turn("a.exe", "q", "a")
        nimbus.recent_turns().clear()
        assert len(nimbus.recent_turns()) == 1


# --- review and folders -----------------------------------------------------


class TestReviewAndFolders:
    def test_quiz_me_goes_through_the_existing_journal_intent(self, nimbus, mocker):
        """So the button and the spoken command can never answer differently."""
        import app as app_module

        mocker.patch.object(app_module, "JOURNAL_ENABLED", True)
        intent = mocker.patch.object(
            nimbus, "_handle_journal_intent", return_value="First question: what is X?")

        nimbus.start_review()

        intent.assert_called_once()
        assert intent.call_args.args[0] == "quiz me"
        nimbus._tts.speak.assert_called_once_with("First question: what is X?")

    def test_quiz_me_says_so_when_the_journal_is_off(self, nimbus, mocker):
        import app as app_module

        mocker.patch.object(app_module, "JOURNAL_ENABLED", False)
        toasts = []
        nimbus.sig_show_toast.connect(lambda text, kind: toasts.append(text))

        nimbus.start_review()

        assert toasts and "switched off" in toasts[0]

    def test_the_memory_folder_is_created_if_missing(self, nimbus, mocker, tmp_path):
        folder = tmp_path / "memory"
        mocker.patch("config.MEMORY_DIR", folder)
        startfile = mocker.patch("app.os.startfile")

        nimbus.open_memory_folder()

        assert folder.is_dir()
        startfile.assert_called_once_with(str(folder))

    def test_a_folder_that_will_not_open_is_reported(self, nimbus, mocker, tmp_path):
        mocker.patch("config.MEMORY_DIR", tmp_path / "memory")
        mocker.patch("app.os.startfile", side_effect=OSError("no shell"))
        toasts = []
        nimbus.sig_show_toast.connect(lambda text, kind: toasts.append(text))

        nimbus.open_memory_folder()

        assert toasts and "memory folder" in toasts[0]


# --- shutdown ---------------------------------------------------------------


class TestKeyboardShortcuts:
    """Ctrl+Alt+H and Ctrl+Alt+N (§4 item 8).

    Routed through the **existing** listener, not a second one. Each ``pynput`` listener installs
    its own ``WH_KEYBOARD_LL`` hook and that hook runs on every keystroke system-wide; a second
    one would double that cost for two shortcuts.
    """

    def _hotkey(self, mocker, shortcuts):
        import hotkey

        listener = mocker.MagicMock()
        hk = hotkey.PushToTalkHotkey(
            on_press=lambda: None,
            on_release=lambda: None,
            listener_class=lambda **kwargs: listener,
            shortcuts=shortcuts,
        )
        hk.start()
        return hk

    def _press(self, hk, *keys):
        for key in keys:
            hk._handle_press(key)

    @staticmethod
    def _letter(character):
        """The ``KeyCode`` Windows actually delivers for a letter **with Ctrl held**.

        This is the whole reason the first implementation of these shortcuts never fired once on
        real hardware while every test here passed. The tests synthesised
        ``KeyCode.from_char("h")``; pynput's Windows backend builds keys through
        ``KeyTranslator.__call__``, which looks the character up in a layout table keyed by
        ``(shift, ctrl, alt)``, so with Ctrl down ``H`` translates to the control character
        ``\\x08`` and ``vk`` is populated with 72. ``from_char`` leaves ``vk`` as ``None``
        (measured), so a character-keyed match had nothing to match on.

        Every test in this class now synthesises this shape. The two fallbacks -- a bare
        character with no ``vk``, and a control character with no ``vk`` -- get their own tests
        below rather than being what the whole class relies on.
        """
        from pynput import keyboard

        return keyboard.KeyCode(char=chr(ord(character.upper()) - 64),
                                vk=ord(character.upper()))

    def test_a_shortcut_fires_when_the_ptt_modifiers_are_held(self, mocker):
        from pynput import keyboard

        fired = []
        hk = self._hotkey(mocker, {"h": lambda: fired.append("h")})

        self._press(hk, keyboard.Key.ctrl_l, keyboard.Key.alt_l, self._letter("h"))

        assert fired == ["h"]

    def test_the_virtual_key_code_is_what_matches(self, mocker):
        """Pins the defect directly: Ctrl+Alt+H arrives as vk 72 with char ``\\x08``.

        A regression here means the shortcuts silently stop working in the built app while the
        rest of this class keeps passing, which is exactly what happened the first time.
        """
        from pynput import keyboard

        import hotkey as hotkey_module

        assert hotkey_module.shortcut_vk("h") == 72
        assert hotkey_module.shortcut_vk("n") == 78
        assert hotkey_module.control_character_vk("\x08") == 72
        # The shape the old implementation was written against carries no vk at all.
        assert keyboard.KeyCode.from_char("h").vk is None

        fired = []
        hk = self._hotkey(mocker, {"h": lambda: fired.append("h")})
        self._press(hk, keyboard.Key.ctrl_l, keyboard.Key.alt_l,
                    keyboard.KeyCode(char="\x08", vk=72))
        assert fired == ["h"]

    def test_a_control_character_without_a_vk_still_matches(self, mocker):
        """The fallback for a key that carries ``\\x08`` but no ``vk``.

        ASCII control codes are the letter's alphabet position, so ``ord(c) + 64`` recovers the
        virtual key code. Cheap, and it means the binding does not depend on which pynput
        backend produced the event.
        """
        from pynput import keyboard

        fired = []
        hk = self._hotkey(mocker, {"n": lambda: fired.append("n")})

        self._press(hk, keyboard.Key.ctrl_l, keyboard.Key.alt_l,
                    keyboard.KeyCode.from_char("\x0e"))

        assert fired == ["n"]

    def test_a_plain_character_without_a_vk_still_matches(self, mocker):
        """Kept working on purpose, so a non-Windows backend or a plain synthesised key binds."""
        from pynput import keyboard

        fired = []
        hk = self._hotkey(mocker, {"h": lambda: fired.append("h")})

        self._press(hk, keyboard.Key.ctrl_l, keyboard.Key.alt_l, self._letter("h"))

        assert fired == ["h"]

    def test_an_unbound_letter_with_the_modifiers_held_does_nothing(self, mocker):
        """Ctrl+Alt+J must not fire the Ctrl+Alt+H binding just because both are letters."""
        from pynput import keyboard

        fired = []
        hk = self._hotkey(mocker, {"h": lambda: fired.append("h")})

        self._press(hk, keyboard.Key.ctrl_l, keyboard.Key.alt_l, self._letter("j"))

        assert fired == []

    def test_the_letter_alone_does_nothing(self, mocker):
        """Otherwise every "h" typed anywhere in Windows would open the chat panel."""
        from pynput import keyboard

        fired = []
        hk = self._hotkey(mocker, {"h": lambda: fired.append("h")})

        self._press(hk, self._letter("h"))

        assert fired == []

    def test_a_partial_modifier_set_does_nothing(self, mocker):
        from pynput import keyboard

        fired = []
        hk = self._hotkey(mocker, {"h": lambda: fired.append("h")})

        self._press(hk, keyboard.Key.ctrl_l, self._letter("h"))

        assert fired == []

    def test_the_shortcuts_follow_a_remapped_hotkey(self, mocker):
        """Reusing the configured modifier set rather than declaring its own: a user who remapped
        push-to-talk to Ctrl+Shift+Space gets Ctrl+Shift+H, which is what they would expect."""
        import hotkey
        from pynput import keyboard

        fired = []
        listener = mocker.MagicMock()
        # Not ctrl+shift+space: `parse_hotkey` rejects it outright because it collides with
        # Excel's and Google Sheets' "select entire worksheet", and the listener is
        # observe-only so the spreadsheet would receive it too.
        hk = hotkey.PushToTalkHotkey(
            on_press=lambda: None,
            on_release=lambda: None,
            hotkey="ctrl+shift+f9",
            listener_class=lambda **kwargs: listener,
            shortcuts={"h": lambda: fired.append("h")},
        )
        hk.start()

        self._press(hk, keyboard.Key.ctrl_l, keyboard.Key.shift, self._letter("h"))
        assert fired == ["h"]

        # And the old modifiers no longer work, because they are no longer the hotkey's.
        fired.clear()
        hk._handle_release(keyboard.Key.shift)
        hk._handle_release(keyboard.Key.ctrl_l)
        self._press(hk, keyboard.Key.alt_l, self._letter("h"))
        assert fired == []

    def test_a_paused_nimbus_ignores_its_shortcuts(self, mocker):
        """Pause means paused. A shortcut that still fired would make "paused" a half-truth."""
        from pynput import keyboard

        fired = []
        hk = self._hotkey(mocker, {"h": lambda: fired.append("h")})
        hk.set_enabled(False)

        self._press(hk, keyboard.Key.ctrl_l, keyboard.Key.alt_l, self._letter("h"))

        assert fired == []

    def test_a_shortcut_that_raises_does_not_kill_the_listener(self, mocker):
        """An exception escaping here kills the pynput thread, and with it push-to-talk for the
        rest of the session -- far worse than a shortcut that did not work."""
        from pynput import keyboard

        def boom():
            raise RuntimeError("window gone")

        hk = self._hotkey(mocker, {"h": boom})

        self._press(hk, keyboard.Key.ctrl_l, keyboard.Key.alt_l, self._letter("h"))

        # Still working: the PTT combo fires afterwards.
        pressed = []
        hk._on_press_cb = lambda: pressed.append(1)
        self._press(hk, keyboard.Key.space)
        assert pressed == [1]

    def test_a_shortcut_never_disturbs_the_ptt_state_machine(self, mocker):
        from pynput import keyboard

        import hotkey

        hk = self._hotkey(mocker, {"h": lambda: None})
        self._press(hk, keyboard.Key.ctrl_l, keyboard.Key.alt_l, self._letter("h"))

        assert hk.state == hotkey.HotkeyState.IDLE

    def test_the_app_binds_both_letters(self, nimbus, mocker):
        captured = {}

        def capture(**kwargs):
            captured.update(kwargs)
            return mocker.MagicMock()

        # Patched on `app`, not on `hotkey`: app.py does `from hotkey import PushToTalkHotkey`,
        # so it holds its own reference and patching the source module would not be seen.
        mocker.patch("app.PushToTalkHotkey", side_effect=capture)
        nimbus._hotkey = None
        nimbus._overlay = mocker.MagicMock()
        nimbus.start()

        assert set(captured["shortcuts"]) == {"h", "n"}

    def test_toggle_shows_a_hidden_panel_and_hides_a_visible_one(self, nimbus, mocker):
        hud = mocker.MagicMock()
        hud.isVisible.return_value = False
        hud.collapsed = False
        nimbus._hud = hud

        nimbus.sig_toggle_chat.emit()
        hud.show.assert_called_once()

        hud.isVisible.return_value = True
        nimbus.sig_toggle_chat.emit()
        hud.hide.assert_called_once()

    def test_toggle_expands_a_collapsed_panel_rather_than_hiding_it(self, nimbus, mocker):
        """A collapsed panel is technically visible, so a plain visibility toggle would "show" a
        bar with no transcript and look like it had done nothing."""
        hud = mocker.MagicMock()
        hud.isVisible.return_value = True
        hud.collapsed = True
        nimbus._hud = hud

        nimbus.sig_toggle_chat.emit()

        hud.set_collapsed.assert_called_once_with(False)
        hud.hide.assert_not_called()

    def test_toggle_is_a_no_op_with_no_panel(self, nimbus):
        nimbus._hud = None
        nimbus.sig_toggle_chat.emit()  # must not raise

    def test_new_chat_shortcut_starts_a_session(self, nimbus, mocker):
        started = mocker.patch.object(nimbus, "start_new_chat")
        # Reconnect, because the constructor bound the original method object.
        nimbus.sig_new_chat_requested.disconnect()
        nimbus.sig_new_chat_requested.connect(started)

        nimbus.sig_new_chat_requested.emit()

        started.assert_called_once()


class TestShutdown:
    def test_stop_hides_both_surfaces(self, nimbus, mocker):
        """A Qt window still on screen while the pipeline is dismantled looks like a hang,
        and the HUD is always-on-top so it would be the last thing visible."""
        hud, window = mocker.MagicMock(), mocker.MagicMock()
        nimbus._hud, nimbus._window = hud, window

        nimbus.stop()

        hud.hide.assert_called_once()
        window.hide.assert_called_once()

    def test_stop_survives_a_surface_that_will_not_hide(self, nimbus, mocker):
        hud = mocker.MagicMock()
        hud.hide.side_effect = RuntimeError("already destroyed")
        nimbus._hud = hud

        nimbus.stop()  # must not raise


class TestChatVisibility:
    """The live chat-panel switch: "when I ask it something it will open the chat again".

    Two halves, and shipping only the first is the trap. Hiding the panel is easy; keeping it
    hidden means also stopping it revealing itself on the next interaction, which
    ``ChatHud.set_auto_reveal`` is for. Without that the panel returns within seconds and the
    switch looks broken.
    """

    def _hud(self, nimbus, mocker):
        hud = mocker.MagicMock()
        hud.collapsed = False
        hud.isVisible.return_value = True
        nimbus._hud = hud
        return hud

    def test_turning_it_off_hides_the_panel_and_stops_it_returning(self, nimbus, mocker):
        mocker.patch("config.persist_setting", return_value=True)
        hud = self._hud(nimbus, mocker)

        nimbus.set_chat_visible(False)

        hud.hide.assert_called_once()
        hud.set_auto_reveal.assert_called_once_with(False)

    def test_turning_it_on_reveals_and_uncollapses(self, nimbus, mocker):
        mocker.patch("config.persist_setting", return_value=True)
        hud = self._hud(nimbus, mocker)
        hud.collapsed = True

        nimbus.set_chat_visible(True)

        hud.set_auto_reveal.assert_called_once_with(True)
        hud.set_collapsed.assert_called_once_with(False)
        hud.reveal.assert_called_once()

    def test_the_choice_is_persisted_to_the_setting_settings_already_shows(self, nimbus, mocker):
        """``CHAT_HUD``, not a second key. Two keys for one idea is how they come to disagree."""
        persist = mocker.patch("config.persist_setting", return_value=True)
        self._hud(nimbus, mocker)

        nimbus.set_chat_visible(False)
        assert persist.call_args_list[-1].args == ("CHAT_HUD", "off")

        nimbus.set_chat_visible(True)
        assert persist.call_args_list[-1].args == ("CHAT_HUD", "on")

    def test_a_locked_keyring_costs_the_persistence_not_the_toggle(self, nimbus, mocker):
        mocker.patch("config.persist_setting", side_effect=RuntimeError("vault locked"))
        hud = self._hud(nimbus, mocker)

        nimbus.set_chat_visible(False)

        hud.hide.assert_called_once()

    def test_it_reports_the_state_achieved_not_the_state_requested(self, nimbus, mocker):
        """With no panel built there is nothing to show, and saying otherwise leaves views lying.

        Same rule as ``set_listening``, which emits ``is_listening`` rather than its argument.
        """
        mocker.patch("config.persist_setting", return_value=True)
        nimbus._hud = None
        seen = []
        nimbus.sig_chat_visible_changed.connect(seen.append)
        toasts = []
        nimbus.sig_show_toast.connect(lambda text, kind: toasts.append(text))

        nimbus.set_chat_visible(True)

        assert seen == [False]
        assert toasts, "a user asking for a panel that cannot exist deserves to be told"

    def test_visibility_is_read_from_the_widget(self, nimbus, mocker):
        hud = self._hud(nimbus, mocker)
        assert nimbus.is_chat_visible is True
        hud.isVisible.return_value = False
        assert nimbus.is_chat_visible is False

    def test_a_hud_that_raises_does_not_take_the_switch_down(self, nimbus, mocker):
        mocker.patch("config.persist_setting", return_value=True)
        hud = self._hud(nimbus, mocker)
        hud.set_auto_reveal.side_effect = RuntimeError("gone")

        nimbus.set_chat_visible(False)  # must not raise

    def test_the_window_switch_routes_through_nimbus(self, window, nimbus, mocker):
        """`S-3`'s arrangement applied to the chat panel: the window asks, NimbusApp writes."""
        mocker.patch("config.persist_setting", return_value=True)
        hud = self._hud(nimbus, mocker)

        window.sig_set_chat_visible.emit(False)

        hud.hide.assert_called_once()


class TestAutoRevealSuppression:
    """The HUD half of the switch, tested on a real ``ChatHud``."""

    @pytest.fixture
    def hud(self, qt_app, tmp_path):
        """Same isolation as ``test_chat_hud.make_hud``: temp paths, stubbed exclusion, no
        auto-hide, and a fixed screen so geometry does not depend on the host's monitors."""
        from PyQt6.QtCore import QRect

        from chat_hud import ChatHud
        from sessions import SessionStore

        built = ChatHud(
            store=SessionStore(
                index_db_path=tmp_path / "index.db", store_screenshots=False),
            exclude=lambda hwnd: True,
            positions_path=tmp_path / "chat_hud.json",
            autohide_seconds=0,
            screen_geometry_fn=lambda: QRect(0, 0, 1920, 1080),
        )
        yield built
        built.hide()

    def test_an_interaction_reveals_the_panel_by_default(self, hud, qt_app):
        hud.hide()
        hud.set_state("listening")
        qt_app.processEvents()
        assert hud.isVisible()

    def test_with_auto_reveal_off_an_interaction_leaves_it_hidden(self, hud, qt_app):
        hud.hide()
        hud.set_auto_reveal(False)
        hud.set_state("listening")
        qt_app.processEvents()
        assert not hud.isVisible()

    def test_the_transcript_still_accumulates_while_hidden(self, hud, qt_app):
        """The panel is a view (Invariant 10). Hiding it must not change what is recorded, or
        turning it back on would show an empty panel instead of what was said."""
        from sessions import ChatMessage

        hud.hide()
        hud.set_auto_reveal(False)
        before = hud.row_count()
        hud.append(ChatMessage(role="user", text="what is this button"))
        qt_app.processEvents()

        assert hud.row_count() == before + 1
        assert not hud.isVisible()

    def test_explicit_reveal_still_works(self, hud, qt_app):
        """Ctrl+Alt+H and the Home switch must still reach it, or the panel is unreachable."""
        hud.hide()
        hud.set_auto_reveal(False)
        hud.reveal()
        qt_app.processEvents()
        assert hud.isVisible()
