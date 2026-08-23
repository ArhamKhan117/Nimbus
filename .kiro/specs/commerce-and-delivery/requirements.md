# Requirements Document

## Introduction

This is the other half of the licence system: the service that signs the tokens the desktop application
verifies, the accounts behind them, and the payment rails that create them. It is a Next.js application
on Postgres, deployed serverless, and every design decision in it is shaped by two constraints that are
easy to forget until they bite.

**The filesystem is ephemeral.** A file-backed database would be wiped on every deploy, taking the
account list with it. So Postgres, hosted.

**Functions do not share memory.** An in-process rate-limit counter resets on every cold start and
protects nothing. So attempt counters live on the row.

The security core is one file. The desktop application trusts a licence because of a signature made
here and for no other reason, which means a spoofed copy of this site cannot grant a licence without the
private key. That file uses the runtime's own crypto and no dependency at all, deliberately: it is the
one place where supply-chain surface is least acceptable.

Three defects here shipped and had to be found in production. A password hash threw on **every**
password because a memory limit was left at its default, so signup returned a server error while login
reported "no account". A signed session cookie stayed cryptographically perfect after its account was
deleted, so two pages disagreed and the browser bounced between them until it gave up on a blank page.
And the build command does not typecheck, so a type error could reach production while the build stayed
green.

One thing is deliberately **not** automated. The manual transfer rail records evidence and does **not**
mint a licence, because there is no signing secret on that rail and a claim is not a confirmation.
Approval is a human action, and the page says so rather than showing a spinner that lies.

**Scope note, stated up front.** Accounts, the trial, activation, licence signing, delivery and the
health check are built and working. The **payment rails are integrated but not connected** — the Stripe
and manual-transfer code paths exist, are typed, and are covered by tests, but no card processor is
live, nothing is charged, and no money has moved through them. The plan and price constants are
placeholders on a rail that has never been switched on. Where this document says "subscription" it is
naming a *token kind*, which is a code identifier. Nimbus was exercised during development by a small
group of testers, mostly close friends, who found bugs and asked for features.

> **Provenance.** Consolidated into Kiro's spec format from the service's own module contracts —
> `schema.prisma`, `licence.ts`, `licences.ts`, `auth.ts`, `easypaisa.ts`, `errors.ts` — and its 23 API
> routes. The measured hash cost, the redirect-loop account and the EasyPaisa gateway research are all
> recorded there.

## Glossary

| Term | Meaning |
|---|---|
| **Claims** | The fields signed into a licence token |
| **Seat** | One active device bound to a licence |
| **Issuance** | Creating or extending a licence for a tester |
| **Idempotent** | Repeated calls produce one licence, not several |
| **Stale session** | A valid cookie for an account that no longer exists |
| **Hosted checkout** | A payment flow where the tester is redirected to the provider |
| **Manual transfer** | A payment that arrives out of band and is approved by hand |
| **Enumeration** | Learning which addresses are registered by submitting a list |

## Requirements

### Requirement 1: Seven models, and no more

**User Story:** As a maintainer, I want the smallest data model that answers the real questions, so that
there is less to keep correct.

#### Acceptance Criteria

1. THE schema SHALL contain exactly seven models: an account, a single-use token, a licence, a device, a
   trial, a manual payment, and an audit event.
2. THE justification SHALL be recorded: the whole data model is who has an account, who is activated, what key
   they got, which machines it is on, whether this device has had its trial, and what happened — and
   every table beyond that is a table someone has to keep correct.
3. THE database SHALL be Postgres rather than a file-backed database, and the reason recorded: this runs
   where the filesystem is ephemeral, so a file would be wiped on every deploy, taking the account holder
   list with it.
4. Deletion SHALL cascade from an account to its licences, tokens and payments, and from a licence to
   its devices.
5. THE audit trail SHALL be append-only, and its purpose stated: when someone says their licence stopped
   working, the answer is in there.

### Requirement 2: Passwords use the runtime's own memory-hard hash

