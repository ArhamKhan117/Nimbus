# Design Document

## Overview

One module of policy, one dialog, and a build chain that bakes a public key into the installer without
ever committing a secret.

`licensing.py` is deliberately boring. Its interesting content is not cryptography — signature
verification is nine lines against a well-tested library — but a set of failure decisions, each of which
answers the same question: *when this goes wrong, does the legitimate user lose access?* The answer is
always no. A network failure returns the cached state. A verification crash starts the application
anyway and writes a file. An unreachable service tries the next candidate. A seat check that cannot run
does not block.

That asymmetry is the whole design, and it follows from the admission at the top of the requirements:
this deters casual sharing and cannot prevent a determined crack. Given that, being strict buys nothing
and costs a support queue.

Four defects here shipped and had to be found in production, all silent:

| Defect | Symptom | Cause |
|---|---|---|
| The gate never ran | Unlicensed instances started, looking normal | A `NameError` swallowed by a blanket `except` in a windowed build with no stdout |
| Every licence call failed | "the licence service returned something unexpected" | `httpx` does not follow redirects; the apex 308'd to `www` |
| Every user "up to date" forever | No update notifications | The checker queried a **private** repository; 404 read as no release |
| Registration always failed | Nobody could create an account | `scrypt` needs ~64 MB, Node caps at ~32 by default |

None produced a stack trace anyone saw. That is the pattern this design keeps trying to break.

> Consolidated from `SHELL_AND_CHAT.md` §0.1 and §5 `S-10`.

## Architecture

```
                             first launch
                                  │
                    ┌─────────────┴──────────────┐
              no licence, no trial          has a key / an account
                    │                             │
      register(email, password)          activate(key)   activate_with_login(email, pw)
                    │  6-digit code              │        │  password used ONCE, never stored
      verify_code(email, code) ───────────────┐  │        │
        server decides: trial OR subscription │  │        │
                    │                         ↓  ↓        ↓
                    └──────────→  POST /activate | /api/desktop/{register,verify,login} | /trial
                                              │
                                    signed token returned
                                              │
                             verify_token()  ← BEFORE storing, always
                          Ed25519, public key baked into this build
                                              │
                        _store_blob: keyring ≤1024 B, else file
                                              │
                                            run
                                              │
                    should_revalidate()  every 7 days, silent, not per launch
                                              │
                          POST /refresh ──→ 4xx? clear.  unreachable? KEEP.
                                              │
                            14-day offline grace from last success


  ┌──────────────────────── the gate, in __main__ ────────────────────────┐
  │  QApplication built                                                   │
  │        ↓                                                              │
  │  _run_startup_licence_gate()      ← extracted; ALL imports LOCAL      │
  │        ↓                            (the NameError lived here)        │
  │    is_activated()? ──yes──→ continue                                  │
  │        │ no                                                           │
  │    ActivationDialog (modal, blocking)                                 │
  │        │  Retry · Use offline · Register · Sign in · Start trial           │
  │        ↓ refused → sys.exit(1)                                        │
  │                                                                       │
  │  except Exception:                                                    │
  │      _record_licence_gate_failure(exc)   ← writes a FILE              │
  │      start anyway            ← our bug must not lock out a tester   │
  └────────────────────────┬──────────────────────────────────────────────┘
                           ↓
        hotkey listener installs · microphone opens   ← never before the gate


  build chain
      tools/set_licence_key.py  ──→  licence_key.py   (git-ignored, PUBLIC half only)
                                          │
      nimbus.spec  hiddenimports + datas  │        installer/nimbus.iss
                                          ↓                │  no Run key, no Startup shortcut
      tools/build_release.py ──→ dist/ ──→ tools/verify_bundle.py ──→ Inno Setup ──→ .exe
                                                    │ asserts the modules are present
      .github/workflows/release.yml ──→ gh release upload  (THIS repo, injected token)
                                          │  checks every exit code
                                          └─ reads the asset list BACK to confirm
      updates.REPOSITORY = this PUBLIC repo   ← must be readable with no credentials
```

## Components and Interfaces

### Constants and their reasons

