"""The licence gate (SHELL_AND_CHAT.md §5, `S-10`).

Every test here signs with a **throwaway keypair generated in the test**. The production private key
does not exist in this repository and must never appear in one, so the suite proves the *mechanism*
rather than any particular key.

The properties worth pinning are not "does it accept a good licence" -- that is the easy half. They
are: a tampered token is refused, an expired one is refused, the offline grace opens and then closes,
a network failure never destroys a working licence, and no raw hardware identifier is ever sent.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest


# --- helpers -----------------------------------------------------------------


def keypair():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    private = ed25519.Ed25519PrivateKey.generate()
    public_raw = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private, base64.urlsafe_b64encode(public_raw).decode().rstrip("=")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def sign(private, claims: dict) -> str:
    payload = json.dumps(claims, separators=(",", ":"), sort_keys=True).encode()
    return f"{_b64(payload)}.{_b64(private.sign(payload))}"


def in_days(days: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


@pytest.fixture
def store(tmp_path, mocker):
    """Redirect both blob stores into ``tmp_path`` and away from the real Credential Manager.

    The keyring is stubbed with a dict rather than mocked per-call: several code paths write to it and
    read it back, and a dict is the only stand-in that makes those round-trips real.
    """
    import licensing

    vault: dict[tuple[str, str], str] = {}

    class FakeKeyring:
        @staticmethod
        def set_password(service, name, value):
            vault[(service, name)] = value

        @staticmethod
        def get_password(service, name):
            return vault.get((service, name))

        @staticmethod
        def delete_password(service, name):
            vault.pop((service, name), None)

    mocker.patch.dict("sys.modules", {"keyring": FakeKeyring})
    mocker.patch.object(licensing, "_data_dir", lambda: tmp_path)
    return vault


@pytest.fixture
def signed(mocker):
    """A keypair whose public half is installed as the build's licence key."""
    import licensing

    private, public_b64 = keypair()
    mocker.patch.object(licensing, "LICENCE_PUBLIC_KEY", public_b64)
    return private


# --- signature verification --------------------------------------------------


class TestVerification:
    def test_valid_signature_accepted(self, signed):
        import licensing

        token = sign(signed, {"kind": "subscription", "expires_at": in_days(30)})
        claims = licensing.verify_token(token)
        assert claims["kind"] == "subscription"

    def test_tampered_payload_rejected(self, signed):
        """The whole point of signing. A licence edited to extend its expiry must not verify."""
        import licensing

        token = sign(signed, {"kind": "subscription", "expires_at": in_days(1)})
        payload_b64, signature_b64 = token.split(".")
        forged_claims = json.dumps(
            {"kind": "subscription", "expires_at": in_days(3650)},
            separators=(",", ":"), sort_keys=True).encode()
        forged = f"{_b64(forged_claims)}.{signature_b64}"

        assert forged != token
        with pytest.raises(licensing.LicenceError):
            licensing.verify_token(forged)

    def test_a_token_signed_by_the_wrong_key_is_rejected(self, signed):
        """Someone running their own licence service against our client."""
        import licensing

        other_private, _ = keypair()
        token = sign(other_private, {"kind": "subscription", "expires_at": in_days(30)})
        with pytest.raises(licensing.LicenceError):
            licensing.verify_token(token)

    def test_malformed_tokens_are_rejected_without_raising_anything_else(self, signed):
        import licensing

        for bad in ("", "no-dot", "a.b.c", "!!!.???", "."):
            with pytest.raises(licensing.LicenceError):
                licensing.verify_token(bad)

    def test_a_build_with_no_public_key_refuses_everything(self, mocker):
        """Fails closed. A build that forgot the key must not accept unsigned licences."""
        import licensing

        mocker.patch.object(licensing, "LICENCE_PUBLIC_KEY", "")
        private, _ = keypair()
        token = sign(private, {"kind": "subscription", "expires_at": in_days(30)})
        with pytest.raises(licensing.LicenceError):
            licensing.verify_token(token)

    def test_the_repository_contains_no_private_key(self):
        """A guard, because committing one is unrecoverable -- it is public the moment it is pushed.

        The invariant is *key material*, not the API that loads it. The licence service legitimately
        calls ``Ed25519PrivateKey.from_private_bytes`` on a value read from the environment, so the
        name alone proves nothing. What must never appear is a **literal**: a PEM armour block, or a
        constant handed to a private-key loader, or a long constant bound to a private-key name.
        """
        import ast
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        skip_dirs = {".venv", "build", "dist", "__pycache__", ".git"}

        def literal_in(node) -> str | None:
            for child in ast.walk(node):
                if isinstance(child, ast.Constant) and isinstance(child.value, (str, bytes)):
                    if len(child.value) >= 32:
                        return repr(child.value)[:24]
            return None

        for path in root.rglob("*.py"):
            if skip_dirs & set(path.parts) or path.name == Path(__file__).name:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            where = path.relative_to(root)

            # A PEM block is unambiguous: no legitimate source file carries one.
            assert "PRIVATE KEY-----" not in text, f"{where} contains PEM private-key armour"

            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = getattr(node.func, "attr", getattr(node.func, "id", ""))
                    if name in ("from_private_bytes", "load_pem_private_key",
                                "load_der_private_key", "load_ssh_private_key"):
                        for arg in list(node.args) + [kw.value for kw in node.keywords]:
                            found = literal_in(arg)
                            assert found is None, (
                                f"{where} passes a literal to {name}(): {found}")
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    names = [getattr(t, "attr", getattr(t, "id", "")).lower() for t in targets]
                    if any("private" in n or "secret_key" in n for n in names):
                        found = literal_in(node.value) if node.value is not None else None
                        assert found is None, (
                            f"{where} binds a literal to {names[0]}: {found}")


