# Design Document

## Overview

A Next.js application on Postgres: seven models, 23 API routes, eight pages, and one file that matters
more than the rest.

That file is the token signer. The desktop application trusts a licence because of a signature made there
and for no other reason, which means a spoofed copy of this site cannot grant a licence without the private
key. It uses the runtime's own crypto and **no dependency at all** — the wrapper the key format needs is a
fixed sixteen-byte prefix, small enough that reaching for a package would add supply-chain surface to the
one file that must not have any.

Two deployment facts shape almost everything else:

- **The filesystem is ephemeral.** A file-backed database would be wiped on every deploy, taking the
  account list with it. So Postgres, hosted, with a direct address alongside the pooled one because
  schema operations need a direct connection.
- **Functions do not share memory.** Every rate limit, attempt counter and lockout lives on a row rather
  than in a process, because an in-process counter resets on every cold start and protects nothing.

Three defects here shipped:

| Defect | Symptom | Cause |
|---|---|---|
| Nobody could sign up | Signup returned 500; login said "no account" | `scrypt` needs ~64 MB; Node caps at ~32 by default |
| Redirect loop to a blank page | `/login` ↔ `/account` forever | A valid cookie for a deleted account; neither page cleared it |
| A type error could ship | Build green, production broken | `next build` does not typecheck |

And one thing is deliberately not automated: the manual payment rail **records evidence and does not
mint**, because there is no signing secret on that rail and a payment claim is not a payment.

> Consolidated from the service's own module contracts and its 23 routes.

## Architecture

```
                              <deployed origin>  (apex = production, www → 308 → apex)
                                    Vercel, region pinned, root directory: web/
                                              │
  ┌────────────────────────── pages ───────────┴────────────── API routes (23) ──────────────────────┐
  │  /            landing, PointerDemo, stages   │  DESKTOP CLIENT                                    │
  │  /signup      AuthForm                       │    POST /activate          key + device → token    │
  │  /login       AuthForm                       │    POST /refresh           7-day revalidation      │
  │  /forgot      ResetForm                      │    POST /deactivate        release a seat          │
  │  /account     LicenceKey, devices, seats     │    POST /trial             device-keyed trial      │
  │  /pay         BuyButton, PaymentMarks        │    POST /api/desktop/register|verify|login         │
  │  /pay/easypaisa  HostedCheckout / Manual     │  BROWSER                                           │
  │  /admin       AdminPayments                  │    /api/auth/signup|login|logout|link|callback     │
  │                                              │    /api/auth/password|reset|stale                  │
  │  every page branches on THREE session states │    /api/checkout           Stripe session          │
  │  none · stale · ok   ← the redirect-loop fix │    /api/easypaisa/initiate|callback|manual         │
  │                                              │    /api/admin/payments     approve → ensureLicence │
  │                                              │    /api/download           session → 302 installer │
  │                                              │    /api/health             SIGN then VERIFY        │
  │                                              │    /api/stripe/webhook     signature-verified      │
  └──────────────────────────────────────────────┴────────────────────────────────────────────────────┘
                                              │
  ┌───────────────────────────────────── src/lib ──────────────────────────────────────────────────────┐
  │  licence.ts    ← THE SECURITY CORE. node:crypto only, zero dependencies                            │
  │                  signClaims: SORTED KEYS, NO WHITESPACE, empties omitted                           │
  │                  newLicenceKey: CSPRNG over an alphabet with no I O 0 1                            │
  │                  tokenExpiry: min(periodEnd, now + TOKEN_TTL_DAYS)                                 │
  │  licences.ts   ← ensureLicence: THE ONE issuance path, idempotent, shared by all three rails        │
  │                  claimDevice: a KNOWN device is readmitted even at the limit                        │
  │                  startTrial: device-keyed, first owner wins, expiry truncated to the second         │
  │  auth.ts       ← scrypt with an EXPLICIT maxmem · signed cookie · sessionState() · codes and links  │
  │  easypaisa.ts  ← hosted checkout fields (sorted, key length checked) + manual availability          │
  │  errors.ts     ← one map: machine code + the sentence to show                                       │
  │  stripe.ts     ← client + webhook signature verification                                            │
  │  email.ts      ← transactional send                                                                 │
  │  db.ts         ← Prisma singleton + logEvent                                                        │
  └───────────────────────────────────────────────────────────────────────────────────────────────────┘
                                              │
        ┌─────────────── three ways a licence is born, ONE function ────────────────┐
        │  Stripe webhook  ─┐                                                       │
        │  success page    ─┼──→  ensureLicence(userId, periodEnd, {source, subId})  │
        │  admin approval  ─┘         existing? EXTEND (this is also renewal)        │
        │       ↑                     none?     create + logEvent                    │
        │  EasyPaisa callback ✗ ── records ManualPayment(pending), MINTS NOTHING      │
        │                          (no signing secret on that rail)                  │
        └───────────────────────────────────────────────────────────────────────────┘
                                              │
                        Postgres · 7 models · cascading deletes
                 User · Token · Licence · Device · Trial · ManualPayment · Event
```