**User Story:** As a tester, I want my password stored properly, so that a database leak does not
expose it.

#### Acceptance Criteria

1. THE password hash SHALL be the runtime's own memory-hard function, salted per account, with no
   third-party password library.
2. THE reason SHALL be recorded: it is in the standard library, it is memory-hard, and it is one less
   dependency to keep patched in the place where a supply-chain problem is worst.
3. THE parameters SHALL be a published starting point, and the measured cost per hash SHALL be recorded
   so the choice is a known trade rather than a guess.
4. THE maximum memory parameter SHALL be declared **explicitly**.
5. THE defect being fixed SHALL be recorded with its arithmetic: the function needs a computable amount
   of memory at these parameters — around 64 megabytes — and the runtime caps it at around 32 unless told
   otherwise, so **every** hash threw a range error. Reads worked and writes did not, so signup returned
   a server error while login reported "no account".
6. THE declared limit SHALL carry headroom well above the current requirement, so that a future
   parameter increase does not silently reintroduce the failure.
7. THE headroom SHALL be justified against the deployment's available memory.
8. Verification SHALL read the parameters back from the stored hash and SHALL declare the same memory
   limit, because verification allocates exactly as much as hashing did.
9. THE comparison SHALL be constant-time, and the reason recorded: a fast equality check on hashes leaks
   how much of the digest matched.
10. THE stored format SHALL carry the algorithm name and every parameter, so a parameter change does not
    invalidate existing hashes.

### Requirement 3: Sessions are a signed cookie, with a stated trade

**User Story:** As a tester, I want to stay signed in across an external redirect, so that a round
trip to another site does not sign me out.

#### Acceptance Criteria

1. THE session SHALL be a signed cookie rather than a database row per login.
2. THE reason SHALL be recorded: a session row is a database round trip on every request, and this
   site's pages are mostly public.
3. THE cookie SHALL be inaccessible to scripts, secure in production, and use the relaxed same-site
   policy — **not** the strict one — because it must survive the redirect back from a payment provider.
4. THE trade SHALL be stated plainly: a signed cookie cannot be revoked server-side before it expires,
   so the realistic worst case is a stolen cookie working until it expires.
5. THE trade SHALL be justified proportionally: for a low-value desktop tool that is the right side of
   the trade, and for anything holding money it would not be.
6. THE signing secret SHALL be required to meet a minimum length, and its absence SHALL be an error
   rather than a default.

### Requirement 4: A session has three states, not two

**User Story:** As a user whose account was deleted, I want to be told to sign in again, so that I do
not land on a blank page.

#### Acceptance Criteria

1. THE system SHALL distinguish **three** states: no cookie, a valid cookie for an account that no
   longer exists, and signed in.
2. THE defect being fixed SHALL be recorded in full: signature verification is by design all a stateless
   cookie checks, so a cookie stays cryptographically perfect after its account is deleted. The sign-in
   page saw a session and redirected to the account page; the account page found no account row and
   redirected back; **neither cleared the cookie**, so the browser bounced between them until it gave up
   on a blank page.
3. THE severity SHALL be recorded: a deleted account is rare in production and routine in development,
   which is exactly the kind of defect that ships.
4. Every page SHALL branch on all three states rather than two.
5. An endpoint SHALL exist to clear a stale cookie, so the loop is breakable from the client.
6. IF the account lookup **throws** THEN THE cookie SHALL be trusted.
7. THE reason SHALL be recorded: a stale cookie is a nuisance for one person, while treating an
   unreachable database as "nobody is signed in" would sign out everybody at once for the duration of an
   outage — and send them to a sign-in page that also cannot reach the database. Failing towards the
   cookie keeps the failure proportional.

### Requirement 5: One model for every single-use secret

**User Story:** As a tester, I want verification codes and sign-in links to work once and expire, so
that an old email is not a way in.

#### Acceptance Criteria

1. Email verification codes, sign-in links and password resets SHALL share one model.
2. THE reason SHALL be recorded: they are the same mechanism with different consequences — proof that
   whoever presented it controls the mailbox — and three half-implementations would be three places to
   get single-use, expiry and hashing wrong.
