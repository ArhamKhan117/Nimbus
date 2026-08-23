# Nimbus web

The site, the accounts, the payments and the licence API. Next.js 15 + TypeScript + Postgres,
deployed to Vercel.

This is the **canonical backend**. The desktop app in the repo root talks to four endpoints here and
verifies everything it gets back with an embedded Ed25519 public key.

---

## What it does

| | |
|---|---|
| **Landing page** | `/` — the product and the privacy position. Server-rendered; the animation is four small client components. |
| **Accounts** | Email and password, plus a sign-in link by email. `/signup`, `/login`, `/account`. |
| **Payments** | Stripe Checkout for cards. a manual-transfer rail approved from `/admin`. **Neither is connected** — nothing is charged. |
| **Email** | Verification, sign-in links, and the licence key on activation. |
| **Licence API** | `/trial`, `/activate`, `/refresh`, `/deactivate` — the desktop contract, signed Ed25519. |
| **Desktop sign-in** | `/api/desktop/login` — activate the app with the account email and password instead of a key. |

### The paths the desktop app uses have no `/api` prefix

`licensing.py` posts to `/trial` and friends, and that contract is already inside every installer
that has shipped. `next.config.ts` rewrites those four paths onto the route handlers, so the app needs
no update and `SERVICE_URL` is simply the site's origin.

---

## Buy → key → install → activate

```
/#pricing ──► sign up ──► Stripe Checkout ──► /account?purchased=1
                  │                │
                  │                └─► POST /api/stripe/webhook  (signature verified first)
                  │                        creates the licence, emails the key
                  └─► /pay ──► send EasyPaisa/bank ──► submit reference
                                     └─► /admin ──► approve ──► same licence, same email
```

Both routes land on **one** function, `ensureLicence`. An EasyPaisa tester gets an identical
licence — same seats, same signing key, same email — and that is enforced by there being one code path
rather than by good intentions.

**Issuance is idempotent.** Stripe sends more than one event per purchase and retries on failure;
`ensureLicence` returns the existing licence instead of minting a second. A tester holding two keys
has no way to know which is theirs.

**The account page is the durable copy of the key.** Email gets deleted, filtered, or sent to an
address someone abandoned. `/account` is the answer to "I lost my key" that does not need us.

---

## Two ways to activate, one seat check

The app accepts either a licence key or the account's email and password
(`POST /api/desktop/login`). The key is offered first in the UI on purpose — it is one paste and it
never puts a password into a desktop application. The sign-in exists because "where is my licence key"
is the most predictable support question a paid desktop app gets, and "check your email from three weeks
ago" is not an answer.

**The password is used once and never stored.** The endpoint returns the licence *key* alongside the
signed token, and that key is what the client caches for revalidation — so both routes end in an
identical local state and nothing downstream can tell which was used.

**"Only two live" is enforced by hardware, not by counting logins.** Both routes go through
`claimDevice`, which binds the salted SHA-256 of the machine's own identifiers (Windows MachineGuid +
system volume serial, hashed on the client — the raw values never leave the machine). Two active devices
per licence; a third is refused with the number named and the remedy stated. A login counter would have
been the wrong mechanism, because signing out would defeat it and a hardware seat cannot be. Freeing a
seat is deliberate: **Account → Deactivate this device**.

---

## EasyPaisa: what is actually possible

I checked this rather than assuming, because it is the part everyone assumes.