# --- expiry and the offline grace -------------------------------------------


class TestExpiry:
    def test_expired_licence_is_not_activated(self, store, signed):
        import licensing

        token = sign(signed, {"kind": "subscription", "expires_at": in_days(-1)})
        licensing._store_blob(licensing.LICENCE_ENTRY, token)

        state = licensing.current_state()
        assert state.expired is True
        assert state.activated is False

    def test_offline_grace_allows_use_within_the_window(self, store, signed):
        """A laptop that has not been online must keep working. §5's rule, and the reason for it:
        a tool that stops working on a flight is worse than one that gets pirated."""
        import licensing

        token = sign(signed, {"kind": "subscription", "expires_at": in_days(-1)})
        licensing._store_blob(licensing.LICENCE_ENTRY, token)
        licensing._store_blob(licensing.LAST_VALIDATED_ENTRY, in_days(-3))

        assert licensing.is_activated() is True

    def test_offline_grace_expires(self, store, signed):
        import licensing

        token = sign(signed, {"kind": "subscription", "expires_at": in_days(-30)})
        licensing._store_blob(licensing.LICENCE_ENTRY, token)
        licensing._store_blob(
            licensing.LAST_VALIDATED_ENTRY, in_days(-(licensing.OFFLINE_GRACE_DAYS + 1)))

        assert licensing.is_activated() is False

    def test_a_trial_gets_no_offline_grace(self, store, signed):
        """Grace exists so a *paying* customer is never locked out. Extending it to trials would
        make the trial 21 days for anyone who stays offline."""
        import licensing

        token = sign(signed, {"kind": "trial", "expires_at": in_days(-1)})
        licensing._store_blob(licensing.TRIAL_ENTRY, token)
        licensing._store_blob(licensing.LAST_VALIDATED_ENTRY, in_days(-2))

        assert licensing.is_activated() is False

    def test_a_subscription_beats_a_live_trial(self, store, signed):
        """Someone who buys mid-trial must not be told they have three days left."""
        import licensing

        licensing._store_blob(licensing.TRIAL_ENTRY, sign(
            signed, {"kind": "trial", "expires_at": in_days(3)}))
        licensing._store_blob(licensing.LICENCE_ENTRY, sign(
            signed, {"kind": "subscription", "plan": "Nimbus", "expires_at": in_days(30)}))

        state = licensing.current_state()
        assert state.kind == "subscription"
        assert state.activated is True

    def test_an_unverifiable_cached_token_is_discarded(self, store, signed):
        """Asked once, rather than shown a broken state forever."""
        import licensing

        licensing._store_blob(licensing.LICENCE_ENTRY, "garbage.garbage")
        assert licensing.current_state().activated is False
        assert licensing._read_blob(licensing.LICENCE_ENTRY) == ""


# --- the trial ---------------------------------------------------------------


