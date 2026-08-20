"""Nimbus licence service (SHELL_AND_CHAT.md §5, server half).

Signs licence tokens, holds the account database, handles Stripe, and serves the landing page.

## The one rule this file exists to keep

**Every response the desktop client acts on is a signed token, and the client re-verifies it.** This
service is not trusted by the client -- it is only able to *sign*. A spoofed or compromised endpoint
cannot grant a licence without the private key, which is why activation is safe over a plain HTTP
call to a URL the user can override.

## Failure posture

Read §0.1: this is deterrence, not enforcement. Where a decision is ambiguous, the honest tester
wins. Concretely, a database error during ``/refresh`` returns 503 rather than 403 -- the client
treats 5xx as "try later, keep the cached licence" and 4xx as "this licence is genuinely refused",
so returning the wrong one would lock out a licensed user over an infrastructure blip.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db
from .keys import sign_claims

PLAN_NAME = "Nimbus"
TOKEN_TTL_DAYS = 30
"""How long a signed token stays valid before the client must refresh.

Shorter than the billing period on purpose. A cancelled subscription stops being re-signed, so this
is the longest a lapsed tester can keep using Nimbus -- and it is comfortably longer than the
7-day revalidation interval, so an honest user never notices it."""

DOWNLOAD_URL = os.getenv(
    "NIMBUS_DOWNLOAD_URL",
    # Was `nimbus-app/nimbus` with a `NimbusSetup.exe` filename -- an organisation and an asset name
    # that have never existed here. It is only a fallback, which is exactly why nobody noticed.
    "https://github.com/ArhamKhan117/Nimbus/releases/latest/download/Nimbus-Windows-Setup.exe")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

SITE_URL = os.getenv("SITE_URL", "http://127.0.0.1:8000").rstrip("/")
"""Where Stripe sends the tester back to. Must be the public origin of this deployment."""

STATIC_DIR = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Create the schema on startup.

    A lifespan handler rather than ``@app.on_event("startup")``, which FastAPI has deprecated. The
    work is the same and it is idempotent, so a restart mid-deploy is safe.
    """
    db.ensure_schema()
    yield


app = FastAPI(title="Nimbus licence service", docs_url=None, redoc_url=None, lifespan=lifespan)


# --- request bodies ----------------------------------------------------------


class TrialRequest(BaseModel):
    device_id: str = Field(min_length=8, max_length=128)
    device_name: str = ""


class ActivateRequest(BaseModel):
    key: str = Field(min_length=4, max_length=64)
    device_id: str = Field(min_length=8, max_length=128)
    device_name: str = ""


class RefreshRequest(BaseModel):
    key: str = Field(min_length=4, max_length=64)
    device_id: str = Field(min_length=8, max_length=128)


class ManualLicenceRequest(BaseModel):
    """For a transfer that arrived out of band rather than by card.

    The reason this endpoint exists: a licence system that only understands one payment rail cannot
    issue a key for anything else, and the rail is an implementation detail of issuing one.

    NOTE: no payment rail is connected in this deployment. Nothing is charged, and this endpoint is
    reached by hand.
    """

    email: str
    months: int = 1
    note: str = ""


# --- helpers -----------------------------------------------------------------


def _token(kind: str, expires_at: datetime, **extra) -> str:
    claims = {
        "kind": kind,
        "plan": extra.pop("plan", PLAN_NAME),
        "expires_at": expires_at.astimezone(timezone.utc).isoformat(),
        "issued_at": db.now().isoformat(),
    }
    claims.update({key: value for key, value in extra.items() if value not in (None, "")})
    return sign_claims(claims)


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# --- client endpoints --------------------------------------------------------


@app.post("/trial")
def start_trial(body: TrialRequest) -> dict:
    """Issue a 7-day trial token for a device that has not had one.

    Keyed on ``device_id``, so a new email address does not buy a second trial. The client sends a
    salted hash, never a raw hardware id, so this table holds nothing that identifies a person or
    correlates outside Nimbus.
    """
    expires, is_new = db.start_trial(body.device_id, body.device_name[:120])
    if expires is None:
        raise HTTPException(status_code=403, detail="This computer has already used its trial.")
    if not is_new and expires <= db.now():
        raise HTTPException(
            status_code=403,
            detail="Your 7-day trial on this computer has ended. Subscribe to keep using Nimbus.")
    return {"token": _token("trial", expires, plan=f"{PLAN_NAME} trial")}