```python
KEYRING_SERVICE       = "Nimbus"          # one namespace, shared with config.py
LICENCE_ENTRY         = "LICENCE_TOKEN"
LICENCE_KEY_ENTRY     = "LICENCE_KEY"
TRIAL_ENTRY           = "TRIAL_TOKEN"
TRIAL_FIRST_RUN_ENTRY = "FIRST_RUN_AT"
LAST_VALIDATED_ENTRY  = "LICENCE_VALIDATED_AT"

TRIAL_DAYS            = 7
OFFLINE_GRACE_DAYS    = 14
REVALIDATE_EVERY_DAYS = 7
KEYRING_SAFE_BYTES    = 1024              # MEASURED, not documented
HTTP_TIMEOUT          = 10.0
```

| Constant | Reason |
|---|---|
| `OFFLINE_GRACE_DAYS = 14` | A tool that stops working on a flight is worse than one that gets pirated |
| `REVALIDATE_EVERY_DAYS = 7` | Detects seat abuse without nagging |
| `KEYRING_SAFE_BYTES = 1024` | Credential Manager accepted 1 KB and **refused 2 KB** on the test machine. A signed token is ~450 bytes, so the store is primary and the file is the safety net |
| `HTTP_TIMEOUT = 10.0` | Activation is interactive, and a hung request reads as a broken app |

### `verify_token`

```python
def verify_token(token: str, public_key_b64: str | None = None) -> dict: ...
```

Format is `<base64url payload>.<base64url signature>` — a compact signed-blob, **deliberately not** a
standard web token. A standard token brings algorithm negotiation with it, and algorithm negotiation is
where those libraries get broken. One algorithm, no header, nothing to negotiate.

Fails closed. With no configured public key, every licence is refused, which is why
`tools/build_release.py` reports whether a key is present rather than leaving its absence to be
discovered by the first person who tries to activate. Every raised error carries a message fit to show
a user, and a tampered token is indistinguishable from a corrupt one from here — both mean "do not
trust".

The public key resolves from the environment first, then the baked constant. Environment first so the
test suite can sign with a throwaway key; baked second so a shipped installer needs no configuration.
The private half never appears in this repository, is never sent to a client, and lives only in the
licence service's environment. A test asserts no private key is present in the repository.

### `device_id`

```python
def device_id(salt: str = "nimbus-device-v1") -> str:
    material = "|".join((_machine_guid(), _volume_serial(), platform.node() or ""))
    if not material.strip("|"):
        material = "unknown-device"
    return sha256((salt + "|" + material).encode()).hexdigest()[:32]
```

**Never a raw hardware identifier.** The reason is custodial rather than technical: a raw installation
GUID or volume serial is a fingerprint that correlates across every service that receives it, and
collecting one makes us responsible for it. A salted hash is stable enough to bind a seat to and useless
for anything else.

The installation GUID comes from the operating system's own registry value — not a hardware serial. The
volume serial is included because it changes on a reformat, which is the intent. Both lookups failing
falls back to the hostname: that weakens the binding on an unusual machine rather than blocking a
legitimate user, which is the right way round. The registry read suppresses its console window, because a
windowed build must not flash a terminal.

### Storage

```python
def _store_blob(name, value) -> bool:   # keyring ≤ KEYRING_SAFE_BYTES, else a file
def _read_blob(name) -> str:            # keyring first, then the file
def _clear_blob(name) -> None:          # both
```

The credential store is primary because it is already a dependency, already encrypted per user, and a
licence file on disk is trivially copied between machines. The size check is not defensive padding:
Credential Manager silently refused a 2 KB write in testing, and a licence that fails to persist means
the tester re-activates on every launch. Every operation swallows its own failures and reports a
boolean.

### The trial's two records

```python
def _first_run_records() -> list[datetime]:   # keyring entry AND file
def _first_run_at() -> datetime:              # min() of whatever exists
def trial_days_left() -> int:                 # ceil, clamped to TRIAL_DAYS
```

Two independent records, written together and read together. Clearing one is a plausible accident;
clearing both is not. **Earliest wins**, so restoring one does not hand back a fresh trial.

