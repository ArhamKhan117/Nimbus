"""The licence service (SHELL_AND_CHAT.md §5, server half).

What is worth pinning here is not "does /activate return 200". It is the set of behaviours that cost
money or trust when they are wrong:

* every token the service hands out verifies against the public key the client ships;
* a device gets exactly one trial, and reinstalling during it returns the remaining days;
* the seat limit holds, but a device already on the licence is never locked out by it;
* ``/refresh`` distinguishes "genuinely refused" (4xx) from "our problem" (5xx), because the client
  clears the licence on one and keeps it on the other;
* the webhook refuses unsigned requests -- without that it is an open licence dispenser;
* one purchase yields one key, however many events Stripe sends for it.
"""
from __future__ import annotations

from datetime import timedelta

from conftest import claims_of


class TestTrial:
    def test_a_new_device_gets_a_signed_seven_day_trial(self, client):
        response = client.post("/trial", json={"device_id": "device-aaaa1111",
                                              "device_name": "Lab PC"})
        assert response.status_code == 200, response.text

        claims = claims_of(response.json()["token"], client.public_key)
        assert claims["kind"] == "trial"
        from app import db

        remaining = db._parse_stamp(claims["expires_at"]) - db.now()
        assert timedelta(days=6, hours=23) < remaining <= timedelta(days=7)

    def test_reinstalling_during_the_trial_returns_the_same_expiry(self, client):
        """Not a refusal. A user who reinstalls mid-trial has not used their trial up."""
        first = client.post("/trial", json={"device_id": "device-bbbb2222"}).json()["token"]
        second = client.post("/trial", json={"device_id": "device-bbbb2222"}).json()["token"]

        assert (claims_of(first, client.public_key)["expires_at"]
                == claims_of(second, client.public_key)["expires_at"])

    def test_a_device_whose_trial_elapsed_is_refused(self, client):
        from app import db

        with db.connect() as conn:
            conn.execute(
                "INSERT INTO trials (device_id, device_name, started_at, expires_at) "
                "VALUES (?, '', ?, ?)",
                ("device-cccc3333",
                 (db.now() - timedelta(days=9)).isoformat(timespec="seconds"),
                 (db.now() - timedelta(days=2)).isoformat(timespec="seconds")))

        response = client.post("/trial", json={"device_id": "device-cccc3333"})
        assert response.status_code == 403
        assert "trial" in response.json()["detail"].lower()

    def test_a_new_email_does_not_buy_a_second_trial(self, client):
        """The trial is keyed on the device and nothing else, which is the whole anti-abuse design."""
        client.post("/trial", json={"device_id": "device-dddd4444"})
        from app import db

        with db.connect() as conn:
            rows = conn.execute("SELECT COUNT(*) AS n FROM trials").fetchone()["n"]
        assert rows == 1
        # There is no email column to vary, so a second signup cannot produce a second row.
        with db.connect() as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(trials)")}
        assert "email" not in columns and "customer_id" not in columns


class TestActivation:
    def test_a_valid_key_returns_a_verifiable_subscription_token(self, client, licence):
        key = licence(email="Buyer@Example.com")
        response = client.post("/activate", json={"key": key, "device_id": "device-eeee5555",
                                                 "device_name": "Desk"})
        assert response.status_code == 200, response.text

        claims = claims_of(response.json()["token"], client.public_key)
        assert claims["kind"] == "subscription"
        assert claims["email"] == "buyer@example.com"     # normalised on the way in
        assert claims["seats_total"] == 2
        assert claims["seats_used"] == 1

    def test_an_unknown_key_is_a_404_not_a_500(self, client):
        response = client.post("/activate", json={"key": "NIMBUS-ZZZZ-ZZZZ-ZZZZ",
                                                 "device_id": "device-ffff6666"})
        assert response.status_code == 404

    def test_a_lapsed_subscription_is_refused(self, client, licence):
        from app import db

        key = licence()
        db.set_licence_status(key, "lapsed")
        response = client.post("/activate", json={"key": key, "device_id": "device-gggg7777"})
        assert response.status_code == 403

    def test_the_seat_limit_holds_and_names_the_number(self, client, licence):
        key = licence()
        for index in range(2):
            assert client.post("/activate", json={"key": key,
                                                  "device_id": f"device-seat{index}000"}
                               ).status_code == 200

        response = client.post("/activate", json={"key": key, "device_id": "device-hhhh8888"})
        assert response.status_code == 403
        detail = response.json()["detail"]
        assert "2 devices" in detail and "Deactivate" in detail

    def test_a_device_already_on_the_licence_is_let_in_at_the_limit(self, client, licence):
        """Reinstalling on a machine that already has a seat must not be refused as a third device.

        With two seats this matters more than it did with three: a customer at the limit is the normal
        case, not an edge case, so "already known" has to beat "no seats left" every time."""
        key = licence()
        for index in range(2):
            client.post("/activate", json={"key": key, "device_id": f"device-seat{index}000"})

        again = client.post("/activate", json={"key": key, "device_id": "device-seat1000"})
        assert again.status_code == 200
        assert claims_of(again.json()["token"], client.public_key)["seats_used"] == 2

    def test_deactivating_frees_the_seat(self, client, licence):
        key = licence()
        for index in range(2):
            client.post("/activate", json={"key": key, "device_id": f"device-seat{index}000"})

        released = client.post("/deactivate", json={"key": key, "device_id": "device-seat1000"})
        assert released.status_code == 200
        assert released.json()["seats_used"] == 1
        assert client.post("/activate", json={"key": key,
                                              "device_id": "device-new00000"}).status_code == 200

    def test_the_token_never_outlives_the_paid_period(self, client, licence):
        """A 24-month licence must not mint a 24-month token: cancellation has to be able to bite."""
        from app import db

        key = licence(months=24)
        response = client.post("/activate", json={"key": key, "device_id": "device-iiii9999"})
        claims = claims_of(response.json()["token"], client.public_key)
        assert db._parse_stamp(claims["expires_at"]) <= db.now() + timedelta(
            days=main_ttl() + 1)


