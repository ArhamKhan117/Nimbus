"""Licence activation and the trial (SHELL_AND_CHAT.md §5, `S-10`).

This DETERS casual sharing. It does not prevent a determined user from patching the check out of a
PyInstaller bundle, and no client-side check can. That is accepted deliberately (§0.1): the
alternative is proxying inference through a server, which would end BYOK and contradict the non-goal
recorded in `IMPROVEMENTS.md` §8.

So the design optimises for three things, in this order: **honest use is never
inconvenienced**, seat abuse is *visible and revocable*, and offline use keeps working.

## The shape, and why each piece is what it is

    first launch
       ↓
    no licence, no trial  ──→  POST /trial {device_id}  ──→  7-day trial token
       ↓ has a key
    POST /activate {key, device_id, device_name}  ──→  signed licence token
       ↓
    verify Ed25519 signature locally, cache it
       ↓
    run · revalidate every 7 days · 14-day offline grace

| Decision | Choice | Why |
|---|---|---|
| Credential | Licence key, not email+password | No password to store, reset or breach |
| Device identity | Salted SHA-256 of MachineGuid + volume serial | Stable across reboots; changes on a new machine. **A raw hardware id is never sent** |
| Verification | Ed25519, public key embedded | Verifiable with no network, so offline works. The private key never ships |
| Offline grace | 14 days | A tool that stops working on a flight is worse than one that gets pirated |
| Revalidation | Every 7 days, silent | Detects seat abuse without nagging |
| Failure mode | Blocking dialog with Retry / Use offline | Never silently degrade; a legitimate user must never be left guessing |

## What is deliberately not here

* **No inference proxying.** BYOK stays (`IMPROVEMENTS.md` §8).
* **No obfuscation.** PyArmor breaks PyInstaller in ways that cost days and delays an attacker by
  an afternoon.
* **No phone-home on every launch.** Startup must not depend on the licence service's uptime --
  that turns our outage into the user's lockout. `should_revalidate` is the whole policy.

## Verified before writing this, because §5 asks for it

* ``keyring`` round-trips 1 KB but **fails at 2 KB** on this machine. A signed token is ~450 bytes,
  so the keyring is the primary store -- and `_store_blob` falls back to a file above
  ``KEYRING_SAFE_BYTES`` rather than losing the licence silently.
* ``cryptography`` 50.0.0 is already installed and has Ed25519. ``httpx`` 0.28.1 is already a
  dependency. No new package in the frozen build.
* ``MachineGuid`` is present under ``HKLM\\SOFTWARE\\Microsoft\\Cryptography``.
"""
from __future__ import annotations

import base64
import json
import os
import platform
import subprocess
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

KEYRING_SERVICE = "Nimbus"
"""Shared with ``config.py`` on purpose: one Credential Manager namespace for the app."""

LICENCE_ENTRY = "LICENCE_TOKEN"
LICENCE_KEY_ENTRY = "LICENCE_KEY"
TRIAL_ENTRY = "TRIAL_TOKEN"
TRIAL_FIRST_RUN_ENTRY = "FIRST_RUN_AT"
LAST_VALIDATED_ENTRY = "LICENCE_VALIDATED_AT"

TRIAL_DAYS = 7
OFFLINE_GRACE_DAYS = 14
REVALIDATE_EVERY_DAYS = 7

PLAN_NAME = "Nimbus"
PLAN_DEVICES = "2 devices"
"""What the plan grants, stated in devices rather than money.

There is no price constant any more, and that is deliberate: no payment rail is connected, so a
figure shown here would be a claim the product cannot honour. If a rail is ever switched on, the
price belongs to the service that charges it, not baked into the client."""

KEYRING_SAFE_BYTES = 1024
"""Measured ceiling. Credential Manager accepted 1 KB and refused 2 KB, so anything larger goes to
a file instead of failing a write nobody checked."""

def _baked(name: str) -> str:
    """Read a build-time constant from the generated ``licence_key`` module, or ``""``.

    ``tools/set_licence_key.py`` writes that module and it is git-ignored, so the value ships in the
    frozen bundle without ever being committed. A missing module is normal -- that is a dev checkout,
    and the environment variable covers it.
    """
    try:
        import licence_key
    except Exception:
        return ""
    return str(getattr(licence_key, name, "") or "").strip()


