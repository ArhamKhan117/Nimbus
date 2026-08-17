"""Account: licence status, device, and the ways out (SHELL_AND_CHAT.md §3 `S-2`, §5).

## There is no licence check here, deliberately

``licensing.py`` does not exist yet -- §9's phase 4 -- and this page will not pretend otherwise.
The state arrives through an injected ``licence_provider``; with nothing injected the page says,
plainly, that activation is not set up yet.

**What this page must not do is stub a check.** A hand-rolled "if key looks valid" here would
read like enforcement to the next person, get wired to something, and then be trusted -- and
§0.1 is explicit that a local desktop app cannot enforce payment at all, only deter casual
sharing. A placeholder that *looks* like enforcement is worse than none: it invites exactly the
false confidence §0.1 sets out to prevent. When ``licensing.py`` lands it supplies a
``LicenceState`` and nothing here changes shape.

## Quit belongs here

Closing the window hides it (Invariant 5: closing must not stop push-to-talk), so there has to
be somewhere in the window that genuinely quits, or the only exit is the tray. ``sig_quit`` is
that. It goes to the same ``NimbusApp`` shutdown the tray's Quit uses -- one shutdown path,
which is what stops a stray worker thread outliving the app.
"""
from __future__ import annotations

import platform
from collections.abc import Callable
from dataclasses import dataclass

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

import theme
from shell.widgets import Card, label

NOT_ACTIVATED = "Not activated"
UNKNOWN = "\u2014"


@dataclass(frozen=True)
class LicenceState:
    """What the Account page needs to render. Supplied by ``licensing.py`` when it exists.

    A plain frozen dataclass rather than an interface into a module that does not exist yet,
    so the shell's dependency is a shape rather than an import.
    """

    activated: bool = False
    plan: str = ""
    email: str = ""
    device_name: str = ""
    seats_used: int = 0
    seats_total: int = 0
    expires: str = ""
    offline_grace_days_left: int | None = None
    detail: str = ""


def device_name() -> str:
    """This machine's name, for the "which device is this seat?" question.

    ``platform.node()`` and nothing more. A hardware fingerprint would be a custodianship
    liability, and §5 requires any real device id to be salted and hashed -- that belongs in
    ``licensing.py``, not in a page that only has to label a row.
    """
    try:
        return platform.node() or UNKNOWN
    except Exception:
        return UNKNOWN