EasyPaisa **does** have an official online payment gateway, and it is **not self-serve**. Per
[EasyPaisa's gateway page](https://easypaisa.com.pk/online-payment-gateway/) it is offered to a
business with a live site or app — which means a merchant application, a registered business, and a
`storeId` plus `hashKey` issued to you. Integrators describe a hosted checkout that redirects the
tester to EasyPaisa's own page and a direct REST alternative, with the request carrying `storeId`,
`amount`, `orderRefNum` and `postBackURL`, hashed with the merchant key
([integration notes](https://github.com/zfhassaan/easypaisa)).
*Content was rephrased for compliance with licensing restrictions.*

So there are two paths, and both are built:

1. **Manual transfer — works today, needs nothing.** Set `EASYPAISA_ACCOUNT_NUMBER` (and/or the bank
   fields) and `/pay` shows them, takes the transaction ID, and emails the tester that a person is
   checking it. You approve at `/admin`. **Not connected** — the code path exists and is covered by
   tests, but nothing is charged and no transfer has been processed through it.
2. **Hosted checkout — dormant until you have a merchant account.** Set `EASYPAISA_STORE_ID` and
   `EASYPAISA_HASH_KEY` and `/pay/easypaisa` appears. `src/lib/easypaisa.ts` builds the signed form.

### One deliberate limitation

`/api/easypaisa/callback` records the postback and marks the payment **for review**. It does not issue
a licence by itself.

Unlike Stripe, there is no webhook signing secret on this rail — the guide's integrity mechanism
covers the *request*, not the response. So a successful-looking postback is evidence, not proof, and
an endpoint that minted licences on it would be a free-licence endpoint for anyone who found the URL.
The upgrade, once you have a merchant account, is to confirm server-side against EasyPaisa's inquiry
API before approving. That is a real piece of work and it is not pretended at here.

---

## Running it

```powershell
cd web
npm install
Copy-Item .env.example .env.local     # then fill it in
npm run keygen                        # unless a keypair already exists — see the warning below
npx prisma db push                    # create the tables
npm run dev
```

> **If a keypair already exists, use it.** The public half is inside installers that have shipped. A
> new pair invalidates every licence already issued.

Point the desktop app at your local site:

```powershell
$env:NIMBUS_LICENCE_URL        = "http://127.0.0.1:3000"
$env:NIMBUS_LICENCE_PUBLIC_KEY = "<public half>"
```

Stripe webhooks in development need the CLI, because Stripe cannot reach `localhost`:

```powershell
stripe listen --forward-to localhost:3000/api/stripe/webhook
```

`stripe listen` prints a signing secret. That is your `STRIPE_WEBHOOK_SECRET` for development, and it
is **not** the one from the dashboard.

---

## Deploying to Vercel

**Root directory is `web/`, not the repository root.** The repo root is the desktop app; Vercel will
find a Python project and fail if this is not set.

`vercel.json` pins three things that are not defaults worth leaving to chance:

| | |
|---|---|
| `"regions": ["iad1"]` | Same region as the Neon database (AWS `us-east-1`). Every request here makes several round trips to Postgres, and a function in the wrong region pays that latency on all of them. |
| `"buildCommand"` | `prisma generate && next build`. The generate step is not optional — Prisma's client is generated code, and a fresh clone has none. |
| `"installCommand": "npm ci"` | Installs from `package-lock.json` exactly. `npm install` is free to resolve a newer minor version, which is how a deploy breaks without a code change. |

TypeScript and ESLint errors do **not** fail the build — see the comment in `next.config.ts`. That is
a release valve, not permission to skip checks: `npx tsc --noEmit` is still the gate, it is just
enforced by you rather than by Vercel.

```powershell
vercel link            # root directory: web
vercel env add DATABASE_URL production
# ...and the rest of .env.local
vercel --prod
```

Then, in order:

1. **Copy every variable from `.env.local`.** All of them, including `DIRECT_DATABASE_URL` — Prisma
   uses it for migrations.
2. **Change two of them.** `AUTH_SECRET` and `ADMIN_TOKEN` in `.env.local` are development
   placeholders with "local" in the name. Generate fresh values; the first signs every session cookie
   and the second is the only thing standing in front of `/admin`.
3. **Set `SITE_URL` to the deployed origin.** Every email link, Stripe return URL and OAuth-style
   redirect is built from it. Left at `localhost:3000`, verification emails point at the tester's
   own machine.
4. **Point the domain**, if you are using one: add it in Vercel, then the DNS records it asks for at
   your registrar. Until it resolves, the desktop app cannot reach the licence service at all. A bare
   Vercel origin works too — it just has to match `SITE_URL` and the baked service URL below.
5. **Check `/healthz`.** It signs a token and verifies it with the public half, so a mismatched
   keypair is caught there rather than by the first tester who tries to activate. Expect
   `database`, `signing`, `stripe`, `email` and `easypaisa_manual` all `ok`;
   `easypaisa_hosted` stays `not configured` until there is a merchant account.

Already done, so not on the list: the Stripe product, price and webhook endpoint exist and the
signing secret is in `.env.local`; the Resend domain is verified; the licence keypair is generated and
its public half is baked into the desktop build with `--service-url <deployed origin>`.

### The database

Postgres, because Vercel's filesystem is ephemeral — a SQLite file would be wiped on every deploy,
taking the account list with it. Neon, Supabase and Vercel Postgres all work; the free tier of any
of them is far more than this needs.

Use the **pooled** connection string for `DATABASE_URL` and the direct one for
`DIRECT_DATABASE_URL`. Serverless functions open many short-lived connections and will exhaust a
direct connection limit under trivial load.

---

## Security notes

Worth reading before changing any of it.

| | |
|---|---|
| **The Stripe webhook verifies the signature before reading anything.** | Without it the endpoint is an open "give me a licence" API. The raw body is used, which is why the route reads `request.text()`. |
| **`/api/admin/payments` needs `ADMIN_TOKEN`,** and refuses everyone when it is unset. | It mints licences. Failing closed is the only correct default. |
| **The admin token is never persisted in the browser.** | Typed into `/admin`, held in memory. A shared or forgotten browser leaves nothing behind. |
| **Email links are stored as SHA-256, never raw.** | A leaked database must not hand out working sign-in links. |
| **Login now says when an account does not exist** (`NO_ACCOUNT`, 404) rather than answering identically. | A deliberate reversal, and a real trade. It weakens account-enumeration defence, which for a product with no social graph is mostly a nuisance; it removes "it says my password is wrong and I know it is not", which is a constant support cost. Rate limiting and lockout are what actually stop someone working through a list, and both stay. The *reset* path still says nothing, because confirming an address to an unauthenticated stranger there has no upside. |
| **Lockout state is in the database.** | Vercel functions do not share memory; an in-process counter resets on every cold start and protects nothing. |
| **Passwords are scrypt from Node's standard library.** | No password dependency to keep patched in the worst possible place for a supply-chain problem. |
| **`/refresh` returns 503 for our faults and 4xx for real refusals.** | The client keeps its licence on 5xx and clears it on 4xx. Backwards, and an outage here becomes a lockout there. |

The private licence key exists in exactly one place: this deployment's environment. If it leaks, every
licence is forgeable and the only remedy is a new keypair plus a new desktop build.

---

## Design and animation

The palette, radii and card treatment are lifted value-for-value from the desktop app's `theme.py` —
the same hex codes, not an approximation. Someone who downloads Nimbus after reading the page should
feel they opened the thing they were just looking at.

Motion is [anime.js v4](https://animejs.com/): `onScroll` for reveals, `createTimeline` for the hero,
`createScope` so every animation is disposed of on unmount. The hero animates the product's actual
behaviour — hotkey, waveform, pointer flying to a control, teaching-mode ring — on one SVG and one
timeline.

**No WebGL and no 3D scene, deliberately.** The target is an ordinary laptop, possibly on a slow connection; the landing page is 3 kB with 129 kB of shared JavaScript and the
whole hero animates transforms and `stroke-dashoffset` only, so it stays on the compositor. A spinning
mesh would cost more than the entire page and demonstrate nothing about the product. If you want a 3D
moment later, the honest place for it is a lazy-loaded section below the fold.

`prefers-reduced-motion` removes all of it, and every reveal is a CSS class that defaults to visible
if the JavaScript never arrives — motion is decoration, the text is the product.