## Components and Interfaces

### `licence.ts` — the security core

```typescript
export const PLAN_NAME = "Nimbus";
export const PLAN_PRICE_USD = 10;
export const TRIAL_DAYS = 7;
export const DEFAULT_SEATS = 2;
export const TOKEN_TTL_DAYS = 30;

const PKCS8_ED25519_PREFIX = Buffer.from("302e020100300506032b657004220420", "hex");

export function signClaims(claims: Claims): string;
export function verifyToken(token: string, publicKeyB64: string): Claims;
export function newLicenceKey(): string;
export function tokenExpiry(periodEnd: Date): Date;
```

**Sorted keys, no whitespace, empties omitted.** The client verifies the signature over the exact bytes it
received, so any difference in key order between what was signed and what was sent breaks every licence.
Omitting empty, null and undefined values keeps an absent optional field from changing the byte sequence.
`web/src/lib/licence.test.ts` asserts those bytes — sorted keys, no whitespace, no base64 padding — because
the two halves are written in different languages and cannot share a serialiser.

`DEFAULT_SEATS = 2` is two devices: a desktop and a laptop. Enough for how one person actually works, small
enough that a key passed round a room runs out immediately — which is the entire point of counting
devices.

`TOKEN_TTL_DAYS = 30` is shorter than the billing period on purpose. A cancelled subscription simply stops
being re-signed, so this is the longest a lapsed licence keeps working, and it is comfortably longer than
the client's seven-day revalidation interval so nobody honest notices.

`KEY_ALPHABET` omits `I`, `O`, `0` and `1`. These keys get read aloud and typed by hand. The blocks come
from a cryptographically secure source, because a guessable licence key is a licence key everyone has.

`verifyToken` exists for the tests and the health endpoint, so a deployment with a mismatched key pair is
caught by a health check rather than by a tester trying to activate.

### `auth.ts` — passwords, sessions, codes

```typescript
const SCRYPT = { N: 2 ** 16, r: 8, p: 1, keylen: 64, maxmem: 192 * 1024 * 1024 };
```

**`maxmem` is not optional, and leaving it out is a live failure rather than a slow path.** The function
needs `128 * N * r` bytes — 64 MB at these parameters — and Node caps it at 32 MB unless told otherwise, so
every hash threw `RangeError: Invalid scrypt params: memory limit exceeded`. **Reads worked and writes did
not**, so signup returned 500 while login happily reported "no account". Measured: 274 ms per hash with the
cap raised, which is the right order for a password hash and invisible on a login.

192 MB of headroom rather than exactly 64, so a future bump to `N = 2**17` does not silently reintroduce
this. The deployment's default function memory is a gigabyte, so there is room.

