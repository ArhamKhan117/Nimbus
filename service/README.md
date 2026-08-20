# Nimbus licence service (Python) — superseded

> **The canonical backend is now [`web/`](../web/README.md)**, a Next.js app on Vercel that owns
> accounts, Stripe, EasyPaisa, email and licence signing.
>
> This FastAPI service still works and its tests still pass — it speaks the same four endpoints, signs
> with the same keypair, and issues tokens the desktop app accepts. Keep it if you ever want to
> self-host the licence API on a container without Vercel or Postgres. Otherwise it is one more thing
> to keep correct, and two implementations of "issue a licence" is one too many.
>
> What it does **not** have: user accounts, password login, email delivery, or the EasyPaisa flow.
> Those are in `web/` only. Seats here are 2, matching.

The server half of `S-10`. Signs licence tokens, holds the account database, accepts Stripe
webhooks, and serves a single-file landing page.

The desktop client in `../licensing.py` talks to exactly four endpoints and verifies everything it
receives with an embedded Ed25519 public key. **The private key lives only here, only in an
environment variable, and never in this repository.**

---

## What it is deliberately not

Read `SHELL_AND_CHAT.md` §0.1 before changing anything here.

> A local desktop application cannot enforce a licence. It can only deter casual sharing.

This service makes abuse **visible and revocable** — you can see a key on nine devices and cut it
off. It does not make Nimbus uncrackable, and no client-side check can. The alternative, proxying
model inference through a server, would end BYOK and is a recorded non-goal.

§5 also says *"do not roll your own licence server"* and recommends a hosted provider. This one
exists anyway for one reason: the same deployment serves the landing page, the download and the
licence API, so there is one thing to run rather than three. Note that no payment rail is connected
in this deployment - the Stripe code exists and its tests pass, but nothing is charged. That is a deliberate departure from the doc, not an oversight.

---

## Endpoints

| Method | Path | Who calls it | Does |
|---|---|---|---|
| `POST` | `/trial` | desktop client | Issues a 7-day token, **keyed on `device_id`**. One per machine, ever. |
| `POST` | `/activate` | desktop client | Exchanges a licence key for a device-bound token. Enforces the seat limit. |
| `POST` | `/refresh` | desktop client | The silent 7-day revalidation. Re-signs with the current subscription state. |
| `POST` | `/deactivate` | desktop client | Frees a seat. |
| `POST` | `/stripe/webhook` | Stripe | Creates and revokes licences as subscriptions start, renew and lapse. |
| `POST` | `/admin/manual-licence` | you | Issues a key for a transfer confirmed by hand. |
| `GET` | `/` | browsers | The landing page. |
| `GET` | `/buy` | browsers | Creates a Stripe Checkout session and redirects to it. |
| `GET` | `/success` | browsers | Where Stripe returns the tester. Shows their licence key. |
| `GET` | `/licence-key` | the success page | The key for a paid `session_id`. |
| `GET` | `/download` | browsers | Redirects to the current installer. |
| `GET` | `/healthz` | your host | Liveness. |

Every client-facing response is `{"token": "<payload>.<signature>"}`. Nothing else is trusted:
the client re-verifies the signature before storing, so a compromised or spoofed service cannot
grant a licence it has no key for.

---

## Buy → key → download → activate

The part with no account and no password in it.

```
/#pricing ──► /buy ──► Stripe Checkout ──► /success?session_id=cs_… ──► the key on screen
                            │                      │
                            │                      └─► GET /licence-key?session_id=…
                            │                            asks Stripe "is this session paid?",
                            │                            then returns the tester's one key
                            └─► POST /stripe/webhook (async, may arrive first or second)
```

**`session_id` is the whole credential.** Stripe issues it, it is unguessable, and only the person
who completed that checkout has it — so there is no password to store on either side, and nothing to
breach. The server asks Stripe whether the session is actually paid before issuing anything, which is
what makes a forged id worthless.

Issuance is idempotent (`db.ensure_licence`). One purchase produces one key even though it is reached
from three directions: `checkout.session.completed`, `customer.subscription.created`, and the success
page asking directly because webhook delivery is asynchronous and the tester is already looking at
the screen.

There is **no email delivery**, deliberately — no SMTP credential is held. The key is shown on the
success page and repeated in Stripe's own receipt. A tester who loses it emails `wolfhoghd@gmail.com`.
That is honest at this scale and the first thing to revisit as volume grows.

---

## The trial, and why the device is the key

A 7-day trial with no card has to survive people trying to take a second one.

`POST /trial` records `device_id` in the `trials` table and refuses a second issue for the same
device — **forever**, not for 7 days. `device_id` is a salted SHA-256 of the machine GUID plus the
system volume serial, computed on the client; the raw values never leave the machine
(`tests/test_licensing.py::TestDeviceIdentity` pins that).