3. Only the hash of the emailed value SHALL be stored, never the value.
4. THE reason SHALL be recorded: a leaked database must not hand out working sign-in links or
   verification codes, and there is no reason for the operator to be able to read one.
5. A code's hash SHALL be salted with the account identifier, so that a code cannot be checked against a
   different account and the same digits for two people are two different hashes.
6. A code SHALL carry an attempt counter **on the row**, and the reason recorded: a six-digit code has a
   million possibilities, which is plenty against a person and nothing at all against a script, so the
   code is useless without a try limit — and the counter cannot live in memory because serverless
   functions do not share it.
7. Issuing a new code SHALL delete any earlier unused code for the same purpose, and the reason
   recorded: two live codes means "it says the code is wrong" from someone reading the first of two
   emails, which is indistinguishable from a defect.
8. A code check SHALL return distinct outcomes for correct, wrong, expired and too-many-attempts.
9. A code SHALL be consumed on success and SHALL increment the counter on failure.
10. A link SHALL be consumed on use and SHALL be rejected if unknown, expired, already used, or issued
    for a different purpose.
11. THE existence of both a code and a link SHALL be justified: the desktop application is where the
    trial starts, and a link cannot get someone from their inbox back into a native window — clicking it
    opens a browser, which then has to hand off to an application that may not be listening. A code is
    typed into the window that is already open and asking for it. The link stays for the browser, where
    it is the better answer.

### Requirement 6: Repeated failures lock out, without shared memory

**User Story:** As an account holder, I want repeated guesses against my address to stop, so that a
list-based attack does not work.

#### Acceptance Criteria

1. THE account SHALL carry a failed-attempt counter and a lockout timestamp.
2. THE reason for storing them SHALL be recorded: serverless functions do not share memory, so an
   in-process counter would reset on every cold start and protect nothing.
3. A lockout SHALL expire after a fixed period rather than requiring intervention.
4. THE lockout SHALL be checked before any password comparison.

### Requirement 7: Licence keys are unguessable and readable aloud

**User Story:** As someone reading a key off a screen to type it into an application, I want no
ambiguous characters.

#### Acceptance Criteria

1. A key SHALL be a fixed prefix followed by three blocks of characters, separated by hyphens.
2. THE alphabet SHALL exclude the characters that are visually ambiguous, and the reason recorded: these
   keys get read aloud and typed by hand.
3. THE key SHALL be generated from a cryptographically secure source, and the reason recorded: a
   guessable licence key is a licence key everyone has.
4. Key lookup SHALL normalise case and surrounding whitespace.

### Requirement 8: Token signing matches the client byte for byte

**User Story:** As a tester, I want my licence to verify on my machine, so that a serialisation
difference does not break activation.

#### Acceptance Criteria

1. THE token SHALL be a base64url payload and signature separated by a single dot, matching the desktop
   client's verifier exactly.
2. THE format SHALL deliberately **not** be a standard web token, for the same reason recorded on the
   client: the standard carries an algorithm field, and algorithm negotiation is where those libraries
   get broken.
3. THE payload SHALL be serialised over **sorted keys with no whitespace**.
4. THE reason SHALL be recorded: the client verifies the signature over the exact bytes it received, so
   any difference in key order between what was signed and what was sent breaks every licence.
5. Empty, null and undefined claim values SHALL be omitted rather than serialised, so that an absent
   optional field does not change the byte sequence.
6. Signing SHALL use the runtime's own crypto with **no** dependency, and the reason recorded: the
   wrapper the key format needs is a fixed short prefix, small enough that reaching for a dependency
   here would add supply-chain surface to the one file that must not have any.
7. THE private key SHALL be read from the environment, SHALL be required, and its decoded length SHALL
   be validated rather than trusted.
8. A verification function SHALL exist using the public half, so that a deployment with a mismatched key
   pair is caught by a health check rather than by a tester.