class TestTrial:
    def test_a_fresh_machine_has_the_full_window(self, store):
        import licensing

        assert licensing.trial_days_left() == licensing.TRIAL_DAYS

    def test_days_left_rounds_up(self, store, mocker):
        """Six hours left reads as "1 day left", not "0 days left" on a licence that still works."""
        import licensing

        started = datetime.now(timezone.utc) - timedelta(days=licensing.TRIAL_DAYS, hours=-6)
        mocker.patch.object(licensing, "_first_run_at", lambda: started)
        assert licensing.trial_days_left() == 1

    def test_a_freshly_issued_trial_token_reads_as_seven_days(self, store, signed):
        """The service issues a token expiring in 6.9999 days. Flooring that said "6 days left" on a
        seven-day trial the second it was granted -- found by driving the real client against the real
        service, and the reason ``_state_from_claims`` rounds up the way ``trial_days_left`` does."""
        import licensing

        token = sign(signed, {"kind": "trial", "expires_at": in_days(6.9999)})
        licensing._store_blob(licensing.TRIAL_ENTRY, token)

        state = licensing.current_state()
        assert state.trial_days_left == licensing.TRIAL_DAYS
        assert state.detail == "Trial \u00b7 7 days left"

    def test_an_elapsed_trial_reports_zero(self, store, mocker):
        import licensing

        mocker.patch.object(
            licensing, "_first_run_at",
            lambda: datetime.now(timezone.utc) - timedelta(days=licensing.TRIAL_DAYS + 1))
        assert licensing.trial_days_left() == 0

    def test_the_first_run_date_is_written_to_two_places(self, store, tmp_path):
        """Clearing one store is a plausible accident; clearing both is not."""
        import licensing

        licensing._first_run_at()

        assert any(name == licensing.TRIAL_FIRST_RUN_ENTRY for _service, name in store)
        assert (tmp_path / f"{licensing.TRIAL_FIRST_RUN_ENTRY.lower()}.dat").is_file()

    def test_the_earliest_record_wins(self, store, tmp_path, mocker):
        """Restoring one record must not hand back a fresh trial."""
        import licensing

        old = datetime.now(timezone.utc) - timedelta(days=30)
        (tmp_path / f"{licensing.TRIAL_FIRST_RUN_ENTRY.lower()}.dat").write_text(
            old.isoformat(), encoding="utf-8")
        store[(licensing.KEYRING_SERVICE, licensing.TRIAL_FIRST_RUN_ENTRY)] = \
            datetime.now(timezone.utc).isoformat()

        assert licensing.trial_days_left() == 0

    def test_signing_out_does_not_restart_the_trial_clock(self, store):
        import licensing

        licensing._first_run_at()
        licensing.sign_out()

        records = licensing._first_run_records()
        assert records, "sign out erased the trial start date, which resets the trial"


# --- privacy -----------------------------------------------------------------


class TestDeviceIdentity:
    def test_the_device_id_is_a_salted_hash(self, mocker):
        """§5: never send a raw hardware id. A raw MachineGuid correlates across every service that
        receives it, and collecting one makes us its custodian."""
        import licensing

        mocker.patch.object(licensing, "_machine_guid", lambda: "11111111-2222-3333-4444-555555555555")
        mocker.patch.object(licensing, "_volume_serial", lambda: "987654321")

        identifier = licensing.device_id()
        assert "11111111" not in identifier
        assert "987654321" not in identifier
        assert len(identifier) == 32
        assert all(character in "0123456789abcdef" for character in identifier)

    def test_it_is_stable_across_calls(self, mocker):
        import licensing

        mocker.patch.object(licensing, "_machine_guid", lambda: "same")
        mocker.patch.object(licensing, "_volume_serial", lambda: "same")
        assert licensing.device_id() == licensing.device_id()

    def test_a_different_machine_gets_a_different_id(self, mocker):
        import licensing

        mocker.patch.object(licensing, "_volume_serial", lambda: "1")
        mocker.patch.object(licensing, "_machine_guid", lambda: "machine-a")
        first = licensing.device_id()
        mocker.patch.object(licensing, "_machine_guid", lambda: "machine-b")
        assert licensing.device_id() != first

    def test_it_still_returns_something_when_win32_fails(self, mocker):
        """A weaker binding beats blocking a legitimate user on an unusual machine."""
        import licensing

        mocker.patch.object(licensing, "_machine_guid", lambda: "")
        mocker.patch.object(licensing, "_volume_serial", lambda: "")
        assert len(licensing.device_id()) == 32

    def test_activation_sends_only_the_hash(self, store, signed, mocker):
        import licensing

        sent = {}

        def fake_post(path, body):
            sent.update(body)
            return {"token": sign(signed, {"kind": "subscription", "expires_at": in_days(30)})}

        mocker.patch.object(licensing, "_post", fake_post)
        mocker.patch.object(licensing, "_machine_guid", lambda: "RAW-GUID-VALUE")
        mocker.patch.object(licensing, "_volume_serial", lambda: "RAWSERIAL")

        licensing.activate("NIMBUS-TEST")

        body = json.dumps(sent)
        assert "RAW-GUID-VALUE" not in body
        assert "RAWSERIAL" not in body
        assert sent["device_id"] == licensing.device_id()


# --- talking to the service --------------------------------------------------


