"""The licence gate's user interface (SHELL_AND_CHAT.md §5, `S-10`).

The gate is the **first thing a new user sees**, before the tray icon, before the mic, before
anything works. So what is tested here is not layout -- it is the four properties that decide whether
someone gets in, gives up, or asks for a refund:

* a licensed machine sees **no dialog at all** and makes no network call;
* a failure produces a readable sentence and **keeps the key that was typed**;
* "Continue offline" is hidden unless something is genuinely cached, because a button that always
  fails teaches people to ignore the buttons;
* declining the gate means quit, and quitting happens **before** a device is claimed.

The dialog takes its licence module by injection, so every test here drives a stub. Nothing touches
the network, the keyring or Credential Manager.
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def qt_app():
    """One QApplication for the module. Qt requires it before any QWidget exists."""
    from PyQt6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


class FakeLicensing:
    """A stand-in for ``licensing`` with the same surface the dialog uses.

    A class rather than a mock: the dialog reads constants, calls functions and inspects the returned
    state's fields, and a mock would happily return a ``Mock`` for ``state.activated`` -- which is
    truthy, so every test would pass whatever the code did.
    """

    PLAN_DEVICES = "2 devices"
    TRIAL_DAYS = 7

    def __init__(self, *, activated=False, trial=None, activate=None, login=None,
                 register=None, verify=None):
        self._activated = activated
        self._trial = trial
        self._activate = activate
        self._login = login
        self._register = register
        self._verify = verify
        self.calls = []

    # -- the surface the dialog uses ------------------------------------------

    def is_activated(self):
        self.calls.append("is_activated")
        return self._activated

    def register(self, email, password):
        self.calls.append(("register", email, password))
        if isinstance(self._register, Exception):
            raise self._register
        return self._register or f"We sent a 6-digit code to {email}."

    def verify_code(self, email, code):
        self.calls.append(("verify_code", email, code))
        if isinstance(self._verify, Exception):
            raise self._verify
        return self._verify

    def start_trial(self):
        self.calls.append("start_trial")
        if isinstance(self._trial, Exception):
            raise self._trial
        return self._trial

    def activate(self, key):
        self.calls.append(("activate", key))
        if isinstance(self._activate, Exception):
            raise self._activate
        return self._activate

    def activate_with_login(self, email, password):
        self.calls.append(("login", email, password))
        if isinstance(self._login, Exception):
            raise self._login
        return self._login

    def checkout_url(self):
        return "https://example.test/#pricing"

    def signup_url(self):
        return "https://example.test/signup"


def state(**fields):
    import licensing

    return licensing.LicenceState(**fields)


# --- the flow ----------------------------------------------------------------


class TestRunActivationFlow:
    def test_a_licensed_machine_never_sees_the_dialog(self, qt_app, mocker):
        """The gate must cost a licensed user nothing at startup: no window, no network call."""
        from activation_dialog import run_activation_flow

        constructed = mocker.patch("activation_dialog.ActivationDialog")
        fake = FakeLicensing(activated=True)

        assert run_activation_flow(fake) is True
        constructed.assert_not_called()

    def test_an_unreadable_licence_shows_the_gate_rather_than_locking_out(self, qt_app, mocker):
        """A corrupt keyring is our bug, not the customer's. §0.1 is deterrence, not enforcement."""
        from activation_dialog import run_activation_flow

        class Broken(FakeLicensing):
            def is_activated(self):
                raise RuntimeError("credential store is unreadable")

        dialog = mocker.patch("activation_dialog.ActivationDialog")
        dialog.return_value.exec.return_value = 0

        assert run_activation_flow(Broken()) is False
        dialog.assert_called_once()

    def test_declining_reports_false_so_the_caller_can_quit(self, qt_app, mocker):
        from PyQt6.QtWidgets import QDialog

        from activation_dialog import run_activation_flow

        dialog = mocker.patch("activation_dialog.ActivationDialog")
        dialog.return_value.exec.return_value = QDialog.DialogCode.Rejected

        assert run_activation_flow(FakeLicensing()) is False

    def test_accepting_reports_true(self, qt_app, mocker):
        from PyQt6.QtWidgets import QDialog

        from activation_dialog import run_activation_flow

        dialog = mocker.patch("activation_dialog.ActivationDialog")
        dialog.return_value.exec.return_value = QDialog.DialogCode.Accepted

        assert run_activation_flow(FakeLicensing()) is True