This is honest about its own limits: it stops only the most casual reset, and it is not where trial abuse
is actually prevented. The server keys the trial on `device_id`, so a new email address gets no second
trial on the same machine. The local records exist so an offline first run still has a start date. And
`sign_out` deliberately does not touch the first-run record — signing out is not a way to restart the
trial clock.

### Day counting — two bugs, one rule

Both counters round **up**, and both had a defect that a floor produced:

| Where | Defect | Fix |
|---|---|---|
| `trial_days_left` | A trial with six hours left read "0 days left" on a licence that still worked | `ceil` |
| `trial_days_left` | A fresh install has 6.9999 days, which rounds up to 8 — promising eight days of a seven-day trial | clamp to `TRIAL_DAYS` |
| `_state_from_claims` | A freshly issued trial read "6 days left" the moment it was granted, because the token expires in 6.9999 days | `ceil` |

The third is the interesting one: it was **caught by driving the real client against the real service,
not by a unit test.** A unit test with a hand-built token would have used a round number.

Saying zero while the application runs is the kind of small dishonesty that makes someone distrust
everything else on the screen. The clamp is the same principle in the other direction.

`days_left` is a separate field from `trial_days_left` rather than a widening of it, because the gate and
the home page both read the trial-specific one and both mean "days of *trial* remaining" — widening it in
place would have made an activated licence look like a trial to both.

### `current_state` and `is_activated`

```python
def current_state() -> LicenceState: ...   # never raises
def is_activated() -> bool: ...            # the gate's only question
def should_revalidate() -> bool: ...
```

`LicenceState` is a frozen dataclass whose fields match, field for field, the shape the account page
already declares — so the page needs no change and the shell depends on a shape rather than on this
module.

Order matters: a real subscription beats a trial, so a tester who activates mid-trial is not told they have
three days left. Both are signed tokens verified identically; the trial is not a special case in the
verification path, only in what its claims say.

A token that cannot be verified is **cleared**, because a token we cannot verify is worse than none — the
user is asked once rather than shown a broken state forever.

`_offline_days_left` returns `None` rather than the full period when the licence was checked within the
day, because the account page renders it only when it is meaningful and a permanent grace line is noise
on a machine that has been online all week.

`is_activated` includes the grace: a cached subscription whose expiry has passed is still honoured for
the grace period past the last successful revalidation, because the common reason for an expired token is
a laptop that has not been online, not a lapsed card.

### `_post` — the two rules

```python
for base in SERVICE_URLS:
    try:
        response = httpx.post(f"{base}{path}", json=body,
                              timeout=HTTP_TIMEOUT,
                              follow_redirects=True)      # ← the field-brick fix
        break
    except Exception as exc:
        unreachable = exc
```

**Rule one: move on only when a service cannot be reached at all** — a name-resolution failure, a refused
connection, a timeout. Anything the service actually answers, including "that password does not match",
is its answer and is returned or raised as-is. Retrying a *rejection* against a second service would turn
one wrong password into two attempts against two rate limiters, and would let a fallback overrule a real
answer from the primary.

**Rule two: follow redirects.** `httpx` does not by default, and that default broke every licence
operation the moment the site went live: the host redirected the apex to `www` with a 308, this saw a
non-JSON body reading "Redirecting…", and every activation failed with *"the licence service returned
something unexpected"*.

The severity is what makes this a requirement rather than a bug fix. The service address is **baked into
shipped installers**, so it cannot be corrected for anyone who already has one. A licence client that
breaks on a redirect is a client a future DNS change can brick in the field. Following redirects is the
only version of this that survives contact with a hosting provider.

`SERVICE_URLS` is a comma-separated list resolved from the environment, then the baked constant, then the
default. During development the real answer is both — the deployed site is what ships, and a local
development server is what can be tested against before the domain exists. One value meant editing it
back and forth, and forgetting to edit it back is how a shipped build ends up talking to `localhost`.
Including a local address is safe because every token is signed and verified against the embedded public
key: the worst a local impostor achieves is a refused activation, which is the same as no answer.
Ordering matters for **speed**, not safety.