class TestService:
    def test_activation_stores_the_token_and_the_key(self, store, signed, mocker):
        import licensing

        token = sign(signed, {"kind": "subscription", "plan": "Nimbus",
                              "email": "a@b.c", "expires_at": in_days(30)})
        mocker.patch.object(licensing, "_post", lambda path, body: {"token": token})

        state = licensing.activate("NIMBUS-1234")

        assert state.activated is True
        assert state.email == "a@b.c"
        assert licensing._read_blob(licensing.LICENCE_ENTRY) == token
        assert licensing._read_blob(licensing.LICENCE_KEY_ENTRY) == "NIMBUS-1234"

    def test_activation_verifies_before_storing(self, store, signed, mocker):
        """A service returning a wrongly-signed token must not poison the cache -- the failure has to
        surface here, where there is a dialog to show it in."""
        import licensing

        other, _ = keypair()
        mocker.patch.object(licensing, "_post", lambda path, body: {
            "token": sign(other, {"kind": "subscription", "expires_at": in_days(30)})})

        with pytest.raises(licensing.LicenceError):
            licensing.activate("NIMBUS-1234")
        assert licensing._read_blob(licensing.LICENCE_ENTRY) == ""

    def test_an_empty_key_is_refused_before_any_request(self, store, mocker):
        import licensing

        posted = mocker.patch.object(licensing, "_post")
        with pytest.raises(licensing.LicenceError):
            licensing.activate("   ")
        posted.assert_not_called()

    def test_activation_clears_a_trial_token(self, store, signed, mocker):
        import licensing

        licensing._store_blob(licensing.TRIAL_ENTRY, sign(
            signed, {"kind": "trial", "expires_at": in_days(3)}))
        mocker.patch.object(licensing, "_post", lambda path, body: {"token": sign(
            signed, {"kind": "subscription", "expires_at": in_days(30)})})

        licensing.activate("NIMBUS-1234")
        assert licensing._read_blob(licensing.TRIAL_ENTRY) == ""

    def test_revalidation_never_clears_a_good_licence_on_a_network_error(
            self, store, signed, mocker):
        """The difference between a revalidation and a lockout. Our outage must not become theirs."""
        import licensing

        token = sign(signed, {"kind": "subscription", "expires_at": in_days(30)})
        licensing._store_blob(licensing.LICENCE_ENTRY, token)
        licensing._store_blob(licensing.LICENCE_KEY_ENTRY, "NIMBUS-1234")

        def boom(path, body):
            raise licensing.LicenceError("network down")

        mocker.patch.object(licensing, "_post", boom)

        state = licensing.revalidate()
        assert state.activated is True
        assert licensing._read_blob(licensing.LICENCE_ENTRY) == token

    def test_revalidation_refreshes_the_cached_token(self, store, signed, mocker):
        import licensing

        licensing._store_blob(licensing.LICENCE_ENTRY, sign(
            signed, {"kind": "subscription", "expires_at": in_days(1)}))
        licensing._store_blob(licensing.LICENCE_KEY_ENTRY, "NIMBUS-1234")
        fresh = sign(signed, {"kind": "subscription", "expires_at": in_days(31)})
        mocker.patch.object(licensing, "_post", lambda path, body: {"token": fresh})

        licensing.revalidate()
        assert licensing._read_blob(licensing.LICENCE_ENTRY) == fresh

    def test_revalidation_is_not_every_launch(self, store, signed):
        """§5: do not phone home on every launch. Startup must not depend on our uptime."""
        import licensing

        licensing._store_blob(licensing.LICENCE_ENTRY, sign(
            signed, {"kind": "subscription", "expires_at": in_days(30)}))
        licensing._store_blob(licensing.LICENCE_KEY_ENTRY, "K")

        licensing._store_blob(licensing.LAST_VALIDATED_ENTRY, in_days(-1))
        assert licensing.should_revalidate() is False

        licensing._store_blob(
            licensing.LAST_VALIDATED_ENTRY, in_days(-(licensing.REVALIDATE_EVERY_DAYS + 1)))
        assert licensing.should_revalidate() is True

    def test_nothing_to_revalidate_without_a_licence(self, store):
        import licensing

        assert licensing.should_revalidate() is False

    def test_deactivation_clears_local_state_even_if_the_call_fails(
            self, store, signed, mocker):
        """The user asked to sign this machine out. A timeout is not a reason to leave a working
        licence behind -- and the seat is reclaimed server-side on the next revalidation."""
        import licensing

        licensing._store_blob(licensing.LICENCE_ENTRY, sign(
            signed, {"kind": "subscription", "expires_at": in_days(30)}))
        licensing._store_blob(licensing.LICENCE_KEY_ENTRY, "NIMBUS-1234")

        def boom(path, body):
            raise licensing.LicenceError("offline")

        mocker.patch.object(licensing, "_post", boom)

        assert licensing.deactivate_device() is False
        assert licensing._read_blob(licensing.LICENCE_ENTRY) == ""
        assert licensing.is_activated() is False

    def test_a_4xx_carries_the_services_message_to_the_user(self, mocker):
        import licensing

        class Response:
            status_code = 403

            @staticmethod
            def json():
                return {"detail": "This key is already on 2 devices."}

        mocker.patch("httpx.post", lambda *a, **k: Response())
        with pytest.raises(licensing.LicenceError) as caught:
            licensing._post("/activate", {})
        assert "2 devices" in str(caught.value)

    def test_a_5xx_does_not_blame_the_user(self, mocker):
        import licensing

        class Response:
            status_code = 503

            @staticmethod
            def json():
                return {}

        mocker.patch("httpx.post", lambda *a, **k: Response())
        with pytest.raises(licensing.LicenceError) as caught:
            licensing._post("/activate", {})
        assert "unavailable" in str(caught.value).lower()


