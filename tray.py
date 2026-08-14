"""System tray icon for Nimbus.

Provides the ONLY clean exit path from the running app — without this
icon, users have no menu/taskbar entry to right-click and quit, and
must reach for Task Manager (Ctrl+Shift+Esc) to kill ``Nimbus.exe``.

Menu structure (right-click the tray icon):
    - Show Nimbus                   ← open/raise the main window
    - Pause push-to-talk            ← the one action worth having in one click
    - --------
    - Quit Nimbus                   ← clean shutdown via callback

Left-click the icon does the same as Show Nimbus, which is what every other tray app does.

## Why the menu shrank (SHELL_AND_CHAT.md §3 `S-5`)

It used to also carry Settings, Open Knowledge Folder, Open Memory Folder and Export Session
History. All four now live in the window — Settings as a page, the knowledge folder on the
Knowledge page, and the memory folder and export on Home. A menu that duplicates the window is
two places to keep in sync and two places to fix a bug.

**The tray does not go away, and Pause stays in it.** The tray is the only surface available
when the window is closed, and pausing is the one action whose entire value is being reachable
without opening anything.

**Pause holds no state of its own** (`S-3`). ``_pause_action``'s checkmark is a *view* of
``hotkey.enabled``; the app pushes the truth in through ``set_paused``, and the window's toggle
does the same. Three views, one source, so they cannot drift apart.

Implementation notes:
- ``QSystemTrayIcon`` is the PyQt6 native widget — no extra deps
  (we explicitly chose this over ``pystray``, which is no longer
  actively maintained). Plays nicely with our existing Qt event loop.
- Tray icon source: ``assets/nimbus_tray.ico`` (multi-res 16/20/24/32/
  40/48/64/128/256, generated from the logo artwork by
  ``tools/make_icons.py``). Still generated programmatically rather than
  exported by hand — image models fail at 16x16, and downscaling needs the
  same background key, crop and per-size sharpening every time the logo
  changes. Re-run that script after replacing the artwork.
- The Quit action calls a parent-supplied callback rather than
  closing windows directly. The callback in app.py runs ``stop()``
  on STT/TTS/hotkey before ``QApplication.quit()`` to avoid leaking
  worker threads / WebSocket connections.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QObject
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

import config


def _display_hotkey(hotkey: str) -> str:
    """Turn pynput's persisted ``ctrl+alt+space`` form into readable UI text."""
    return "+".join(part.capitalize() for part in hotkey.split("+"))


class NimbusTray(QObject):
    """Tray icon + right-click menu wrapper.

    The icon is shown as soon as ``__init__`` completes. All callbacks fire on the Qt main
    thread.

    Args:
        on_quit: the clean-shutdown path. The only one; the window's Quit button routes here
            too, so there is a single shutdown sequence rather than two that drift.
        on_show_window: open/raise the main window. Also bound to a left-click on the icon.
        on_pause_changed: the user toggled Pause. Receives ``True`` for paused. The tray does
            **not** apply this itself -- the app writes ``hotkey.enabled`` and pushes the
            result back through ``set_paused``.
        on_settings: **fallback only.** Adds a Settings item, for the case where the main
            window could not be constructed and Settings would otherwise be unreachable. In
            normal operation this is ``None`` and Settings lives in the window (`S-5`).
    """

    def __init__(
        self,
        *,
        on_quit: Callable[[], None],
        on_show_window: Callable[[], None] | None = None,
        on_pause_changed: Callable[[bool], None] | None = None,
        on_settings: Callable[[], None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)

        # Guard against weird Windows configs without a system tray
        # (kiosk mode, custom shells, certain VMs). Without this check
        # the tray icon silently fails to appear and the user has no
        # diagnostic — just an apparently-running app with no quit
        # menu. Caller in app.py wraps with try/except + QMessageBox
        # so the user gets an actionable dialog before exit.
        if not QSystemTrayIcon.isSystemTrayAvailable():
            raise RuntimeError(
                "System tray is not available on this Windows "
                "configuration. Nimbus needs the system tray to "
                "provide its quit menu and settings access. Check "
                "Windows taskbar settings (Settings -> "
                "Personalisation -> Taskbar -> Other system tray "
                "icons)."
            )

        self._on_quit = on_quit
        self._on_show_window = on_show_window or (lambda: None)
        self._on_pause_changed = on_pause_changed or (lambda _paused: None)
        self._on_settings = on_settings

        self._icon = QSystemTrayIcon(parent=self)
        self._icon.setToolTip("Nimbus — push-to-talk AI buddy")
        icon_path = Path(__file__).parent / "assets" / "nimbus_tray.ico"
        if icon_path.is_file():
            self._icon.setIcon(QIcon(str(icon_path)))

        self._menu = QMenu()
        self._build_menu()
        self._icon.setContextMenu(self._menu)
        self._icon.activated.connect(self._on_activated)
        self._icon.show()
        self._show_onboarding_once()

    def _show_onboarding_once(self) -> None:
        """Show a native tray balloon exactly once after the icon is available."""
        if config.onboarding_seen():
            return
        self._icon.showMessage(
            "Nimbus is running",
            f"Hold {_display_hotkey(config.HOTKEY)} to talk to Nimbus. "
            "Right-click this icon to open Settings.",
            QSystemTrayIcon.MessageIcon.Information,
            8_000,
        )
        config.mark_onboarding_seen()

    def notify(self, title: str, message: str) -> None:
        """Show a tray balloon. Used for "Nimbus is still running" after a window close.

        Closing the window hides it rather than quitting (Invariant 5), which is correct but
        invisible: without a word, a user who closed it reasonably concludes Nimbus is gone and
        is then surprised when the hotkey still answers.
        """
        try:
            self._icon.showMessage(
                title, message, QSystemTrayIcon.MessageIcon.Information, 4_000)
        except Exception:
            pass  # a balloon that fails is not worth an exception path

    # ---------- Menu construction ---------------------------------------

    def _build_menu(self) -> None:
        act_show = QAction("Show Nimbus", self)
        act_show.triggered.connect(lambda: self._on_show_window())
        self._menu.addAction(act_show)

        self._pause_action = QAction("Pause push-to-talk", self)
        self._pause_action.setCheckable(True)
        self._pause_action.toggled.connect(self._on_pause_changed)
        self._menu.addAction(self._pause_action)

        # Present only when the window is unavailable, so Settings can never become
        # unreachable. See the class docstring.
        if self._on_settings is not None:
            act_settings = QAction("Settings...", self)
            act_settings.triggered.connect(self._on_settings)
            self._menu.addAction(act_settings)

        self._menu.addSeparator()

        act_quit = QAction("Quit Nimbus", self)
        act_quit.triggered.connect(self._on_quit)
        self._menu.addAction(act_quit)

    # ---------- State reflection (S-3) ----------------------------------

    def set_paused(self, paused: bool) -> None:
        """Reflect externally-owned pause state without re-emitting ``toggled``.

        Signals are blocked deliberately. Without that, pushing the truth in from the app
        would look like a user click, the app would act on it, and the two would ping-pong --
        the same reason ``shell.widgets.PowerToggle.set_on`` is silent.
        """
        blocked = self._pause_action.blockSignals(True)
        self._pause_action.setChecked(bool(paused))
        self._pause_action.blockSignals(blocked)

    def _on_activated(self, reason) -> None:
        """Left-click (or double-click) the icon opens the window, as every tray app does.

        ``Context`` is excluded: that is the right-click that opens the menu, and showing the
        window as well would be actively hostile.
        """
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._on_show_window()