**The last-resort default is a reserved domain, deliberately.** It is `https://nimbus.example`, and
`.example` is reserved by IANA for documentation, so it can never resolve to anybody. That default is
only reached when neither the environment variable nor the baked constant is set, which means the build
was packaged without being told where its licence service lives. A name that cannot resolve turns that
into an immediate connection error rather than a silent attempt against whatever host happens to answer,
which is the failure you want from missing configuration. `tests/test_licensing.py` pins the value so it
cannot drift to something live.

### The three ways in

```python
def activate(key) -> LicenceState: ...
def activate_with_login(email, password) -> LicenceState: ...
def register(email, password) -> str          # returns the message to show
def verify_code(email, code) -> LicenceState  # verifies AND starts the trial
def start_trial() -> LicenceState: ...
```

All five verify the returned token **before** storing it. A service returning an unsigned or wrongly
signed token would otherwise poison the cache, and the failure would surface later as an unexplained
lockout rather than here, where there is a dialog to show it in.

**The login route exists because of a support question.** *"Where is my licence key"* is the most
predictable question a desktop application with accounts gets, and "check your email from three weeks ago" is not
an answer. The tester already has an account, because registration creates one. The password is used once and
**never stored**; the service returns the licence key alongside the token, and that key is what gets
cached for revalidation. So this ends in exactly the state `activate()` would have produced, and nothing
downstream can tell which route was used. The seat limit is the same check either way, because the server
binds the machine's salted hash. A login count would have been the wrong mechanism: signing out would
defeat it, and a hardware seat cannot be.

**Verification and trial start are one call**, because from the user's side it is one action: they type
six digits and Nimbus starts working. Splitting them would give the flow two ways to fail halfway, and a
verified account with no trial is a support conversation nobody wants to have. The server decides what
comes back, so an account that already has a subscription gets a subscription token rather than a trial —
someone already activated who then reinstalls is not handed a fresh trial.

The trial being identified rather than anonymous is a deliberate trade. A device hash stops a second
trial and is useless for everything else: nobody to email when a trial is ending, and no way to reach a
tester who stopped using it to ask why. What it costs the person is one email address and six digits
typed into a window that is already open and asking for them, and it costs them none of the seven days.

The browser signup route is the escape hatch, not the main road: an address that already has a
subscription, a password needing a reset, or someone who does not want to type a new password into a
desktop window they met a minute ago.

### `revalidate` — the lockout boundary

```python
def revalidate() -> LicenceState:
    key = _read_blob(LICENCE_KEY_ENTRY)
    if not key:
        return current_state()
    try:
        payload = _post("/refresh", {"key": key, "device_id": device_id()})
    except LicenceError:
        return current_state()          # ← NEVER clears
    try:
        claims = verify_token(payload.get("token") or "")
    except LicenceError:
        return current_state()          # ← NEVER clears
    _store_blob(LICENCE_ENTRY, token)
    _store_blob(LAST_VALIDATED_ENTRY, _now().isoformat())
    return _state_from_claims(claims, None)
```

**Never clears a good licence because the network was down.** That is the difference between a
revalidation and a lockout, and getting it wrong means an outage on our side becomes a lockout on
theirs. Only an explicit refusal from the service — a revoked key, a seat limit — clears the cache,
and that arrives as a 4xx from `_post`.

### `deactivate_device` and `sign_out`

Local state is cleared **even if the service call fails**. The user asked to sign this machine out;
leaving a working licence behind because a request timed out would be the wrong answer to a deliberate
action, and the seat is reclaimed by the next revalidation from the server side. The return value says
whether the service acknowledged, so the interface can be honest about it.

Sign-out from inside the application raises `sig_licence_gate_required`, and the application re-runs the
gate through a deferred single-shot timer rather than inline — the signal originates in a widget's own
handler, and re-entering a modal flow from there is not safe. Before that signal existed, sign-out did
nothing observable.

### The gate

```python
# app.py __main__, AFTER QApplication and BEFORE the hotkey listener and the microphone
try:
    if not _run_startup_licence_gate():
        sys.exit(1)
except Exception as exc:
    _record_licence_gate_failure(exc)      # writes a FILE
    # and start anyway
```

Before the listener and the microphone, because an unlicensed instance should consume no devices and
register no global hooks.

