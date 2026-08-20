"""Open the Nimbus window and chat HUD with sample content, for looking at them.

    python -m tools.preview_ui              both surfaces
    python -m tools.preview_ui --window     just the window
    python -m tools.preview_ui --hud        just the chat panel
    python -m tools.preview_ui --cursor      add the on-screen pointer to the above
    python -m tools.preview_ui --cursor-only just the pointer, nothing else
    python -m tools.preview_ui --nav right  preview NAV_SIDE=right without saving it

## Why this exists

Reviewing a design by reading a stylesheet does not work, and the alternative -- launching the
real app -- means a microphone, API keys, a global keyboard hook, and ``SHELL_ON_STARTUP``
being on before anything is even visible. None of that has any bearing on how the interface
looks.

So this is the visual review harness: real widgets, real ``theme.build_qss()``, real pages,
fed with sample data. **No pipeline, no hotkey, no network, no microphone.**

## It writes nothing

Every path is redirected into a temporary folder before ``config`` is imported, so a preview
cannot touch ``~/.nimbus``, the real chat database, or the user's knowledge folder. That
matters more than it looks: ``SessionStore`` creates tables on construction, and a preview that
quietly wrote to the real database would corrupt exactly the history it was previewing.

Settings is the one page shown with a stub rather than the real form, because the real
``SettingsForm`` reads Windows Credential Manager on construction. Use the real app to review
Settings, or pass ``--real-settings`` to accept the keyring read.
"""
from __future__ import annotations

import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="nimbus-preview-"))


def _redirect_paths_to_temp() -> None:
    """Point every on-disk artefact at a temp folder. Must run before anything imports it."""
    import config

    config.INDEX_DB_PATH = _TMP / "index.db"
    config.MEMORY_DIR = _TMP / "memory"
    config.KB_DIR = _TMP / "knowledge"
    config.INSIGHTS_PATH = _TMP / "insights.md"
    for folder in (config.MEMORY_DIR, config.KB_DIR):
        Path(folder).mkdir(parents=True, exist_ok=True)


def _seed_knowledge(folder: Path) -> None:
    """Both knowledge-base layouts, so the Knowledge page has something real to list."""
    import kb

    (folder / kb.GUIDE_FILENAME).write_text(
        "# Nimbus knowledge base\n\n"
        "Drop a Markdown file named after the program's .exe, for example\n"
        "`orionflow.exe.md`, or a folder of the same name holding .md, .txt,\n"
        ".pdf and .docx files.\n\n"
        "Picked up on your next question. No restart needed.\n",
        encoding="utf-8")
    (folder / "excel.exe.md").write_text(
        "# Excel notes\n\nPivot tables live under Insert.\n", encoding="utf-8")
    app_folder = folder / "orionflow.exe"
    app_folder.mkdir(exist_ok=True)
    (app_folder / "render-queue.md").write_text(
        "The render queue is under View.\n", encoding="utf-8")
    (app_folder / "shortcuts.txt").write_text("Ctrl+R renders.\n", encoding="utf-8")


def _sample_recent() -> list[dict]:
    now = datetime.now()
    return [
        {"question": "where is the render queue?", "app": "orionflow.exe",
         "when": now - timedelta(seconds=30), "target": "View menu"},
        {"question": "what does this error mean?", "app": "code.exe",
         "when": now - timedelta(minutes=4), "target": ""},
        {"question": "how do I make a pivot table?", "app": "excel.exe",
         "when": now - timedelta(minutes=22), "target": "Insert ribbon"},
        {"question": "circle the export button for me", "app": "orionflow.exe",
         "when": now - timedelta(hours=2), "target": "Export"},
        {"question": "quiz me on what we covered", "app": "excel.exe",
         "when": now - timedelta(hours=5), "target": ""},
    ]


