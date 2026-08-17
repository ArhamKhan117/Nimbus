"""Nimbus application shell: the main window (SHELL_AND_CHAT.md §3 `S-1`..`S-5`).

A frameless window with a custom title bar, a nav rail and a page stack. It is a **view**: the
push-to-talk pipeline in ``app.py`` is unchanged, and nothing here is on its path. §0.2's rule
holds -- if a shell change needed ``_pipeline_worker`` touched, the design would be wrong.

## Constructible with no ``NimbusApp``, and that is load-bearing

Every data source is an injected callable and every outbound action is a signal, exactly as
``stt.py``, ``realtime.py`` and ``gemini_live.py`` already do. There is no ``import app``
anywhere in ``shell/``. That is what makes the window testable without starting the whole
application, and it is also the seam that keeps the pipeline from acquiring a UI dependency.

## What was measured before writing this, and what it changed

**Frameless windows lose the resize border, and the usual fix does not apply as written.**
§3 offers ``WM_NCHITTEST`` via ``nativeEvent`` or a resize grip. Measured here: a Qt window with
``FramelessWindowHint`` has ``GWL_STYLE = 0x96000000`` against ``0x96CF0000`` for an ordinary
one -- **``WS_THICKFRAME`` is gone**, and Windows only runs its sizing loop for a window that has
it. Returning ``HTBOTTOMRIGHT`` from ``WM_NCHITTEST`` would therefore do nothing until
``WS_THICKFRAME`` is restored *and* ``WM_NCCALCSIZE`` handled to hide the frame that comes back
with it -- and that route also has to convert ``lParam``'s physical pixels per monitor, the
exact per-monitor-DPI assumption §3's ⚠ VERIFY #2 warns about.

So the window hands both gestures to the OS: ``QWindow.startSystemResize(edge)`` from a
``theme.SPACE[0]``-wide border, and ``startSystemMove()`` from the title bar. Snap comes back,
and **no code in the shell converts a coordinate or caches a device-pixel ratio** -- there is
nothing to get wrong when the window is dragged between monitors at different scaling. A
``QSizeGrip`` sits in the corner as the visible affordance and the fallback when there is no
native handle (under pytest, for instance). Full reasoning in ``shell/titlebar.py``.

**Other verifications, with their results:**

* ``PushToTalkHotkey.set_enabled`` gates callbacks without touching ``self._listener``: the hook
  stays installed, ``listener.stop()`` is never called, and toggling is instant. The power
  control therefore needs no restart, unlike the settings marked ``↻``.
* The grain overlay swallows every click without ``WA_TransparentForMouseEvents``. Measured with
  ``QWidget.childAt`` at a button's centre: without the flag the hit resolves to the grain
  widget, with it to the ``QPushButton``.
* A ``0ms`` ``QPropertyAnimation`` still emits ``finished``, so the reduced-motion path still
  runs the cleanup that hangs off it (here: disabling the page-fade effect).
* This process is already ``PER_MONITOR_AWARE`` (``GetProcessDpiAwareness`` -> 2) before the
  window is built, and ``QApplication`` exists first because ``app.py`` builds it early.

## INTEGRATION REQUIRED

Neither ``app.py``, ``config.py`` nor ``nimbus.spec`` is touched by this workstream (§9.1). The
integration pass needs the following. Everything below already works with defaults, so nothing
here blocks the window from running -- it just runs with empty numbers until wired.

**1. ``config.py`` settings.** All three are read here with ``resolve_setting(name, default)``
today, so they work before being declared; declaring them adds the Settings UI and, per §10.1,
the ``↻`` marker plus an entry in ``RESTART_REQUIRED_SETTINGS``:

    SHELL_ON_STARTUP  "on"    window opens at launch, or Nimbus starts to tray
    NAV_SIDE          "left"  left | right (§0.3 -- one value to reverse)
    REDUCE_MOTION     "auto"  auto follows Windows; on/off override (read by theme.py)

``NAV_SIDE`` and ``REDUCE_MOTION`` are read at construction, so they are restart-gated in
practice. ``SHELL_ON_STARTUP`` is read by ``should_open_on_startup()`` below.

**2. Construction**, after ``QApplication`` exists and never from ``_pipeline_worker``:

    QApplication.instance().setStyleSheet(theme.build_qss())   # menus and dialogs too
    self._window = MainWindow(
        listening_provider=lambda: self._hotkey is not None and self._hotkey.enabled,
        hotkey_provider=lambda: config.HOTKEY,
        usage_provider=...,            # questions this week -- see 5
        privacy_provider=...,          # screenshots skipped this week -- see 5
        recent_provider=...,           # last 5 interactions -- see 5
        review_queue_provider=lambda: self._review_queue,
    )
    self._window.set_provider(config.LLM_PROVIDER, <model in use>)
    self._window.set_local_mode(<fully local?>)
    self._window.set_privacy_guard(config.PRIVACY_GUARD == "on")
    if should_open_on_startup():
        self._window.show()

**3. Inbound.** ``MainWindow`` never polls. Call these when the app's state changes:

    set_listening(on)                 -> after hotkey.set_enabled, from sig_listening_changed
    set_provider(provider, model)     -> after a provider switch
    set_local_mode(local)             -> sidebar footer dot
    set_privacy_guard(on)             -> sidebar footer tick
    show_page(name)                   -> "home"|"knowledge"|"journal"|"settings"|"account"

**4. Outbound signals.** All fire on the Qt main thread:

    sig_set_listening(bool)  -> hotkey.set_enabled(on); tts.stop() when pausing, mirroring
                                app._set_ptt_paused. This is `S-3`'s single write path: the
                                window never sets the state itself, so the window, the tray
                                item and the tray icon cannot disagree.
    sig_quit()               -> the same shutdown the tray's Quit uses. One shutdown path.
    sig_quiz_me()            -> the existing "quiz me" review intent, no transcript needed
    sig_local_data_cleared() -> what app.py already does for `dlg._local_data_cleared`:
                                nimbus.stop() and close for a clean restart
    sig_deactivate_device(), sig_sign_out()  -> licensing.py (§5), phase 4. Until then the
                                buttons are disabled because nothing is activated.
    sig_hidden_to_tray()     -> show the tray balloon **once** so a user who closed the window
                                is not left wondering where Nimbus went. Closing hides
                                (Invariant 5); it must not stop push-to-talk.
    sig_export_history()     -> sig_export_session_history.emit(), the existing slot
    sig_open_memory_folder() -> open config.MEMORY_DIR in Explorer
                                Both inherited from the tray when `S-5` trimmed its menu.

**5. The three numbers this window will not invent.** With no provider they render as an em
dash, not ``0`` -- a measured zero and an unmeasured one are different claims, and the Privacy
Guard's count is the most trust-building item on Home precisely because it is an observation:

    usage_provider    -> questions asked in the last 7 days. `memory.py`'s `apps` table has
                         interaction_count but no per-turn timestamps, so this needs either a
                         counter in NimbusApp or `review_queue.recap(since=...)` as a proxy.
    privacy_provider  -> screenshots suppressed in the last 7 days. `_capture_screens_guarded`
                         already logs every suppression; it needs to also count them somewhere
                         durable. Until then this is honestly blank.
    recent_provider   -> up to 5 mappings with keys: question, app, when (datetime or a
                         preformatted string), target. `_history` holds the text but no app
                         name or timestamp, so build these where the turn completes.

**6. Tray relationship (`S-5`).** The tray stays -- it is the only surface available when the
window is closed, and Pause is the one action whose whole value is being one click away. Left
click on the tray icon should show/raise the window (``activateWindow`` + ``raise_``), and the
menu can shed Settings, Open Knowledge Folder, Open Memory Folder and Export Session History,
all of which now have a better home in the window. ``tray.py`` needs no change from this
workstream; trimming it is an integration decision. The tray's checkable Pause action and this
window's toggle must both read ``hotkey.enabled`` and write it only through their own
callbacks -- ``NimbusTray._pause_action.setChecked(not hotkey.enabled)`` on change, with no
second boolean anywhere.

**7. ``nimbus.spec``.** ``shell``, ``shell.window``, ``shell.nav``, ``shell.titlebar``,
``shell.widgets``, ``shell.pages`` and each page module in ``hiddenimports``, plus the same list
in ``--selftest``'s ``runtime_modules``. ``shell/__init__.py`` imports ``MainWindow`` lazily
through ``__getattr__``, which PyInstaller's static graph cannot see, so this is exactly the gap
that caught ``gemini_cache`` and ``gemini_live`` in Tier 1.

**8. The overlay is untouched.** The shell is an ordinary top-level window; it does not
participate in the overlay's click-through styles or its per-monitor windows, and it takes focus
only when the user asks for it. Nothing here changes the "overlays hide before ``mss.grab()``"
guarantee (Invariant 2).
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QScrollArea,
    QSizeGrip,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import brand
import theme
from shell.nav import NAV_ITEMS, Sidebar
from shell.pages.account import AccountPage
from shell.pages.home import HomePage
from shell.pages.journal import JournalPage
from shell.pages.knowledge import KnowledgePage
from shell.pages.settings import SettingsPage
from shell.titlebar import TitleBar, titlebar_qss

MIN_WIDTH = 760
MIN_HEIGHT = 480
"""The floor, lowered from 1040x680 after the pages were made scrollable.