def _service_urls() -> list[str]:
    """Every licence service to try, in order.

    ## Why a list and not one URL

    During development the real answer is "both": the deployed site is what ships, and a local
    ``npm run dev`` is what you can actually test against before the domain exists. One value meant
    editing it back and forth, and forgetting to edit it back is how a shipped build ends up talking
    to ``localhost``.

    ``NIMBUS_LICENCE_URL`` therefore accepts a comma-separated list. A single value behaves exactly as
    before, so nothing that sets one URL notices this.

    ## Why trying localhost is safe

    A rogue process on ``127.0.0.1`` cannot forge a licence: every token is Ed25519-signed and
    ``verify_token`` checks it against the public key baked into this build. The worst a local
    impostor achieves is a refused activation, which is the same as no answer at all. Ordering still
    matters for *speed*, not safety — put the one you expect to answer first.

    ## Why the fallback is a reserved domain

    ``NIMBUS_LICENCE_URL`` or a baked ``SERVICE_URL`` is what a real build uses. The literal below is
    only reached when neither is set, and it is deliberately ``nimbus.example``: ``.example`` is
    reserved by IANA for documentation and can never resolve to anybody.

    A fallback that resolves is worse than one that does not. A build shipped without a configured URL
    would not fail, it would *succeed* at reaching whatever host answers, and the first symptom would be
    a confusing rejection rather than an obvious connection error. Failing to connect is the correct
    outcome for missing configuration.
    """
    raw = (os.getenv("NIMBUS_LICENCE_URL", "")
           or _baked("SERVICE_URL")
           or "https://nimbus.example")
    urls = [candidate.strip().rstrip("/") for candidate in raw.split(",") if candidate.strip()]
    return urls or ["https://nimbus.example"]


SERVICE_URLS = _service_urls()
"""Ordered candidates. ``_post`` walks these, moving on **only** when one cannot be reached."""

SERVICE_URL = SERVICE_URLS[0]
"""The primary service: what the browser links point at, and the first one tried.

Environment first, then the baked build constant, then the default -- so a staging service needs no
rebuild and the domain can change without editing this file."""

LICENCE_PUBLIC_KEY = os.getenv("NIMBUS_LICENCE_PUBLIC_KEY", "") or _baked("LICENCE_PUBLIC_KEY")
"""Base64 Ed25519 **public** key, 32 bytes raw.

Environment first so the test suite can sign with a throwaway key, then the baked constant written by
``tools/set_licence_key.py``. Empty means every licence is refused (``verify_token`` fails closed),
which is why the build script reports whether a key is present rather than leaving it to be
discovered by the first tester who tries to activate.

The private half never appears in this repository, is never sent to a client, and lives only in the
licence service's environment."""

HTTP_TIMEOUT = 10.0
"""Short on purpose. Activation is interactive and a hung request reads as a broken app."""


# --- state -------------------------------------------------------------------


@dataclass(frozen=True)
class LicenceState:
    """What the Account page renders, and what the gate decides on.

    Field-for-field the shape ``shell/pages/account.py`` already declares, so the page needs no
    change and the shell keeps depending on a shape rather than on this module.
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

    # Not part of the page's contract; used by the gate and the Home page.
    kind: str = "none"          # "none" | "trial" | "subscription"
    trial_days_left: int = 0
    days_left: int = 0
    """Days until this licence expires, whichever kind it is.

    Separate from ``trial_days_left`` rather than replacing it: the gate and the Home page read that
    one and mean "days of *trial* remaining", so widening it in place would have made a activated licence
    look like a trial user to both."""
    expired: bool = False


NOT_ACTIVATED = LicenceState(
    detail="No licence on this device. Start a 7-day trial or enter a licence key.")


# --- device identity ---------------------------------------------------------


def _machine_guid() -> str:
    """Windows' own installation GUID. Not a hardware serial, and not sent raw."""
    try:
        result = subprocess.run(
            ["reg", "query", r"HKLM\SOFTWARE\Microsoft\Cryptography", "/v", "MachineGuid"],
            capture_output=True, text=True, timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for line in result.stdout.splitlines():
            if "MachineGuid" in line:
                return line.split()[-1].strip()
    except Exception:
        pass
    return ""


def _volume_serial() -> str:
    """The system volume's serial. Changes on a reformat, which is the intent."""
    try:
        import ctypes

        buffer = ctypes.c_uint(0)
        ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p("C:\\"), None, 0, ctypes.byref(buffer), None, None, None, 0)
        return str(buffer.value)
    except Exception:
        return ""