Verification reads the parameters back from the stored hash and declares the **same** cap, because it
allocates exactly as much as hashing did. The comparison is constant-time: a fast equality on hashes leaks
how much of the digest matched. The stored format carries the algorithm name and every parameter, so a
parameter change does not invalidate existing hashes.

**The session is a signed cookie, not a row.** A session row per login is a database round trip on every
request, and this site's pages are mostly public. It is `httpOnly`, `secure` in production, and
`sameSite=lax` — **not** `strict`, because it has to survive the redirect back from the payment provider.

The trade is stated rather than hidden: a signed cookie cannot be revoked server-side before it expires, so
the realistic worst case is a stolen cookie working until it does. For a tool of this scope
that is the right side of the trade; for anything holding money it would not be.

### `sessionState()` — three states, not two

```typescript
export type SessionState = "none" | "stale" | "ok";
export async function sessionState(): Promise<{ state: SessionState; session: Session | null }>;
```

`readSession` verifies a signature and nothing else — by design, that is the point of a stateless cookie.
But it means a cookie stays cryptographically perfect after its account is deleted, and pages that only ask
"is there a session" disagree with pages that go on to load the user:

- `/login` saw a session and redirected to `/account`;
- `/account` found no user row and redirected to `/login`;
- **neither cleared the cookie**, so the browser bounced between them until it gave up on a blank page.

A deleted account is rare in production and routine in development, which is exactly the kind of defect
that ships. The cure is for every page to branch on all three states, plus `GET /api/auth/stale` so the
loop is breakable from the client.

**A database outage counts as `ok`.** If the lookup throws, the cookie is trusted. A stale cookie is a
nuisance for one person; treating an unreachable database as "nobody is signed in" would sign out everybody
at once for the duration of a blip, and send them to a login page that also cannot reach the database.
Failing towards the cookie keeps the failure proportional.

### Codes and links — one model, three purposes

```typescript
export const CODE_LENGTH = 6;
export const MAX_CODE_ATTEMPTS = 6;
export const CODE_MINUTES = 20;
export const MAX_FAILED_LOGINS = 8;
export const LOCKOUT_MINUTES = 15;

export async function issueCode(userId, purpose: "verify" | "reset"): Promise<string>;
export async function checkCode(userId, code, purpose): Promise<CodeResult>;
export async function issueToken(userId, purpose, minutes): Promise<string>;
export async function consumeToken(raw, purpose): Promise<string | null>;
```

One model for verification codes, sign-in links and password resets, because they are the same mechanism
with different consequences: proof that whoever presented it controls the mailbox. Three
half-implementations would be three places to get single-use, expiry and hashing wrong.

**Only the hash is stored.** A leaked database must not hand out working sign-in links or verification
codes, and there is no reason for us to be able to read one — we only ever need to check whether the one
presented matches. A code's hash is salted with the account id, so a code cannot be checked against a
different account and the same six digits for two people are two different hashes.

Six digits is a million possibilities: plenty against a person, nothing against a script. So this is only
safe **with** the attempt limit, and the counter is on the row rather than in memory because serverless
functions do not share memory.

Issuing deletes any earlier unused code for the same purpose. Two live codes means "it says the code is
wrong" from someone reading the first of two emails, which is indistinguishable from a defect.

**Why a code as well as a link.** The desktop application is where the trial starts, and a link cannot get
someone from their inbox back into a native window — clicking it opens a browser, which then has to hand
off to an application that may not be listening. A code is typed into the window that is already open and
asking for it. The link stays for the browser, where it is the better answer.

### `licences.ts` — the one issuance path

```typescript
export async function ensureLicence(userId, periodEnd, options)
  : Promise<{ licence: Licence; created: boolean }>;
export async function claimDevice(licence, deviceId, deviceName): Promise<boolean>;
export async function releaseDevice(licenceId, deviceId): Promise<void>;
export async function subscriptionToken(licence, deviceId): Promise<string>;
export async function startTrial(deviceId, deviceName, userId?)
  : Promise<{ expiresAt: Date; isNew: boolean }>;
```