**Every import inside `_run_startup_licence_gate` is local to that function.** That is not style; it is
the fix for the worst defect in this feature. A module was referenced but not imported, producing a
`NameError` that a blanket `except` swallowed — in a windowed build with no stdout, so nothing anywhere
reported it. **The gate never ran at all.** The application started unlicensed and looked completely
normal.

`_record_licence_gate_failure` writes to a file precisely because there is no console in a frozen
windowed build. And the caller starts anyway on an evaluation failure, because refusing to run because
the licence is invalid is correct while refusing to run because *our check crashed* is not.

### Build chain

| Piece | Role |
|---|---|
| `tools/set_licence_key.py` | Writes `licence_key.py` with the **public** key and the service address |
| `licence_key.py` | Generated, git-ignored, public half only. A missing module is normal — that is a dev checkout |
| `nimbus.spec` | `hiddenimports` for every lazily imported module |
| `tools/build_release.py` | Builds, and **reports whether a key is present** |
| `tools/verify_bundle.py` | Asserts the produced bundle contains what it should |
| `tools/issue_local_licence.py` | Issues a licence locally so activation can be exercised without the live service |
| `installer/nimbus.iss` | Installer. **No run key and no startup shortcut** |
| `.github/workflows/release.yml` | Publishes to **this** repository's releases, with the injected token |
| `updates.py` | Checks a repository readable with no credentials — the public source repository |

`_baked` never raises: a missing generated module is a development checkout, and the environment variable
covers it.

One release-workflow rule exists because its absence produced a silent success: **read the asset list
back** and confirm the installer is present. A publish step that reports success while uploading nothing
is indistinguishable from a working release until someone tries to download.

There is deliberately **no credential check** beside it. The token the platform injects cannot be absent,
so a guard for it would be dead code, and a guard that can never fire is worse than none: it reads as
though the case is handled.

**Releases live in this repository, beside the commits they describe.** That is what lets
`--generate-notes` produce a changelog about the build it is attached to, and it means one place to look
and no token spanning repositories to maintain.

`updates.REPOSITORY` must name a repository readable with no credentials, and the failure mode is why
that is stated as a requirement rather than left to judgement: a private, renamed or misspelled
repository all answer 404 to an unauthenticated call, none of them raise, and a 404 is read as "no newer
release", so the user is told they are up to date forever with no error anywhere. It is baked into every
installer, so a wrong value cannot be corrected for
anyone who already has one.

The installer writes no auto-start entry, which is why the shell window defaults to opening: every launch
is a person double-clicking a shortcut, and the only useful answer to that is to appear.

## Data Models

Nothing is stored relationally on the client. Five named blobs, each in the credential store or a file
beside it:

| Entry | Contents | Cleared by |
|---|---|---|
| `LICENCE_TOKEN` | The signed subscription token | sign-out, failed verification |
| `LICENCE_KEY` | The key, cached for revalidation | sign-out |
| `TRIAL_TOKEN` | The signed trial token | sign-out, subscription activation |
| `FIRST_RUN_AT` | Install start date, in **two** places | **never** by sign-out |
| `LICENCE_VALIDATED_AT` | Last successful revalidation | sign-out |

Token claims consumed by the client: `kind` (`trial` or `subscription`), `expires_at`, `plan`, `email`,
`seats_used`, `seats_total`. Anything else the service adds is ignored, so the service can grow claims
without a client release.

```python
@dataclass(frozen=True)
class LicenceState:
    activated: bool = False
    plan: str = ""
    email: str = ""
    device_name: str = ""
    seats_used: int = 0
    seats_total: int = 0
    expires: str = ""
    offline_grace_days_left: int | None = None
    detail: str = ""
    kind: str = "none"              # none | trial | subscription
    trial_days_left: int = 0        # trial specifically — the gate and Home read this
    days_left: int = 0              # either kind — the Account page reads this
    expired: bool = False
```

## Correctness Properties

### Property 1: Verification fails closed

For an empty public key, any token — including a validly signed one — is refused. Asserted directly,
because the alternative failure mode is a build that accepts anything.

**Validates: Requirements 2.6**

### Property 2: Only a correctly signed token verifies

For any token, verification succeeds if and only if the signature is valid for the payload under the
configured key. Generator: valid tokens, tokens with a flipped payload byte, tokens with a flipped
signature byte, tokens signed by a different key, tokens with the wrong number of separators, empty
tokens, and payloads that are valid base64 but not a mapping.