def main_ttl() -> int:
    from app import main

    return main.TOKEN_TTL_DAYS


class TestRefresh:
    def test_refresh_returns_a_fresh_token(self, client, licence):
        key = licence()
        client.post("/activate", json={"key": key, "device_id": "device-jjjj0000"})
        response = client.post("/refresh", json={"key": key, "device_id": "device-jjjj0000"})
        assert response.status_code == 200
        assert claims_of(response.json()["token"], client.public_key)["kind"] == "subscription"

    def test_a_database_failure_is_a_503_so_the_client_keeps_its_licence(self, client, licence,
                                                                        monkeypatch):
        """The single most important status code in the service.

        The client clears its cached licence on 4xx and keeps it on 5xx. Returning 403 for an
        infrastructure fault would turn a blip on our side into a lockout on the customer's.
        """
        from app import db

        key = licence()
        client.post("/activate", json={"key": key, "device_id": "device-kkkk1111"})

        def explode(_key):
            raise db.sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(db, "licence_by_key", explode)
        response = client.post("/refresh", json={"key": key, "device_id": "device-kkkk1111"})
        assert response.status_code == 503

    def test_a_revoked_licence_is_a_403_so_the_client_clears_it(self, client, licence):
        from app import db

        key = licence()
        client.post("/activate", json={"key": key, "device_id": "device-llll2222"})
        db.set_licence_status(key, "cancelled")
        assert client.post("/refresh", json={"key": key,
                                             "device_id": "device-llll2222"}).status_code == 403


class TestStripeWebhook:
    def test_an_unsigned_webhook_is_refused(self, client, monkeypatch):
        """Without this the endpoint is an open "give me a licence" API."""
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
        response = client.post("/stripe/webhook",
                               json={"type": "checkout.session.completed", "data": {"object": {}}})
        assert response.status_code == 400

    def test_a_webhook_with_no_secret_configured_fails_closed(self, client, monkeypatch):
        monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
        response = client.post("/stripe/webhook", json={"type": "invoice.paid"})
        assert response.status_code == 500

    def test_two_events_for_one_purchase_yield_one_key(self, client):
        """Stripe sends both `checkout.session.completed` and `customer.subscription.created`."""
        from app import db

        customer_id = db.upsert_customer("double@example.com")
        first, created_first = db.ensure_licence(customer_id, db.now() + timedelta(days=31))
        second, created_second = db.ensure_licence(customer_id, db.now() + timedelta(days=31))

        assert first == second
        assert created_first is True and created_second is False


class TestAdminAndSite:
    def test_the_manual_licence_endpoint_needs_the_token(self, client):
        """This mints licences, so it is the second most sensitive endpoint after the webhook."""
        assert client.post("/admin/manual-licence",
                           json={"email": "x@y.z"}).status_code == 401
        assert client.post("/admin/manual-licence", json={"email": "x@y.z"},
                           headers={"Authorization": "Bearer wrong"}).status_code == 401

    def test_a_manual_licence_activates_like_any_other(self, client, licence):
        """The EasyPaisa path must produce an identical licence, not a lesser one."""
        key = licence(email="easypaisa@example.com")
        claims = claims_of(
            client.post("/activate", json={"key": key,
                                           "device_id": "device-mmmm3333"}).json()["token"],
            client.public_key)
        assert claims["kind"] == "subscription"
        assert claims["seats_total"] == 2

    def test_download_redirects_rather_than_proxying_the_installer(self, client):
        response = client.get("/download", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"].endswith(".exe")

    def test_the_landing_page_and_success_page_are_served(self, client):
        assert client.get("/").status_code == 200
        assert "Nimbus" in client.get("/").text
        assert client.get("/success").status_code == 200

    def test_the_logo_the_pages_reference_is_actually_served(self, client):
        """Both pages hard-code `/static/nimbus_mark.png`. A 404 there is the first impression."""
        assert client.get("/static/nimbus_mark.png").status_code == 200
        for page in ("/", "/success"):
            assert "/static/nimbus_mark.png" in client.get(page).text

    def test_the_licence_key_endpoint_refuses_a_request_with_no_session(self, client):
        """No session id means no authorisation. There is nothing else to check against."""
        assert client.get("/licence-key").status_code == 404

    def test_buy_falls_back_to_pricing_when_stripe_is_not_configured(self, client):
        """An unconfigured button must go somewhere honest, not 500."""
        response = client.get("/buy", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == "/#pricing"

    def test_healthz(self, client):
        assert client.get("/healthz").json() == {"ok": True}


class TestKeyGeneration:
    def test_keys_avoid_characters_that_get_misread(self, client):
        from app import db

        for _ in range(200):
            key = db.new_licence_key()
            assert key.startswith("NIMBUS-")
            assert not (set("IO01") & set(key[7:]))

    def test_keys_do_not_repeat(self, client):
        from app import db

        assert len({db.new_licence_key() for _ in range(500)}) == 500