9. A test SHALL assert the exact byte sequence the client will accept, because the two halves are written
   in different languages and cannot share a serialiser.

### Requirement 9: Token lifetime is shorter than the billing period

**User Story:** As a maintainer, I want a cancelled subscription to stop working without any explicit
revocation, so that lapsing is the default rather than an action.

#### Acceptance Criteria

1. A token SHALL expire after a fixed maximum, or at the end of the licensed period, whichever is
   sooner.
2. THE maximum SHALL be shorter than the billing period, and the reason recorded: a cancelled
   subscription simply stops being re-signed, so this is the longest a lapsed licence keeps working.
3. THE maximum SHALL be comfortably longer than the client's revalidation interval, so that nobody honest
   notices it.

### Requirement 10: Issuance has exactly one path, and it is idempotent

**User Story:** As a tester, I want one licence key however many times issuance is triggered, so that
I am not holding three and unsure which is mine.

#### Acceptance Criteria

1. THE three ways a licence can be created — a payment webhook, the success page asking before the
   webhook lands, and a hand-approved manual transfer — SHALL share **one** issuance function.
2. THE reason SHALL be recorded: three copies of "issue a licence" is three chances to issue two.
3. Issuance SHALL be idempotent, and the reason recorded with its trigger: the payment provider sends
   more than one event for a single activation, and the success page may ask first, so minting a key per
   arrival would leave one account holding three, two of which count devices nobody is using, and no
   way to know which is theirs.
4. WHERE an active licence exists, issuance SHALL **extend** rather than replace it, because that path is
   also how a renewal arrives.
5. THE period end SHALL move only forward, never backwards.
6. Issuance SHALL record an audit event.
7. THE default seat count SHALL be justified: enough for how one person actually works — a desktop and a
   laptop — and small enough that a key passed round a room runs out immediately, which is the entire
   point of counting seats.

### Requirement 11: A known device is always readmitted

**User Story:** As a tester who reinstalled, I want back onto my own machine, so that a device limit
does not lock me out of my own computer.

#### Acceptance Criteria

1. Binding a device SHALL succeed for a device already on the licence, **even at the seat limit**.
2. THE reason SHALL be recorded: it is re-activating rather than taking a new seat, and refusing it would
   lock a legitimate user out of their own machine after a reinstall.
3. THE frequency SHALL be noted: with a small seat count that is the normal case rather than an edge
   case.
4. A known device SHALL be reactivated and its name refreshed, rather than duplicated.
5. A new device SHALL be refused once the active count reaches the seat total.
6. THE seat total SHALL be treated as at least one, so a misconfigured zero does not lock out everybody.
7. Releasing a device SHALL mark it inactive rather than deleting the row, so the audit trail survives.
8. THE device identifier SHALL be the salted hash the desktop application computes, and SHALL never be a
   raw hardware identifier.
9. THE pairing of licence and device SHALL be unique, so a double request cannot create two rows for one
   machine.

### Requirement 12: A trial belongs to a machine, kept forever

**User Story:** As a maintainer, I want a new email address not to earn a second trial, so that the
trial is a trial.

#### Acceptance Criteria

1. THE trial SHALL be keyed on the device identifier **alone**, as its primary key.
2. THE record SHALL carry no address and no required account relation, and the reason recorded: a new
   address cannot obtain a second trial because the address was never what a trial was counted against.
3. Expired rows SHALL be **kept forever**, and the reason recorded: deleting them would hand back the
   trial.
4. THE record SHALL carry an optional account reference recording *who* asked, while the device still
   decides *whether they may*.
5. THE reference SHALL be nullable only so the column could be added to an existing database without a
   migration that could fail, and new trials SHALL always have one.
6. An existing owner SHALL **never** be overwritten, and the reason recorded: the first verified account
   on a machine is the one the trial belongs to, and letting a later address claim it would make the row
   rewritable by anyone who can reach the endpoint.
7. Starting a trial for a device that already has one SHALL return the **existing** expiry rather than an
   error, and the reason recorded: someone who reinstalls mid-trial has not used their trial up.