**Validates: Requirements 2.1, 2.8, 2.11**

### Property 3: Every raised message is user-presentable

For every reachable error, the message contains no stack fragment, no module path, no exception class
name and no file path. These strings are shown in a dialog.

**Validates: Requirements 2.9**

### Property 4: No private key exists in the repository

A scan of every tracked file finds no private key material and no key-pair header. Asserted as a test,
because the consequence of getting this wrong once is unrecoverable.

**Validates: Requirements 2.4, 2.5**

### Property 5: The device identifier is stable, opaque and bounded

For any machine, two calls return the same value; the value is a fixed-length hexadecimal string; and it
contains no substring of the installation identifier, the volume serial or the hostname. Generator: each
lookup independently failing, both failing, all three empty.

**Validates: Requirements 3.1, 3.2, 3.6, 3.7**

### Property 6: Storage round-trips at every size

For any value, storing then reading returns it unchanged. Values at and below the ceiling use the
credential store; values above it use the file. A credential-store failure at any size falls through to
the file. Clearing removes both.

**Validates: Requirements 4.1, 4.3, 4.6, 4.7**

### Property 7: No storage operation raises

For any failure of the credential store or the filesystem — locked, absent, read-only, full — store, read
and clear all return normally.

**Validates: Requirements 4.8**

### Property 8: The first-run date only ever moves earlier

For any combination of the two records — both present, either missing, either restored — the resolved date
is the earliest available. Restoring a deleted record never yields a later date than before.

**Validates: Requirements 5.3, 5.4**

### Property 9: Signing out preserves the trial clock

For any state, signing out leaves the first-run record byte-identical. Asserted directly, because this is
the one deletion that would hand back a fresh trial.

**Validates: Requirements 5.6, 11.2**

### Property 10: A subscription always beats a trial

For any pair of stored tokens, the reported state is derived from the subscription. Generator: valid and
expired subscriptions against valid and expired trials, in both storage orders.

**Validates: Requirements 5.7**

### Property 11: Day counts never read zero while the licence works

For any expiry strictly in the future, the reported days are at least one. For any expiry at or in the
past, they are zero. Generator: remaining times from one second to the full period, in seconds.

**Validates: Requirements 6.1, 6.2**

### Property 12: Day counts never exceed the nominal period

For any freshly issued licence, the reported days are at most the nominal period. Generator: issue times
sampled across a full day, which is what surfaced the just-under-seven-days case.

**Validates: Requirements 6.3, 6.5**

### Property 13: The two day fields agree on kind

For any trial, both fields are equal. For any subscription, the trial-specific field is zero and the
kind-agnostic one is the real count. No input makes an activated licence look like a trial.

**Validates: Requirements 6.6, 6.7**

### Property 14: Reading the state never raises

For any stored content — absent, empty, truncated, valid base64 of nonsense, a token signed by a
different key, a token with missing claims — the state read returns a value. An unverifiable token is
cleared, so a second read returns the not-activated state.

**Validates: Requirements 7.1, 7.3**

### Property 15: The state's shape matches the page's contract

Every field the account page reads is present on the state with a compatible type. Asserted structurally,
so a field rename cannot break the page silently.

**Validates: Requirements 7.2**

### Property 16: Grace is reported only when meaningful

For a licence validated within the day, the grace figure is absent. For an older validation, it is the
remaining days, floored at zero and never above the configured period.

**Validates: Requirements 7.4**

### Property 17: The grace period keeps an expired subscription running

For any subscription whose expiry has passed but whose last validation is within the grace period, the
run decision is yes. Outside the grace period, no. For a trial, an expired token is never graced.

**Validates: Requirements 7.5, 7.6**

### Property 18: Revalidation never clears on anything but a refusal

For every failure mode — unreachable service, timeout, server error, malformed body, unsigned token,
wrongly signed token — the stored token and key are unchanged afterwards and the returned state equals the
prior state. For a client-error refusal, and only then, the cache is cleared.

**Validates: Requirements 8.3, 8.5, 8.6, 8.7**

