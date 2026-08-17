"""Home: is it on, and what is it using? (SHELL_AND_CHAT.md §3 `S-2`, `S-3`)

Those two questions are the ones a tray-only app cannot answer without opening a menu, so the
power card is deliberately dominant and everything else on the page is secondary. The layout
mirrors the reference screenshot's hierarchy -- one dominant control, a row of supporting
cards, then a dense table -- because that hierarchy is where its professionalism comes from,
not the decoration.

## The power control holds no state (`S-3`)

``hotkey.enabled`` already exists, the tray already toggles it, and this page must not become a
third opinion. So:

* the toggle emits ``sig_set_listening(on)`` and **nothing else** -- it does not write state;
* the displayed state is always re-read from the injected ``listening_provider``, which the
  integration wires to ``hotkey.enabled``;
* after emitting, the view is immediately re-synced from that provider, so if the app declines
  or fails to make the change, the toggle snaps back instead of lying.

Verified while building this: ``PushToTalkHotkey.set_enabled`` gates callbacks *without*
touching ``self._listener`` -- the hook stays installed and ``listener.stop()` is never called
-- so the toggle is instant and needs no restart, unlike the settings marked ``↻``.

## Numbers this page will not invent

"Questions this week" and "Screenshots skipped this week" are injected. With nothing injected
they render as ``--`` rather than ``0``: a zero is a claim, and claiming the Privacy Guard has
suppressed nothing when in truth nobody counted would be worse than admitting it is not
wired up yet. The skipped-screenshot number is the single most trust-building item on the page
precisely because it is an observation rather than a promise.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

import theme
from shell.widgets import Card, PowerSwitch, label, style_table, table_item

RECENT_ROWS = 5
"""§3's "last-5 interactions". Five is what fits without the table needing its own scroll."""

NO_NUMBER = "\u2014"
"""An em dash for "nobody measured this", as distinct from a measured zero."""


def relative_time(when: datetime | str | None, now: datetime | None = None) -> str:
    """Human "2m ago" text for a timestamp. Pure, so it is unit-testable with no widgets.

    Accepts a string unchanged: the integration may already have a formatted value, and
    reformatting someone else's display string is how "2m ago ago" happens.
    """
    if when is None:
        return NO_NUMBER
    if isinstance(when, str):
        return when
    now = now or datetime.now()
    seconds = max(0, int((now - when).total_seconds()))
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def display_hotkey(chord: str) -> str:
    """``ctrl+alt+space`` -> ``Ctrl+Alt+Space``, matching ``tray.py``'s presentation."""
    return "+".join(part.capitalize() for part in (chord or "").split("+") if part)