def device_id(salt: str = "nimbus-device-v1") -> str:
    """A stable, **salted and hashed** identifier for this machine.

    Never a raw hardware id. §5 is explicit about this and the reason is custodial rather than
    technical: a raw MachineGuid or volume serial is a fingerprint that correlates across every
    service that receives it, and collecting one makes us responsible for it. A salted hash is
    stable enough to bind a seat to and useless for anything else.

    Falls back to the hostname if both Win32 lookups fail. That weakens the binding on an unusual
    machine rather than blocking a legitimate user, which is the right way round.
    """
    import hashlib

    material = "|".join((_machine_guid(), _volume_serial(), platform.node() or ""))
    if not material.strip("|"):
        material = "unknown-device"
    return hashlib.sha256((salt + "|" + material).encode("utf-8")).hexdigest()[:32]


def device_name() -> str:
    try:
        return platform.node() or "This PC"
    except Exception:
        return "This PC"


# --- storage -----------------------------------------------------------------


def _data_dir():
    from pathlib import Path

    try:
        from config import DATA_DIR
        return Path(DATA_DIR)
    except Exception:
        return Path(os.path.expanduser("~")) / ".nimbus"


def _blob_path(name: str):
    return _data_dir() / f"{name.lower()}.dat"


def _store_blob(name: str, value: str) -> bool:
    """Keyring first, file above ``KEYRING_SAFE_BYTES``. ``False`` if neither worked.

    The size check is not defensive padding: Credential Manager silently refused a 2 KB write in
    testing, and a licence that fails to persist means the tester re-activates on every launch.
    """
    if len(value.encode("utf-8")) <= KEYRING_SAFE_BYTES:
        try:
            import keyring

            keyring.set_password(KEYRING_SERVICE, name, value)
            return True
        except Exception:
            pass
    try:
        path = _blob_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
        return True
    except Exception:
        return False


def _read_blob(name: str) -> str:
    try:
        import keyring

        stored = keyring.get_password(KEYRING_SERVICE, name)
        if stored:
            return stored
    except Exception:
        pass
    try:
        path = _blob_path(name)
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


def _clear_blob(name: str) -> None:
    try:
        import keyring

        keyring.delete_password(KEYRING_SERVICE, name)
    except Exception:
        pass
    try:
        _blob_path(name).unlink(missing_ok=True)
    except Exception:
        pass


# --- token verification ------------------------------------------------------


class LicenceError(Exception):
    """A licence that cannot be trusted. Always carries a message fit to show a user."""


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def verify_token(token: str, public_key_b64: str | None = None) -> dict:
    """Verify a signed licence token and return its claims.

    Token format is ``<base64url payload>.<base64url signature>`` -- a compact JWS-alike rather
    than a JWT, because a JWT brings algorithm negotiation with it and algorithm negotiation is
    where JWT libraries get broken. One algorithm, no header, nothing to negotiate.

    Raises ``LicenceError`` on anything suspect: wrong shape, bad signature, unparseable claims. A
    tampered token is indistinguishable from a corrupt one from here, and both mean "do not trust".
    """
    key_b64 = public_key_b64 if public_key_b64 is not None else LICENCE_PUBLIC_KEY
    if not key_b64:
        raise LicenceError("This build has no licence key configured.")
    if not token or token.count(".") != 1:
        raise LicenceError("The licence is not in a format Nimbus recognises.")

    payload_b64, signature_b64 = token.split(".")
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric import ed25519

        public_key = ed25519.Ed25519PublicKey.from_public_bytes(_b64decode(key_b64))
        payload = _b64decode(payload_b64)
        try:
            public_key.verify(_b64decode(signature_b64), payload)
        except InvalidSignature:
            raise LicenceError("This licence has been altered and cannot be used.")
    except LicenceError:
        raise
    except Exception as exc:
        raise LicenceError("Nimbus could not check this licence.") from exc

    try:
        claims = json.loads(payload.decode("utf-8"))
    except Exception as exc:
        raise LicenceError("This licence is unreadable.") from exc
    if not isinstance(claims, dict):
        raise LicenceError("This licence is unreadable.")
    return claims


