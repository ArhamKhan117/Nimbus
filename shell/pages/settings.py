"""Settings: the second host for ``settings_dialog.SettingsForm`` (SHELL_AND_CHAT.md §3 `S-4`).

**No settings are defined here.** The whole point of `S-4` is that there is one settings
implementation with two hosts -- the first-launch modal and this page -- so the provider/model/
key matrix, the OpenRouter key-reuse rule, keyring persistence, hotkey capture, the Privacy
group, the experimental group and the restart labels exist once. A "nicer" shell-native
reimplementation would have quietly dropped several of them, and the 40+ tests protecting them
would have kept passing against the dialog nobody looked at any more.

## One scroll area, not two

``SettingsForm`` deliberately contains no scroll area: whoever hosts it owns that, because
whoever hosts it also owns the Save button that must stay reachable. So this page adds exactly
one ``QScrollArea`` with the action row **outside** it -- the same arrangement, and for the same
reason, as the dialog. Nesting a second scroll region inside the form is the failure mode to
avoid, and ``test_settings_page_has_exactly_one_scroll_area`` pins it.

## The wipe path has to work from here too

§3's ⚠ VERIFY singles this out: ``_local_data_cleared`` must keep working from the shell, and the
shell has to *react* to it rather than merely store it. It does -- the form's
``sig_local_data_cleared`` shows a restart banner, disables the form so nothing can be typed
into settings that are about to be discarded, and re-emits so ``MainWindow`` can pass it to the
integration, which closes Nimbus for a clean restart exactly as ``app.py`` already does for the
dialog.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import theme
from shell.widgets import label


class SettingsPage(QWidget):
    """Hosts the shared settings form with its own Save.

    ``form_factory`` exists so a test can host a stub instead of the real form, which touches
    the keyring on construction. Production always gets the real one.
    """

    sig_local_data_cleared = pyqtSignal()
    sig_saved = pyqtSignal()
    sig_hotkey_capture_changed = pyqtSignal(bool)
    """True while the hotkey-capture button is armed and wants raw key events.

    ``MainWindow`` swallows the configured push-to-talk chord so it cannot press whatever control
    has focus (see ``MainWindow._install_hotkey_guard``). That guard has to come off while the user
    is recording a chord, or the capture button silently ignores the one they are already using."""

    def __init__(self, *, form_factory=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(theme.SPACE[3])
        outer.addWidget(label("Settings", "PageTitle"))

        self.restart_banner = label("", "Secondary")
        self.restart_banner.setObjectName("RestartBanner")
        self.restart_banner.setVisible(False)
        outer.addWidget(self.restart_banner)

        self.form = (form_factory or _real_form)()
        self.form.sig_local_data_cleared.connect(self._on_local_data_cleared)
        self.form.sig_validity_changed.connect(self._set_save_enabled)
        self.form.sig_saved.connect(self.sig_saved.emit)

        # Forwarded rather than reached for. A test may host a stub with no capture button, and a
        # missing one must cost the guard-lifting, not the page.
        capture = getattr(self.form, "_hotkey_capture", None)
        toggled = getattr(capture, "toggled", None)
        if toggled is not None:
            try:
                toggled.connect(self.sig_hotkey_capture_changed.emit)
            except Exception:
                pass

        # Exactly one scroll area, and the actions live outside it.
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.form)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll, stretch=1)

        actions = QWidget()
        row = QHBoxLayout(actions)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.SPACE[1])
        self.status = label("", "Muted")
        row.addWidget(self.status)
        row.addStretch(1)
        self.save_button = QPushButton("Save settings")
        self.save_button.setObjectName("Primary")
        self.save_button.clicked.connect(self.save)
        row.addWidget(self.save_button)
        outer.addWidget(actions)

        self.setStyleSheet(settings_page_qss())
        self._set_save_enabled(self.form.is_valid())

    # -- public ---------------------------------------------------------------

    @property
    def local_data_cleared(self) -> bool:
        """Whether the user has wiped local data in this session, straight off the form."""
        return bool(getattr(self.form, "local_data_cleared", False))

    def save(self) -> bool:
        """Persist through the shared form. ``False`` means nothing was written.

        Both refusal paths are the form's and are unchanged by being hosted here: an invalid
        hotkey shows its error and writes nothing, and the Ollama compatibility warning can be
        cancelled. The status line reflects the outcome rather than assuming success, which the
        dialog could get away with only because it closed on save.
        """
        from settings_dialog import RESTART_MARKER

        saved = bool(self.form.save())
        # The marker is imported, not repeated. It was a hardcoded ``\u21bb`` here, so when the
        # glyph changed this line would have gone on explaining a symbol no label used any more.
        self.status.setText(
            f"Saved. Settings marked{RESTART_MARKER} apply next time Nimbus starts."
            if saved else "Not saved.")
        return saved

    # -- internals ------------------------------------------------------------

    def _set_save_enabled(self, enabled: bool) -> None:
        self.save_button.setEnabled(bool(enabled))

    def _on_local_data_cleared(self) -> None:
        self.restart_banner.setText(
            "Local data cleared. Close Nimbus and reopen it to start with a clean setup.")
        self.restart_banner.setVisible(True)
        # Disabling matters: everything in the form now describes settings that have just been
        # deleted, and letting the user edit and save them would recreate half a config.
        self.form.setEnabled(False)
        self.save_button.setEnabled(False)
        self.sig_local_data_cleared.emit()


def settings_page_qss() -> str:
    return f"""
QLabel#RestartBanner {{
    background: {theme.rgba(theme.WARNING, 0.12)};
    border: 1px solid {theme.rgba(theme.WARNING, 0.45)};
    border-radius: {theme.RADIUS_CONTROL}px;
    padding: {theme.SPACE[1]}px {theme.SPACE[2]}px;
    color: {theme.TEXT_PRIMARY};
}}
"""


def _real_form():
    from settings_dialog import SettingsForm
    return SettingsForm()