# --- the dialog itself -------------------------------------------------------


class TestTheTrialPath:
    """Register, receive a code, type it in. The trial is no longer anonymous."""

    def _registered(self, qt_app, **kwargs):
        from activation_dialog import ActivationDialog

        fake = FakeLicensing(**kwargs)
        dialog = ActivationDialog(fake)
        dialog.trial_email.setText("student@example.com")
        dialog.trial_password.setText("correct horse battery")
        dialog.trial_button.click()
        return dialog, fake

    def test_the_code_field_is_hidden_until_a_code_has_been_sent(self, qt_app):
        """Asking for a code before one exists is asking a question the user cannot answer."""
        from activation_dialog import ActivationDialog

        dialog = ActivationDialog(FakeLicensing())
        assert dialog.code_row.isVisible() is False

    def test_registering_reveals_the_code_field_and_reports_where_the_code_went(self, qt_app):
        dialog, fake = self._registered(qt_app)

        assert ("register", "student@example.com", "correct horse battery") in fake.calls
        assert dialog.code_row.isVisibleTo(dialog) is True
        assert "student@example.com" in dialog.status.text()

    def test_the_chosen_password_is_cleared_once_it_has_been_used(self, qt_app):
        """It is not needed again, and a password sitting in a visible form can be read over a shoulder."""
        dialog, _ = self._registered(qt_app)
        assert dialog.trial_password.text() == ""

    def test_a_correct_code_starts_the_trial_and_accepts(self, qt_app):
        dialog, fake = self._registered(
            qt_app, verify=state(activated=True, kind="trial", detail="Trial \u00b7 7 days left"))
        emitted = []
        dialog.sig_activated.connect(lambda: emitted.append(True))

        dialog.code_input.setText("123456")
        dialog.verify_button.click()

        assert ("verify_code", "student@example.com", "123456") in fake.calls
        assert emitted == [True]
        assert dialog.result() == dialog.DialogCode.Accepted

    def test_a_wrong_code_is_cleared_but_the_email_is_kept(self, qt_app):
        import licensing

        dialog, _ = self._registered(qt_app, verify=licensing.LicenceError(
            "That code is not right. Check the email and try again."))
        dialog.code_input.setText("000000")
        dialog.verify_button.click()

        assert dialog.code_input.text() == ""
        assert dialog.trial_email.text() == "student@example.com"
        assert "not right" in dialog.status.text()

    def test_a_used_trial_says_so_and_leaves_the_dialog_open(self, qt_app):
        """The machine is the key, so this is a real answer -- not an error to hide."""
        import licensing

        dialog, _ = self._registered(qt_app, verify=licensing.LicenceError(
            "The free trial on this computer has already been used. A licence key activates it again."))
        dialog.code_input.setText("123456")
        dialog.verify_button.click()

        assert dialog.result() != dialog.DialogCode.Accepted
        assert "already been used" in dialog.status.text()

    def test_a_network_failure_shows_the_message_and_re_enables_everything(self, qt_app):
        """A dialog left disabled after a failure is where people give up."""
        import licensing
        from activation_dialog import ActivationDialog

        fake = FakeLicensing(register=licensing.LicenceError(
            "Nimbus could not reach the licence service. Check your connection."))
        dialog = ActivationDialog(fake)
        dialog.trial_email.setText("student@example.com")
        dialog.trial_password.setText("correct horse battery")
        dialog.trial_button.click()

        assert "could not reach" in dialog.status.text()
        assert dialog.trial_button.isEnabled()
        assert dialog.key_input.isEnabled()
        assert dialog.code_row.isVisible() is False, "a failed registration must not ask for a code"

    def test_an_unexpected_exception_still_produces_a_sentence(self, qt_app):
        """Not a traceback on the first screen a new user sees."""
        from activation_dialog import ActivationDialog

        dialog = ActivationDialog(FakeLicensing(register=ZeroDivisionError()))
        dialog.trial_email.setText("student@example.com")
        dialog.trial_password.setText("correct horse battery")
        dialog.trial_button.click()

        assert dialog.status.text().strip() != ""
        assert dialog.trial_button.isEnabled()

    def test_a_licence_module_without_accounts_falls_back_to_the_device_only_trial(self, qt_app):
        """Keeps this dialog working against an older module instead of showing a form that cannot win."""
        from activation_dialog import ActivationDialog

        class Legacy(FakeLicensing):
            def __getattribute__(self, name):
                if name == "register":
                    raise AttributeError(name)
                return super().__getattribute__(name)

        fake = Legacy(trial=state(activated=True, kind="trial"))
        dialog = ActivationDialog(fake)
        dialog.trial_button.click()

        assert "start_trial" in fake.calls
        assert dialog.result() == dialog.DialogCode.Accepted