class AccountPage(QWidget):
    """Licence status and the account-level actions."""

    sig_deactivate_device = pyqtSignal()
    sig_sign_out = pyqtSignal()
    sig_quit = pyqtSignal()

    def __init__(
        self,
        *,
        licence_provider: Callable[[], LicenceState | None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._licence_provider = licence_provider

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(theme.SPACE[3])
        outer.addWidget(label("Account", "PageTitle"))

        outer.addWidget(self._build_licence_card())
        outer.addWidget(self._build_device_card())
        outer.addStretch(1)
        outer.addWidget(self._build_actions_card())
        self.refresh()

    # -- public ---------------------------------------------------------------

    def licence(self) -> LicenceState | None:
        """The injected licence state, or ``None`` when activation is not wired up."""
        if self._licence_provider is None:
            return None
        try:
            return self._licence_provider()
        except Exception:
            # A licence lookup that throws must not take the window with it. It renders as
            # "not activated", which is the safe reading of "we do not know".
            return None

    @property
    def is_activated(self) -> bool:
        state = self.licence()
        return bool(state is not None and state.activated)

    def refresh(self) -> None:
        state = self.licence()
        if state is None:
            self.status.setText(NOT_ACTIVATED)
            self.status.setStyleSheet(f"color: {theme.TEXT_MUTED};")
            self.plan.setText(UNKNOWN)
            self.seats.setText(UNKNOWN)
            self.expires.setText(UNKNOWN)
            self.detail.setText(
                "Activation is not set up on this build. Nimbus runs with your own API keys "
                "and everything on this machine, so nothing here is gating you.")
        else:
            self.status.setText("Active" if state.activated else NOT_ACTIVATED)
            self.status.setStyleSheet(
                f"color: {theme.SUCCESS if state.activated else theme.WARNING};")
            self.plan.setText(state.plan or UNKNOWN)
            self.seats.setText(
                f"{state.seats_used} of {state.seats_total}" if state.seats_total else UNKNOWN)
            # The date *and* the count. A date alone makes the reader do the arithmetic, and the one
            # thing anyone opens this page to find out is how long they have got.
            # `getattr` for both, because `LicenceState.kind` and `days_left` are documented as *not*
            # part of this page's contract -- the page is fed an injected provider and a stub is
            # entitled to supply only the contract fields. A test caught exactly that.
            days = getattr(state, "days_left", 0)
            self.expires_caption.setText(
                "Trial ends" if getattr(state, "kind", "") == "trial" else "Renews")
            self.expires.setText(
                f"{state.expires} \u00b7 {days} day{'s' if days != 1 else ''} left"
                if state.expires and days else (state.expires or UNKNOWN))
            grace = state.offline_grace_days_left
            self.detail.setText(state.detail or (
                f"Offline grace: {grace} day{'s' if grace != 1 else ''} left."
                if grace is not None else ""))

        self.device.setText(device_name())
        self.deactivate_button.setEnabled(self.is_activated)
        self.sign_out_button.setEnabled(self.is_activated)

    # -- construction ---------------------------------------------------------

    def _build_licence_card(self) -> Card:
        card = Card("Licence")
        self.status = label(NOT_ACTIVATED, "Display")
        card.add(self.status)

        grid = QGridLayout()
        grid.setHorizontalSpacing(theme.SPACE[3])
        grid.setVerticalSpacing(theme.SPACE[0])
        self.plan = label(UNKNOWN, "Secondary")
        self.seats = label(UNKNOWN, "Secondary")
        self.expires = label(UNKNOWN, "Secondary")
        # The third caption is kept on the instance because it is not always "Renews": a trial does
        # not renew, it ends, and telling a trial user their trial "renews" on a date is a promise we
        # are not making.
        self.expires_caption = label("Renews", "Muted")
        for row_index, (caption, value) in enumerate((
            (label("Plan", "Muted"), self.plan),
            (label("Seats used", "Muted"), self.seats),
            (self.expires_caption, self.expires),
        )):
            grid.addWidget(caption, row_index, 0)
            grid.addWidget(value, row_index, 1)
        grid.setColumnStretch(1, 1)
        card.body.addLayout(grid)

        self.detail = label("", "Muted")
        card.add(self.detail)
        return card

    def _build_device_card(self) -> Card:
        card = Card("This device")
        self.device = label(UNKNOWN, "Mono")
        card.add(self.device)
        card.add(label(
            "Nimbus stores your keys in Windows Credential Manager on this machine and reads "
            "them per request. Deactivating frees the seat; it does not delete anything "
            "local.", "Muted"))
        return card

    def _build_actions_card(self) -> Card:
        card = Card("Actions")
        actions = QWidget()
        row = QHBoxLayout(actions)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.SPACE[1])

        self.deactivate_button = QPushButton("Deactivate this device")
        self.deactivate_button.clicked.connect(self.sig_deactivate_device.emit)
        row.addWidget(self.deactivate_button)

        self.sign_out_button = QPushButton("Sign out")
        self.sign_out_button.clicked.connect(self.sig_sign_out.emit)
        row.addWidget(self.sign_out_button)

        row.addStretch(1)

        quit_button = QPushButton("Quit Nimbus")
        quit_button.setObjectName("Danger")
        quit_button.setToolTip(
            "Closes Nimbus completely, including push-to-talk. Closing the window only hides "
            "it -- Nimbus is a background tool, so the hotkey keeps working."
        )
        quit_button.clicked.connect(self.sig_quit.emit)
        row.addWidget(quit_button)

        card.add(actions)
        return card