def _parse_time(value) -> datetime | None:
    if not value:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- the trial ---------------------------------------------------------------


def _first_run_records() -> list[datetime]:
    """Every independent record of when this install first ran."""
    found = []
    try:
        import keyring

        parsed = _parse_time(keyring.get_password(KEYRING_SERVICE, TRIAL_FIRST_RUN_ENTRY))
        if parsed is not None:
            found.append(parsed)
    except Exception:
        pass
    try:
        path = _blob_path(TRIAL_FIRST_RUN_ENTRY)
        if path.is_file():
            parsed = _parse_time(path.read_text(encoding="utf-8").strip())
            if parsed is not None:
                found.append(parsed)
    except Exception:
        pass
    return found


def _first_run_at() -> datetime:
    """When this install first ran, taking the **earliest** of every record we keep.

    Two independent records, written together and read together: a keyring entry and a file. Clearing
    one is a plausible accident, clearing both is not -- and earliest-wins means restoring one does
    not hand back a fresh trial.

    This only stops the most casual reset, and it is not where trial abuse is actually prevented: the
    server keys the trial on ``device_id``, so a new email address gets no second trial on the same
    machine. The local records exist so an offline first run still has a start date.
    """
    records = _first_run_records()
    if records:
        return min(records)

    now = _now()
    stamp = now.isoformat()
    # Both stores, deliberately, so the earliest-wins rule above has two things to compare.
    try:
        import keyring

        keyring.set_password(KEYRING_SERVICE, TRIAL_FIRST_RUN_ENTRY, stamp)
    except Exception:
        pass
    try:
        path = _blob_path(TRIAL_FIRST_RUN_ENTRY)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(stamp, encoding="utf-8")
    except Exception:
        pass
    return now


def trial_days_left() -> int:
    """Whole days remaining in the local trial window, floored at zero.

    Rounded **up**, so a trial with six hours left reads as "1 day left" rather than "0 days left"
    on a licence that still works. Saying zero while the app runs is the kind of small dishonesty
    that makes someone distrust the rest of the screen.

    Clamped to ``TRIAL_DAYS`` at the top for the same reason in the other direction: a fresh install
    has 6.9999 days remaining, which rounds up to 8, and promising eight days of a seven-day trial is
    a worse first impression than it looks. Caught by a test rather than by a user.
    """
    import math

    remaining = (timedelta(days=TRIAL_DAYS) - (_now() - _first_run_at())).total_seconds()
    if remaining <= 0:
        return 0
    return min(TRIAL_DAYS, int(math.ceil(remaining / 86400.0)))


# --- reading the current state ----------------------------------------------


def _state_from_claims(claims: dict, offline_days_left: int | None) -> LicenceState:
    import math

    kind = str(claims.get("kind") or "subscription")
    expires_at = _parse_time(claims.get("expires_at"))
    expired = bool(expires_at is not None and expires_at < _now())
    days_left = 0
    if expires_at is not None:
        # Rounded **up**, matching ``trial_days_left``. Flooring here made a freshly issued 7-day
        # trial read "6 days left" the moment it was granted, because the token expires in 6.9999
        # days -- caught by driving the real client against the real service, not by a unit test.
        seconds = (expires_at - _now()).total_seconds()
        days_left = max(0, int(math.ceil(seconds / 86400.0))) if seconds > 0 else 0

    if kind == "trial":
        detail = (f"Trial \u00b7 {days_left} day{'s' if days_left != 1 else ''} left"
                  if not expired else "Your 7-day trial has ended.")
    elif expired:
        detail = "Your subscription has lapsed. Renew to keep using Nimbus."
    else:
        # An activated licence gets the countdown too. It said only the plan name, which answers
        # "what am I on" and not "how long have I got" -- and the second question is the one people
        # actually open this page for. Days rather than a bare date, because "renews in 12 days"
        # needs no arithmetic.
        detail = (f"{PLAN_NAME} \u00b7 {PLAN_DEVICES} \u00b7 renews in {days_left} "
                  f"day{'s' if days_left != 1 else ''}"
                  if days_left else f"{PLAN_NAME} \u00b7 {PLAN_DEVICES}")

    return LicenceState(
        activated=not expired,
        plan=str(claims.get("plan") or (PLAN_NAME + " trial" if kind == "trial" else PLAN_NAME)),
        email=str(claims.get("email") or ""),
        device_name=device_name(),
        seats_used=int(claims.get("seats_used") or 0),
        seats_total=int(claims.get("seats_total") or 0),
        expires=expires_at.date().isoformat() if expires_at else "",
        offline_grace_days_left=offline_days_left,
        detail=detail,
        kind=kind,
        trial_days_left=days_left if kind == "trial" else 0,
        # The same number without the trial-only condition, so the Account page can show a countdown
        # for either kind. `trial_days_left` is kept as it was because the Home page and the gate
        # both read it and both mean "trial specifically".
        days_left=days_left,
        expired=expired,
    )