class TestTheKeyPath:
    def test_a_valid_key_accepts(self, qt_app):
        from activation_dialog import ActivationDialog

        fake = FakeLicensing(activate=state(activated=True, kind="subscription"))
        dialog = ActivationDialog(fake)
        dialog.key_input.setText("  nimbus-abcd-efgh-jklm  ")
        dialog.activate_button.click()

        assert ("activate", "nimbus-abcd-efgh-jklm") in fake.calls  # trimmed before sending
        assert dialog.result() == dialog.DialogCode.Accepted

    def test_a_rejected_key_is_left_in_the_field(self, qt_app):
        """Clearing it means retyping 20 characters to retry, which is how a transient failure
        becomes a refund request."""
        import licensing
        from activation_dialog import ActivationDialog

        fake = FakeLicensing(activate=licensing.LicenceError("That licence key was not recognised."))
        dialog = ActivationDialog(fake)
        dialog.key_input.setText("NIMBUS-AAAA-BBBB-CCCC")
        dialog.activate_button.click()

        assert dialog.key_input.text() == "NIMBUS-AAAA-BBBB-CCCC"
        assert "not recognised" in dialog.status.text()

    def test_enter_activates(self, qt_app):
        """What everyone tries first after typing a key."""
        from activation_dialog import ActivationDialog

        fake = FakeLicensing(activate=state(activated=True, kind="subscription"))
        dialog = ActivationDialog(fake)
        dialog.key_input.setText("NIMBUS-AAAA-BBBB-CCCC")
        dialog.key_input.returnPressed.emit()

        assert dialog.result() == dialog.DialogCode.Accepted

    def test_a_lapsed_licence_shows_its_own_explanation(self, qt_app):
        from activation_dialog import ActivationDialog

        fake = FakeLicensing(activate=state(
            activated=False, kind="subscription", expired=True,
            detail="Your subscription has lapsed. Renew to keep using Nimbus."))
        dialog = ActivationDialog(fake)
        dialog.activate_button.click()

        assert "lapsed" in dialog.status.text()