@app.post("/activate")
def activate(body: ActivateRequest) -> dict:
    """Exchange a licence key for a device-bound token, enforcing the seat limit."""
    licence = db.licence_by_key(body.key)
    if licence is None:
        raise HTTPException(status_code=404, detail="That licence key was not recognised.")
    if licence["status"] != "active":
        raise HTTPException(
            status_code=403,
            detail="This subscription is not active. Renew it to keep using Nimbus.")

    if not db.claim_device(int(licence["id"]), body.device_id,
                           body.device_name[:120], int(licence["seats_total"])):
        # Named numbers, not "seat limit reached". A tester who knows they are on 3 of 3 devices
        # can go and deactivate one; a tester told "limit reached" writes to support.
        raise HTTPException(
            status_code=403,
            detail=(f"This licence is already on {licence['seats_total']} devices. "
                    "Open Nimbus on one of them and use Account \u2192 Deactivate this device."))

    db.log_event("licence.activated", f"{licence['key']} {body.device_id[:12]}")
    return {"token": _token(
        "subscription",
        min(_parse(licence["current_period_end"]), db.now() + timedelta(days=TOKEN_TTL_DAYS)),
        plan=str(licence["plan"]),
        email=str(licence["email"]),
        seats_used=db.active_devices(int(licence["id"])),
        seats_total=int(licence["seats_total"]),
        device_id=body.device_id,
    )}


@app.post("/refresh")
def refresh(body: RefreshRequest) -> dict:
    """The silent 7-day revalidation.

    Returns **503 rather than 403** when the licence cannot be read for an infrastructure reason. The
    client keeps its cached licence on 5xx and clears it on 4xx, so this distinction is the
    difference between an outage and a lockout.
    """
    try:
        licence = db.licence_by_key(body.key)
    except Exception:
        raise HTTPException(status_code=503, detail="Try again shortly.")
    if licence is None:
        raise HTTPException(status_code=404, detail="That licence key was not recognised.")
    if licence["status"] != "active":
        raise HTTPException(status_code=403, detail="This subscription is no longer active.")

    known = db.claim_device(int(licence["id"]), body.device_id, "",
                            int(licence["seats_total"]))
    if not known:
        raise HTTPException(
            status_code=403, detail="This device is no longer on the licence.")
    return {"token": _token(
        "subscription",
        min(_parse(licence["current_period_end"]), db.now() + timedelta(days=TOKEN_TTL_DAYS)),
        plan=str(licence["plan"]),
        email=str(licence["email"]),
        seats_used=db.active_devices(int(licence["id"])),
        seats_total=int(licence["seats_total"]),
        device_id=body.device_id,
    )}


@app.post("/deactivate")
def deactivate(body: RefreshRequest) -> dict:
    licence = db.licence_by_key(body.key)
    if licence is None:
        raise HTTPException(status_code=404, detail="That licence key was not recognised.")
    db.release_device(int(licence["id"]), body.device_id)
    db.log_event("licence.deactivated", f"{licence['key']} {body.device_id[:12]}")
    return {"ok": True, "seats_used": db.active_devices(int(licence["id"]))}


# --- Stripe ------------------------------------------------------------------


@app.post("/stripe/webhook")
async def stripe_webhook(request: Request,
                         stripe_signature: str = Header(default="")) -> dict:
    """Create and revoke licences as subscriptions change.

    **The signature is verified before anything is read.** Without that, this endpoint is an open
    "give me a licence" API -- anyone who knows the URL could POST a fake
    ``customer.subscription.created``. That is the single most important line in this file.
    """
    import stripe

    secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
    payload = await request.body()
    if not secret:
        raise HTTPException(status_code=500, detail="Webhook secret is not configured.")
    try:
        event = stripe.Webhook.construct_event(payload, stripe_signature, secret)
    except Exception:
        raise HTTPException(status_code=400, detail="Bad signature.")

    kind = event["type"]
    data = event["data"]["object"]

    if kind in ("checkout.session.completed", "customer.subscription.created"):
        email = (data.get("customer_email")
                 or (data.get("customer_details") or {}).get("email") or "")
        subscription_id = data.get("subscription") or data.get("id") or ""
        if not email:
            db.log_event("stripe.no_email", str(subscription_id))
            return {"ok": True}
        customer_id = db.upsert_customer(email, stripe_id=str(subscription_id))
        # `ensure_licence`, not `create_licence`: Stripe sends both events for one purchase, and the
        # success page may have asked for the key before either arrived. All three paths must land on
        # the same key or the tester ends up holding several.
        key, created = db.ensure_licence(customer_id, db.now() + timedelta(days=31))
        db.log_event("stripe.licence_issued" if created else "stripe.licence_reused",
                     f"{email} {key}")
        # The key is shown on the success page and repeated in Stripe's own receipt automation; this
        # service deliberately sends no mail, so there is no SMTP credential to hold.
        return {"ok": True, "key": key}

    if kind in ("invoice.paid", "invoice.payment_succeeded"):
        licence = db.licence_for_stripe_subscription(str(data.get("subscription") or ""))
        if licence is not None:
            db.set_licence_status(str(licence["key"]), "active",
                                  db.now() + timedelta(days=31))
        return {"ok": True}

    if kind in ("customer.subscription.deleted", "invoice.payment_failed"):
        licence = db.licence_for_stripe_subscription(
            str(data.get("subscription") or data.get("id") or ""))
        if licence is not None:
            db.set_licence_status(str(licence["key"]), "lapsed")
        return {"ok": True}

    return {"ok": True, "ignored": kind}