**Idempotent because it has to be.** Stripe sends both `checkout.session.completed` and
`customer.subscription.created` for one activation, and the success page may ask first. Minting a key per
arrival would leave one account holding three, two of which count seats they are not using — and no way to
know which is theirs. Three copies of "issue a licence" would be three chances to issue two, which is why
this is one module shared by the webhook, the success page and the manual approval.

Where an active licence exists it is **extended**, not replaced, because that path is also how a renewal
arrives. The period end moves only forward.

**`claimDevice` always readmits a known device, even at the limit.** It is re-activating, not taking a new
seat, and refusing it would lock a legitimate user out of their own machine after a reinstall. With two
seats that is the normal case rather than an edge case. The seat total is floored at one, so a
misconfigured zero does not lock out everybody. Release marks inactive rather than deleting, so the audit
trail survives. The licence-and-device pairing is unique, so a double request cannot create two rows for
one machine.

**`startTrial` returns the existing expiry for a device that already has a trial, rather than an error.**
Someone who reinstalls mid-trial has not used their trial up. Only an *elapsed* trial is refused, and that
is the caller's decision from `isNew` and the date. The expiry is truncated to the second because that is
what gets stored — otherwise the token issued on the first request and the one issued on a reinstall
disagree about the same trial's expiry.

An existing trial owner is **never** overwritten. The first verified account on a machine is the one the
trial belongs to, and letting a later address claim it would make the row rewritable by anyone who can
reach the endpoint.

### `easypaisa.ts` — two paths, both honest

The research is recorded in the module, with sources cited, because this is the part everyone assumes. The
provider does have an official gateway; it is not self-serve; it needs a merchant application, a registered
business, and issued credentials. There is no equivalent of simply adding a card processor.

So two paths, and availability is derived from the configuration rather than hardcoded:

| Path | Available when | Behaviour |
|---|---|---|
| Hosted checkout | Store id **and** hash key are set | Builds the signed form the browser posts |
| Manual transfer | An account number is set | Tester sends money, submits a reference, a human approves |

**The manual path is deliberately not disguised as automatic.** A page that says confirmation happens by
hand, usually within a few hours, and then does, is better than a spinner that lies. It is the existing
behaviour made less manual rather than a stopgap, because this is how the rail is expected to work once it is connected.

**And the callback records evidence rather than minting.** There is no signing secret on that rail, so the
callback carries no proof, and a payment claim is not a payment. `POST /api/easypaisa/manual` writes a
pending `ManualPayment`; `POST /api/admin/payments` is where a human approves it, and only that calls
`ensureLicence`.

The hash sorts the parameters and **validates the key length rather than trusting it**, with an error
naming the actual and expected byte counts. Integrators report that a rejection almost always traces to
parameter order, a non-secure callback address, or the wrong key length. The amount is a deliberately set
local-currency figure rather than a converted one, because a rate that moves under a tester mid-checkout
is how a payment ends up a rupee short and rejected.

### `errors.ts` — one map

Every user-facing failure has an entry carrying both a machine code and the sentence to show. Ten routes
each inventing their own wording produces ten different ways of saying "that did not work", and the ones
written last are always the worst. A shared map also makes **coverage** reviewable: the list *is* the list
of things that can go wrong, so a missing case is visible as a missing entry rather than as a generic
message in production.

**The enumeration decision is recorded as an argued choice.** The previous version answered identically for
"no such account" and "wrong password", and computed a hash either way so the timing did not give it away —
the textbook defence against account enumeration. It now says when an address is unrecognised, because the
support cost of "it says wrong password and I know my password" is real and constant, while enumeration is
mostly a nuisance for a product with no social graph to mine, and the major providers all answer honestly.

What is kept: rate limiting and lockout, which is what actually stops someone working through a list, and
silence on the **reset** path, where confirming an address to an unauthenticated stranger has no upside at
all.