class TestOfflineAndQuit:
    def test_continue_offline_is_hidden_when_nothing_is_cached(self, qt_app):
        from activation_dialog import ActivationDialog

        dialog = ActivationDialog(FakeLicensing(activated=False))
        assert dialog.offline_button.isVisible() is False

    def test_continue_offline_is_offered_when_a_licence_is_cached(self, qt_app):
        """Reached when the *gate* was shown for another reason -- a due revalidation, say."""
        from activation_dialog import ActivationDialog

        dialog = ActivationDialog(FakeLicensing(activated=True))
        dialog.show()
        try:
            assert dialog.offline_button.isVisible() is True
            dialog.offline_button.click()
            assert dialog.result() == dialog.DialogCode.Accepted
        finally:
            dialog.hide()

    def test_quitting_is_always_available(self, qt_app):
        """Every path out has to be visible. A gate with no exit is a hostage situation."""
        from PyQt6.QtWidgets import QPushButton

        from activation_dialog import ActivationDialog

        dialog = ActivationDialog(FakeLicensing())
        labels = [b.text() for b in dialog.findChildren(QPushButton)]
        assert "Quit" in labels
        assert any("trial" in label.lower() for label in labels)
        # The route to the website. Named by what it does rather than by the old "subscribe" wording,
        # so the assertion survives the copy change instead of pinning a sentence nobody reads.
        assert any("website" in label.lower() for label in labels)

    def test_the_screen_says_the_trial_is_device_bound(self, qt_app):
        """Said up front rather than discovered by reinstalling."""
        from PyQt6.QtWidgets import QLabel

        from activation_dialog import ActivationDialog

        dialog = ActivationDialog(FakeLicensing())
        text = " ".join(label.text() for label in dialog.findChildren(QLabel)).lower()
        assert "this computer" in text
        assert "no card" in text

    def test_buying_opens_the_checkout_url_rather_than_embedding_a_browser(self, qt_app, mocker):
        from PyQt6.QtWidgets import QPushButton

        from activation_dialog import ActivationDialog

        opened = mocker.patch("PyQt6.QtGui.QDesktopServices.openUrl")
        dialog = ActivationDialog(FakeLicensing())
        buy = next(b for b in dialog.findChildren(QPushButton) if "website" in b.text().lower())
        buy.click()

        opened.assert_called_once()
        assert "example.test" in opened.call_args[0][0].toString()

    def test_the_browser_signup_opens_the_website_and_says_the_gate_is_still_waiting(
            self, qt_app, mocker):
        """The escape hatch for what the in-app form cannot finish itself.

        The status line matters as much as the URL: a browser opening over a modal looks like Nimbus
        has gone away, and someone who signs up in Chrome needs to know a window here is still
        expecting them.
        """
        from activation_dialog import ActivationDialog

        opened = mocker.patch("PyQt6.QtGui.QDesktopServices.openUrl")
        dialog = ActivationDialog(FakeLicensing())
        dialog.website_signup_button.click()

        assert opened.call_args[0][0].toString() == "https://example.test/signup"
        assert "come back here" in dialog.status.text().lower()

    def test_an_older_licensing_module_hides_the_browser_signup(self, qt_app):
        """A button that cannot work must not be visible -- same rule as the sign-in card."""
        from activation_dialog import ActivationDialog

        signup_url = FakeLicensing.signup_url
        del FakeLicensing.signup_url  # simulate a build predating `signup_url`
        try:
            # The dialog is bound to a name deliberately: dropping it lets Python collect the widget
            # and the next line dies with "wrapped C/C++ object has been deleted" instead of testing
            # anything.
            older = ActivationDialog(FakeLicensing())
            # `isHidden`, not `isVisible`. Nothing inside a dialog that was never `show()`n is
            # visible, so `isVisible() is False` would pass even if the button were never hidden --
            # a test that cannot fail. `isHidden` reports the explicit `setVisible(False)`.
            assert older.website_signup_button.isHidden() is True
        finally:
            FakeLicensing.signup_url = signup_url

        # The other half, so the assertion above is known to be measuring something.
        current = ActivationDialog(FakeLicensing())
        assert current.website_signup_button.isHidden() is False