# --- storage -----------------------------------------------------------------


class TestStorage:
    def test_a_small_token_goes_to_the_keyring(self, store, tmp_path):
        import licensing

        licensing._store_blob("SMALL", "x" * 64)
        assert (licensing.KEYRING_SERVICE, "SMALL") in store
        assert not (tmp_path / "small.dat").exists()

    def test_a_large_blob_falls_back_to_a_file(self, store, tmp_path):
        """Measured: Credential Manager accepted 1 KB and refused 2 KB. A write nobody checked would
        mean re-activating on every launch."""
        import licensing

        licensing._store_blob("BIG", "x" * (licensing.KEYRING_SAFE_BYTES + 1))

        assert (licensing.KEYRING_SERVICE, "BIG") not in store
        assert (tmp_path / "big.dat").is_file()
        assert licensing._read_blob("BIG") == "x" * (licensing.KEYRING_SAFE_BYTES + 1)

    def test_a_real_token_fits_in_the_keyring(self, signed):
        """The reason the keyring is the primary store rather than a file."""
        import licensing

        token = sign(signed, {
            "kind": "subscription", "plan": "Nimbus", "email": "someone@example.com",
            "seats_used": 1, "seats_total": 2, "expires_at": in_days(30),
            "device_id": "0" * 32,
        })
        assert len(token.encode()) < licensing.KEYRING_SAFE_BYTES

    def test_clearing_removes_both_stores(self, store, tmp_path):
        import licensing

        licensing._store_blob("BOTH", "x")
        (tmp_path / "both.dat").write_text("x", encoding="utf-8")

        licensing._clear_blob("BOTH")

        assert (licensing.KEYRING_SERVICE, "BOTH") not in store
        assert not (tmp_path / "both.dat").exists()


# --- the honest note ---------------------------------------------------------


def test_the_module_states_that_this_is_deterrence_not_enforcement():
    """§0.1 requires this to be written down where the code is, not only in the design doc.

    A future reader who believes this *enforces* payment will make worse decisions than one who
    knows it does not -- most obviously, they will be tempted to proxy inference to "fix" it, which
    ends BYOK.
    """
    import licensing

    doc = (licensing.__doc__ or "").lower()
    assert "deter" in doc
    assert "does not prevent" in doc
    assert "§0.1" in (licensing.__doc__ or "")


# --- the trial now needs a verified account ---------------------------------


class TestRegisterAndVerify:
    """`register` then `verify_code`: the trial is no longer anonymous.

    What is worth pinning is the validation that happens *before* the network, and the fact that the
    server decides what comes back -- a returning subscriber must not be handed a trial.
    """

    def test_registering_posts_the_email_and_the_device(self, store, mocker):
        import licensing

        post = mocker.patch.object(licensing, "_post", return_value={"detail": "code sent"})
        message = licensing.register("Student@Example.com", "correct horse battery")

        path, body = post.call_args[0]
        assert path == "/api/desktop/register"
        assert body["email"] == "Student@Example.com"
        assert body["device_id"] == licensing.device_id()
        assert message == "code sent"

    def test_a_short_password_or_bad_email_never_reaches_the_network(self, store, mocker):
        """Validated here as well as on the server: a round trip to be told "too short" is a round trip
        someone on mobile data paid for."""
        import licensing

        post = mocker.patch.object(licensing, "_post")
        for email, password in (("", "correct horse battery"), ("not-an-email", "correct horse battery"),
                                ("a@b.c", "short")):
            with pytest.raises(licensing.LicenceError):
                licensing.register(email, password)
        post.assert_not_called()

    def test_a_verified_code_stores_the_trial_token(self, store, signed, mocker):
        import licensing

        token = sign(signed, {"kind": "trial", "plan": "Nimbus trial",
                              "email": "student@example.com", "expires_at": in_days(6.9999)})
        mocker.patch.object(licensing, "_post", return_value={"token": token, "kind": "trial"})

        state = licensing.verify_code("student@example.com", "123456")

        assert state.kind == "trial"
        assert state.trial_days_left == licensing.TRIAL_DAYS
        assert licensing._read_blob(licensing.TRIAL_ENTRY) == token
        assert licensing._read_blob(licensing.LICENCE_ENTRY) == ""

    def test_a_returning_subscriber_gets_a_licence_not_a_trial(self, store, signed, mocker):
        """Someone who paid and then reinstalled must not be handed seven days. The server decides, and
        the client has to store the answer in the right place -- a subscription token in the trial slot
        would be superseded by the next trial check."""
        import licensing

        token = sign(signed, {"kind": "subscription", "plan": "Nimbus",
                              "expires_at": in_days(30), "seats_total": 2, "seats_used": 1})
        mocker.patch.object(licensing, "_post", return_value={
            "token": token, "kind": "subscription", "key": "NIMBUS-AAAA-BBBB-CCCC"})

        state = licensing.verify_code("buyer@example.com", "123456")

        assert state.kind == "subscription"
        assert licensing._read_blob(licensing.LICENCE_ENTRY) == token
        assert licensing._read_blob(licensing.LICENCE_KEY_ENTRY) == "NIMBUS-AAAA-BBBB-CCCC"
        assert licensing._read_blob(licensing.TRIAL_ENTRY) == ""

    def test_the_code_is_reduced_to_digits_before_sending(self, store, signed, mocker):
        """People type "123 456" and paste "Code: 123456". Both are the same six digits."""
        import licensing

        token = sign(signed, {"kind": "trial", "expires_at": in_days(7)})
        post = mocker.patch.object(licensing, "_post", return_value={"token": token})
        licensing.verify_code("student@example.com", " 123 456 ")

        assert post.call_args[0][1]["code"] == "123456"

    def test_an_empty_or_too_short_code_never_reaches_the_network(self, store, mocker):
        import licensing

        post = mocker.patch.object(licensing, "_post")
        for email, code in (("a@b.c", ""), ("a@b.c", "12"), ("", "123456")):
            with pytest.raises(licensing.LicenceError):
                licensing.verify_code(email, code)
        post.assert_not_called()

    def test_an_unverifiable_token_is_not_stored(self, store, signed, mocker):
        import licensing

        other_private, _ = keypair()
        mocker.patch.object(licensing, "_post", return_value={
            "token": sign(other_private, {"kind": "trial", "expires_at": in_days(7)})})

        with pytest.raises(licensing.LicenceError):
            licensing.verify_code("student@example.com", "123456")
        assert licensing._read_blob(licensing.TRIAL_ENTRY) == ""