def _stripe():
    """The configured Stripe client, or ``None`` when card payments are not set up.

    Returning ``None`` rather than raising lets the site stay useful before Stripe exists: the buy
    button falls back to the local-transfer route. Neither rail is connected; nothing is charged.
    """
    key = os.getenv("STRIPE_SECRET_KEY", "")
    if not key:
        return None
    import stripe

    stripe.api_key = key
    return stripe


@app.get("/buy")
def buy() -> RedirectResponse:
    """Start a Stripe Checkout session for the one plan and send the visitor to it.

    A GET that redirects, so the pricing button is a plain link with no JavaScript. The tester
    comes back to ``/success?session_id=...``, which is where they are shown their licence key.
    """
    stripe = _stripe()
    price = os.getenv("STRIPE_PRICE_ID", "")
    if stripe is None or not price:
        # Honest fallback rather than a broken button: the local-payment panel is a real route to
        # buying Nimbus, not an apology for the missing one.
        return RedirectResponse("/#pricing", status_code=302)
    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": price, "quantity": 1}],
            success_url=f"{SITE_URL}/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{SITE_URL}/#pricing",
            allow_promotion_codes=True,
        )
    except Exception as exc:
        db.log_event("stripe.checkout_failed", str(exc)[:200])
        raise HTTPException(status_code=503, detail="Checkout is unavailable. Try again shortly.")
    return RedirectResponse(str(session.url), status_code=302)


@app.get("/licence-key")
def licence_key(session_id: str = "") -> dict:
    """The licence key for a completed Checkout session.

    ``session_id`` is the authorisation. It is issued by Stripe, unguessable, and only the person who
    completed that checkout has it -- so this needs no password and stores none. Stripe is asked
    whether the session is actually paid before anything is issued, which is the part that makes a
    forged session id worthless.

    Also *creates* the licence if the webhook has not landed yet. Webhook delivery is asynchronous and
    the tester is already looking at the page; making them refresh until an event arrives is not an
    acceptable first minute of a paid product.
    """
    stripe = _stripe()
    if stripe is None or not session_id:
        raise HTTPException(status_code=404, detail="No checkout session.")
    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except Exception:
        raise HTTPException(status_code=404, detail="That checkout session was not found.")

    if str(getattr(session, "payment_status", "")) not in ("paid", "no_payment_required"):
        raise HTTPException(status_code=402, detail="This checkout has not been paid.")

    details = getattr(session, "customer_details", None) or {}
    email = (getattr(session, "customer_email", "")
             or (details.get("email") if isinstance(details, dict) else getattr(details, "email", ""))
             or "")
    if not email:
        raise HTTPException(status_code=409, detail="Stripe did not return an email address.")

    customer_id = db.upsert_customer(str(email),
                                     stripe_id=str(getattr(session, "subscription", "") or ""))
    key, created = db.ensure_licence(customer_id, db.now() + timedelta(days=31))
    db.log_event("licence.shown" if not created else "licence.issued_on_success", f"{email} {key}")
    return {"key": key, "email": str(email), "download": "/download"}


# --- admin -------------------------------------------------------------------


@app.post("/admin/manual-licence")
def manual_licence(body: ManualLicenceRequest,
                   authorization: str = Header(default="")) -> dict:
    """Issue a licence for a payment taken outside Stripe.

    Bearer-token protected rather than unauthenticated-and-obscure. This mints licences, so it is the
    second most sensitive endpoint here after the webhook.
    """
    if not ADMIN_TOKEN or authorization != f"Bearer {ADMIN_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized.")
    customer_id = db.upsert_customer(body.email, source="manual")
    months = max(1, min(24, int(body.months)))
    key = db.create_licence(customer_id, db.now() + timedelta(days=31 * months))
    db.log_event("licence.manual", f"{body.email} {key} {body.note}"[:200])
    return {"key": key, "email": body.email, "months": months}


# --- the site ----------------------------------------------------------------


@app.get("/download")
def download() -> RedirectResponse:
    return RedirectResponse(DOWNLOAD_URL, status_code=302)


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


if STATIC_DIR.is_dir():
    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/success")
    def success() -> FileResponse:
        """The page a tester lands on after paying. It asks ``/licence-key`` for their key."""
        return FileResponse(STATIC_DIR / "success.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
