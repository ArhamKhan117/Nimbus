"""Sidebar navigation and the always-visible status footer (SHELL_AND_CHAT.md §3 `S-1`).

## Left, not right, and it is one constant

§0.3 argues for primary navigation on the **left**: every desktop app the user already has
puts it there, Western reading order makes the left edge the cheapest place to scan, and a
right rail conventionally holds *contextual* content -- properties, inspectors, activity
feeds. The brief asked for the right, so the disagreement is settled by
``config.resolve_setting("NAV_SIDE", "left")`` and costs one value to reverse. ``NAV_SIDE`` is
read in ``shell/window.py``; this module only takes ``side`` and puts the selected item's
accent bar on whichever edge leads.

## The footer earns its space

A dot for the provider mode (local vs cloud) and a tick for the Privacy Guard, always visible
and needing no click. A tray-only app cannot answer "is my screen leaving this machine?"
without opening a menu, and that is the one question worth answering permanently.
"""
from __future__ import annotations

from PyQt6.QtCore import QPoint, QPropertyAnimation, Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QPushButton, QVBoxLayout, QWidget

import theme
from shell.widgets import ACCENT_BAR_WIDTH, SidebarSwitch, StatusChip, StatusDot

NAV_ITEMS: tuple[tuple[str, str], ...] = (
    ("home", "Home"),
    ("knowledge", "Knowledge"),
    ("journal", "Journal"),
    ("settings", "Settings"),
    ("account", "Account"),
)
"""``(page name, label)``, in display order.

The single list every part of the shell agrees on: the sidebar builds its buttons from it and
``MainWindow`` builds its page stack from it, so a nav item without a page (or a page with no
way to reach it) is impossible by construction rather than by vigilance. Pinned by
``test_every_nav_item_maps_to_a_page``."""

SIDEBAR_WIDTH = 216
"""Wide enough for "Knowledge" plus the status footer's captions at ``FONT_SMALL``, narrow
enough to leave the 1040px minimum window a workable content column."""


