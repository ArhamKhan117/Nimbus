"""Ed25519 keypair generation and token signing for the licence service.

The private key exists in exactly one place: the ``NIMBUS_LICENCE_PRIVATE_KEY`` environment variable
on the deployed service. It is never written to disk by this code, never logged, and never returned
by an endpoint. If it leaks, every issued licence is forgeable and the only remedy is generating a
new pair and shipping a client build with the new public half -- so treat it accordingly.

Run ``python -m app.keys`` to generate a pair.
"""
from __future__ import annotations

import base64
import json
import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519


def _b64(data: bytes) -> str:
    """URL-safe base64 without padding, matching ``licensing._b64decode`` on the client."""
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def generate() -> tuple[str, str]:
    """A fresh ``(private_b64, public_b64)`` pair. Raw 32-byte keys, base64url encoded."""
    private = ed25519.Ed25519PrivateKey.generate()
    private_raw = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_raw = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return _b64(private_raw), _b64(public_raw)


def _load_private() -> ed25519.Ed25519PrivateKey:
    encoded = os.getenv("NIMBUS_LICENCE_PRIVATE_KEY", "")
    if not encoded:
        raise RuntimeError(
            "NIMBUS_LICENCE_PRIVATE_KEY is not set. The service cannot sign licences without it.")
    padding = "=" * (-len(encoded) % 4)
    return ed25519.Ed25519PrivateKey.from_private_bytes(
        base64.urlsafe_b64decode(encoded + padding))


def sign_claims(claims: dict) -> str:
    """Sign ``claims`` into the ``<payload>.<signature>`` token the client expects.

    ``sort_keys`` and a compact separator are not cosmetic: the client verifies the signature over
    the exact bytes it received, so the payload must be reproducible. Sorting also keeps a token
    stable across Python versions, which matters when comparing what was issued to what was stored.

    Deliberately **not a JWT**. A JWT carries an algorithm field, and algorithm negotiation is where
    JWT libraries get broken -- "alg": "none" being the classic. One algorithm, no header, nothing
    to negotiate.
    """
    payload = json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = _load_private().sign(payload)
    return f"{_b64(payload)}.{_b64(signature)}"


if __name__ == "__main__":
    private_b64, public_b64 = generate()
    print("Generated an Ed25519 licence keypair.\n")
    print("Service secret -- set this on the server and nowhere else:")
    print(f"  NIMBUS_LICENCE_PRIVATE_KEY={private_b64}\n")
    print("Client build -- bake this into the desktop app:")
    print(f"  NIMBUS_LICENCE_PUBLIC_KEY={public_b64}\n")
    print("The private half is not saved anywhere by this script. Store it now.")
