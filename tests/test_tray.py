"""Unit tests for tray.py — system tray menu + window/pause actions.

Tray icon construction needs a QApplication. We use a session-scoped
fixture that creates one if none exists. The actual rendering
(``self._icon.show()``) is silently no-op'd on systems without a
display, so tests run headless cleanly.

**The menu shrank deliberately** (SHELL_AND_CHAT.md §3 `S-5`). Settings, Open Knowledge
Folder, Open Memory Folder and Export Session History all moved into the main window, so the
tray is down to Show Nimbus, Pause and Quit. The tests for the removed items went with them;
the capabilities are covered by ``tests/test_shell.py`` and ``tests/test_app.py`` instead.
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="session")
def qapp():
    """Session-shared QApplication. Created once; reused across tests."""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def test_tray_module_importable():
    import tray  # noqa: F401


class TestNimbusTrayMenu:
    """The tray menu exposes the window, pause, and quit — and nothing the window owns."""

    @pytest.fixture(autouse=True)
    def _suppress_real_onboarding_persistence(self, mocker):
        """Tray construction must never write the developer's real keyring."""
        mocker.patch("tray.config.onboarding_seen", return_value=True)

    def test_menu_is_show_pause_quit(self, qapp, mocker):
        """`S-5`: the tray is a background-utility surface, not a second copy of the window."""
        from tray import NimbusTray

        t = NimbusTray(
            on_quit=mocker.MagicMock(),
            on_show_window=mocker.MagicMock(),
            on_pause_changed=mocker.MagicMock(),
        )

        actions = [a for a in t._menu.actions() if not a.isSeparator()]
        assert [a.text() for a in actions] == [
            "Show Nimbus",
            "Pause push-to-talk",
            "Quit Nimbus",
        ]

    def test_the_window_owns_settings_and_the_folders(self, qapp, mocker):
        """A drift guard: these four must not creep back in beside their window homes."""
        from tray import NimbusTray

        t = NimbusTray(on_quit=mocker.MagicMock(), on_show_window=mocker.MagicMock())

        labels = [a.text() for a in t._menu.actions()]
        for gone in ("Settings...", "Open Knowledge Folder", "Open Memory Folder",
                     "Export Session History"):
            assert gone not in labels

    def test_settings_returns_only_as_an_explicit_fallback(self, qapp, mocker):
        """If the window cannot be built, Settings must not become unreachable."""
        from tray import NimbusTray

        on_settings = mocker.MagicMock()
        t = NimbusTray(
            on_quit=mocker.MagicMock(),
            on_show_window=mocker.MagicMock(),
            on_settings=on_settings,
        )

        action = next(a for a in t._menu.actions() if a.text() == "Settings...")
        action.trigger()
        on_settings.assert_called_once()

    def test_show_action_opens_the_window(self, qapp, mocker):
        from tray import NimbusTray

        on_show = mocker.MagicMock()
        t = NimbusTray(on_quit=mocker.MagicMock(), on_show_window=on_show)

        next(a for a in t._menu.actions() if a.text() == "Show Nimbus").trigger()
        on_show.assert_called_once()

    @pytest.mark.parametrize("reason_name", ["Trigger", "DoubleClick"])
    def test_left_click_on_the_icon_opens_the_window(self, qapp, mocker, reason_name):
        from PyQt6.QtWidgets import QSystemTrayIcon

        from tray import NimbusTray

        on_show = mocker.MagicMock()
        t = NimbusTray(on_quit=mocker.MagicMock(), on_show_window=on_show)

        t._on_activated(getattr(QSystemTrayIcon.ActivationReason, reason_name))
        on_show.assert_called_once()

    def test_right_click_does_not_also_open_the_window(self, qapp, mocker):
        """``Context`` is the menu-opening click; showing the window too would be hostile."""
        from PyQt6.QtWidgets import QSystemTrayIcon

        from tray import NimbusTray

        on_show = mocker.MagicMock()
        t = NimbusTray(on_quit=mocker.MagicMock(), on_show_window=on_show)

        t._on_activated(QSystemTrayIcon.ActivationReason.Context)
        on_show.assert_not_called()

    def test_pause_action_is_checkable_and_notifies_app(self, qapp, mocker):
        from tray import NimbusTray
        changed = mocker.MagicMock()
        tray = NimbusTray(
            on_quit=mocker.MagicMock(),
            on_show_window=mocker.MagicMock(),
            on_pause_changed=changed,
        )
        action = next(a for a in tray._menu.actions() if a.text() == "Pause push-to-talk")
        assert action.isCheckable()
        action.trigger()
        changed.assert_called_once_with(True)

    def test_set_paused_reflects_state_without_re_emitting(self, qapp, mocker):
        """`S-3`: the checkmark is a view of ``hotkey.enabled``, not a second copy of it.

        If pushing the truth in looked like a user click, the app would act on its own
        notification and the tray and window would ping-pong.
        """
        from tray import NimbusTray

        changed = mocker.MagicMock()
        t = NimbusTray(
            on_quit=mocker.MagicMock(),
            on_show_window=mocker.MagicMock(),
            on_pause_changed=changed,
        )

        t.set_paused(True)
        assert t._pause_action.isChecked() is True
        t.set_paused(False)
        assert t._pause_action.isChecked() is False
        changed.assert_not_called()

    def test_notify_shows_a_balloon_and_never_raises(self, qapp, mocker):
        from tray import NimbusTray

        show_message = mocker.patch("tray.QSystemTrayIcon.showMessage")
        t = NimbusTray(on_quit=mocker.MagicMock(), on_show_window=mocker.MagicMock())

        t.notify("Nimbus is still running", "Push-to-talk is still active.")
        show_message.assert_called_once()

        show_message.side_effect = RuntimeError("no tray")
        t.notify("a", "b")  # must not raise

    def test_first_launch_shows_configured_hotkey_onboarding_once(self, qapp, mocker):
        """The balloon uses config.HOTKEY and only marks state after display."""
        mocker.patch("tray.config.onboarding_seen", return_value=False)
        marked = mocker.patch("tray.config.mark_onboarding_seen")
        mocker.patch("tray.config.HOTKEY", "ctrl+shift+f2")
        show_message = mocker.patch("tray.QSystemTrayIcon.showMessage")

        from tray import NimbusTray
        NimbusTray(on_quit=mocker.MagicMock(), on_show_window=mocker.MagicMock())

        show_message.assert_called_once()
        assert "Ctrl+Shift+F2" in show_message.call_args.args[1]
        assert "Right-click" in show_message.call_args.args[1]
        marked.assert_called_once()

    def test_seen_onboarding_never_shows_again(self, qapp, mocker):
        mocker.patch("tray.config.onboarding_seen", return_value=True)
        marked = mocker.patch("tray.config.mark_onboarding_seen")
        show_message = mocker.patch("tray.QSystemTrayIcon.showMessage")

        from tray import NimbusTray
        NimbusTray(on_quit=mocker.MagicMock(), on_show_window=mocker.MagicMock())

        show_message.assert_not_called()
        marked.assert_not_called()

    def test_quit_action_triggers_on_quit_callback(self, qapp, mocker):
        from tray import NimbusTray
        on_quit = mocker.MagicMock()
        on_show = mocker.MagicMock()
        t = NimbusTray(on_quit=on_quit, on_show_window=on_show)

        quit_action = next(
            a for a in t._menu.actions() if a.text() == "Quit Nimbus"
        )
        quit_action.trigger()
        on_quit.assert_called_once()
        on_show.assert_not_called()

    def test_raises_runtime_error_when_system_tray_unavailable(
        self, qapp, mocker
    ):
        """If QSystemTrayIcon.isSystemTrayAvailable() returns False (rare
        Windows config — kiosk mode, custom shell, certain VMs), the
        constructor must raise RuntimeError so the caller can show a
        QMessageBox + exit cleanly. Without this guard the tray icon
        silently doesn't appear and users have no diagnostic."""
        from tray import NimbusTray
        mocker.patch(
            "tray.QSystemTrayIcon.isSystemTrayAvailable",
            return_value=False,
        )
        with pytest.raises(RuntimeError, match="System tray is not available"):
            NimbusTray(on_quit=mocker.MagicMock())