`messageFromThrow` handles the case every hand-rolled form gets wrong: a network failure, where there is no
response to read a message out of and the browser's own error text means nothing to anyone.

### Status codes as a protocol

The desktop client clears its cache on a client-error refusal and keeps it on a server error. So the
distinction between "you are not activated" and "we are broken" has to be carried by the status code.

| Situation | Status | Client behaviour |
|---|---|---|
| No active subscription | **402**, not 403 | Clears; prompts to subscribe |
| Unknown or revoked key | 4xx | Clears |
| Seat limit reached | 4xx | Clears; tells the user to deactivate one |
| Trial already used | 4xx | Clears |
| Database unreachable | 5xx | **Keeps** the cached licence |
| Signing key missing | 5xx | **Keeps** |

Every error response carries both the code and the sentence, so the client can show the message without
knowing the code.

## Data Models

Seven models. The whole data model is *who has an account, who is activated, what key they got, which
machines it is on, whether this device has had its trial, what arrived out of band, and what happened* —
and every table beyond that is a table someone has to keep correct.

```prisma
model User {
  id, email @unique, passwordHash, emailVerified, name?
  createdAt, failedLogins, lockedUntil?, stripeCustomerId? @unique
  licences[], tokens[], payments[]
}

model Token {                       // verify | login | reset — ONE model, three purposes
  id, userId, tokenHash @unique     // SHA-256 of what was emailed, never the value
  purpose, expiresAt, usedAt?, attempts, createdAt
  user @relation(onDelete: Cascade)
}

model Licence {
  id, key @unique, userId, plan, seatsTotal (2), status, periodEnd
  source ("stripe" | "manual"), stripeSubscriptionId? @unique, createdAt
  user @relation(onDelete: Cascade), devices[]
}

model Device {
  id, licenceId, deviceId, deviceName, active, firstSeen, lastSeen @updatedAt
  @@unique([licenceId, deviceId])   // a double request cannot make two rows
}

model Trial {
  deviceId @id                      // the DEVICE is the primary key
  deviceName, startedAt, expiresAt
  userId?                           // records WHO asked; the device decides WHETHER
}

model ManualPayment {
  id, userId, method, reference, amount, note
  status ("pending" | "approved" | "rejected"), createdAt, reviewedAt?
}

model Event { id, at, kind, detail }   // append-only
```

**The trial has no address column and no required account relation, deliberately.** A new address
cannot obtain a second trial because the address was never what a trial was counted against. Expired
rows are kept
forever, because deleting them would hand back the trial. The optional account reference is nullable only
so the column could be added to an existing database without a migration that could fail; new trials always
have one.

Environment: `DATABASE_URL` pooled plus `DIRECT_DATABASE_URL` for schema operations, `AUTH_SECRET`,
`NIMBUS_LICENCE_PRIVATE_KEY`, the Stripe pair, the payment-provider credentials, `SITE_URL`.

## Correctness Properties

### Property 1: Signed bytes are canonical

For any claim object, the signed payload has keys in sorted order, no whitespace, and no entry whose value
is empty, null or undefined. Two objects differing only in key insertion order produce identical bytes.

**Validates: Requirements 8.3, 8.4, 8.5**

### Property 2: The client accepts what this signs

For any claim object, the produced token verifies under the desktop client's verifier. Asserted as a
contract test on the exact bytes, because the two halves cannot share a serialiser.

**Validates: Requirements 8.1, 8.9**

### Property 3: Signing rejects a malformed key

For any private key value — absent, wrong length, not base64 — signing throws with a message naming what is
wrong, and no token is produced.

**Validates: Requirements 8.7**

### Property 4: Sign then verify round-trips

For any claims and the matching public half, verification returns the claims unchanged. For a mismatched
pair, verification fails. This is what the health endpoint runs.

**Validates: Requirements 8.8, 17.1**