class HomePage(QWidget):
    """The landing page. Every data source is optional and injected."""

    sig_set_listening = pyqtSignal(bool)
    sig_export_history = pyqtSignal()
    sig_open_memory_folder = pyqtSignal()
    """The two capabilities the tray gave up in `S-5`.

    Both are about the record of past interactions, which is what this page already shows, so
    they sit under Recent rather than becoming a fifth nav item. Emitting rather than acting
    keeps the page free of a ``MemoryStore``: the export reads memory *and* the live in-memory
    history, and only ``NimbusApp`` has both."""

    def __init__(
        self,
        *,
        listening_provider: Callable[[], bool] | None = None,
        hotkey_provider: Callable[[], str] | None = None,
        usage_provider: Callable[[], int] | None = None,
        privacy_provider: Callable[[], int] | None = None,
        recent_provider: Callable[[], Sequence[Mapping[str, object]]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._listening_provider = listening_provider
        self._hotkey_provider = hotkey_provider or _configured_hotkey
        self._usage_provider = usage_provider
        self._privacy_provider = privacy_provider
        self._recent_provider = recent_provider

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(theme.SPACE[3])
        outer.addWidget(label("Home", "PageTitle"))

        cards = QGridLayout()
        cards.setSpacing(theme.SPACE[3])
        cards.addWidget(self._build_power_card(), 0, 0)
        cards.addWidget(self._build_provider_card(), 0, 1)
        cards.addWidget(self._build_week_card(), 0, 2)
        # The power card is the answer to the page's first question, so it gets the space.
        cards.setColumnStretch(0, 2)
        cards.setColumnStretch(1, 1)
        cards.setColumnStretch(2, 1)
        outer.addLayout(cards)

        self._outer = outer
        self._recent_card = self._build_recent_card()
        outer.addWidget(self._recent_card, stretch=1)
        outer.addWidget(self._build_privacy_card())

        # Spare height lives below the cards, not inside one. See ``knowledge.py`` -- the first fix
        # put a filler in the card and moved the gap rather than removing it.
        self._tail = QWidget()
        outer.addWidget(self._tail, stretch=1)

        self.refresh()

    # -- public ---------------------------------------------------------------

    @property
    def is_listening(self) -> bool:
        """The live answer, re-read from the injected provider every time.

        A property rather than a field on purpose: a cached boolean here is exactly the
        duplicated state `S-3` forbids, and it is what lets the window, the tray item and the
        tray icon drift apart.
        """
        if self._listening_provider is None:
            return bool(self.toggle.isChecked())
        try:
            return bool(self._listening_provider())
        except Exception:
            return False

    def set_listening(self, on: bool) -> None:
        """Push externally-owned state into the view.

        ``on`` is honoured only when there is no provider to ask. With one wired up the
        provider wins, so a caller cannot make this page display something the source of truth
        disagrees with.
        """
        if self._listening_provider is None:
            self.toggle.set_on(bool(on))
        self.refresh_power()

    def refresh_power(self) -> None:
        """Re-sync the switch and its captions from the source of truth."""
        on = self.is_listening
        self.toggle.set_on(on)
        chord = display_hotkey(self._safe(self._hotkey_provider, ""))

        self.power_state.setText("Nimbus is listening" if on else "Nimbus is paused")
        self.power_state.setStyleSheet(
            f"color: {theme.TEXT_PRIMARY if on else theme.TEXT_MUTED};")
        # The hint says what to *do*, which is what a status line is for. "PAUSED" on its own
        # tells the user where they are and nothing about how to leave.
        self.power_hint.setText(
            f"Hold {chord} and ask about anything on your screen." if on
            else "The hotkey is ignored while paused. Nothing is listening.")
        self.hotkey_label.setText(chord)

    def set_provider(self, provider: str, model: str) -> None:
        """Name the provider and model in use. Called by ``MainWindow.set_provider``."""
        self.provider_name.setText(provider or NO_NUMBER)
        self.provider_model.setText(model or "")

    def refresh(self) -> None:
        """Re-read every injected source. Cheap, and safe to call on every page change."""
        self.refresh_power()
        usage = self._safe(self._usage_provider, None)
        self.week_count.setText(NO_NUMBER if usage is None else str(usage))
        skipped = self._safe(self._privacy_provider, None)
        self.privacy_count.setText(
            "Screenshots skipped this week: " + (NO_NUMBER if skipped is None else str(skipped)))
        self._fill_recent(self._safe(self._recent_provider, None) or ())

    # -- construction ---------------------------------------------------------

    def _build_power_card(self) -> Card:
        """The page's dominant element, and it has to look like it.

        Previously a 20pt ``PAUSED`` label with a 40x22 switch under it, which reviewers did not
        register as a control -- *"I don't see the turn on Nimbus button"*. Now the switch is
        full-card-width, labelled on the track, and the state is also stated in words above it.
        Deliberate redundancy: this is the one thing on the page nobody should have to look for.
        """
        card = Card("Push-to-talk")

        self.power_state = label("PAUSED", "Hero")
        card.add(self.power_state)

        self.power_hint = label("", "Muted")
        card.add(self.power_hint)

        self.toggle = PowerSwitch()
        self.toggle.setToolTip(
            "Turn Nimbus's push-to-talk listening on or off.\n\n"
            "Takes effect immediately -- the keyboard listener stays installed either way, so\n"
            "there is nothing to restart. The tray icon's Pause item is the same switch."
        )
        self.toggle.toggled.connect(self._on_power_toggled)
        card.add(self.toggle)

        self.hotkey_label = label("", "Mono")
        card.add(self.hotkey_label)

        # The chat-panel switch used to sit here, as a bare `QCheckBox` under the hotkey line. It
        # has moved to the nav rail, above the Privacy Guard chip: inside this card it read as part
        # of push-to-talk, which it is not, and a system checkbox was the one control in the
        # interface that looked like it came from a different decade. See `shell/nav.py`.

        card.body.addStretch(1)
        return card

    def _build_provider_card(self) -> Card:
        card = Card("Provider")
        self.provider_name = label(NO_NUMBER, "Title")
        self.provider_model = label("", "Muted")
        card.add(self.provider_name)
        card.add(self.provider_model)
        card.body.addStretch(1)
        return card

    def _build_week_card(self) -> Card:
        card = Card("This week")
        self.week_count = label(NO_NUMBER, "Display")
        card.add(self.week_count)
        card.add(label("questions", "Muted"))
        card.body.addStretch(1)
        return card

    def _build_recent_card(self) -> Card:
        card = Card("Recent")
        self.recent_empty = label(
            "Nothing yet. Hold your hotkey and ask about anything on your screen.", "Muted")
        card.add(self.recent_empty)

        self.recent = QTableWidget(0, 4)
        self.recent.setHorizontalHeaderLabels(
            ["Question", "Application", "When", "Pointed at"])
        # Sentence case, not lower case. The old lower-case headers ("question", "app") read as
        # a debug dump; "Application" rather than "app" because the column holds an executable
        # name and there is room for the word.
        style_table(self.recent, stretch_column=0)
        card.add(self.recent, stretch=1)

        actions = QWidget()
        row = QHBoxLayout(actions)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.SPACE[1])
        self.export_button = QPushButton("Export session history\u2026")
        self.export_button.setToolTip(
            "Saves this conversation and what Nimbus remembers about the current app as a\n"
            "Markdown file in your Documents folder. Plain text you can read, edit or delete\n"
            "in any editor."
        )
        self.export_button.clicked.connect(self.sig_export_history.emit)
        row.addWidget(self.export_button)

        self.memory_button = QPushButton("Open memory folder\u2026")
        self.memory_button.setToolTip(
            "What Nimbus remembers per application, as ordinary Markdown files. Editing or\n"
            "deleting them is supported -- that is the point of keeping it in plain text."
        )
        self.memory_button.clicked.connect(self.sig_open_memory_folder.emit)
        row.addWidget(self.memory_button)
        row.addStretch(1)
        self.status = label("", "Muted")
        row.addWidget(self.status)
        card.add(actions)
        return card

    def _build_privacy_card(self) -> Card:
        card = Card("Privacy")
        self.privacy_count = label("", "Secondary")
        card.add(self.privacy_count)
        card.add(label(
            "Your API keys never leave this machine. Screen contents go only to the model "
            "provider you chose, and not at all on a local one.", "Muted"))
        return card

    # -- internals ------------------------------------------------------------

    def _on_power_toggled(self, on: bool) -> None:
        """User flipped the switch: ask the app to change the real state, then re-read it."""
        self.sig_set_listening.emit(bool(on))
        self.refresh_power()

    def _fill_recent(self, rows: Sequence[Mapping[str, object]]) -> None:
        rows = list(rows)[:RECENT_ROWS]
        self.recent.setRowCount(len(rows))
        for index, entry in enumerate(rows):
            question = str(entry.get("question", "") or "")
            # Sentence-cased for display only. Speech-to-text returns lower-case starts often
            # enough that a column of them looks like a log rather than a list of questions.
            if question:
                question = question[0].upper() + question[1:]
            cells = (
                table_item(question),
                table_item(str(entry.get("app", "") or ""), mono=True),
                table_item(relative_time(entry.get("when")), muted=True),  # type: ignore[arg-type]
                table_item(str(entry.get("target", "") or NO_NUMBER), muted=True),
            )
            for column, cell in enumerate(cells):
                cell.setToolTip(cell.text())
                self.recent.setItem(index, column, cell)
        self.recent.setVisible(bool(rows))
        self.recent_empty.setVisible(not rows)
        self._outer.setStretchFactor(self._recent_card, 1 if rows else 0)
        self._tail.setVisible(not rows)

    @staticmethod
    def _safe(provider, fallback):
        """Call an injected provider, tolerating failure.

        Home is a status page. A provider raising must degrade to "unknown", never take the
        window down -- the same reasoning as the chat HUD's Invariant 10.
        """
        if provider is None:
            return fallback
        try:
            return provider()
        except Exception:
            return fallback


def _configured_hotkey() -> str:
    """The real configured chord, read the same way ``config`` resolves it.

    Read live rather than captured at import so the page shows what the user actually set,
    including after a Settings save in the same session.
    """
    try:
        from config import resolve_setting
        return resolve_setting("HOTKEY", "ctrl+alt+space")
    except Exception:
        return "ctrl+alt+space"