# --- signing in instead of pasting a key ------------------------------------


class TestActivateWithLogin:
    """`activate_with_login` must end in exactly the state `activate` would.

    The negative property is the important one: **the password must not be stored anywhere.** A desktop
    app that caches a password to re-authenticate later has turned a single-use secret into a permanent
    one, and the whole design here avoids holding credentials.
    """

    def test_a_successful_sign_in_caches_the_token_and_the_key_but_not_the_password(
            self, store, signed, mocker):
        import licensing

        token = sign(signed, {"kind": "subscription", "plan": "Nimbus",
                              "email": "buyer@example.com", "seats_used": 1, "seats_total": 2,
                              "expires_at": in_days(30)})
        mocker.patch.object(licensing, "_post",
                            return_value={"token": token, "key": "NIMBUS-AAAA-BBBB-CCCC"})

        state = licensing.activate_with_login("buyer@example.com", "correct horse battery")

        assert state.activated is True
        assert licensing._read_blob(licensing.LICENCE_ENTRY) == token
        assert licensing._read_blob(licensing.LICENCE_KEY_ENTRY) == "NIMBUS-AAAA-BBBB-CCCC"

        stored = " ".join(str(value) for value in store.values())
        assert "correct horse battery" not in stored, "the password must never be persisted"

    def test_the_password_is_sent_once_and_the_device_is_named(self, store, signed, mocker):
        import licensing

        token = sign(signed, {"kind": "subscription", "expires_at": in_days(30)})
        post = mocker.patch.object(licensing, "_post", return_value={"token": token, "key": "K"})
        licensing.activate_with_login("buyer@example.com", "pw")

        path, body = post.call_args[0]
        assert path == "/api/desktop/login"
        assert body["email"] == "buyer@example.com"
        assert body["device_id"] == licensing.device_id()
        assert len(body["device_id"]) == 32 and body["device_id"].isalnum()

    def test_a_missing_email_or_password_never_reaches_the_network(self, store, mocker):
        import licensing

        post = mocker.patch.object(licensing, "_post")
        for email, password in (("", "pw"), ("a@b.c", ""), ("   ", "pw")):
            with pytest.raises(licensing.LicenceError):
                licensing.activate_with_login(email, password)
        post.assert_not_called()

    def test_an_unverifiable_token_is_not_stored(self, store, signed, mocker):
        """A service that returned a wrongly-signed token would otherwise poison the cache, and the
        failure would surface later as an unexplained lockout instead of here."""
        import licensing

        other_private, _ = keypair()
        mocker.patch.object(licensing, "_post", return_value={
            "token": sign(other_private, {"kind": "subscription", "expires_at": in_days(30)}),
            "key": "NIMBUS-AAAA-BBBB-CCCC",
        })

        with pytest.raises(licensing.LicenceError):
            licensing.activate_with_login("buyer@example.com", "pw")
        assert licensing._read_blob(licensing.LICENCE_ENTRY) == ""

    def test_signing_in_clears_a_running_trial(self, store, signed, mocker):
        """A paid subscription supersedes the trial: otherwise `current_state` could keep reporting
        "3 days left" to someone who has just paid."""
        import licensing

        licensing._store_blob(licensing.TRIAL_ENTRY,
                              sign(signed, {"kind": "trial", "expires_at": in_days(3)}))
        mocker.patch.object(licensing, "_post", return_value={
            "token": sign(signed, {"kind": "subscription", "expires_at": in_days(30)}),
            "key": "NIMBUS-AAAA-BBBB-CCCC",
        })

        licensing.activate_with_login("buyer@example.com", "pw")
        assert licensing._read_blob(licensing.TRIAL_ENTRY) == ""
        assert licensing.current_state().kind == "subscription"