### Property 5: Token expiry never exceeds either bound

For any period end, the token expiry is no later than the period end and no later than the maximum lifetime
from now. For a period end in the past, the expiry is that past date rather than a future one.

**Validates: Requirements 9.1, 9.2**

### Property 6: Keys are unambiguous and unguessable

For any generated key, it matches the fixed shape and contains no character from the excluded set.
Generator: a large sample, asserting no collisions and uniform character distribution.

**Validates: Requirements 7.1, 7.2, 7.3**

### Property 7: Key lookup is normalisation-insensitive

For any key, lookup succeeds regardless of case and surrounding whitespace, and fails for any other string.

**Validates: Requirements 7.4**

### Property 8: Hashing round-trips and is parameter-explicit

For any password, hashing then verifying succeeds; verifying a different password fails. The stored string
carries the algorithm and every parameter, and verification reads them back. Both paths declare the same
memory limit. Generator: passwords including empty, very long, and multi-byte.

**Validates: Requirements 2.1, 2.4, 2.8, 2.10**

### Property 9: Hashing does not throw at the configured parameters

For the configured parameters, hashing completes. Asserted directly, because the failure mode was that
**every** hash threw and reads still worked, so the symptom pointed at the wrong layer.

**Validates: Requirements 2.5, 2.6**

### Property 10: Comparison is constant-time

The comparison uses the constant-time primitive and never an equality operator. Asserted structurally,
because a timing property cannot be measured reliably in a test suite.

**Validates: Requirements 2.9**

### Property 11: The session cookie survives a cross-site redirect

The cookie's same-site policy is the relaxed one, not the strict one. Asserted directly, because the
symptom of getting this wrong is being signed out by a round trip to another site.

**Validates: Requirements 3.3**

### Property 12: Session state is exactly three-valued and total

For any cookie — absent, malformed, expired, valid for a live account, valid for a deleted account — the
state is one of three values and nothing throws. For a database error, the state is signed-in.

**Validates: Requirements 4.1, 4.6, 4.7**

### Property 13: No page can loop

For every page that redirects on session state, the set of redirect targets across all three states
contains no cycle. Asserted as a graph check over the routes, because the defect was a two-page cycle
neither page could see.

**Validates: Requirements 4.4, 4.5**

### Property 14: Only hashes of secrets are stored

For any issued code or link, no row contains the emitted value. The stored hash for a code differs for the
same digits under two different accounts.

**Validates: Requirements 5.3, 5.4, 5.5**

### Property 15: A code is single-use and attempt-limited

For any code, the first correct submission succeeds and every later one fails. Wrong submissions increment
the counter, and submission past the limit returns the too-many outcome without checking. Expiry is
enforced independently of the counter.

**Validates: Requirements 5.6, 5.8, 5.9**

### Property 16: Only one live code exists per purpose

For any sequence of issuances, at most one unused code exists for a given account and purpose. Generator:
repeated issuance, then submitting each emitted code — only the last works.

**Validates: Requirements 5.7**

### Property 17: A link is single-use and purpose-bound

For any link, it is accepted once for its own purpose and rejected for any other purpose, after use, and
after expiry.

**Validates: Requirements 5.10**

### Property 18: Lockout is enforced before any comparison

For a locked account, no hash comparison is performed and the lockout message is returned. The lockout
expires on its own after the fixed period.

**Validates: Requirements 6.1, 6.3, 6.4**

### Property 19: Issuance is idempotent

For any number of issuance calls for one account, exactly one active licence exists. Generator: the
real event sequence an activation produces, in every order, including duplicates and a success-page call
interleaved.

**Validates: Requirements 10.1, 10.3**

### Property 20: A period end only moves forward

For any sequence of issuance calls with arbitrary period ends, the stored period end is the maximum seen.
An earlier date never shortens an existing licence.

**Validates: Requirements 10.4, 10.5**

### Property 21: All three rails reach the same function