### Property 19: Revalidation is periodic, not per launch

For a licence validated within the interval, the check says no. For one validated longer ago, or never
validated, yes. With no licence at all, no.

**Validates: Requirements 8.1, 8.9**

### Property 20: A rejection is never retried against a fallback

For any candidate list of any length, a service that answers with a rejection is the final answer, and no
later candidate receives a request. Only unreachability advances the loop. Generator: rejection at each
position in the list.

**Validates: Requirements 9.5, 9.6, 9.7**

### Property 21: Redirects are followed

For a service responding with a redirect to a working endpoint, the operation succeeds. Asserted directly
against a redirecting fixture, because this is the defect that bricked every shipped installer's licence
path.

**Validates: Requirements 9.8, 9.9**

### Property 22: The candidate list degrades to a single value

For a single configured address, behaviour is identical to having no list at all. For an empty or
whitespace-only configuration, the default is used.

**Validates: Requirements 9.1, 9.2**

### Property 23: Every activation route ends in the same state

For any successful route — key, login, register-and-verify, trial — the stored entries, the last-validated
record and the returned state are indistinguishable by route, except for which token entry is written.
Nothing downstream can tell which was used.

**Validates: Requirements 10.5**

### Property 24: The password is never stored

For any login, no stored blob and no file contains the password or any derivative of it. Asserted by
scanning every written value.

**Validates: Requirements 10.4**

### Property 25: Nothing is stored before verification

For any service response containing an unsigned or wrongly signed token, no blob is written. Generator: a
response with a valid shape but an invalid signature, at every route.

**Validates: Requirements 10.14**

### Property 26: Input is validated before any request

For empty keys, addresses without an at sign, short passwords and short codes, no request is made and the
error names what is missing.

**Validates: Requirements 10.15**

### Property 27: A seat release always clears locally

For any outcome of the service call — success, rejection, unreachable — the local licence, key and trial
are cleared afterwards, and the return value reflects whether the service acknowledged.

**Validates: Requirements 11.3, 11.4, 11.5**

### Property 28: The gate runs before the listener and the microphone

Static analysis of the entry point confirms the gate call precedes both. Asserted as a test, because the
ordering is the whole point of having a gate.

**Validates: Requirements 12.1**

### Property 29: A gate crash starts the application and leaves a record

For any exception raised anywhere inside the gate, the application starts and a failure record exists on
disk. Generator: an exception injected at each import and each call inside the gate.

**Validates: Requirements 12.8, 12.9, 12.10**

### Property 30: The gate has no module-level dependency it does not import

Static analysis finds every name the gate function references resolved by a local import or a parameter.
This is the property whose absence meant the gate never ran.

**Validates: Requirements 12.6, 12.7**

### Property 31: Every lazily imported module is registered twice

For every module imported inside a function anywhere in the application, that module appears in both the
bundler's hidden-import list and the selftest's runtime module list.

**Validates: Requirements 13.9**

### Property 32: A missing baked module is not an error

With no generated module present, reading any baked constant returns empty and nothing raises. The
environment override still works.

**Validates: Requirements 13.4, 13.5, 13.6**

### Property 33: Version comparison is a pure total function

For any pair of version strings, including malformed ones, comparison returns a boolean and never raises.
Ordering is consistent and antisymmetric on well-formed inputs.

**Validates: Requirements 15.3**

### Property 34: Hashing and verification agree on parameters

For any password, hashing then verifying succeeds, and the parameters used are identical on both paths.
Asserted because a mismatch would fail against every stored hash rather than only new ones.

**Validates: Requirements 16.1, 16.4**

### Property 35: An unsigned release cannot be published

For an absent or empty signing credential, the workflow fails and no asset is uploaded. Asserted the same
way the existing publish guard is, because the failure this prevents is a release that reports success
while shipping the warning it was meant to remove.

**Validates: Requirements 17.3, 17.5**

### Property 36: The published asset carries a valid signature

After publishing, the asset list is read back and the installer's signature verifies against the expected
subject. Read back rather than assumed, for the same reason the existing verification reads the asset
list: a step that reports success is not evidence.

**Validates: Requirements 17.1, 17.6**

### Property 37: Bundle verification runs on the signed artefact