What that stops: new email addresses, reinstalling Nimbus, clearing the keyring, deleting
`%LOCALAPPDATA%`. What it does not stop: a new PC, or a VM per trial. That is the accepted
ceiling — the same one every device-bound trial has — and it is a deterrent rather than a wall.

---

## Running it

```powershell
cd service
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt

# Generate the signing keypair. Prints both halves; the public one goes in the client build.
.\.venv\Scripts\python -m app.keys

$env:NIMBUS_LICENCE_PRIVATE_KEY = "<private half>"
$env:STRIPE_SECRET_KEY          = "sk_test_..."
$env:STRIPE_WEBHOOK_SECRET      = "whsec_..."
$env:STRIPE_PRICE_ID            = "price_..."
$env:ADMIN_TOKEN                = "<a long random string>"
$env:SITE_URL                   = "http://127.0.0.1:8000"

.\.venv\Scripts\uvicorn app.main:app --reload
```

Then point the desktop client at it. The public half is baked into a build by
`tools/set_licence_key.py`; the environment variable overrides it, which is what to use against a
local service:

```powershell
$env:NIMBUS_LICENCE_URL        = "http://127.0.0.1:8000"
$env:NIMBUS_LICENCE_PUBLIC_KEY = "<public half>"
```

| Variable | Needed for | Notes |
|---|---|---|
| `NIMBUS_LICENCE_PRIVATE_KEY` | everything | The one secret. Signs every token. |
| `ADMIN_TOKEN` | `/admin/manual-licence` | Without it the endpoint returns 401 to everyone. |
| `STRIPE_SECRET_KEY`, `STRIPE_PRICE_ID` | `/buy`, `/licence-key` | Missing → `/buy` redirects to `/#pricing` instead of 500ing. |
| `STRIPE_WEBHOOK_SECRET` | `/stripe/webhook` | Missing → the webhook fails closed with a 500. |
| `SITE_URL` | `/buy` | Stripe's return URL. Must be this deployment's public origin. |
| `NIMBUS_DOWNLOAD_URL` | `/download` | Where the installer actually lives. |
| `SQLITE_PATH` | storage | Defaults to `./nimbus.db`; the container sets `/data/nimbus.db`. |

---

## Tests

```powershell
cd service
..\.venv\Scripts\python.exe -m pytest -q
```

25 tests, no network, no Stripe account, throwaway keypair per test. They run from `service/`
because the package is called `app` and would collide with the desktop app's `app.py` on the repo
root's import path — the root `pytest.ini` restricts collection to `tests/` for the same reason.

What they pin: every issued token verifies against the public key the client ships; a device gets one
trial and a reinstall returns the remaining days; the seat limit holds while a device already on the
licence is never locked out by it; `/refresh` returns 503 for infrastructure faults and 4xx for real
refusals; the webhook refuses unsigned requests; and one purchase yields one key.

The full loop — real client, real service, real signing key — was verified by starting this service
locally, minting a licence through `/admin/manual-licence`, and driving `licensing.start_trial`,
`activate`, `revalidate`, the seat limit and `deactivate_device` against it. That found the one
user-visible bug in the pair: a freshly issued trial read "6 days left" because the client floored a
6.9999-day token. Now `tests/test_licensing.py::TestTrial::test_a_freshly_issued_trial_token_reads_as_seven_days`.

---

## Deploying

Any container host. The included `Dockerfile` and `fly.toml` are what this runs on:

```powershell
fly launch --no-deploy --copy-config
fly volumes create nimbus_data --size 1 --region sin
fly secrets set NIMBUS_LICENCE_PRIVATE_KEY=... STRIPE_SECRET_KEY=... `
                STRIPE_WEBHOOK_SECRET=... STRIPE_PRICE_ID=... ADMIN_TOKEN=...
fly deploy
```

Four things that must be true in production, and are easy to get wrong:

1. **`NIMBUS_LICENCE_PRIVATE_KEY` is set only in the host's secret store.** If it leaks, anyone can
   mint licences and the only remedy is rotating the public key in a new client build.
2. **The Stripe webhook signature is verified.** `/stripe/webhook` rejects unsigned requests;
   without that check anyone could POST a fake `subscription.created`.
3. **`/data` is a mounted volume.** SQLite inside the container filesystem means every deploy wipes
   the account list. The `Dockerfile` declares the volume and `fly.toml` mounts it; a different
   host needs the equivalent.
4. **One worker.** Several uvicorn workers against one SQLite file produce "database is locked" under
   no load at all. Move to Postgres before scaling out.
