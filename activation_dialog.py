"""The licence gate's user interface (SHELL_AND_CHAT.md §5, `S-10`).

Separate from ``licensing.py`` for the same reason ``settings_dialog.py`` is separate from
``config.py``: the module that decides whether Nimbus may run must be testable without Qt, and it is
-- ``tests/test_licensing.py`` never constructs a widget.

## What this screen has to get right

It is the **first thing a new user sees**, before the tray icon, before the mic, before anything
works. Three things follow from that:

* **Every path out is visible.** Start the trial, enter a key, buy, or quit. A dialog with a
  disabled button and no explanation is where people give up.
* **A failure is never silent.** §5's rule: "a legitimate user must never be left guessing". A network
  error says so, keeps the key they typed, and offers Retry -- it does not clear the field or close.
* **It says what it cannot do.** The trial is device-bound, and the screen says so rather than
  letting someone discover it by reinstalling.

Styled from ``theme.build_qss`` like every other window, so the first impression matches the app.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import theme

TRIAL_HEADLINE = "Try Nimbus free for 7 days"
BUY_HEADLINE = "Activate Nimbus to keep using it"


class ActivationDialog(QDialog):
    """Blocking licence gate. ``exec()`` returns ``Accepted`` only when Nimbus may run.

    ``licence_module`` is injected so tests drive a stub rather than the network. Production passes
    nothing and gets the real ``licensing``.
    """

    sig_activated = pyqtSignal()

    def __init__(self, licence_module=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if licence_module is None:
            import licensing as licence_module
        self._licensing = licence_module

        self.setWindowTitle("Nimbus")
        self.setModal(True)
        # Wider and bounded. 520px forced the trial note and the blurb to wrap to four lines each,
        # which is what made the screen feel like a form rather than a welcome. 600 lets every line
        # of body copy sit at roughly 70 characters, which is the readable measure.
        self.setMinimumWidth(600)
        self.setMaximumWidth(680)
        self.setStyleSheet(theme.build_qss())

        # The taskbar icon. `qt_app.setWindowIcon` runs *after* the gate in `__main__` -- it has to,
        # the gate is the first window -- so without this the first thing a user sees has a blank
        # placeholder in the taskbar and in Alt-Tab.
        try:
            import brand
            self.setWindowIcon(brand.window_icon())
        except Exception:
            pass

        outer = QVBoxLayout(self)
        outer.setContentsMargins(
            theme.SPACE[5], theme.SPACE[4], theme.SPACE[5], theme.SPACE[4])
        # SPACE[3] between blocks rather than SPACE[2]: three stacked cards at 12px read as one
        # striped mass, and the eye needs a bigger gap between groups than inside them.
        outer.setSpacing(theme.SPACE[3])

        outer.addLayout(self._build_header())

        outer.addWidget(self._build_trial_card())
        outer.addWidget(self._build_key_card())
        outer.addWidget(self._build_login_card())

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setObjectName("Muted")
        outer.addWidget(self.status)

        row = QHBoxLayout()
        row.setSpacing(theme.SPACE[1])
        self.offline_button = QPushButton("Continue offline")
        self.offline_button.setToolTip(
            "Use the licence already cached on this machine.\n"
            "Available for 14 days after the last successful check."
        )
        self.offline_button.clicked.connect(self._on_offline)
        row.addWidget(self.offline_button)
        row.addStretch(1)
        quit_button = QPushButton("Quit")
        quit_button.setObjectName("Danger")
        quit_button.clicked.connect(self.reject)
        row.addWidget(quit_button)
        outer.addLayout(row)

        theme.focus_visible_only(self)
        self._refresh_offline_button()
        # The system frame is this dialog's only piece of undesigned chrome: a white caption bar on a
        # near-black window, on the first screen anybody sees. See `theme.apply_dark_titlebar`.
        theme.apply_dark_titlebar(self)

    def showEvent(self, event) -> None:
        """Reapply the dark caption on show.

        DWM attributes are set against an HWND, and asking for one in ``__init__`` is what forces the
        HWND to exist. Qt can still recreate the native window between construction and first show --
        a style change or a parent change does it -- and a recreated window comes back with the
        default white caption. Setting it twice is cheap; a white flash on the first screen is not.
        """
        super().showEvent(event)
        theme.apply_dark_titlebar(self)

    # -- construction ---------------------------------------------------------

    def _build_header(self) -> QVBoxLayout:
        """The headline and the price.

        No mark in the body. It was here to identify the window, but the window already carries the
        logo where Windows shows it -- the title bar, the taskbar and Alt-Tab -- and repeating it
        beside the headline only pushed the copy inwards and stole width from a measure that had just
        been widened to fix exactly that.
        """
        text = QVBoxLayout()
        text.setSpacing(theme.SPACE[0])
        text.setContentsMargins(0, 0, 0, 0)

        self.headline = QLabel(TRIAL_HEADLINE)
        self.headline.setObjectName("Display")
        text.addWidget(self.headline)

        self.blurb = QLabel(
            # "points at the answer" replaced with "guides you". Pointing is one of the things Nimbus
            # does; on a multi-step task it draws the order, boxes the next control and traces the
            # route -- "guides" covers all of that, and "points at the answer" undersells it.
            "Ask about anything on your screen, out loud, and Nimbus guides you to it. "
            "Seven-day trial, no card, on up to two of your own computers."
        )
        self.blurb.setWordWrap(True)
        self.blurb.setObjectName("Secondary")
        text.addWidget(self.blurb)

        return text

    def _build_trial_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("FeatureRow")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(
            theme.SPACE[2], theme.SPACE[2], theme.SPACE[2], theme.SPACE[2])
        layout.setSpacing(theme.SPACE[1])

        # The trial now needs a verified email, so this card collects one. Two fields and a button, in
        # the order someone reads them -- not a separate "sign up" screen, because a screen whose only
        # purpose is to precede the thing you wanted is a screen people abandon.
        self.trial_email = QLineEdit()
        self.trial_email.setPlaceholderText("you@example.com")
        layout.addWidget(self.trial_email)

        self.trial_password = QLineEdit()
        self.trial_password.setPlaceholderText("Choose a password (10+ characters)")
        self.trial_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.trial_password.returnPressed.connect(self._on_trial)
        layout.addWidget(self.trial_password)

        self.trial_button = QPushButton(f"Start the {self._licensing.TRIAL_DAYS}-day trial")
        self.trial_button.setObjectName("Primary")
        self.trial_button.clicked.connect(self._on_trial)
        layout.addWidget(self.trial_button)

        # The code row, hidden until a code has actually been sent. Showing an empty code field before
        # anything has been emailed asks a question the user cannot answer yet.
        self.code_row = QWidget()
        code_layout = QHBoxLayout(self.code_row)
        code_layout.setContentsMargins(0, 0, 0, 0)
        code_layout.setSpacing(theme.SPACE[1])
        self.code_input = QLineEdit()
        self.code_input.setPlaceholderText("6-digit code from your email")
        self.code_input.setObjectName("Mono")
        self.code_input.setMaxLength(6)
        self.code_input.returnPressed.connect(self._on_verify)
        code_layout.addWidget(self.code_input, 1)
        self.verify_button = QPushButton("Verify")
        self.verify_button.setObjectName("Primary")
        self.verify_button.clicked.connect(self._on_verify)
        code_layout.addWidget(self.verify_button)
        self.code_row.setVisible(False)
        layout.addWidget(self.code_row)

        note = QLabel(
            "No card needed. We email a code to confirm the address, and the trial is tied to this "
            "computer -- so it runs once per machine."
        )
        note.setWordWrap(True)
        note.setObjectName("Muted")
        layout.addWidget(note)

        # The browser escape hatch. The two fields above already create the account, so this is not
        # the main road -- it is for what the in-app form cannot finish on its own: an address that
        # already has a subscription, a password that needs resetting, or someone who would simply
        # rather not type a new password into a desktop window they met a minute ago.
        #
        # Hidden on an older `licensing` without `signup_url`, on the same rule as the login card: a
        # button that cannot work should not be visible.
        self.website_signup_button = QPushButton("Or create your account in a browser.")
        self.website_signup_button.setObjectName("Ghost")
        self.website_signup_button.clicked.connect(self._on_website_signup)
        self.website_signup_button.setVisible(hasattr(self._licensing, "signup_url"))
        layout.addWidget(self.website_signup_button)
        return card

    def _build_key_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(
            theme.SPACE[2], theme.SPACE[2], theme.SPACE[2], theme.SPACE[2])
        layout.setSpacing(theme.SPACE[1])

        heading = QLabel("ALREADY HAVE A KEY?")
        heading.setObjectName("CardHeader")
        layout.addWidget(heading)

        # Where the key comes from, because "paste your licence key" is useless to someone who has
        # never been given one. A trial account has no key at all -- keys are minted only when a
        # licence is issued, by the webhook or by an approved transfer -- so a trial user looking for
        # one is hunting for something that does not exist. Saying so here is the difference between
        # a clear screen and a support email.
        note = QLabel(
            "A key is emailed when your licence is issued, and it is always on your account page. "
            "A free trial has no key \u2014 use the box above instead."
        )
        note.setWordWrap(True)
        note.setObjectName("Muted")
        layout.addWidget(note)

        row = QHBoxLayout()
        row.setSpacing(theme.SPACE[1])
        self.key_input = QLineEdit()
        self.key_input.setPlaceholderText("NIMBUS-XXXX-XXXX-XXXX")
        self.key_input.setObjectName("Mono")
        # Enter activates, because typing a key and pressing Enter is what everyone tries first.
        self.key_input.returnPressed.connect(self._on_activate)
        row.addWidget(self.key_input, 1)
        self.activate_button = QPushButton("Activate")
        self.activate_button.clicked.connect(self._on_activate)
        row.addWidget(self.activate_button)
        layout.addLayout(row)

        buy = QPushButton("See the plan on the website.")
        buy.setObjectName("Ghost")
        buy.clicked.connect(self._on_buy)
        layout.addWidget(buy)
        return card

    def _build_login_card(self) -> QFrame:
        """Sign in with the email and password used to register.

        Second, under the key, on purpose. The key is one paste and cannot be forgotten wrongly; the
        password is the recovery path for someone who has lost the email. Offering the password first
        would teach every tester to type credentials into a desktop application when they did not
        need to.

        Hidden entirely when the licence module cannot do it, so an older build never shows a form that
        would fail -- and a tester never wonders whether they typed something wrong.
        """
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(
            theme.SPACE[2], theme.SPACE[2], theme.SPACE[2], theme.SPACE[2])
        layout.setSpacing(theme.SPACE[1])

        heading = QLabel("LOST YOUR KEY?")
        heading.setObjectName("CardHeader")
        layout.addWidget(heading)

        note = QLabel("Sign in with the email and password you registered with.")
        note.setWordWrap(True)
        note.setObjectName("Muted")
        layout.addWidget(note)

        self.email_input = QLineEdit()
        self.email_input.setPlaceholderText("you@example.com")
        layout.addWidget(self.email_input)

        row = QHBoxLayout()
        row.setSpacing(theme.SPACE[1])
        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Password")
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.returnPressed.connect(self._on_login)
        row.addWidget(self.password_input, 1)
        self.login_button = QPushButton("Sign in")
        self.login_button.clicked.connect(self._on_login)
        row.addWidget(self.login_button)
        layout.addLayout(row)

        if not hasattr(self._licensing, "activate_with_login"):
            card.setVisible(False)
        return card

    # -- actions --------------------------------------------------------------

    def _busy(self, busy: bool, message: str = "") -> None:
        for widget in (self.trial_button, self.activate_button, self.key_input,
                       self.login_button, self.email_input, self.password_input,
                       self.trial_email, self.trial_password, self.code_input,
                       self.verify_button):
            widget.setEnabled(not busy)
        if message:
            self._say(message, theme.TEXT_SECONDARY)

    def _say(self, message: str, colour: str) -> None:
        self.status.setText(message)
        self.status.setStyleSheet(f"color: {colour};")

    def _on_trial(self) -> None:
        """Register, then reveal the code field.

        Falls back to the old device-only ``start_trial`` when the licence module has no ``register`` --
        which keeps this dialog working against an older or stubbed module rather than presenting a form
        that cannot succeed.
        """
        if not hasattr(self._licensing, "register"):
            self._legacy_trial()
            return

        self._busy(True, "Creating your account\u2026")
        try:
            message = self._licensing.register(
                self.trial_email.text(), self.trial_password.text())
        except Exception as exc:
            # Deliberately broad: any failure here must produce a readable sentence, never a traceback
            # on the first screen a new user sees.
            self._busy(False)
            self._say(str(exc) or "The trial could not be started.", theme.DANGER)
            return

        self._busy(False)
        # The password field is cleared once it has been used. It is not needed again, and a password
        # sitting in a visible form is a password someone can walk past and read.
        self.trial_password.clear()
        self.code_row.setVisible(True)
        self.code_input.setFocus()
        self._say(message, theme.TEXT_SECONDARY)

    def _on_verify(self) -> None:
        """Check the code. On success this is a working trial, or a licence if one already existed."""
        self._busy(True, "Checking your code\u2026")
        try:
            state = self._licensing.verify_code(self.trial_email.text(), self.code_input.text())
        except Exception as exc:
            self._busy(False)
            # The code is cleared, the email is kept: a wrong code is retyped, a correct email is not.
            self.code_input.clear()
            self._say(str(exc) or "That code was not accepted.", theme.DANGER)
            return
        self._busy(False)
        if state.activated:
            self.sig_activated.emit()
            self.accept()
            return
        self._say(state.detail or "That code did not start a trial.", theme.WARNING)

    def _legacy_trial(self) -> None:
        """The device-only trial, for a licence module without accounts."""
        self._busy(True, "Starting your trial\u2026")
        try:
            state = self._licensing.start_trial()
        except Exception as exc:
            self._busy(False)
            self._say(str(exc) or "The trial could not be started.", theme.DANGER)
            return
        self._busy(False)
        if state.activated:
            self.sig_activated.emit()
            self.accept()
            return
        self._say("This computer has already used its trial.", theme.WARNING)

    def _on_activate(self) -> None:
        key = self.key_input.text().strip()
        self._busy(True, "Checking your licence\u2026")
        try:
            state = self._licensing.activate(key)
        except Exception as exc:
            self._busy(False)
            # The key is deliberately left in the field. Clearing it after a network error means
            # retyping a 20-character key to retry, which is how a transient failure becomes a
            # refund request.
            self._say(str(exc) or "That licence key was not accepted.", theme.DANGER)
            return
        self._busy(False)
        if state.activated:
            self.sig_activated.emit()
            self.accept()
            return
        self._say(state.detail or "That licence is no longer active.", theme.WARNING)

    def _on_login(self) -> None:
        """Sign in and activate. The password is never stored -- see ``activate_with_login``."""
        self._busy(True, "Signing you in\u2026")
        try:
            state = self._licensing.activate_with_login(
                self.email_input.text(), self.password_input.text())
        except Exception as exc:
            self._busy(False)
            # The password field is cleared and the email is kept. A failed sign-in is usually a
            # mistyped password, and leaving it in place invites the same failure again -- whereas
            # retyping the email as well is just friction.
            self.password_input.clear()
            self._say(str(exc) or "That email and password were not accepted.", theme.DANGER)
            return
        self._busy(False)
        self.password_input.clear()
        if state.activated:
            self.sig_activated.emit()
            self.accept()
            return
        self._say(state.detail or "That subscription is not active.", theme.WARNING)

    def _on_buy(self) -> None:
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl(self._licensing.checkout_url()))

    def _on_website_signup(self) -> None:
        """Open the website's sign-up page, and say the dialog is still waiting.

        The message is the point. A browser opening over the top of a modal looks like the app has
        gone away, and someone who finishes signing up in Chrome needs to know there is still a
        window here expecting a code -- otherwise they quit Nimbus and start again.
        """
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl(self._licensing.signup_url()))
        self._say(
            "Opened your browser. Come back here afterwards and start the trial with the same "
            "email and password.",
            theme.TEXT_SECONDARY,
        )

    def _on_offline(self) -> None:
        if self._licensing.is_activated():
            self.accept()
            return
        self._say(
            "There is no valid licence cached on this computer.", theme.DANGER)

    def _refresh_offline_button(self) -> None:
        """Only offer "continue offline" when there is genuinely something cached.

        A button that always fails teaches people to ignore the buttons.
        """
        try:
            available = self._licensing.is_activated()
        except Exception:
            available = False
        self.offline_button.setVisible(bool(available))


def run_activation_flow(licence_module=None, parent: QWidget | None = None) -> bool:
    """Show the gate and report whether Nimbus may run. ``False`` means quit.

    Returns True without showing anything when the machine is already licensed, so the gate costs a
    licensed user nothing on startup -- no dialog, no network call.
    """
    if licence_module is None:
        import licensing as licence_module
    try:
        if licence_module.is_activated():
            return True
    except Exception:
        # An unreadable licence is not a reason to lock someone out silently; show the gate.
        pass
    dialog = ActivationDialog(licence_module, parent)
    try:
        return dialog.exec() == QDialog.DialogCode.Accepted
    finally:
        dialog.deleteLater()