8. Refusing an elapsed trial SHALL be the caller's decision, made from the returned flag and date rather
   than inside the trial function.
9. THE expiry SHALL be truncated to the same precision it is stored at, and the reason recorded:
   otherwise the token issued on the first request and the one issued on a reinstall disagree about the
   same trial's expiry.
10. Starting a new trial SHALL record an audit event.

### Requirement 13: The manual rail records evidence and does not mint

**User Story:** As a maintainer, I want a payment claim not to be a payment, so that anyone typing a
reference number cannot grant themselves a licence.

#### Acceptance Criteria

1. THE manual payment endpoint SHALL record a pending payment with the tester's reference and SHALL
   **not** issue a licence.
2. THE reason SHALL be recorded: there is no signing secret on that rail, so the callback carries no
   proof and a claim is not a payment.
3. Approval SHALL be an explicit administrative action, which then calls the shared issuance path.
4. THE manual path SHALL **not** be disguised as automatic, and the reasoning recorded: a page that says
   confirmation happens by hand, usually within a few hours, and then does, is better than a spinner
   that lies.
5. THE manual path SHALL be described as the existing behaviour made less manual rather than as a
   stopgap, because this is how the rail is expected to work once it is connected.
6. A payment record SHALL carry a status and a review timestamp so its lifecycle is visible.
7. THE research behind the automated alternative SHALL be recorded, with its sources cited: the provider
   does have an official gateway, it is not self-serve, and it requires a merchant application, a
   registered business, and issued credentials — so there is no equivalent of simply adding a card
   processor.
8. WHERE credentials are configured, a hosted checkout SHALL be offered; WHERE they are not, only the
   manual transfer SHALL be.
9. THE availability of each path SHALL be derived from the configuration rather than hardcoded, so a
   deployment without credentials shows only what it can honour.

### Requirement 14: The hosted checkout signature is built defensively

**User Story:** As a tester exercising the payment form, I want a well-formed request, so that a
rejection is not the first thing the rail teaches us.

#### Acceptance Criteria

1. THE request parameters SHALL be **sorted** before hashing.
2. THE key length SHALL be validated rather than trusted, with an error naming the actual and expected
   lengths.
3. THE reason SHALL be recorded: integrators report that a rejection almost always traces to parameter
   order, a non-secure callback address, or the wrong key length.
4. THE amount SHALL be a deliberately set local-currency figure rather than a converted one, and the
   reason recorded: a rate that moves under a tester mid-checkout is how a payment ends up short and
   rejected.
5. THE endpoint SHALL differ between the test and live environments, selected by configuration.

### Requirement 15: Error messages live in one place

**User Story:** As a user, I want an error that tells me what to do, so that I am not reading a generic
failure.

#### Acceptance Criteria

1. Every user-facing failure SHALL have an entry in one shared map, carrying both a machine code and the
   sentence to show.
2. THE reason SHALL be recorded: ten routes each inventing their own wording produces ten different ways
   of saying "that did not work", and the ones written last are always the worst.
3. THE second reason SHALL be recorded: a shared map makes **coverage** reviewable, so a missing case is
   visible as a missing entry rather than as a generic message in production.
4. THE decision to reveal whether an account exists SHALL be recorded as a deliberate, argued choice
   rather than an oversight, along with what it gives up — the textbook defence against enumeration — and
   why: the support cost of "it says wrong password and I know my password" is real and constant, while
   enumeration is mostly a nuisance for a product with no social graph to mine, and major providers
   already answer honestly.
5. What is **kept** SHALL be recorded: rate limiting and lockout, which is what actually stops someone
   working through a list.
6. THE reset path SHALL stay silent about whether an address exists, because confirming one to an
   unauthenticated stranger has no upside at all.
7. A client helper SHALL turn any response into a message, handling the case where there is no response
   to read — a network failure — because the browser's own error text means nothing to anyone.
8. A failure that charges nothing SHALL say so.

### Requirement 16: Status codes tell the client what to do