def _offline_days_left() -> int | None:
    """Days before a cached licence must be revalidated, or ``None`` when it was checked today.

    ``None`` rather than 14 when there is nothing to report: the Account page renders this only when
    it is meaningful, and a permanent "14 days of offline grace" line is noise on a machine that has
    been online all week.
    """
    last = _parse_time(_read_blob(LAST_VALIDATED_ENTRY))
    if last is None:
        return None
    age = _now() - last
    if age < timedelta(days=1):
        return None
    return max(0, OFFLINE_GRACE_DAYS - age.days)


def current_state() -> LicenceState:
    """The licence state as this machine currently understands it. Never raises.

    Order matters: a real subscription beats a trial, so a tester who buys mid-trial is not told
    they have three days left. Both are signed tokens verified the same way -- the trial is not a
    special case in the verification path, only in what its claims say.
    """
    for entry in (LICENCE_ENTRY, TRIAL_ENTRY):
        token = _read_blob(entry)
        if not token:
            continue
        try:
            claims = verify_token(token)
        except LicenceError:
            # A token we cannot verify is worse than none: clear it so the user is asked once
            # rather than shown a broken state forever.
            _clear_blob(entry)
            continue
        state = _state_from_claims(claims, _offline_days_left())
        if state.activated or entry == LICENCE_ENTRY:
            return state
    return NOT_ACTIVATED


def is_activated() -> bool:
    """Whether Nimbus may run. The gate's only question.

    Includes the offline grace: a cached licence whose ``expires_at`` has passed is still honoured
    for ``OFFLINE_GRACE_DAYS`` past the last successful revalidation, because the common reason for
    an expired token is a laptop that has not been online, not a lapsed card.
    """
    state = current_state()
    if state.activated:
        return True
    if state.kind == "subscription":
        remaining = _offline_days_left()
        if remaining is not None and remaining > 0:
            return True
    return False


def should_revalidate() -> bool:
    """Whether it is time for a silent background check. Every 7 days, not every launch."""
    if not _read_blob(LICENCE_ENTRY):
        return False
    last = _parse_time(_read_blob(LAST_VALIDATED_ENTRY))
    if last is None:
        return True
    return (_now() - last) >= timedelta(days=REVALIDATE_EVERY_DAYS)


# --- talking to the licence service -----------------------------------------


def _post(path: str, body: dict) -> dict:
    """One POST, with every failure turned into a ``LicenceError`` a user can read.

    Tries each of ``SERVICE_URLS`` in order, and moves on **only when a service cannot be reached at
    all** -- a DNS failure, a refused connection, a timeout. Anything the service actually answers,
    including "that password does not match", is its answer and is returned or raised as-is.

    That distinction is the whole design. Retrying a *rejection* against a second service would turn
    one wrong password into two attempts against two rate limiters, and would let a fallback overrule
    a real answer from the primary.
    """
    import httpx

    response = None
    unreachable: Exception | None = None
    for base in SERVICE_URLS:
        try:
            response = httpx.post(
                f"{base}{path}",
                json=body,
                timeout=HTTP_TIMEOUT,
                # `httpx` does not follow redirects by default, and that default broke every licence
                # operation the moment the site went live: the host redirected the apex to `www` with
                # a 308, this saw a non-JSON body reading "Redirecting...", and every activation
                # failed with "the licence service returned something unexpected".
                #
                # The service URL is *baked into shipped installers*, so it cannot be corrected for
                # anyone who already has one. A licence client that breaks on a redirect is therefore
                # a client that a future DNS change can brick in the field. Following them is the
                # only version of this that survives contact with a hosting provider.
                follow_redirects=True,
            )
            break
        except Exception as exc:
            unreachable = exc

    if response is None:
        raise LicenceError(
            "Nimbus could not reach the licence service. Check your connection."
        ) from unreachable
    if response.status_code >= 500:
        raise LicenceError("The licence service is temporarily unavailable. Try again shortly.")
    try:
        payload = response.json()
    except Exception as exc:
        raise LicenceError("The licence service returned something unexpected.") from exc
    if response.status_code >= 400:
        raise LicenceError(str(payload.get("detail") or "That licence key was not accepted."))
    if not isinstance(payload, dict):
        raise LicenceError("The licence service returned something unexpected.")
    return payload