class TestSigningIn:
    """Activating with the account instead of the key.

    The property that matters most is negative: the password must not survive the attempt, successful
    or not. Everything else here is about a customer who has lost their key still getting in.
    """

    def test_signing_in_activates(self, qt_app):
        from activation_dialog import ActivationDialog

        fake = FakeLicensing(login=state(activated=True, kind="subscription"))
        dialog = ActivationDialog(fake)
        dialog.email_input.setText("buyer@example.com")
        dialog.password_input.setText("correct horse battery")
        dialog.login_button.click()

        assert ("login", "buyer@example.com", "correct horse battery") in fake.calls
        assert dialog.result() == dialog.DialogCode.Accepted

    def test_the_password_field_is_cleared_on_success(self, qt_app):
        from activation_dialog import ActivationDialog

        dialog = ActivationDialog(FakeLicensing(login=state(activated=True, kind="subscription")))
        dialog.email_input.setText("buyer@example.com")
        dialog.password_input.setText("correct horse battery")
        dialog.login_button.click()

        assert dialog.password_input.text() == ""

    def test_a_failure_clears_the_password_but_keeps_the_email(self, qt_app):
        """A failed sign-in is usually a mistyped password. Keeping it invites the same failure;
        making them retype the email as well is just friction."""
        import licensing
        from activation_dialog import ActivationDialog

        fake = FakeLicensing(login=licensing.LicenceError(
            "That email and password do not match an account."))
        dialog = ActivationDialog(fake)
        dialog.email_input.setText("buyer@example.com")
        dialog.password_input.setText("wrong")
        dialog.login_button.click()

        assert dialog.password_input.text() == ""
        assert dialog.email_input.text() == "buyer@example.com"
        assert "do not match" in dialog.status.text()

    def test_the_seat_limit_message_reaches_the_user_unchanged(self, qt_app):
        """The server names the number and the remedy. Rewording it here would lose both."""
        import licensing
        from activation_dialog import ActivationDialog

        message = ("Your licence is already on 2 computers. Open Nimbus on one of them and use "
                   "Account \u2192 Deactivate this device.")
        dialog = ActivationDialog(FakeLicensing(login=licensing.LicenceError(message)))
        dialog.email_input.setText("buyer@example.com")
        dialog.password_input.setText("correct horse battery")
        dialog.login_button.click()

        assert dialog.status.text() == message

    def test_enter_in_the_password_field_signs_in(self, qt_app):
        from activation_dialog import ActivationDialog

        dialog = ActivationDialog(FakeLicensing(login=state(activated=True, kind="subscription")))
        dialog.email_input.setText("buyer@example.com")
        dialog.password_input.setText("correct horse battery")
        dialog.password_input.returnPressed.emit()

        assert dialog.result() == dialog.DialogCode.Accepted

    def test_the_password_is_masked(self, qt_app):
        from PyQt6.QtWidgets import QLineEdit

        from activation_dialog import ActivationDialog

        dialog = ActivationDialog(FakeLicensing())
        assert dialog.password_input.echoMode() == QLineEdit.EchoMode.Password

    def test_the_card_is_hidden_when_the_licence_module_cannot_sign_in(self, qt_app):
        """An older or stubbed licence module must not present a form that would fail."""
        from activation_dialog import ActivationDialog

        class WithoutLogin(FakeLicensing):
            activate_with_login = None

            def __getattribute__(self, name):
                if name == "activate_with_login":
                    raise AttributeError(name)
                return super().__getattribute__(name)

        dialog = ActivationDialog(WithoutLogin())
        dialog.show()
        try:
            assert dialog.login_button.isVisible() is False
        finally:
            dialog.hide()

    def test_the_key_route_is_offered_above_the_password_route(self, qt_app):
        """Order is a decision: the key needs no password typed into a desktop app, so it comes first."""
        from PyQt6.QtWidgets import QLabel

        from activation_dialog import ActivationDialog

        dialog = ActivationDialog(FakeLicensing())
        headings = [label.text() for label in dialog.findChildren(QLabel)
                    if label.objectName() == "CardHeader"]
        assert headings.index("ALREADY HAVE A KEY?") < headings.index("LOST YOUR KEY?")