The original numbers were an honest guess at "below this the sidebar plus a content column stops
working", and they were also the *only* thing stopping the window from shrinking. Measured on the
window as built: ``layout().minimumSize()`` was 810x646 while ``setMinimumSize`` said 1040x680,
so the explicit floor was 230px wider and 34px taller than anything the layout actually needed --
the user was being stopped by a constant, not by the content.

What made 646 unavoidable was the Home page asking for 549px of height with no way to give less.
Each page now sits in its own scroll area (see ``__init__``), so a short window scrolls instead of
clipping, and the floor is whatever the chrome itself needs. That is also the answer to "does it
work on every monitor": the failure mode on a small or heavily scaled screen is now a scrollbar
rather than an unreachable control.

Not the whole story on its own -- ``minimum_for_screen`` clamps these against the actual screen,
because a floor larger than the display is the one way a minimum size can still trap a user."""

MIN_SCREEN_FRACTION = 0.9
"""How much of the available screen the minimum size may claim.

A hard-coded floor is a bug on hardware you did not test on. At 250% scaling a 1920x1080 panel
reports 768x432 logical pixels, which is *below* ``MIN_HEIGHT`` -- the window would open unable to
fit on its own screen and unable to shrink."""

OPEN_WIDTH = 1240
OPEN_HEIGHT = 780
SCREEN_FRACTION = 0.88
"""Opening size, clamped so there is always visible desktop around the window. Same approach as
``SettingsDialog._size_to_screen``, deliberately not reinvented."""

RESIZE_MARGIN = theme.SPACE[0]
"""The grabbable border. 4px of window showing around the content, which doubles as a bezel."""

LOCAL_PROVIDER_HINTS = ("ollama", "local", "faster-whisper", "kokoro")
"""Provider ids that mean nothing left the machine. Used only for the sidebar's footer dot;
``set_local_mode`` overrides it when the integration knows the full STT/TTS picture."""

PAGE_LABELS: dict[str, str] = dict(NAV_ITEMS)


def nav_side() -> str:
    """``"left"`` or ``"right"`` from ``NAV_SIDE`` (§0.3).

    Read through ``resolve_setting`` so it works before the setting is declared in
    ``config.py``. Anything other than ``right`` is ``left``: an unrecognised value must not
    produce a third layout.
    """
    try:
        from config import resolve_setting
        value = resolve_setting("NAV_SIDE", "left").strip().lower()
    except Exception:
        return "left"
    return "right" if value == "right" else "left"


class _ResizeGrip(QWidget):
    """An invisible strip of window edge that resizes it, and owns its own cursor.

    Owning the cursor is the point. The window used to set the resize cursor on *itself* from
    ``mouseMoveEvent``, which every child without its own cursor then inherited -- and clearing it
    needed another move event over the window, which never arrives once the pointer is over a card.
    A per-widget cursor is set on enter and restored on leave by Qt, with no state of ours to get
    stuck.

    Transparent to painting, not to the mouse: no background, no content, so the bezel underneath
    shows through unchanged.
    """

    def __init__(self, edges: Qt.Edge, cursor: Qt.CursorShape, parent: QWidget) -> None:
        super().__init__(parent)
        self._edges = edges
        self.setCursor(cursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    @property
    def edges(self) -> Qt.Edge:
        return self._edges

    def mousePressEvent(self, event) -> None:
        """Hand the drag to Windows.

        ``startSystemResize`` rather than tracking the mouse ourselves, so the OS owns the gesture:
        snapping keeps working and nothing here converts a coordinate or caches a device-pixel
        ratio, which is what makes dragging between monitors at different scaling a non-event.
        """
        window = self.window()
        if event.button() == Qt.MouseButton.LeftButton and not window.isMaximized():
            handle = window.windowHandle()
            if handle is not None and handle.startSystemResize(self._edges):
                event.accept()
                return
        super().mousePressEvent(event)


def configured_hotkey() -> str:
    """The push-to-talk chord, resolved the way ``config`` resolves it.

    Used to build the guard in ``_install_hotkey_guard`` when no ``hotkey_provider`` was injected,
    so the window still protects itself when constructed bare (tests, ``tools/preview_ui.py``).
    """
    try:
        from config import resolve_setting
        return resolve_setting("HOTKEY", "ctrl+alt+space")
    except Exception:
        return "ctrl+alt+space"


def should_open_on_startup() -> bool:
    """Whether the window opens at launch, from ``SHELL_ON_STARTUP`` (default ``on``).

    On by default: nothing starts Nimbus at login -- the installer writes no ``Run`` key and no
    Startup shortcut -- so every launch is a person double-clicking a shortcut, and the only useful
    answer to that is to appear. See ``config.SHELL_ON_STARTUP`` for the full reasoning.

    An unreadable config also opens the window. Failing towards *invisible* would turn a keyring
    hiccup into "I clicked Nimbus and nothing happened", which is the complaint this default exists
    to remove.
    """
    try:
        from config import resolve_setting
        return resolve_setting("SHELL_ON_STARTUP", "on").strip().lower() in {
            "1", "on", "true", "yes"}
    except Exception:
        return True


class MainWindow(QWidget):
    """The Nimbus window: title bar, nav rail, page stack.

    Constructible with no arguments. Every provider is optional, every action is a signal, and
    nothing here imports ``app``.
    """

    sig_set_listening = pyqtSignal(bool)
    sig_set_chat_visible = pyqtSignal(bool)
    """Show or hide the chat panel, live. ``NimbusApp.set_chat_visible`` is the only writer, for
    the same reason as ``sig_set_listening``: the switch is a view, not a second opinion."""

    sig_quit = pyqtSignal()
    sig_quiz_me = pyqtSignal()
    sig_local_data_cleared = pyqtSignal()
    sig_deactivate_device = pyqtSignal()
    sig_sign_out = pyqtSignal()
    sig_hidden_to_tray = pyqtSignal()
    sig_export_history = pyqtSignal()
    sig_open_memory_folder = pyqtSignal()
    """Inherited from the tray when `S-5` trimmed its menu. Home raises them; only
    ``NimbusApp`` can service them, because the export needs both the persisted memory and the
    live in-memory history."""

    def __init__(
        self,
        *,
        listening_provider: Callable[[], bool] | None = None,
        hotkey_provider: Callable[[], str] | None = None,
        usage_provider: Callable[[], int] | None = None,
        privacy_provider: Callable[[], int] | None = None,
        recent_provider: Callable[[], Sequence[Mapping[str, object]]] | None = None,
        chat_visible_provider: Callable[[], bool] | None = None,
        kb_dir: Path | str | None = None,
        open_folder: Callable[[Path], bool] | None = None,
        review_queue_provider: Callable[[], object] | None = None,
        licence_provider: Callable[[], object] | None = None,
        settings_form_factory: Callable[[], QWidget] | None = None,
        nav_side_override: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._chat_visible_provider = chat_visible_provider
        self.setObjectName("Root")
        self.setWindowTitle("Nimbus")
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setMouseTracking(True)
        self._snap_enabled = False
        self._hotkey_guard = None
        """Set in ``_install_hotkey_guard`` at the end of construction. Declared here so
        ``set_hotkey_capture_active`` is safe to call at any point during it."""
        # The window itself takes the initial focus. Measured without this: ``focusWidget()`` on
        # activation was the ``PowerSwitch`` -- Qt hands focus to the first widget in the tab chain
        # -- so a freshly opened window had the one control that turns Nimbus off already armed,
        # complete with the platform's focus frame around it. Nothing is armed now until the user
        # presses Tab.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        try:
            # The orange mark, not the old blue ``nimbus_tray.ico``. That file stays as the
            # executable's PE resource (``nimbus.spec``'s ``icon=``), because Windows reads the
            # taskbar and Alt-Tab icon from there and it must be a real multi-resolution .ico.
            self.setWindowIcon(brand.window_icon())
        except Exception:
            pass  # icon missing in a dev install; not critical

        self.nav_side = nav_side_override or nav_side()

        # --- title bar ------------------------------------------------------
        self.titlebar = TitleBar("Nimbus", self)
        self.titlebar.sig_minimise.connect(self.showMinimized)
        self.titlebar.sig_maximise_toggled.connect(self.toggle_maximised)
        self.titlebar.sig_close.connect(self.close)

        # --- pages ----------------------------------------------------------
        self.stack = QStackedWidget(self)
        self.pages: dict[str, QWidget] = {}

        self.home = HomePage(
            listening_provider=listening_provider,
            hotkey_provider=hotkey_provider,
            usage_provider=usage_provider,
            privacy_provider=privacy_provider,
            recent_provider=recent_provider,
        )
        self.home.sig_set_listening.connect(self.sig_set_listening.emit)
        self.home.sig_export_history.connect(self.sig_export_history.emit)
        self.home.sig_open_memory_folder.connect(self.sig_open_memory_folder.emit)

        self.knowledge = KnowledgePage(kb_dir=kb_dir, open_folder=open_folder)
        self.journal = JournalPage(queue_provider=review_queue_provider)
        self.journal.sig_quiz_me.connect(self.sig_quiz_me.emit)
        self.settings = SettingsPage(form_factory=settings_form_factory)
        self.settings.sig_local_data_cleared.connect(self.sig_local_data_cleared.emit)
        self.settings.sig_hotkey_capture_changed.connect(self.set_hotkey_capture_active)
        self.account = AccountPage(licence_provider=licence_provider)
        self.account.sig_quit.connect(self.sig_quit.emit)
        self.account.sig_deactivate_device.connect(self.sig_deactivate_device.emit)
        self.account.sig_sign_out.connect(self.sig_sign_out.emit)

        # --- pages, each in its own scroll area --------------------------------
        #
        # This is what let the minimum size come down. Measured before the change: the window's
        # `layout().minimumSize()` was 810x646, of which the Home page alone accounted for 549px
        # of height -- five cards that had no way to render in less. Below that the only options
        # are clip the content or refuse to shrink, and it refused.
        #
        # Scrolling makes "too small" a recoverable state instead of a hard stop, which matters
        # well beyond one user's preference: a 1920x1080 panel at 250% scaling reports 768x432
        # logical pixels, and a window with a 680px floor cannot fit on it at all.
        #
        # **Settings is the exception.** It brings its own scroll area with Save pinned outside
        # it, for the reason in `settings_dialog.SettingsForm`'s docstring -- the form wants
        # ~742px against ~728 usable on a 1366x768 laptop, and the dialog is modal at first
        # launch, so an unreachable Save means an unusable app. Wrapping it again would nest one
        # scroll region inside another and put Save back below the fold, which is exactly what
        # `test_settings_page_has_exactly_one_scroll_area` exists to prevent.
        self.page_hosts: dict[str, QWidget] = {}
        for name, page in (
            ("home", self.home),
            ("knowledge", self.knowledge),
            ("journal", self.journal),
            ("settings", self.settings),
            ("account", self.account),
        ):
            container = QWidget()
            padding = QVBoxLayout(container)
            padding.setContentsMargins(
                theme.SPACE[5], theme.SPACE[4], theme.SPACE[5], theme.SPACE[4])
            padding.addWidget(page)
            if name == "settings":
                host: QWidget = container
            else:
                host = QScrollArea()
                host.setWidget(container)
                host.setWidgetResizable(True)
                host.setFrameShape(QFrame.Shape.NoFrame)
                host.setHorizontalScrollBarPolicy(
                    Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                # Not a tab stop. Measured after adding these: Tab from the nav rail landed on
                # the QScrollArea, which is a page-sized container with nothing to do -- and with
                # Windows' keyboard cues on it would draw a focus frame around the whole page.
                # The wheel still scrolls it, and tabbing between the page's own controls scrolls
                # them into view, so nothing is lost.
                host.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.stack.addWidget(host)
            self.pages[name] = page
            self.page_hosts[name] = host

        # --- why there is no page crossfade -----------------------------------
        #
        # §2.6 asks for a 160ms crossfade on page change, and this used to implement it with a
        # `QGraphicsOpacityEffect` on the stack. **Removed after seeing it on real hardware.**
        #
        # A `QGraphicsEffect` renders its target into an offscreen buffer, and the shell's pages
        # contain exactly the widgets that go wrong there -- `QScrollArea`s and `QTableWidget`s
        # with transparent viewports. The result was stale pixels from the *previous* page
        # visible inside the new one for the duration of the fade, worst on the Knowledge page
        # where the table occupies most of the card. A transition whose whole job is to feel
        # smooth cannot leave visible tearing.
        #
        # The alternatives were: paint every viewport opaque, which defeats the card gradient
        # showing through; or animate a real overlay widget, which is a lot of machinery for
        # 160ms. Neither is worth it. The selection marker still slides, the power switch still
        # ripples, and buttons still invert their gradient on press, so the interface is not
        # static -- it just does not fade whole pages. Recorded here rather than silently
        # dropped, because "the spec says crossfade" is otherwise a reasonable thing to re-add.
        self.stack.setAutoFillBackground(True)

        # --- nav ------------------------------------------------------------
        self.sidebar = Sidebar(self.nav_side, self)
        self.sidebar.sig_page_requested.connect(self.show_page)
        self.sidebar.sig_chat_visible_requested.connect(self._on_chat_switch)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        if self.nav_side == "right":
            body.addWidget(self.stack, stretch=1)
            body.addWidget(self.sidebar)
        else:
            body.addWidget(self.sidebar)
            body.addWidget(self.stack, stretch=1)
        self.body_layout = body

        root = QVBoxLayout(self)
        root.setContentsMargins(
            RESIZE_MARGIN, RESIZE_MARGIN, RESIZE_MARGIN, RESIZE_MARGIN)
        root.setSpacing(0)
        root.addWidget(self.titlebar)
        # The accent divider under the title bar, as its own 1px widget rather than a border.
        #
        # Identical to the chat panel's hairlines -- same ``accent_rule()`` gradient, same
        # construction -- so the two surfaces divide their chrome the same way. A widget rather
        # than ``border-bottom`` because Qt cannot put a gradient on a single border edge, and
        # ``border-image`` on one side does not render reliably across styles.
        self.titlebar_rule = QFrame(self)
        self.titlebar_rule.setObjectName("AccentRule")
        self.titlebar_rule.setFixedHeight(1)
        root.addWidget(self.titlebar_rule)
        root.addLayout(body, stretch=1)

        # --- overlays -------------------------------------------------------
        from shell.widgets import GrainOverlay

        self.grain = GrainOverlay(self)
        self.grip = QSizeGrip(self)
        self._build_resize_grips()

        # Last, so it catches every button in the window including the pages'. Kills the dotted
        # white focus frame that a mouse click used to leave behind, without taking the nav rail
        # away from the keyboard -- full reasoning in ``shell.widgets.focus_visible_only``.
        theme.focus_visible_only(self)
        self._install_hotkey_guard(hotkey_provider)

        self.setStyleSheet(theme.build_qss() + titlebar_qss() + window_qss())
        self.apply_minimum_size()
        self.resize_to_screen()
        self.show_page("home", animate=False)

    # -- public API (the §9.1 integration surface) ---------------------------

    def show_page(self, name: str, animate: bool = True) -> None:
        """Switch to a page by name. Unknown names are ignored rather than raising.

        Ignoring is deliberate: this is reachable from a signal, and a typo in a tray action
        should not be able to take the window down.
        """
        page = self.pages.get(name)
        host = self.page_hosts.get(name)
        if page is None or host is None:
            return
        self.stack.setCurrentWidget(host)
        self.sidebar.select(name)
        self.titlebar.set_subtitle(PAGE_LABELS.get(name, ""))
        self.refresh_chat()
        refresh = getattr(page, "refresh", None)
        if callable(refresh):
            try:
                refresh()
            except Exception:
                pass  # a page failing to refresh must not block navigation
        # ``animate`` is retained as part of the signature: ``__init__`` passes False for the
        # first page, and a caller that wants a silent switch should keep being able to say so
        # if a transition is ever reintroduced. See the note in ``__init__``.

    def set_listening(self, on: bool) -> None:
        """Reflect push-to-talk state. Delegates to Home, which re-reads the source of truth."""
        self.home.set_listening(bool(on))

    @property
    def is_chat_visible(self) -> bool:
        """The live answer, from the injected provider. No copy is kept here."""
        if self._chat_visible_provider is None:
            return bool(self.sidebar.chat_switch.is_on())
        try:
            return bool(self._chat_visible_provider())
        except Exception:
            return False

    def set_chat_visible(self, on: bool) -> None:
        """Reflect chat-panel visibility in the rail.

        ``on`` is honoured only when there is no provider to ask, exactly as with
        ``set_listening``: with one wired up the source of truth wins, so a caller cannot make the
        rail show something the panel disagrees with.
        """
        if self._chat_visible_provider is None:
            self.sidebar.set_chat_visible(bool(on))
        self.refresh_chat()

    def refresh_chat(self) -> None:
        """Re-sync the rail's switch. Called on every page change and on every ``refresh``.

        Necessary because two other things move the panel without the window knowing: Ctrl+Alt+H,
        and the 45-second auto-hide.
        """
        self.sidebar.set_chat_visible(self.is_chat_visible)

    def _on_chat_switch(self, on: bool) -> None:
        """The rail asked. Pass it on, then re-read -- if the app declines, the switch snaps back."""
        self.sig_set_chat_visible.emit(bool(on))
        self.refresh_chat()

    def set_provider(self, provider: str, model: str) -> None:
        """Name the provider and model in use, and update the sidebar's local/cloud dot."""
        self.home.set_provider(provider, model)
        lowered = (provider or "").lower()
        self.sidebar.set_provider_mode(
            any(hint in lowered for hint in LOCAL_PROVIDER_HINTS),
            detail=provider or "")

    def set_local_mode(self, local: bool, detail: str = "") -> None:
        """Override the footer dot when the caller knows the whole STT/TTS/LLM picture."""
        self.sidebar.set_provider_mode(bool(local), detail=detail)

    def set_privacy_guard(self, on: bool) -> None:
        self.sidebar.set_privacy_guard(bool(on))

    def refresh(self) -> None:
        """Re-read every page's injected sources. Cheap enough for a tray "show window"."""
        self.refresh_chat()
        for page in self.pages.values():
            refresh = getattr(page, "refresh", None)
            if callable(refresh):
                try:
                    refresh()
                except Exception:
                    pass

    @property
    def is_listening(self) -> bool:
        """The live push-to-talk state, straight from Home's provider. No copy is kept here."""
        return self.home.is_listening

    def toggle_maximised(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        self.titlebar.set_maximised(self.isMaximized())

    def minimum_for_screen(self) -> tuple[int, int]:
        """``(width, height)`` for ``setMinimumSize``, clamped to the screen it is on.

        A constant floor is only correct on hardware you tested. ``MIN_SCREEN_FRACTION`` of the
        *available* geometry -- which already excludes the taskbar -- is what makes this safe on a
        small or heavily scaled display, where the logical screen can be smaller than the floor.
        """
        from PyQt6.QtWidgets import QApplication

        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return (MIN_WIDTH, MIN_HEIGHT)
        available = screen.availableGeometry()
        return (
            min(MIN_WIDTH, int(available.width() * MIN_SCREEN_FRACTION)),
            min(MIN_HEIGHT, int(available.height() * MIN_SCREEN_FRACTION)),
        )

    def apply_minimum_size(self) -> None:
        """Set the floor from ``minimum_for_screen``. Re-run when the window changes screen."""
        width, height = self.minimum_for_screen()
        self.setMinimumSize(width, height)

    def resize_to_screen(self) -> None:
        """Open at ``OPEN_WIDTH`` x ``OPEN_HEIGHT``, clamped to 88% of the available screen.

        Reuses ``SettingsDialog._size_to_screen``'s approach rather than reinventing it: ask for
        a natural size, then clamp so there is always visible desktop around the window. The
        clamp is what makes this safe on a 1366x768 laptop.
        """
        from PyQt6.QtWidgets import QApplication

        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            self.resize(OPEN_WIDTH, OPEN_HEIGHT)
            return
        available = screen.availableGeometry()
        self.resize(
            min(OPEN_WIDTH, int(available.width() * SCREEN_FRACTION)),
            min(OPEN_HEIGHT, int(available.height() * SCREEN_FRACTION)),
        )

    # -- window behaviour ---------------------------------------------------

    def closeEvent(self, event) -> None:
        """Hide to tray. **Never quit** (Invariant 5).

        Nimbus is a background tool: closing the window must not stop push-to-talk. Quitting is
        the tray's "Quit Nimbus" and the Account page's Quit button, both of which go through
        ``sig_quit`` to one shutdown path.
        """
        event.ignore()
        self.hide()
        self.sig_hidden_to_tray.emit()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # After the first show, because the styles are applied to a real HWND and `winId()` only
        # returns one once the window has been created.
        self._enable_native_snap()
        self.apply_minimum_size()
        # Claim focus for the window itself. `setFocusPolicy(StrongFocus)` in the constructor is
        # not enough on its own: measured, Qt still handed focus to the first widget in the tab
        # chain on activation, which is the `PowerSwitch` -- so the control that turns Nimbus off
        # was armed, and wearing the platform's focus frame, the instant the window opened.
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self._layout_overlays()
        self.refresh()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        # Dragged to a screen with a different size or scaling, the floor has to be recomputed --
        # otherwise a minimum measured on a 4K panel follows the window onto a 1366x768 laptop.
        self.apply_minimum_size()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_overlays()

    def _layout_overlays(self) -> None:
        """Keep the grain over the **content area only**, and the grip in its corner.

        Not the whole window any more. The grain exists to stop large low-contrast gradients
        banding, and the only gradients left are on the cards -- the title bar and nav rail are
        flat ``CHROME_FLAT`` now, so there is nothing there for it to fix, and a 4% noise tile
        over flat black is visible *as noise*. The chrome was still reading as textured for
        exactly that reason.

        The grain is raised above the content on purpose -- it is a texture over everything in
        its region -- which is why it carries ``WA_TransparentForMouseEvents``. Verified: without
        that flag it swallows clicks on the controls underneath.
        """
        self.grain.setGeometry(self.stack.geometry())
        self.grain.raise_()
        self._layout_resize_grips()
        size = self.grip.sizeHint()
        self.grip.setGeometry(
            self.width() - size.width(), self.height() - size.height(),
            size.width(), size.height())
        # Raised last so the native size grip stays on top of the bottom-right resize grip. Both
        # resize; the size grip is the visible affordance and the fallback where there is no native
        # handle to hand the gesture to.
        self.grip.raise_()

    # -- frameless resize ---------------------------------------------------
    #
    # ``_edge_at`` and ``_EDGE_CURSORS`` used to live here: a hit-test over the whole window, run
    # from ``mouseMoveEvent``, deciding which cursor to set. Both are gone with the mechanism they
    # served -- the grips below carry their own edge and their own cursor, so there is nothing left
    # to hit-test. They were still referenced by one test after the change, which is how dead code
    # survives a refactor; the geometry assertions in ``TestResizeGrips`` cover the same ground
    # against the code that actually runs.

    # -- why the resize gutter is eight widgets and not a mouseMoveEvent ------
    #
    # It used to be `mouseMoveEvent` calling `self.setCursor(...)` on the window, and that is
    # where "my cursor turns into the resize cursor and stays like that" came from.
    #
    # `setCursor` on a parent applies to every child that has not set its own, so the resize
    # cursor was inherited by all the cards and labels. Resetting it needed a move event over the
    # *window*, and a move from the 4px gutter into the content lands on a child -- the window
    # never sees the pointer leave, so it never resets. One brush past an edge and every page had
    # a resize cursor until the pointer happened to cross the gutter again.
    #
    # Eight small children with their own cursors is deterministic instead: Qt sets the cursor on
    # enter and restores it on leave, per widget, with no state of ours involved. It also gives the
    # corners a real 16px target -- the same fix the chat panel needed, where two 5px strips
    # crossing left a 5x5 corner nobody could hit.

    CORNER_SIZE = 16
    """Corner grab size. Deliberately larger than ``RESIZE_MARGIN``, which is the visible bezel."""

    def _build_resize_grips(self) -> None:
        edge = Qt.Edge
        self._grips: list[_ResizeGrip] = [
            _ResizeGrip(edge.LeftEdge | edge.TopEdge, Qt.CursorShape.SizeFDiagCursor, self),
            _ResizeGrip(edge.RightEdge | edge.TopEdge, Qt.CursorShape.SizeBDiagCursor, self),
            _ResizeGrip(edge.LeftEdge | edge.BottomEdge, Qt.CursorShape.SizeBDiagCursor, self),
            _ResizeGrip(edge.RightEdge | edge.BottomEdge, Qt.CursorShape.SizeFDiagCursor, self),
            _ResizeGrip(edge.LeftEdge, Qt.CursorShape.SizeHorCursor, self),
            _ResizeGrip(edge.RightEdge, Qt.CursorShape.SizeHorCursor, self),
            _ResizeGrip(edge.TopEdge, Qt.CursorShape.SizeVerCursor, self),
            _ResizeGrip(edge.BottomEdge, Qt.CursorShape.SizeVerCursor, self),
        ]

    def _layout_resize_grips(self) -> None:
        """Place the eight grips around the frame, and hide them all when maximised."""
        if not getattr(self, "_grips", None):
            return
        margin, corner = RESIZE_MARGIN, self.CORNER_SIZE
        width, height = self.width(), self.height()
        span_x = max(0, width - corner * 2)
        span_y = max(0, height - corner * 2)
        boxes = (
            (0, 0, corner, corner),
            (width - corner, 0, corner, corner),
            (0, height - corner, corner, corner),
            (width - corner, height - corner, corner, corner),
            (0, corner, margin, span_y),
            (width - margin, corner, margin, span_y),
            (corner, 0, span_x, margin),
            (corner, height - margin, span_x, margin),
        )
        maximised = self.isMaximized()
        for grip, box in zip(self._grips, boxes):
            grip.setGeometry(*box)
            grip.setVisible(not maximised)
            if not maximised:
                grip.raise_()

    # -- Aero Snap ----------------------------------------------------------
    #
    # ## Why dragging to a screen edge did nothing
    #
    # Snap is not something an application implements; it is something Windows does *to* a window
    # during its own move loop, and only for a window that says it is sizable. Measured here:
    # `GWL_STYLE` was `0x96000000` -- `WS_POPUP | WS_VISIBLE | WS_CLIPSIBLINGS | WS_CLIPCHILDREN`
    # and nothing else. An ordinary Qt window reads `0x96CF0000`, so `FramelessWindowHint` had
    # stripped `WS_CAPTION`, `WS_SYSMENU`, `WS_THICKFRAME`, `WS_MINIMIZEBOX` and `WS_MAXIMIZEBOX`.
    # No `WS_THICKFRAME` means not sizable, so the OS had nothing to snap; no `WS_MAXIMIZEBOX`
    # means the top edge cannot maximise. Dragging itself worked because `startSystemMove` posts
    # into the OS move loop -- the loop ran, it just had no reason to offer a snap.
    #
    # ## Putting the bits back, and the frame that did not come back with them
    #
    # `WS_THICKFRAME` normally means Windows reserves and paints a non-client sizing border, which
    # is the frame this window exists in order not to have. The textbook answer is to intercept
    # `WM_NCCALCSIZE` and leave the client rectangle equal to the window rectangle.
    #
    # **Measured, and it is not needed here.** With the styles restored and no message handling at
    # all, `GetClientRect` and `GetWindowRect` both report 400x300 on a test window, and maximising
    # lands exactly on `availableGeometry` rather than over the taskbar. Qt's own frameless
    # handling already answers that message. So this is two `SetWindowLong`-family calls and no
    # `nativeEvent` override -- see `tests/test_shell.py::TestAeroSnap`, which pins the measurement
    # so a future Qt that stops doing it is a failing test rather than a returning frame.
    #
    # An override was written first and is worth recording as a dead end: calling
    # `super().nativeEvent(...)` from PyQt6 crashes the process with an access violation on the
    # first message the window receives. If a handler is ever genuinely needed, return `(False, 0)`
    # for the unhandled case rather than delegating.
    #
    # The Win32 call is wrapped and returns a bool: failing here must degrade to "no snap", never
    # to a window that will not open.

    # -- the push-to-talk chord must not press buttons -----------------------

    def _install_hotkey_guard(self, hotkey_provider) -> None:
        """Stop the push-to-talk chord activating whatever control happens to have focus.

        ## The bug this fixes

        Reported as "when I press Ctrl+Alt+Space the push-to-talk listens and then pauses".
        Measured, and the cause is two correct decisions meeting badly:

        * the global hook is deliberately ``suppress=False`` (see ``hotkey.py`` -- pynput's
          suppress flag is all-or-nothing and would block every key on the system), so the chord
          reaches the focused widget as well as Nimbus;
        * ``QAbstractButton::keyPressEvent`` activates on ``Key_Space`` **without looking at
          modifiers**, so a focused button treats Ctrl+Alt+Space as a click.

        Measured consequences, all three real: with the power switch focused the chord emitted
        ``sig_set_listening(False)`` -- pausing Nimbus at the moment the user asked it to
        listen; with "Open memory folder" focused it opened Explorer; with a nav item focused it
        changed page. And ``focusWidget()`` on activation was the ``PowerSwitch``, so this fired
        on the very first question after opening the window.

        ## Why a QShortcut

        Qt's shortcut map runs *before* a key event is delivered to the focus widget, which is the
        only place this can be stopped without an application-wide event filter. The slot does
        nothing on purpose: the global hook already handles the chord, and this window must not
        become a second push-to-talk path.

        Built from the **configured** chord rather than a literal, so a user who remapped
        push-to-talk is protected by the same guard. ``WindowShortcut`` context keeps it to this
        window; the chat panel needs no equivalent because it is ``WindowDoesNotAcceptFocus`` and
        ``NoFocus`` throughout, so it never receives a key event at all.

        The one thing this costs: while the Settings page's hotkey-capture button is armed, this
        window swallows the chord that is already bound, so the user cannot re-record the chord
        they are already using. That is a no-op rebind, and ``set_hotkey_capture_active`` exists
        so the page can lift the guard anyway.
        """
        from PyQt6.QtGui import QKeySequence, QShortcut

        self._hotkey_guard = None
        try:
            from hotkey import parse_hotkey

            chord = ((hotkey_provider() if hotkey_provider is not None else None)
                     or configured_hotkey())
            sequence = QKeySequence(parse_hotkey(str(chord)).display)
        except Exception:
            # An unparseable or missing chord costs the guard, not the window. The default is
            # still worth guarding, so fall back to it rather than giving up.
            try:
                sequence = QKeySequence("Ctrl+Alt+Space")
            except Exception:
                return
        if sequence.isEmpty():
            return
        guard = QShortcut(sequence, self)
        guard.setContext(Qt.ShortcutContext.WindowShortcut)
        guard.setAutoRepeat(False)
        guard.activated.connect(lambda: None)
        self._hotkey_guard = guard

    def set_hotkey_capture_active(self, capturing: bool) -> None:
        """Lift the chord guard while Settings is recording a new hotkey.

        Without this the capture button would appear to ignore the chord the user is currently
        bound to, because the guard consumes it before the button sees it.
        """
        if self._hotkey_guard is not None:
            self._hotkey_guard.setEnabled(not bool(capturing))

    def _enable_native_snap(self) -> None:
        """Restore ``WS_THICKFRAME`` and the box bits so Windows will snap this window."""
        if self._snap_enabled:
            return
        try:
            handle = int(self.winId())
        except Exception:
            return
        if not handle:
            return
        self._snap_enabled = bool(enable_snap_styles(handle))


# --- Win32: the two calls Aero Snap needs -----------------------------------
#
# Module-level rather than methods so they are testable without a window, and so the whole Win32
# surface of this file is two functions in one place. Both are no-ops off Windows.

GWL_STYLE = -16
WS_THICKFRAME = 0x00040000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
SNAP_STYLES = WS_THICKFRAME | WS_MAXIMIZEBOX | WS_MINIMIZEBOX
"""``WS_THICKFRAME`` makes the window sizable, which is what snapping requires;
``WS_MAXIMIZEBOX`` is what lets the top edge maximise; ``WS_MINIMIZEBOX`` is what makes
``Win+Down`` and the taskbar's minimise-on-click work. ``WS_CAPTION`` is deliberately absent --
that one really would bring back a title bar."""

SWP_FLAGS = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020
"""``SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED``. The frame
change is the point: without it Windows does not re-ask ``WM_NCCALCSIZE`` and the new style has no
visible effect until the next resize."""

def enable_snap_styles(hwnd: int) -> bool:
    """Add ``SNAP_STYLES`` to ``hwnd`` and force a frame recalculation. ``False`` if unavailable.

    Returning a bool rather than raising is deliberate: the caller's fallback is "no snap", which
    is the behaviour that shipped, and a window that refuses to open would be a far worse trade.

    ``argtypes`` are declared rather than left to ctypes' defaults. An undeclared ``HWND`` is
    marshalled as a C ``int``, which truncates a 64-bit handle, and the resulting call fails
    silently against a handle that does not exist -- an easy bug to spend an afternoon on.
    ``GetWindowLongW``'s return is also declared unsigned, because the real value here is
    ``0x96000000`` and a signed read makes it negative.
    """
    import sys

    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongW.restype = wintypes.DWORD
        user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.DWORD]
        user32.SetWindowLongW.restype = wintypes.DWORD
        user32.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.SetWindowPos.restype = wintypes.BOOL

        handle = wintypes.HWND(hwnd)
        current = int(user32.GetWindowLongW(handle, GWL_STYLE))
        if not current:
            return False
        if current & SNAP_STYLES == SNAP_STYLES:
            return True
        user32.SetWindowLongW(handle, GWL_STYLE, current | SNAP_STYLES)
        # Read back rather than trusting the return value: ``SetWindowLongW`` returns the
        # *previous* style, so a legitimate call can return 0 and a failed one cannot be told
        # apart without ``GetLastError``.
        applied = int(user32.GetWindowLongW(handle, GWL_STYLE))
        if applied & SNAP_STYLES != SNAP_STYLES:
            return False
        # The frame change is the point: without it Windows does not re-ask ``WM_NCCALCSIZE`` and
        # the new style has no effect until the next resize.
        user32.SetWindowPos(handle, None, 0, 0, 0, 0, SWP_FLAGS)
        return True
    except Exception:
        return False


def window_qss() -> str:
    """Shell chrome specific to the window frame.

    The page stack is given an explicit opaque fill, not ``transparent``. That is what stops the
    grain overlay and any half-painted child leaving remnants on a page change -- the same class
    of artefact that retired the crossfade.
    """
    return f"""
QWidget#Root {{
    background: {theme.BG_BASE};
    border: 1px solid {theme.BORDER_STRONG};
}}
QStackedWidget, QStackedWidget > QWidget {{ background: {theme.BG_BASE}; }}
"""