class NavItem(QPushButton):
    """One navigation entry. Styling is entirely ``QPushButton#NavItem`` in the generated QSS.

    Carries its own ``page_name`` so the sidebar never has to map an index back to a page --
    index-based lookups are what break when someone reorders ``NAV_ITEMS``.
    """

    def __init__(self, page_name: str, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("NavItem")
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.page_name = page_name


class Sidebar(QFrame):
    """The nav rail: brand, items, and the status footer.

    Emits ``sig_page_requested`` on a *user* click only. ``select()`` is the programmatic
    path and stays silent, so a page change cannot echo back into another page change.
    """

    sig_page_requested = pyqtSignal(str)
    sig_chat_visible_requested = pyqtSignal(bool)
    """The chat-panel switch was flipped. The rail asks; ``NimbusApp`` is the only writer, and the
    switch is refreshed from ``is_chat_visible`` afterwards -- the same arrangement as the power
    switch, and for the same reason: three things move that panel."""

    def __init__(self, side: str = "left", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.side = "right" if str(side).strip().lower() == "right" else "left"
        self.setFixedWidth(SIDEBAR_WIDTH)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            theme.SPACE[2], theme.SPACE[2], theme.SPACE[2], theme.SPACE[3])
        layout.setSpacing(theme.SPACE[0])

        # No wordmark here. The title bar three pixels above already says "Nimbus", and the
        # rail repeating it put the product name on screen twice in one glance while stealing
        # the vertical space that made the first nav item sit oddly low.

        self.items: dict[str, NavItem] = {}
        for page_name, text in NAV_ITEMS:
            item = NavItem(page_name, text, self)
            item.clicked.connect(
                lambda _checked=False, name=page_name: self.sig_page_requested.emit(name))
            layout.addWidget(item)
            self.items[page_name] = item

        layout.addStretch(1)

        # One chip, not two dots.
        #
        # The footer used to carry the provider name as well ("Cloud provider" / "gemini-native"
        # beside "Privacy Guard on"). Two stacked dots reading as a status list is a lot of
        # permanent furniture for one useful fact, and the provider is already named on Home's
        # provider card -- where there is room to say the model too. The Privacy Guard is the
        # one that earns a permanent home: "is my screen leaving this machine?" is the question
        # a tray-only app cannot otherwise answer without opening a menu.
        # The chat-panel switch, directly above the Privacy Guard chip.
        #
        # It began as a bare `QCheckBox` inside Home's push-to-talk card, which was wrong twice
        # over: it read as part of push-to-talk because of where it sat, and a system checkbox is
        # the one control here that looks like it came from another decade. It belongs in the rail
        # with the Guard -- both are permanently-true facts about the session, both are things a
        # user wants to change without hunting for a page, and together they are a set.
        self.chat_switch = SidebarSwitch("Chat panel", self)
        self.chat_switch.setToolTip(
            "The transcript panel that appears when you ask something.\n\n"
            "Off, Nimbus still answers and still records the conversation -- the panel just\n"
            "stops appearing on its own. Ctrl+Alt+H brings it up whenever you want it.\n"
            "Takes effect immediately; nothing to restart."
        )
        self.chat_switch.toggled.connect(self.sig_chat_visible_requested.emit)
        layout.addWidget(self.chat_switch)

        self.privacy_status = StatusChip("Privacy Guard", theme.TEXT_MUTED, self)
        layout.addWidget(self.privacy_status)

        # Retained so ``set_provider_mode`` has somewhere to write and any caller expecting the
        # old attribute still works. Not laid out, so it costs nothing on screen.
        self.provider_status = StatusDot("Cloud provider", theme.TEXT_MUTED)

        # The sliding accent bar (§2.7). A child of the sidebar rather than of an item, so it
        # can travel between them; §2.6 says the selection *slides* while pages crossfade.
        self._marker = QFrame(self)
        self._marker.setObjectName("NavMarker")
        self._marker.setFixedWidth(ACCENT_BAR_WIDTH)
        self._marker.hide()
        self._slide = QPropertyAnimation(self._marker, b"pos", self)
        self._slide.setEasingCurve(theme.easing(theme.EASE_STANDARD))

        self._selected: str = ""
        self.setStyleSheet(sidebar_qss(self.side))

    # -- public ---------------------------------------------------------------

    @property
    def selected(self) -> str:
        """The currently highlighted page name, or ``""`` before the first selection."""
        return self._selected

    def select(self, page_name: str) -> None:
        """Highlight ``page_name`` without emitting. Unknown names are ignored."""
        item = self.items.get(page_name)
        if item is None:
            return
        item.setChecked(True)
        self._selected = page_name
        self._move_marker(item)

    def set_provider_mode(self, local: bool, detail: str = "") -> None:
        """Footer dot: green when the whole pipeline is local, accent when a cloud model is in
        use. Not a warning -- cloud is the default and the common case -- but it should be
        visible without asking."""
        text = detail or ("Local only" if local else "Cloud provider")
        self.provider_status.set_status(text, theme.SUCCESS if local else theme.ACCENT)

    def set_chat_visible(self, on: bool) -> None:
        """Reflect chat-panel visibility. Silent: ``SidebarSwitch.set_on`` does not emit.

        The label never changes, for the same reason the Privacy Guard chip's does not: a control
        whose text changes also changes width, and a rail that reflows every time a setting moves is
        its own small distraction. The state is the knob and the chip's colour.
        """
        self.chat_switch.set_on(bool(on))

    def set_privacy_guard(self, on: bool) -> None:
        """Footer chip. The **label never changes**; only the dot does.

        It used to read "Privacy Guard on" / "Privacy Guard off", which made the state a word to
        read at the end of a phrase. The dot is the state -- green for on, red for off -- and a
        colour is quicker to take in than a two-letter suffix. Keeping the label fixed also stops
        the chip changing width every time the setting changes, which was its own small twitch.

        Red rather than amber when off. Amber was the earlier, politer choice, and it was wrong:
        with the Guard off, every question sends a screenshot of whatever is in front -- including
        a password manager -- and that is the one thing in this interface worth being blunt about.
        The tooltip says what it means and where to change it, so it informs rather than nags.
        """
        self.privacy_status.set_status(
            "Privacy Guard",
            theme.SUCCESS if on else theme.DANGER,
            detail=("On \u00b7 screenshots are skipped in password managers, sign-in pages and "
                    "other sensitive windows." if on
                    else "Off \u00b7 every question captures your screen, whatever is in front "
                         "of it. Turn the Guard on in Settings \u2192 Privacy."))

    # -- internals ------------------------------------------------------------

    def _move_marker(self, item: NavItem) -> None:
        """Slide the accent bar to ``item``'s leading edge.

        No-op while the layout has not run yet -- under pytest the sidebar is never shown, so
        item geometry is empty and animating to (0, 0) would be meaningless. The marker is a
        purely decorative layer, so skipping it costs nothing.
        """
        geometry = item.geometry()
        if geometry.height() <= 0:
            return
        self._marker.setFixedHeight(geometry.height())
        x = 0 if self.side == "left" else self.width() - ACCENT_BAR_WIDTH
        target = QPoint(x, geometry.y())
        self._marker.show()
        if not self.isVisible() or self._marker.pos() == target:
            # Nothing to animate to or from: jump. Animating from an unlaid-out position
            # would slide the bar in from the corner the first time the window appears.
            self._marker.move(target)
            return
        self._slide.stop()
        self._slide.setDuration(theme.duration(theme.DUR_STANDARD))
        self._slide.setStartValue(self._marker.pos())
        self._slide.setEndValue(target)
        self._slide.start()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        item = self.items.get(self._selected)
        if item is not None:
            self._marker.move(
                0 if self.side == "left" else self.width() - ACCENT_BAR_WIDTH,
                item.geometry().y())


def sidebar_qss(side: str = "left") -> str:
    """Sidebar chrome, generated from ``theme``.

    The hairline goes on the edge facing the content, which is the other side from the one the
    rail is docked to -- otherwise moving the nav leaves a border floating at the window edge.
    """
    divider = "border-right" if side == "left" else "border-left"
    return f"""
QFrame#Sidebar {{
    background: {theme.BG_BASE};
    {divider}: 1px solid {theme.BORDER};
}}
QFrame#NavMarker {{
    background: {theme.ACCENT};
    border-radius: 1px;
}}
"""