Static analysis finds exactly one licence-creating call, and every rail routes through it. No route creates
a licence directly.

**Validates: Requirements 10.1, 10.2, 13.3**

### Property 22: A known device is always readmitted

For any licence at or over its seat limit, binding a device already on the licence succeeds. Binding a new
one fails. Generator: seat totals from zero upwards, with the device present and absent.

**Validates: Requirements 11.1, 11.5, 11.6**

### Property 23: Binding is idempotent per machine

For any number of binds of one device, exactly one row exists and its name reflects the latest value.
Concurrent binds do not create two rows.

**Validates: Requirements 11.4, 11.9**

### Property 24: Releasing preserves the row

For any release, the row remains and is marked inactive. The active count decreases by exactly one.

**Validates: Requirements 11.7**

### Property 25: A device gets one trial, forever

For any device, the first trial start creates a row and later starts return the same expiry with the
new-trial flag false. No code path deletes a trial row. Generator: repeated starts across different
accounts on one device.

**Validates: Requirements 12.1, 12.3, 12.7**

### Property 26: The first trial owner is immutable

For any trial with an owner, a later start by a different account leaves the owner unchanged. For a trial
with no owner, the first identified start sets it.

**Validates: Requirements 12.4, 12.6**

### Property 27: The trial expiry is stable across restarts

For any device, the expiry returned on the first start equals the expiry returned on every later start,
byte for byte in the signed token. This is what the truncation exists for.

**Validates: Requirements 12.9**

### Property 28: The manual rail creates no licence

For any manual payment submission and any callback body — including one claiming success — no licence and
no device row is created. Only the administrative approval endpoint can cause issuance.

**Validates: Requirements 13.1, 13.2, 13.3**

### Property 29: Payment availability follows the configuration

For any configuration, the hosted checkout is offered if and only if both credentials are present, and the
manual transfer if and only if an account number is present. A deployment with neither offers neither.

**Validates: Requirements 13.8, 13.9**

### Property 30: The checkout hash is order-independent of input

For any parameter object, the hashed string has parameters in sorted order, so two objects differing only
in insertion order hash identically. An invalid key length throws with both lengths named.

**Validates: Requirements 14.1, 14.2**

### Property 31: Every error code has a message, and every message is reachable

The error map's keys and its used codes are the same set. No route returns a message not in the map, and no
map entry is unreferenced. Every message names an action the user can take.

**Validates: Requirements 15.1, 15.3**

### Property 32: The reset path never reveals account existence

For any address, the reset response is identical whether or not an account exists. Asserted directly,
because this is the one path where the enumeration trade was **not** taken.

**Validates: Requirements 15.6**

### Property 33: Client-side message extraction is total

For any response body — valid, malformed, empty, a thrown network rejection — a non-empty human sentence is
returned. Offline is distinguished from a server failure.

**Validates: Requirements 15.7**

### Property 34: Status codes partition by who is at fault

For every error path, a tester-state failure returns 4xx and a service failure returns 5xx. A missing
subscription returns exactly 402. Asserted per route, because the client's cache behaviour depends on it.

**Validates: Requirements 16.1, 16.2, 16.3, 16.4**

### Property 35: The health endpoint leaks nothing

The health response contains no secret, no partial key and no connection string, under every failure mode.

**Validates: Requirements 17.4**

### Property 36: The typecheck gate is separate from the build

The typecheck command exists, is not part of the build command, and passes. The build's error-suppressing
options are documented as a release valve.

**Validates: Requirements 19.2, 19.3**

### Property 37: Dependencies are pinned

Every dependency version is exact rather than ranged. Asserted as a test, because a range is how an
advisory-fixed version silently regresses.

**Validates: Requirements 19.4**

## Error Handling