# --- how app.py is wired to all this ----------------------------------------


class TestAppWiring:
    """`NimbusApp`'s three licence actions and the Account page's provider.

    Built with ``__new__`` and no ``__init__``, the house pattern for testing ``NimbusApp`` methods
    without constructing Qt, audio or the hotkey listener. Note that ``getattr`` on a ``QObject`` in
    that state raises ``RuntimeError``, so the code under test reads ``self.__dict__`` -- a real
    constraint that broke five tests before it was understood.
    """

    def _app(self):
        from app import NimbusApp

        class _Sig:
            def __init__(self):
                self.calls = []

            def emit(self, *args):
                self.calls.append(args)

        instance = NimbusApp.__new__(NimbusApp)
        instance.__dict__["sig_show_toast"] = _Sig()
        # Sign out and deactivate now re-run the licence gate in place instead of promising
        # something about the next launch, so this needs stubbing for the same reason as the toast:
        # a real signal on a QObject with no ``__init__`` raises.
        instance.__dict__["sig_licence_gate_required"] = _Sig()
        instance._refresh_window = lambda: instance.__dict__.setdefault("refreshed", []).append(True)
        return instance

    def test_deactivating_reports_a_released_seat_differently_from_a_local_sign_out(self, mocker):
        """Two outcomes that mean different things to someone about to reinstall: a released seat is
        immediately reusable, an unreleased one frees up on the next revalidation."""
        import licensing

        app = self._app()
        mocker.patch.object(licensing, "deactivate_device", return_value=True)
        app.deactivate_device()
        assert "deactivated" in app.__dict__["sig_show_toast"].calls[0][0]

        app = self._app()
        mocker.patch.object(licensing, "deactivate_device", return_value=False)
        app.deactivate_device()
        assert "Signed out locally" in app.__dict__["sig_show_toast"].calls[0][0]

    def test_a_failed_deactivation_says_so_rather_than_failing_silently(self, mocker):
        import licensing

        app = self._app()
        mocker.patch.object(licensing, "deactivate_device", side_effect=RuntimeError("boom"))
        app.deactivate_device()

        message, level = app.__dict__["sig_show_toast"].calls[0]
        assert level == "error"
        assert "could not deactivate" in message

    def test_signing_out_forgets_the_licence_and_asks_for_one_again_now(self, mocker):
        """The invariant that matters, and the one this used to get wrong.

        It asserted a toast reading "Signed out. Nimbus will ask for your licence next time it
        starts." Every word was true of the code and misleading in practice: the gate runs once in
        ``__main__``, this process was already past it, and Invariant 5 means closing the window
        hides it. "Next time" only arrived if the user found Quit in the tray, and until then a
        signed-out Nimbus kept its hotkey and microphone. So the guard is now on the re-gate rather
        than on the wording of a promise.
        """
        import licensing

        app = self._app()
        signed_out = mocker.patch.object(licensing, "sign_out")
        app.sign_out_licence()

        signed_out.assert_called_once()
        assert app.__dict__["sig_licence_gate_required"].calls, "the gate must re-run immediately"

    def test_a_failed_sign_out_says_so_and_does_not_re_gate(self, mocker):
        """If the licence is still on disk, throwing up the activation dialog would be a lie."""
        import licensing

        app = self._app()
        mocker.patch.object(licensing, "sign_out", side_effect=RuntimeError("keyring locked"))
        app.sign_out_licence()

        message, level = app.__dict__["sig_show_toast"].calls[0]
        assert level == "error"
        assert "could not sign out" in message
        assert not app.__dict__["sig_licence_gate_required"].calls

    def test_deactivating_also_re_gates_because_the_local_licence_is_gone(self, mocker):
        """``licensing.deactivate_device`` clears local state even when the server call fails, so
        this process is unlicensed either way and must stop running on it now."""
        import licensing

        app = self._app()
        mocker.patch.object(licensing, "deactivate_device", return_value=False)
        app.deactivate_device()

        assert app.__dict__["sig_licence_gate_required"].calls

    def test_revalidation_is_skipped_when_the_last_check_was_recent(self, mocker):
        """Startup must not depend on the licence service's uptime -- that turns our outage into the
        customer's. `should_revalidate` is the whole policy."""
        import licensing

        mocker.patch.object(licensing, "should_revalidate", return_value=False)
        revalidated = mocker.patch.object(licensing, "revalidate")

        app = self._app()
        thread = mocker.patch("app.threading.Thread")
        app.revalidate_licence_async()

        worker = thread.call_args.kwargs["target"]
        worker()
        revalidated.assert_not_called()

    def test_revalidation_never_raises_out_of_its_thread(self, mocker):
        import licensing

        mocker.patch.object(licensing, "should_revalidate", side_effect=RuntimeError("no network"))
        app = self._app()
        thread = mocker.patch("app.threading.Thread")
        app.revalidate_licence_async()

        thread.call_args.kwargs["target"]()  # must not raise

    def test_revalidation_runs_off_the_main_thread_as_a_daemon(self, mocker):
        """A licence check must never delay the hotkey being ready, nor keep Nimbus alive at exit."""
        app = self._app()
        thread = mocker.patch("app.threading.Thread")
        app.revalidate_licence_async()

        assert thread.call_args.kwargs["daemon"] is True
        thread.return_value.start.assert_called_once()

    def test_the_account_page_gets_a_provider_not_a_snapshot(self):
        """The page polls, so a licence that changes while the window is open is reflected."""
        import app as app_module
        import licensing

        provider = app_module._licence_state_provider()
        assert provider is licensing.current_state

    def test_the_provider_is_none_when_licensing_cannot_be_imported(self, mocker):
        """The Account page then shows its honest "activation is not set up" text rather than an
        error for something the user did not do."""
        import app as app_module

        mocker.patch.dict("sys.modules", {"licensing": None})
        mocker.patch("builtins.__import__", side_effect=ImportError("no licensing"))
        assert app_module._licence_state_provider() is None


