"""Fixtures for the licence service suite.

Every test runs against a **throwaway keypair generated in the fixture** and a SQLite file in
``tmp_path``. Nothing here touches a real key, a real database or Stripe.
"""
from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def signing_key(monkeypatch):
    """Install a throwaway private key in the environment and return its public half (base64url)."""
    from app import keys

    private_b64, public_b64 = keys.generate()
    monkeypatch.setenv("NIMBUS_LICENCE_PRIVATE_KEY", private_b64)
    return public_b64


@pytest.fixture
def client(tmp_path, monkeypatch, signing_key):
    """A ``TestClient`` on an empty database, with the admin endpoint enabled."""
    from app import db, main

    monkeypatch.setattr(db, "SQLITE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(main, "ADMIN_TOKEN", "test-admin-token")
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    db.ensure_schema()
    with TestClient(main.app) as test_client:
        test_client.public_key = signing_key
        yield test_client


def claims_of(token: str, public_key_b64: str) -> dict:
    """Verify a token the way the desktop client does, then return its claims.

    Verifying rather than merely decoding is the point: these tests assert that what the service
    hands out is something ``licensing.verify_token`` would accept. A test that only read the payload
    would pass against a service that had stopped signing correctly.
    """
    import json

    from cryptography.hazmat.primitives.asymmetric import ed25519

    def unpad(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    payload_b64, signature_b64 = token.split(".")
    public = ed25519.Ed25519PublicKey.from_public_bytes(unpad(public_key_b64))
    public.verify(unpad(signature_b64), unpad(payload_b64))
    return json.loads(unpad(payload_b64))


@pytest.fixture
def licence(client):
    """A factory for an active licence: ``licence(email="a@b.c", months=1) -> key``."""
    def make(email: str = "buyer@example.com", months: int = 1) -> str:
        response = client.post(
            "/admin/manual-licence",
            json={"email": email, "months": months},
            headers={"Authorization": "Bearer test-admin-token"})
        assert response.status_code == 200, response.text
        return response.json()["key"]

    return make