def _sample_review_queue():
    """A real ``ReviewQueue`` in the temp folder, with items already due."""
    import review

    queue = review.ReviewQueue(_TMP / "index.db")
    learned = date.today() - timedelta(days=4)
    for app_name, question, answer, target in (
        ("orionflow.exe", "Where is the render queue?", "Under the View menu.", "View menu"),
        ("excel.exe", "How do you insert a pivot table?", "Insert > PivotTable.", "Insert"),
        ("code.exe", "What does the squiggly underline mean?", "A type error.", ""),
    ):
        queue.add(app_name, question, answer, target_label=target, today=learned)
    return queue


def build_window(nav_side: str = "left", real_settings: bool = False):
    """The real ``MainWindow``, with every provider fed sample data."""
    from shell.window import MainWindow

    _seed_knowledge(_TMP / "knowledge")
    queue = _sample_review_queue()
    listening = {"on": True}
    chat_visible = {"on": True}

    def form_factory():
        if real_settings:
            from settings_dialog import SettingsForm
            return SettingsForm()
        return _StubSettingsForm()

    window = MainWindow(
        listening_provider=lambda: listening["on"],
        hotkey_provider=lambda: "ctrl+alt+space",
        usage_provider=lambda: 37,
        privacy_provider=lambda: 4,
        recent_provider=_sample_recent,
        # Backed by a dict so the switch actually holds in the preview. In the app this reads
        # ``ChatHud.isVisible()``, and there is no HUD here.
        chat_visible_provider=lambda: chat_visible["on"],
        kb_dir=_TMP / "knowledge",
        review_queue_provider=lambda: queue,
        settings_form_factory=form_factory,
        nav_side_override=nav_side,
    )
    # The preview owns the state the app would own, so the toggle behaves for real.
    window.sig_set_listening.connect(lambda on: listening.__setitem__("on", on))
    window.sig_set_chat_visible.connect(lambda on: chat_visible.__setitem__("on", on))
    window.sig_set_chat_visible.connect(lambda _on: window.refresh_chat())
    window.sig_quit.connect(_quit)
    window.set_provider("gemini-native", "gemini-3-flash-preview")
    window.set_privacy_guard(True)
    return window


def build_hud():
    """The real ``ChatHud`` with a real store in the temp folder, and a sample conversation."""
    import sessions
    from chat_hud import ChatHud
    from sessions import ChatMessage

    store = sessions.SessionStore(index_db_path=_TMP / "index.db")
    hud = ChatHud(store=store)
    hud.set_session(sessions.start_new_session(store, "orionflow.exe"), "")

    for role, text, coordinate in (
        ("user", "where is the render queue in this thing?", None),
        ("nimbus", "It's under the View menu, third item down. I've put the cursor on it.",
         (412, 96)),
        ("user", "and how do I add a frame range?", None),
        ("nimbus", "Same panel, the Range field at the top right. Type the start and end "
                   "frames separated by a dash.", (1180, 210)),
    ):
        hud.append(ChatMessage(role=role, text=text, coordinate=coordinate))
    hud.set_state("idle")
    return hud


class _StubSettingsForm:
    """Placeholder for the Settings page, so a preview never reads the keyring.

    Not a subclass of ``SettingsForm``: the point is that it shares none of its behaviour.
    """

    def __new__(cls):
        from PyQt6.QtCore import pyqtSignal
        from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

        class Stub(QWidget):
            sig_validity_changed = pyqtSignal(bool)
            sig_local_data_cleared = pyqtSignal()
            sig_saved = pyqtSignal()

            def __init__(self):
                super().__init__()
                layout = QVBoxLayout(self)
                note = QLabel(
                    "Settings is not shown in the preview.\n\n"
                    "The real form reads Windows Credential Manager when it is built, and a "
                    "visual preview has no business touching your saved keys.\n\n"
                    "Run Nimbus itself to review this page, or pass --real-settings.")
                note.setWordWrap(True)
                note.setObjectName("Secondary")
                layout.addWidget(note)
                layout.addStretch(1)

            def is_valid(self):
                return False

            def save(self):
                return False

            @property
            def local_data_cleared(self):
                return False

        return Stub()