class TestServiceUrlFailover:
    """One setting that means "the deployed site, and the dev server I can actually test against".

    Before this, `NIMBUS_LICENCE_URL` held a single value, so testing locally meant editing it and
    remembering to edit it back -- and forgetting is how a shipped build ends up talking to
    ``localhost``. A comma-separated list removes the edit entirely: the deploy starts answering and
    the fallback stops being consulted.
    """

    def test_one_url_behaves_exactly_as_before(self, monkeypatch):
        import licensing

        monkeypatch.setenv("NIMBUS_LICENCE_URL", "https://example.test/")

        assert licensing._service_urls() == ["https://example.test"]

    def test_a_list_is_split_ordered_and_stripped(self, monkeypatch):
        import licensing

        monkeypatch.setenv(
            "NIMBUS_LICENCE_URL", " https://example.test/ , http://localhost:3000 , ")

        assert licensing._service_urls() == ["https://example.test", "http://localhost:3000"]

    def test_an_empty_setting_still_has_a_default(self, monkeypatch):
        """An empty string must not produce an empty candidate list, which would make every call fail
        with no attempt and no explanation."""
        import licensing

        monkeypatch.setenv("NIMBUS_LICENCE_URL", "  ,  ")
        monkeypatch.setattr(licensing, "_baked", lambda _name: "")

        assert licensing._service_urls() == ["https://nimbus.example"]

    def test_an_unreachable_primary_falls_through_to_the_next(self, monkeypatch, mocker):
        """The case this exists for: the deployed site fails DNS, the dev server answers."""
        import httpx

        import licensing

        monkeypatch.setattr(
            licensing, "SERVICE_URLS", ["https://down.test", "http://localhost:3000"])
        answer = mocker.MagicMock(status_code=200)
        answer.json.return_value = {"ok": True}
        calls = []

        def post(url, **kwargs):
            calls.append(url)
            if url.startswith("https://down.test"):
                raise httpx.ConnectError("name resolution failed")
            return answer

        mocker.patch.object(httpx, "post", side_effect=post)

        assert licensing._post("/activate", {}) == {"ok": True}
        assert calls == ["https://down.test/activate", "http://localhost:3000/activate"]

    def test_a_rejection_is_never_retried_against_the_fallback(self, monkeypatch, mocker):
        """The distinction the whole design turns on.

        A 4xx is the service's *answer*. Retrying it elsewhere would spend one wrong password on two
        rate limiters, and would let a fallback overrule a real answer from the primary.
        """
        import httpx

        import licensing

        monkeypatch.setattr(
            licensing, "SERVICE_URLS", ["https://primary.test", "http://localhost:3000"])
        refusal = mocker.MagicMock(status_code=401)
        refusal.json.return_value = {"detail": "That password does not match."}
        post = mocker.patch.object(httpx, "post", return_value=refusal)

        with pytest.raises(licensing.LicenceError, match="does not match"):
            licensing._post("/desktop/login", {})

        assert post.call_count == 1, "a rejection must not be tried against the next service"

    def test_all_unreachable_reports_a_connection_problem(self, monkeypatch, mocker):
        import httpx

        import licensing

        monkeypatch.setattr(licensing, "SERVICE_URLS", ["https://a.test", "https://b.test"])
        mocker.patch.object(httpx, "post", side_effect=httpx.ConnectError("no route"))

        with pytest.raises(licensing.LicenceError, match="could not reach"):
            licensing._post("/activate", {})