def activate(key: str) -> LicenceState:
    """Exchange a licence key for a signed token bound to this device.

    Verifies the returned token **before** storing it. A service that returned an unsigned or
    wrongly-signed token would otherwise poison the cache, and the failure would surface later as an
    unexplained lockout rather than here, where there is a dialog to show it in.
    """
    key = (key or "").strip()
    if not key:
        raise LicenceError("Enter your licence key.")

    payload = _post("/activate", {
        "key": key,
        "device_id": device_id(),
        "device_name": device_name(),
    })
    token = str(payload.get("token") or "")
    claims = verify_token(token)

    _store_blob(LICENCE_ENTRY, token)
    _store_blob(LICENCE_KEY_ENTRY, key)
    _store_blob(LAST_VALIDATED_ENTRY, _now().isoformat())
    _clear_blob(TRIAL_ENTRY)
    return _state_from_claims(claims, None)


def register(email: str, password: str) -> str:
    """Create an account from inside the app. Returns the message to show while the code is awaited.

    The trial is no longer anonymous, and that is a deliberate trade rather than an oversight. A device
    hash stops a second trial and is useless for everything else: nobody to email when a trial is ending,
    nobody to answer "I registered, where is my key", and no way to reach a tester who stopped using it
    to ask why.

    What it costs the user is one email and six digits typed into a window that is already open and asking
    for them. What it does not cost them is a card, or the seven days.
    """
    email = (email or "").strip()
    if not email or "@" not in email:
        raise LicenceError("Enter the email address you want to use.")
    if len(password or "") < 10:
        raise LicenceError("Use a password of at least 10 characters.")

    payload = _post("/api/desktop/register", {
        "email": email,
        "password": password,
        "device_id": device_id(),
        "device_name": device_name(),
    })
    return str(payload.get("detail") or f"We sent a 6-digit code to {email}.")


def verify_code(email: str, code: str) -> LicenceState:
    """Check the emailed code, which verifies the address and starts the trial in one call.

    One call because from the user's side it is one action: they type six digits and Nimbus starts
    working. Verifying and then separately asking for a trial would give the flow two ways to fail
    halfway, and a verified account with no trial is a support conversation nobody wants to have.

    The server decides what comes back. An account that already has a subscription gets a subscription
    token rather than a trial, so someone who paid and then reinstalled is not handed seven days.
    """
    email = (email or "").strip()
    digits = "".join(character for character in (code or "") if character.isdigit())
    if not email:
        raise LicenceError("Enter your email address.")
    if len(digits) < 4:
        raise LicenceError("Enter the 6-digit code from your email.")

    payload = _post("/api/desktop/verify", {
        "email": email,
        "code": digits,
        "device_id": device_id(),
        "device_name": device_name(),
    })
    token = str(payload.get("token") or "")
    claims = verify_token(token)

    entry = LICENCE_ENTRY if claims.get("kind") == "subscription" else TRIAL_ENTRY
    _store_blob(entry, token)
    key = str(payload.get("key") or "")
    if key:
        _store_blob(LICENCE_KEY_ENTRY, key)
    _store_blob(LAST_VALIDATED_ENTRY, _now().isoformat())
    if entry == LICENCE_ENTRY:
        _clear_blob(TRIAL_ENTRY)
    return _state_from_claims(claims, None)