def build_overlay():
    """The real ``OverlayController``, idling so the pointer follows the mouse.

    The on-screen pointer is the one part of Nimbus that cannot be reviewed by opening a window:
    it lives in a click-through, always-on-top overlay that only appears once the pipeline puts
    it into a state. So this drives it directly -- ``resume_idle`` starts the follow-the-mouse
    loop, and a timer fires a ``point_at`` at the current cursor every few seconds so the flight
    animation, the accent, the trail and the black border can all be seen.

    Nothing here touches the pipeline: the controller is a view, and this is the same public API
    ``app.py`` drives it with. It starts following the mouse the moment it is constructed -- the
    constructor shows one click-through window per screen and starts a 60Hz follow timer -- so
    there is nothing to switch on.
    """
    from PyQt6.QtCore import QTimer
    from PyQt6.QtGui import QCursor

    from overlay import OverlayController

    controller = OverlayController()

    def fly_somewhere():
        """Point at a spot near the mouse, so the flight is visible without hunting for it."""
        position = QCursor.pos()
        monitors = [
            {"left": screen.geometry().left(), "top": screen.geometry().top(),
             "width": screen.geometry().width(), "height": screen.geometry().height()}
            for screen in _screens()
        ]
        monitor = next(
            (m for m in monitors
             if m["left"] <= position.x() < m["left"] + m["width"]
             and m["top"] <= position.y() < m["top"] + m["height"]),
            monitors[0] if monitors else None,
        )
        if monitor is None:
            return
        controller.point_at(position.x() + 120, position.y() + 80, monitor)

    timer = QTimer()
    timer.timeout.connect(fly_somewhere)
    timer.start(4000)
    controller._preview_timer = timer  # keep it alive for the process
    return controller


def _screens():
    from PyQt6.QtGui import QGuiApplication

    return QGuiApplication.screens()


def _quit() -> None:
    from PyQt6.QtWidgets import QApplication

    print("preview: quit")
    app = QApplication.instance()
    if app is not None:
        app.quit()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # `--cursor` is *additive*: it adds the pointer to whatever else is showing, because the
    # pointer is the thing you want to see moving across the rest of the interface.
    # `--cursor-only` is the way to get it on its own.
    only_cursor = "--cursor-only" in argv
    show_cursor = only_cursor or "--cursor" in argv
    show_window = "--hud" not in argv and not only_cursor
    show_hud = "--window" not in argv and not only_cursor
    real_settings = "--real-settings" in argv
    nav_side = "right" if "right" in argv else "left"

    _redirect_paths_to_temp()

    from PyQt6.QtWidgets import QApplication

    import theme

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    # The whole stylesheet, applied exactly as app.py applies it, so dialogs and menus opened
    # from the preview look like they do in the real app.
    app.setStyleSheet(theme.build_qss())

    print("=" * 68)
    print("Nimbus UI preview")
    print(f"  temp folder      : {_TMP}")
    print(f"  animations       : {'on' if theme.animations_enabled() else 'reduced'}")
    print(f"  nav side         : {nav_side}")
    print("=" * 68)

    window = hud = None
    if show_window:
        window = build_window(nav_side=nav_side, real_settings=real_settings)
        window.show()
        print(f"  window           : {window.width()}x{window.height()}  "
              f"(min {window.minimumWidth()}x{window.minimumHeight()})")
        print("  Closing the window hides it, exactly as in the real app. Use the Account")
        print("  page's Quit button, or Ctrl+C here, to end the preview.")
    if show_hud:
        hud = build_hud()
        hud.show()
        print(f"  chat panel       : {hud.width()}x{hud.height()}  "
              f"capture-excluded={getattr(hud, 'capture_exclusion_active', None)}")

    overlay = None
    if show_cursor:
        overlay = build_overlay()
        print(f"  pointer          : following the mouse on "
              f"{len(overlay.overlays)} screen(s)")
        print("  It flies to a new spot every 4s so the flight, trail and border are visible.")
        print("  The pointer is click-through -- you can keep working while it is up.")

    if window is None and hud is not None and overlay is None:
        # With no window, closing the panel should end the preview rather than orphan it.
        app.setQuitOnLastWindowClosed(True)
    if overlay is not None and window is None and hud is None:
        print("  Ctrl+C in this terminal to stop.")

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