**User Story:** As a desktop client, I want to know whether to clear my cached licence, so that an outage
does not become a lockout.

#### Acceptance Criteria

1. A missing subscription SHALL return the payment-required status rather than the forbidden status.
2. THE reason SHALL be recorded: the client clears its cache on a client-error refusal and keeps it on a
   server error, so the distinction between "you are not activated" and "we are broken" has to be carried by
   the status code.
3. A refusal the client should act on — a revoked key, a seat limit, an unknown key — SHALL be a
   client-error status.
4. A failure on the service's side SHALL be a server-error status, so the client keeps its cache.
5. Every error response SHALL carry both the machine code and the human sentence, so the client can show
   the message without knowing the code.

### Requirement 17: A health check catches a broken deployment before a tester does

**User Story:** As a maintainer, I want a mismatched key pair to fail a check rather than an activation.

#### Acceptance Criteria

1. A health endpoint SHALL sign a token and verify it with the configured public half.
2. THE reason SHALL be recorded: a deployment with a mismatched pair is otherwise caught by a tester
   trying to activate.
3. THE endpoint SHALL report database reachability.
4. THE endpoint SHALL NOT disclose any secret or any part of one.

### Requirement 18: Delivery is a redirect to the public installer, behind sign-in

**User Story:** As a tester, I want the download to work, so that finding it leads to running it.

#### Acceptance Criteria

1. THE site's download endpoint SHALL redirect to the installer published in this repository's releases.
2. THE endpoint SHALL require a session, and SHALL send a signed-out visitor to sign-in carrying a
   return target, so that signing in resumes the download instead of ending on an account page.
3. THE reversal SHALL be recorded with both sides of it. The endpoint previously required no session, on
   the grounds that a licence key needs no account and the licence gate lives in the application. That
   remains true for someone holding a key. The gate is reinstated because the trial is how nearly
   everyone arrives, the trial needs a verified account and an emailed six-digit code, and so an account
   is required within two minutes of launching either way. Asking before a 152 MB download is a better
   order than asking after it.
4. THE gate SHALL fail open on a session that cannot be read, because an unreadable cookie must not be
   able to turn "hand over the installer" into an error.
5. THE apex address SHALL be the canonical one, with any alternative redirecting to it.

### Requirement 19: The build command is not the type gate

**User Story:** As a maintainer, I want a type error to fail before deployment, so that a green build is
not misleading.

#### Acceptance Criteria

1. THE defect SHALL be recorded: the framework's build command does not typecheck, so a type error can
   reach production with the build reporting success.
2. THE real gate SHALL be an explicit typecheck command, run separately.
3. THE build configuration's error-suppressing options SHALL be documented as a **release valve**, not as
   the normal state, so that nobody reads their presence as permission to skip the check.
4. Dependency versions SHALL be pinned rather than ranged.
5. A security advisory against a pinned dependency SHALL be resolved by an explicit version bump, and the
   advisory identifier recorded alongside it.

### Requirement 20: The service is deployed against one region and one canonical host

**User Story:** As a tester anywhere, I want licence calls to be fast and consistent.

#### Acceptance Criteria

1. THE deployment SHALL pin a single region, so that database round trips are not crossing continents
   unpredictably.
2. THE apex host SHALL be the production target, and the alternative host SHALL redirect to it
   permanently.
3. THE consequence for the desktop client SHALL be recorded: the client must follow redirects, because
   the service address is baked into shipped installers and cannot be corrected retroactively.
4. Environment configuration SHALL include a direct database address alongside the pooled one, because
   schema operations need a direct connection.

### Requirement 21: Superseded implementations are marked, not deleted

**User Story:** As a maintainer, I want to know which of two similar directories is live.

#### Acceptance Criteria

1. THE earlier standalone service implementation SHALL be recorded as **superseded** by the current one.
2. Its tests SHALL be noted as still passing, so that its presence is not mistaken for rot.
3. THE reason for keeping it SHALL be recorded, or it SHALL be removed — but its status SHALL NOT be left
   ambiguous.