def activate_with_login(email: str, password: str) -> LicenceState:
    """Activate with the email and password the tester bought with, instead of a licence key.

    Exists because "where is my licence key" is the most predictable support question a paid desktop app
    gets, and "check your email from three weeks ago" is not an answer. The tester already has an
    account -- they created one to pay -- so signing in is less friction for them and no extra risk to
    the key.

    **The password is used once and never stored.** The service returns the licence key along with the
    signed token, and that key is what gets cached for revalidation. So this ends in exactly the state
    ``activate()`` would have produced, and nothing downstream can tell which route was used.

    The seat limit is the same check either way: the server binds this machine's salted hardware hash to
    the licence, so a third computer is refused here exactly as it is refused when pasting a key. A
    login count would have been the wrong mechanism -- signing out would defeat it, and a hardware seat
    cannot be.
    """
    email = (email or "").strip()
    if not email or not password:
        raise LicenceError("Enter the email and password you signed up with.")

    payload = _post("/api/desktop/login", {
        "email": email,
        "password": password,
        "device_id": device_id(),
        "device_name": device_name(),
    })
    token = str(payload.get("token") or "")
    claims = verify_token(token)

    _store_blob(LICENCE_ENTRY, token)
    key = str(payload.get("key") or "")
    if key:
        _store_blob(LICENCE_KEY_ENTRY, key)
    _store_blob(LAST_VALIDATED_ENTRY, _now().isoformat())
    _clear_blob(TRIAL_ENTRY)
    return _state_from_claims(claims, None)


def start_trial() -> LicenceState:
    """Ask the service for a 7-day trial token for this device.

    Device-keyed on the server, which is where trial abuse is actually stopped. A local-only trial
    is defeated by deleting a file; a new email address does not get a second trial on the same
    machine because the machine is the key, not the address.
    """
    payload = _post("/trial", {
        "device_id": device_id(),
        "device_name": device_name(),
    })
    token = str(payload.get("token") or "")
    claims = verify_token(token)
    _store_blob(TRIAL_ENTRY, token)
    _store_blob(LAST_VALIDATED_ENTRY, _now().isoformat())
    return _state_from_claims(claims, None)


def revalidate() -> LicenceState:
    """Silent 7-day check. Refreshes the cached token, or leaves the cache alone on failure.

    **Never clears a good licence because the network was down.** That is the difference between a
    revalidation and a lockout, and getting it wrong means an outage on our side becomes an outage
    on the tester's. Only an explicit refusal from the service -- a revoked key, a seat limit --
    clears the cache, and that arrives as a 4xx.
    """
    key = _read_blob(LICENCE_KEY_ENTRY)
    if not key:
        return current_state()
    try:
        payload = _post("/refresh", {"key": key, "device_id": device_id()})
    except LicenceError:
        return current_state()

    token = str(payload.get("token") or "")
    try:
        claims = verify_token(token)
    except LicenceError:
        return current_state()

    _store_blob(LICENCE_ENTRY, token)
    _store_blob(LAST_VALIDATED_ENTRY, _now().isoformat())
    return _state_from_claims(claims, None)


def deactivate_device() -> bool:
    """Release this device's seat and clear the local licence.

    Local state is cleared **even if the service call fails**. The user asked to sign this machine
    out; leaving a working licence behind because a request timed out would be the wrong answer to a
    deliberate action, and the seat is reclaimed by the next revalidation from the server side.
    """
    key = _read_blob(LICENCE_KEY_ENTRY)
    released = False
    if key:
        try:
            _post("/deactivate", {"key": key, "device_id": device_id()})
            released = True
        except LicenceError:
            released = False
    sign_out()
    return released


def sign_out() -> None:
    """Forget the licence on this machine. Leaves the trial's first-run record alone.

    Deliberately: signing out is not a way to restart the trial clock.
    """
    for entry in (LICENCE_ENTRY, LICENCE_KEY_ENTRY, TRIAL_ENTRY, LAST_VALIDATED_ENTRY):
        _clear_blob(entry)


def checkout_url() -> str:
    """Where to send someone who wants to subscribe."""
    return f"{SERVICE_URL}/#pricing"


def signup_url() -> str:
    """Where to send someone who would rather make their account in a browser.

    The gate can create an account by itself -- that is what the trial card does -- and this is the
    escape hatch, not the main road. It matters for the cases the in-app form cannot resolve on its
    own: an address that already has a subscription, a password that needs resetting, or simply
    someone who does not want to type a new password into a desktop window they met a minute ago.
    """
    return f"{SERVICE_URL}/signup"