The check that the licence modules are present in the bundle passes against the file that ships, not the
file before signing. Asserted by ordering, because signing rewrites the file and a check that ran only
before it would prove nothing about what a user downloads.

**Validates: Requirements 17.7**

### Property 38: The build degrades to unsigned locally

With no certificate configured, the build produces a working installer and says it is unsigned. Asserted
directly, because development must not depend on a paid credential.

**Validates: Requirements 17.8**

## Error Handling

| Failure | Response | Why |
|---|---|---|
| No public key in the build | Refuse every licence | Fail closed; the build script reports it first |
| Malformed or tampered token | Raise a user-readable error, clear the stored copy | Asked once beats broken forever |
| Both device lookups fail | Fall back to the hostname | Weaken the binding, never block a tester |
| Credential store refuses a write | Fall back to a file | Otherwise re-activation on every launch |
| Credential store and file both fail | Return false | Caller decides; nothing raises |
| One first-run record deleted | Use the other | Two records; earliest wins |
| Service unreachable | Try the next candidate, then raise a readable error | Speed, not safety |
| Service answers with a rejection | That is the answer; do not retry | A fallback must not overrule the primary |
| Service returns a 5xx | "Temporarily unavailable, try again shortly" | Distinguishable from a rejection |
| Response is not JSON, or not a mapping | Readable error | This is what the redirect looked like |
| Redirect response | **Follow it** | The address is baked into shipped installers |
| Revalidation fails for any reason | Keep the cached licence | Our outage must not be their lockout |
| Revalidation refused with a 4xx | Clear the cache | A revoked key or a seat limit is a real answer |
| Seat release call fails | Clear locally anyway | The user asked; the server reclaims the seat |
| Verification of a returned token fails | Store nothing | Otherwise the cache is poisoned |
| The gate raises anything at all | Start anyway, write a file | Our bug must not lock out a legitimate user |
| No console in a frozen build | Write failures to a file | This is why the original defect was invisible |
| Publishing credential empty | Fail the workflow immediately | A silent no-op publish looks like success |
| Published asset missing | Fail the workflow | Read the list back rather than trusting the exit code |
| Update check returns not-found | Treat as an error, not as up-to-date | This is what silenced updates for every user |

## Testing Strategy

`tests/test_licensing.py`, plus the contract test on the service side that asserts the token format byte
for byte.

- **Sign with a throwaway key in the tests.** The environment override exists for exactly this, so the
  full verification path is exercised without the real key going anywhere near a test run.
- **Both directions of every failure decision.** For each of unreachable, 4xx, 5xx, malformed body and
  bad signature, assert what happens to the cache. The property that matters is not "revalidation works"
  but "revalidation cannot lock anyone out", and that needs the failure cases.
- **The day-rounding tests exist because a user saw the bug.** One is a unit test with a hand-built
  token; the other required driving the real client against the real service, because a unit test with a
  round number would never have produced the just-under-seven-days case. Both are in the suite now.
- **The redirect test uses a redirecting fixture**, not a mock that returns the final response. A mock
  would pass with the defect present.
- **The gate is tested by injecting an exception at each import and each call inside it**, asserting the
  application still starts and a record lands on disk. That is the direct regression test for the defect
  that made the gate never run.
- **Static assertions, not conventions.** No private key in the repository. Every lazily imported module
  in both registration lists. The gate call ordered before the listener and the microphone. Each of these
  is a test because each is invisible until it costs something.
- **A contract test across the boundary.** The service's own test suite asserts the exact token bytes the
  client will accept — sorted keys, no whitespace, no base64 padding — because the two halves are written
  in different languages and cannot share a serialiser.
- **No test touches** the real credential store, the real service or the real data directory. The keyring
  fixture and `tmp_path` throughout.
- **Manual verification, required, and against the live service.** Activate with a key on a clean
  machine. Activate the same key on a second machine and confirm the seat count moves. Activate on a
  third and confirm refusal. Disconnect the network and confirm Nimbus still starts. Advance the clock
  past the grace period and confirm it stops. Sign out and confirm the gate reappears. Register, receive
  the code, verify, and confirm the trial reads the full period rather than one day short.