| Failure | Response | Why |
|---|---|---|
| Signing key absent or wrong length | Throw at startup of the call, 5xx | Better than signing nothing and returning success |
| Session secret absent or too short | Throw | A weak secret is worse than no session |
| Password hash memory limit unset | Would throw on **every** password | The defect: reads worked, writes did not |
| Cookie valid, account deleted | Stale state, offer to clear | The redirect loop |
| Account lookup throws | Trust the cookie | Failing towards the cookie keeps the failure proportional |
| Code wrong | Increment, return wrong | Distinct from expired |
| Code past the attempt limit | Return too-many without checking | Six digits is nothing against a script |
| Two codes issued | Older deleted at issue time | Two live codes reads as a defect |
| Link reused, expired or wrong purpose | Reject | Single-use means single-use |
| Repeated login failures | Lock out for a fixed period | What actually stops a list attack |
| Duplicate activation events | One licence, extended | Idempotent by construction |
| Device already bound, at the limit | Readmit | It is not taking a new seat |
| New device, at the limit | Refuse with a 4xx and an action | Tell them to deactivate one |
| Trial already used | Refuse with a 4xx | The caller decides, not the trial function |
| Reinstall mid-trial | Return the existing expiry | They have not used their trial up |
| Manual payment submitted | Record pending, mint nothing | No signing secret on that rail |
| Manual callback claiming success | Record only | A claim is not a payment |
| Checkout key wrong length | Throw with both lengths | Rejections almost always trace to this |
| Payment provider unreachable | 5xx, say nothing was charged | Client keeps its cache |
| Database unreachable | 5xx | Client keeps its cache; an outage is not a lapse |
| Reset for an unknown address | Identical response | No upside in confirming to a stranger |

## Testing Strategy

`web/src/lib/licence.test.ts` is the file that matters most: 14 tests over the signing core, run against
the desktop client's expectations rather than against itself.

- **The contract test is the load-bearing one.** It asserts the exact byte sequence the Python client will
  accept — sorted keys, no whitespace, no base64 padding. The two halves are written in different languages
  and cannot share a serialiser, so a shared fixture is the only thing keeping them in agreement. A test
  that only round-trips within this codebase would pass while every real licence failed.
- **Idempotency is tested against the real event sequence.** The payment provider sends more than one
  event for one activation; the test replays that sequence in every order, with duplicates, and with a
  success-page
  call interleaved, asserting exactly one active licence.
- **The seat boundary is tested from both sides.** A known device at the limit is admitted; a new device at
  the limit is refused. The first is the one that matters, because refusing it locks a legitimate user out
  of their own machine.
- **Both directions of the enumeration decision.** The sign-in path says when an address is unrecognised;
  the reset path must not. Two tests, because the two paths made opposite calls deliberately.
- **The hash test asserts it does not throw at the configured parameters.** That reads like a tautology
  until you know the failure mode: every hash threw while reads kept working, so the symptom pointed at the
  wrong layer entirely.
- **Redirect cycles are checked as a graph**, not page by page. The defect was a two-page cycle neither
  page could see on its own.
- **Status codes are asserted per route**, because the desktop client's cache behaviour depends on the
  4xx-versus-5xx split and a wrong code turns an outage into a lockout.
- **`npx tsc --noEmit` is the real gate**, run separately. The framework's build command does not typecheck,
  so a green build is not evidence of a type-clean tree. The build configuration's error-suppressing options
  are a release valve and are documented as such.
- **The health endpoint signs and verifies on every call**, so a mismatched key pair is caught by a check
  rather than by a tester.
- **The superseded standalone service directory keeps its own 27 tests passing**, so its presence is not
  mistaken for rot. Its status is recorded rather than left ambiguous.
- **Manual verification, required, against the live deployment.** Complete a card activation and confirm one key
  arrives, not three. Activate on two machines and confirm the seat count. Try a third and confirm the
  refusal names the action. Submit a manual payment and confirm no licence appears until approval. Delete an
  account with a live cookie and confirm the sign-in page recovers rather than looping. Request a reset for
  an address that does not exist and confirm the response is identical to one that does.
