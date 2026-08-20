"""Licence this machine without a licence service. For your own machines only.

    python -m tools.issue_local_licence                 licence this PC for 30 days
    python -m tools.issue_local_licence --days 365      ... for a year
    python -m tools.issue_local_licence --status        what does Nimbus think right now?
    python -m tools.issue_local_licence --clear         forget it again

## Why this exists

The licence gate runs before the hotkey and the mic (§5), so a build whose service is not deployed
yet cannot be opened at all -- not to test the shell, not to test push-to-talk, not to test anything.
That is correct behaviour and a terrible way to spend an afternoon.

This signs the same token `service/app/main.py` would sign, with the same private key, and writes it
into the same two stores `licensing` reads. The app cannot tell the difference, because there is no
difference: it verifies an Ed25519 signature and does not care who made it.

## What it is not

**Not a way to hand licences to testers.** It writes to *this* machine's Credential Manager and
takes no payment, records no seat and leaves nothing to revoke. Testers get keys from the service,
which is the only thing that knows who paid. Use `/admin/manual-licence` for EasyPaisa and bank
transfers.

It also needs the **private** key, so it only runs where that key already is -- your machine and the
deployed service, and nowhere else.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SECRET = Path(os.path.expanduser("~")) / "nimbus-licence-secret.txt"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def looks_like_a_key(candidate: str) -> bool:
    """Whether ``candidate`` decodes to 32 base64url bytes."""
    try:
        return len(base64.urlsafe_b64decode(candidate + "=" * (-len(candidate) % 4))) == 32
    except Exception:
        return False


def read_private_key(secret_file: Path) -> str:
    """From ``NIMBUS_LICENCE_PRIVATE_KEY``, else from the file `node scripts/keygen.mjs` output went into.

    The file is read as UTF-16 when it carries a BOM: PowerShell's ``>`` redirection writes UTF-16 by
    default, which is how the key ends up looking like binary to a naive reader.

    **The environment value is validated rather than trusted.** A documented setup step reads
    ``$env:NIMBUS_LICENCE_PRIVATE_KEY = "<from the secret file>"``, and pasting that line verbatim
    leaves the placeholder in the environment -- where it silently wins over a perfectly good key file
    and fails several frames later inside base64. That happened here, so a value that is not 32 bytes
    is now reported and stepped over instead of used.
    """
    from_env = os.getenv("NIMBUS_LICENCE_PRIVATE_KEY", "").strip()
    if from_env and looks_like_a_key(from_env):
        return from_env
    if from_env:
        print(f"Ignoring NIMBUS_LICENCE_PRIVATE_KEY={from_env[:24]!r}: not a 32-byte key.\n"
              f"Reading {secret_file.name} instead. Clear it with:\n"
              f"  Remove-Item Env:NIMBUS_LICENCE_PRIVATE_KEY\n")

    if not secret_file.is_file():
        raise SystemExit(
            f"No private key. Set NIMBUS_LICENCE_PRIVATE_KEY, or put the output of\n"
            f"`node scripts/keygen.mjs` (in web/) at {secret_file}")
    raw = secret_file.read_bytes()
    encoding = "utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8"
    match = re.search(r"NIMBUS_LICENCE_PRIVATE_KEY=(\S+)",
                      raw.decode(encoding, errors="replace"))
    if not match:
        raise SystemExit(f"{secret_file} does not contain NIMBUS_LICENCE_PRIVATE_KEY=...")
    if not looks_like_a_key(match.group(1)):
        raise SystemExit(f"The key in {secret_file} is not a 32-byte Ed25519 private key.")
    return match.group(1)


def sign(private_b64: str, claims: dict) -> str:
    from cryptography.hazmat.primitives.asymmetric import ed25519

    padding = "=" * (-len(private_b64) % 4)
    private = ed25519.Ed25519PrivateKey.from_private_bytes(
        base64.urlsafe_b64decode(private_b64 + padding))
    payload = json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return f"{_b64(payload)}.{_b64(private.sign(payload))}"


def show_status(licensing) -> int:
    state = licensing.current_state()
    print(f"  activated   {state.activated}")
    print(f"  kind        {state.kind or '(none)'}")
    print(f"  plan        {state.plan or '(none)'}")
    print(f"  expires     {state.expires or '(none)'}")
    print(f"  detail      {state.detail}")
    print(f"  device      {licensing.device_name()} ({licensing.device_id()[:12]}...)")
    return 0 if state.activated else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=30, help="how long the licence lasts")
    parser.add_argument("--email", default="dev@nimbus.example", help="what the Account page shows")
    parser.add_argument("--secret-file", type=Path, default=DEFAULT_SECRET)
    parser.add_argument("--status", action="store_true", help="report and exit")
    parser.add_argument("--clear", action="store_true", help="forget the local licence")
    args = parser.parse_args(argv)

    sys.path.insert(0, str(ROOT))
    import licensing

    if not licensing.LICENCE_PUBLIC_KEY:
        raise SystemExit(
            "This checkout has no public key, so nothing it verifies would be trusted.\n"
            "Run: python -m tools.set_licence_key --public-key <key>")

    if args.status:
        return show_status(licensing)

    if args.clear:
        licensing.sign_out()
        print("Cleared. Nimbus will show the activation gate next time it starts.")
        return 0

    expires = datetime.now(timezone.utc) + timedelta(days=max(1, args.days))
    token = sign(read_private_key(args.secret_file), {
        "kind": "subscription",
        "plan": licensing.PLAN_NAME,
        "email": args.email,
        "expires_at": expires.isoformat(timespec="seconds"),
        "issued_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seats_used": 1,
        "seats_total": 2,
        "device_id": licensing.device_id(),
    })

    # Verified with the *public* half before storing, exactly as the app will. A mismatched pair is
    # the likeliest mistake here, and finding out now beats finding out from a locked-out app.
    licensing.verify_token(token)

    if not licensing._store_blob(licensing.LICENCE_ENTRY, token):
        raise SystemExit("Could not write the licence to the keyring or to disk.")
    licensing._store_blob(licensing.LAST_VALIDATED_ENTRY,
                          datetime.now(timezone.utc).isoformat(timespec="seconds"))
    licensing._clear_blob(licensing.TRIAL_ENTRY)
    # No licence *key* is stored on purpose: without one, `revalidate` returns early instead of
    # asking a service that does not exist about a licence it never issued.
    print(f"Licensed this machine until {expires.date().isoformat()}.\n")
    return show_status(licensing)


if __name__ == "__main__":
    raise SystemExit(main())
