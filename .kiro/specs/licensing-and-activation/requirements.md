# Requirements Document

## Introduction

This feature starts from an admission rather than a goal:

> **A local desktop application cannot enforce payment. It can only deter casual sharing.**

Nimbus ships as a frozen Python bundle. That bundle can be unpacked, decompiled, and a licence check
patched out. This is not a defect in the implementation — it is true of every locally executing
application, including ones from companies with large security teams. It bites harder here for an
architectural reason: payment gating is genuinely enforceable when the gated value lives on *your*
server, and Nimbus's value lives entirely on the *user's* machine. Their key, their model calls, their
screen. There is nothing on a server to withhold.

The one option that would be genuinely enforceable — proxying inference through a server — is a
**recorded non-goal**. It would end bring-your-own-key, add per-user cost and latency, create a privacy
liability, and make the "nothing leaves your machine" claim on the local path untrue. Choosing it would
mean knowingly reversing a decision that is the product's main differentiator.

So the design optimises for three things, in this order: **honest use is never inconvenienced**,
seat abuse is *visible and revocable*, and **offline use keeps working**. A crack is possible but
pointless rather than prevented.

**Scope note, stated up front.** The activation layer, the account flow, the trial and the token signing
are built and working. The **payment rails are not connected** — the Stripe and manual-transfer code
exists and is exercised by tests, but nothing is charged and there is no live billing. Where this
document uses the word "subscription" it is naming a *token kind*, which is a code identifier, not a
commercial claim. Nimbus was used during development by a small group of testers, mostly close friends,
who reported bugs and suggested features; the plan and price constants in the code are placeholders on a
rail that has never been switched on.

Four defects in this area shipped and had to be found in production, all of them **silent**. A
`NameError` swallowed by a blanket exception handler in a windowed build with no console meant the gate
never ran at all. An HTTP client not following redirects meant every licence operation failed the moment
the apex domain started redirecting. An update check aimed somewhere it could not read anonymously took
the resulting 404 as "you are up to date". A hash function threw on every password because a memory limit
was left at its default.
Each is a requirement below, because each was invisible until a user hit it.

> **Provenance.** Consolidated into Kiro's spec format from `SHELL_AND_CHAT.md` §0.1 and §5 `S-10`, plus
> the module contract in `licensing.py` and the build and release tooling. Every measurement quoted —
> the credential-store size ceiling, the day-rounding defects, the redirect failure — is recorded there.

## Glossary

| Term | Meaning |
|---|---|
| **Licence key** | The credential a tester pastes in; no password to store, reset or breach |
| **Token** | A signed, self-verifying blob carrying the licence claims |
| **Device identifier** | A salted hash binding a seat to a machine; never a raw hardware value |
| **Seat** | One activated device against a licence |
| **Offline grace** | The period a cached licence keeps working with no successful revalidation |
| **Revalidation** | A silent periodic check that refreshes the cached token |
| **The gate** | The startup check that decides whether Nimbus may run |
| **Baked constant** | A value written into a generated, uncommitted module at build time |

## Requirements

### Requirement 1: The honest position is recorded in the code

**User Story:** As a maintainer, I want the limits of this mechanism written down where the code is, so
that nobody later mistakes deterrence for enforcement and over-invests.

#### Acceptance Criteria

1. THE module SHALL state plainly that it **deters** casual sharing and does not prevent a determined
   user from patching the check out of a frozen bundle, and that no client-side check can.
2. THE reason the alternative is rejected SHALL be recorded, referring to the existing non-goal:
   proxying inference would end bring-your-own-key.
3. THE three optimisation targets SHALL be stated in priority order: honest use is never
   inconvenienced, seat abuse is visible and revocable, offline use keeps working.
4. Obfuscation SHALL be recorded as a non-goal, with its reason: it adds build fragility and breaks the
   bundler in ways that cost days, in exchange for delaying a determined attacker by an afternoon.
5. Phoning home on every launch SHALL be recorded as a non-goal, with its reason: it makes startup
   depend on the licence service's uptime, which turns our outage into the user's lockout.

### Requirement 2: Tokens verify locally, with one algorithm

**User Story:** As a user on a flight, I want Nimbus to keep working, so that a licence check does not
require a network.

#### Acceptance Criteria

1. A token SHALL be a payload and a signature, each base64url-encoded, separated by a single dot.
2. THE format SHALL deliberately **not** be a standard web token, and the reason SHALL be recorded: the
   standard brings algorithm negotiation with it, and algorithm negotiation is where those libraries get
   broken. One algorithm, no header, nothing to negotiate.
3. Verification SHALL use a public key embedded in the build, so that it needs no network.
4. THE private key SHALL never appear in the repository, SHALL never be sent to a client, and SHALL
   exist only in the licence service's environment.
5. A test SHALL assert that no private key is present in the repository.
6. Verification SHALL fail closed: with no configured public key, every licence SHALL be refused.
7. THE build script SHALL report whether a key is present, rather than leaving its absence to be
   discovered by the first person who tries to activate.
8. Verification SHALL raise on anything suspect — wrong shape, bad signature, unparseable claims — and
   the rationale SHALL be recorded: a tampered token is indistinguishable from a corrupt one from here,
   and both mean "do not trust".
9. Every error raised SHALL carry a message fit to show a user.
10. Base64 padding SHALL be reconstructed on decode, because the encoded halves are stored unpadded.
11. Claims SHALL be rejected unless they decode to a mapping.

### Requirement 3: Device identity is salted, hashed and truncated

**User Story:** As a user, I want my machine bound to my licence without handing over a fingerprint, so
that a seat check does not become tracking.

#### Acceptance Criteria

1. THE device identifier SHALL be a salted hash of the operating system's installation identifier, the
   system volume's serial, and the hostname, truncated to a fixed length.
2. A raw hardware identifier SHALL **never** be sent.
3. THE reason SHALL be recorded as custodial rather than technical: a raw identifier is a fingerprint
   that correlates across every service that receives it, and collecting one makes us responsible for
   it. A salted hash is stable enough to bind a seat to and useless for anything else.
4. THE installation identifier SHALL be read from the operating system's own registry value, and SHALL
   be described as not a hardware serial.
5. THE volume serial SHALL be included because it changes on a reformat, which is the intent.
6. IF both platform lookups fail THEN THE system SHALL fall back to the hostname, weakening the binding
   on an unusual machine rather than blocking a legitimate user.
7. WHERE no material at all is available, a fixed sentinel SHALL be used rather than an empty hash.
8. THE subprocess used for the registry read SHALL suppress its console window, so that a windowed build
   does not flash a terminal.

### Requirement 4: Storage is the credential store, with a measured fallback

**User Story:** As a user, I want my activation to persist, so that I do not re-activate on every
launch.

#### Acceptance Criteria

1. THE system SHALL store tokens in the operating system's per-user credential store as the primary
   location.
2. THE reason SHALL be recorded: it is already a dependency, already encrypted per user, and a licence
   file on disk is trivially copied between machines.
3. THE system SHALL fall back to a file when a value exceeds a measured size ceiling.
4. THE ceiling SHALL be justified by measurement rather than by the documented limit: the credential
   store accepted one kilobyte and **refused two** on the test machine, and a signed token is a few
   hundred bytes, so the store is the primary location and the fallback exists so that a larger value
   is never lost to a write nobody checked.
5. THE consequence of getting this wrong SHALL be stated: a licence that fails to persist means re-activation on every launch.
6. Reading SHALL try the credential store first and then the file, so that either location works.
7. Clearing SHALL remove both locations.
8. Every storage operation SHALL swallow its own failures and report success as a boolean rather than
   raising.

### Requirement 5: The trial is device-keyed, with two local records

**User Story:** As a new tester, I want a week of unrestricted use, so that I can find out whether it
helps before committing to anything.

#### Acceptance Criteria

1. THE trial SHALL be a signed token with the same verification path as a subscription, differing only
   in its claims.
2. THE trial SHALL be keyed on the device identifier **on the server**, and the reason SHALL be
   recorded: that is where trial abuse is actually stopped. A local-only trial is defeated by deleting a
   file, and a new email address must not get a second trial on the same machine.
3. THE system SHALL keep **two** independent local records of when the install first ran: a credential
   entry and a file, written together and read together.
4. THE earliest record SHALL win, and the reason SHALL be recorded: clearing one is a plausible
   accident, clearing both is not, and earliest-wins means restoring one does not hand back a fresh
   trial.
5. THE local records' purpose SHALL be stated honestly: they stop only the most casual reset, and they
   exist so an offline first run still has a start date.
6. Signing out SHALL **not** clear the first-run record, because signing out is not a way to restart the
   trial clock.
7. A real subscription SHALL take precedence over a trial when both are present, so that someone whose
   activation lands mid-trial is not told they have three days left.
8. Activating a subscription SHALL clear any trial token.

### Requirement 6: Day counts round up, and are clamped

**User Story:** As a user, I want the days-remaining number to match the fact that the application is
still working, so that I do not distrust the rest of the screen.

#### Acceptance Criteria

1. Days remaining SHALL be rounded **up**, so that a licence with six hours left reads as one day rather
   than zero.
2. THE reason SHALL be recorded: saying zero while the application runs is the kind of small dishonesty
   that makes someone distrust everything else on the screen.
3. Days remaining SHALL be clamped at the top to the nominal period, and the reason recorded: a fresh
   install has just under the full period remaining, which rounds up to one more than the promise, and
   promising eight days of a seven-day trial is a worse first impression than it looks.
4. THE clamp SHALL be recorded as caught by a test rather than by a user.
5. THE same rounding SHALL apply to the token-derived count, and the defect being fixed SHALL be
   recorded: flooring made a freshly issued trial read one day short the moment it was granted, because
   the token expires in just under the nominal period. That one was **caught by driving the real client
   against the real service, not by a unit test.**
6. Days remaining SHALL be exposed both as a trial-specific value and as a kind-agnostic value.
7. THE reason for two fields SHALL be recorded: the gate and the home page read the trial-specific one
   and mean "days of *trial* remaining", so widening it in place would have made an activated licence look like
   a trial to both.
8. An activated licence SHALL get a countdown too, and the reason recorded: the page previously answered "what am
   I on" and not "how long have I got", and the second question is the one people open that page for.
   Days rather than a bare date, because a relative figure needs no arithmetic.

### Requirement 7: Reading the current state never raises

**User Story:** As a user, I want the account page and the gate to always render something, so that a
storage hiccup is not a crash.

#### Acceptance Criteria

1. THE state read SHALL never raise.
2. THE state SHALL be a frozen record whose fields match, field for field, the shape the account page
   already declares, so that the page needs no change and the shell depends on a shape rather than on
   this module.
3. IF a stored token cannot be verified THEN THE system SHALL clear it, and the reason SHALL be
   recorded: a token we cannot verify is worse than none, so the user is asked once rather than shown a
   broken state forever.
4. THE offline-grace figure SHALL be absent rather than full when the licence was checked recently, and
   the reason recorded: the page renders it only when it is meaningful, and a permanent grace line is
   noise on a machine that has been online all week.
5. THE run decision SHALL include the offline grace: a cached subscription whose expiry has passed SHALL
   still be honoured for the grace period past the last successful revalidation.
6. THE reason SHALL be recorded: the common reason for an expired token is a laptop that has not been
   online, not a lapsed card.
7. THE detail string SHALL differ by kind and by expiry, so that a lapsed subscription, an ended trial
   and a healthy licence each read differently.

### Requirement 8: Revalidation is silent, periodic, and never a lockout

**User Story:** As someone relying on this day to day, I want a service outage not to become a
lockout.

#### Acceptance Criteria

1. Revalidation SHALL happen on a fixed interval rather than on every launch.
2. Revalidation SHALL be silent when it succeeds.
3. Revalidation SHALL **never** clear a good licence because the network was down.
4. THE distinction SHALL be stated: that is the difference between a revalidation and a lockout.
5. Only an explicit refusal from the service — a revoked key, a seat limit — SHALL clear the cache, and
   that arrives as a client-error status.
6. IF the service is unreachable THEN THE cached state SHALL be returned unchanged.
7. IF the returned token fails verification THEN THE cached state SHALL be returned unchanged.
8. WHERE no licence key is cached, revalidation SHALL be a no-op returning the current state.
9. THE revalidation check SHALL return false when there is no licence at all, and true when there is one
   that has never been validated.

### Requirement 9: The service address is a list, and redirects are followed

**User Story:** As a user with an installer built months ago, I want licence operations to keep working
after the site's hosting changes.

#### Acceptance Criteria

1. THE service address SHALL be resolved from the environment first, then a baked build constant, then a
   default.
2. THE address SHALL accept a comma-separated **list**, and a single value SHALL behave exactly as
   before.
3. THE reason SHALL be recorded: during development the real answer is both — the deployed site is what
   ships and a local development server is what can actually be tested against before the domain exists.
   One value meant editing it back and forth, and forgetting to edit it back is how a shipped build ends
   up talking to a local address.
4. THE safety of including a local address SHALL be recorded: every token is signed and verified against
   the embedded public key, so a local impostor achieves at most a refused activation, which is the same
   as no answer. Ordering matters for **speed**, not safety.
5. A request SHALL move on to the next candidate **only when a service cannot be reached at all** — a
   name-resolution failure, a refused connection, a timeout.
6. Anything the service actually answers, including a rejection, SHALL be that answer and SHALL be
   returned or raised as such.
7. THE reason SHALL be recorded: retrying a *rejection* against a second service would turn one wrong
   password into two attempts against two rate limiters, and would let a fallback overrule a real answer
   from the primary.
8. THE HTTP client SHALL **follow redirects**.
9. THE defect being fixed SHALL be recorded in full: the client does not follow redirects by default, and
   that default broke every licence operation the moment the site went live — the host redirected the
   apex to a subdomain with a permanent redirect, the client saw a non-JSON body reading "Redirecting…",
   and every activation failed with an unhelpful message about an unexpected response.
10. THE severity SHALL be recorded: the service address is **baked into shipped installers**, so it
    cannot be corrected for anyone who already has one. A licence client that breaks on a redirect is a
    client a future hosting change can brick in the field.
11. THE request timeout SHALL be short, because activation is interactive and a hung request reads as a
    broken application.
12. A server-error status SHALL produce a "temporarily unavailable, try again shortly" message rather
    than a generic failure.
13. A non-mapping response body SHALL be rejected.

### Requirement 10: There are three ways in, ending in one state

**User Story:** As a tester who cannot find my licence key, I want to sign in with the account I
registered with, so that "check your email from three weeks ago" is not the answer.

#### Acceptance Criteria

1. THE system SHALL support activation by licence key.
2. THE system SHALL support activation by the email and password the tester registered with.
3. THE reason SHALL be recorded: "where is my licence key" is the most predictable question a desktop
   application with accounts gets, the tester already has an account because registration creates one,
   and signing in is less friction for them and no extra risk to the key.
4. THE password SHALL be used once and **never stored**. The service SHALL return the licence key
   alongside the token, and that key SHALL be what is cached for revalidation.
5. THE outcome SHALL be identical to the key route, so that nothing downstream can tell which was used.
6. THE seat limit SHALL be the same check either way, because the server binds the machine's salted hash
   to the licence.
7. A login count SHALL be recorded as the wrong mechanism, with its reason: signing out would defeat it,
   and a hardware seat cannot be.
8. THE system SHALL support creating an account from inside the application, followed by a code
   verification that also starts the trial.
9. Verification and trial start SHALL be **one call**, and the reason recorded: from the user's side it
   is one action, and splitting it would give the flow two ways to fail halfway — and a verified account
   with no trial is a support conversation nobody wants to have.
10. THE server SHALL decide what comes back, so that an account which already has a subscription gets a
    subscription token rather than a trial, and someone already activated who then reinstalls is not handed a
    fresh trial period.
11. THE trade of an anonymous trial for an identified one SHALL be recorded with its reasoning: a device
    hash stops a second trial and is useless for everything else — nobody to email when a trial is
    ending, nobody to answer "I registered, where is my key", and no way to reach a tester who stopped
    using it to ask why.
12. THE cost to the user SHALL be stated honestly: one email address and six digits typed into a window
    that is already open, and **not** a card, and **not** any of the trial days.
13. A browser signup route SHALL exist as an escape hatch rather than the main road, for the cases the
    in-application form cannot resolve: an address that already has a subscription, a password needing a
    reset, or someone who does not want to type a new password into a desktop window they met a minute
    ago.
14. Every route SHALL verify the returned token **before** storing it, and the reason recorded: a service
    returning an unsigned or wrongly signed token would otherwise poison the cache, and the failure
    would surface later as an unexplained lockout rather than here, where there is a dialog to show it
    in.
15. Input SHALL be validated before any request: a non-empty key, an address containing an at sign, a
    minimum password length, a minimum number of digits in a code.
16. Signing in SHALL start the trial on a machine that has never had one, when the account's address is
    already verified, and SHALL return the trial token.
17. THE gap being closed SHALL be recorded. Signing in previously returned an existing trial and nothing
    else, so a verified account on a second machine, or on the same machine after its credentials were
    cleared, was refused with "that account has no active licence yet" while the machine itself was
    perfectly eligible. The only way through was to ask for a new code, which is the flow the person had
    already completed once and had no reason to expect again.
18. THE anti-abuse property SHALL be unchanged by this, and the reason recorded: the trial is counted
    against the **device**, not the account, so an account that seeds a trial on a second machine has
    given that machine its one and only trial. A verified password proves ownership of the address
    exactly as a code does, so this adds a second door to the same room rather than a second trial.
19. AN unverified address SHALL be refused with the action that resolves it, namely asking for a code,
    rather than with a statement about licences.
20. THE gate SHALL offer signing in and creating an account as two actions on **one** set of fields, and
    the defect SHALL be recorded: they were two separate cards, each with its own email and password box,
    and the sign-in card was headed "lost your key". A returning user reported that there was no way to
    sign in at all. There was, three inches lower, under a question they were not asking.

### Requirement 11: Signing out and releasing a seat are different actions

**User Story:** As a user moving to a new machine, I want to free my seat, so that I am not blocked by
my own old computer.

#### Acceptance Criteria

1. Signing out SHALL clear the licence token, the licence key, the trial token and the last-validated
   record.
2. Signing out SHALL leave the first-run record alone.
3. Releasing a seat SHALL notify the service and then sign out locally.
4. Local state SHALL be cleared **even if the service call fails**, and the reason recorded: the user
   asked to sign this machine out, and leaving a working licence behind because a request timed out would
   be the wrong answer to a deliberate action. The seat is reclaimed by the next revalidation from the
   server side.
5. THE release SHALL report whether the service acknowledged, so the interface can say so.
6. Signing out from inside the application SHALL cause the gate to run again rather than leaving the
   application in a signed-out but running state.
7. THE re-run SHALL be deferred rather than invoked inline, because it is triggered from a widget's own
   signal handler and re-entering a modal flow from there is not safe.
8. THE defect being fixed SHALL be recorded: sign-out previously did nothing observable.

### Requirement 12: The gate runs at the right moment, and a gate failure is not a lockout

**User Story:** As a legitimate user, I want a bug in the licence check never to lock me out of the
application.

#### Acceptance Criteria

1. THE gate SHALL run after the application object exists and **before** the hotkey listener installs
   and before the microphone opens.
2. THE reason SHALL be recorded: an unlicensed instance should consume no devices and register no global
   hooks.
3. THE gate SHALL be a blocking, modal flow.
4. THE gate SHALL never silently degrade, and SHALL offer a retry and an offline option on a service
   failure, because a legitimate user must never be left guessing.
5. THE gate SHALL be extracted into its own function rather than written inline at the entry point.
6. All imports the gate needs SHALL be **local to that function**.
7. THE defect being fixed SHALL be recorded in full, because it is the worst kind: a name error on a
   module referenced but not imported was swallowed by a blanket exception handler, in a windowed build
   with no console, so **the gate never ran at all** and nothing anywhere reported it. The application
   started unlicensed and looked completely normal.
8. A gate **evaluation** failure SHALL NOT be a lockout: the caller SHALL catch it and start anyway.
9. Any such failure SHALL be recorded to a file, so that it is discoverable after the fact rather than
   lost with the absent console.
10. THE distinction SHALL be stated: refusing to run because the licence is invalid is correct; refusing
    to run because *our check* crashed is not.

### Requirement 13: The signing key ships without being committed

**User Story:** As a maintainer, I want the public key in the installer and nothing secret in the
repository.

#### Acceptance Criteria

1. THE build-time constants SHALL be written into a generated module by a tool.
2. THE generated module SHALL be excluded from version control.
3. THE module SHALL contain only the **public** half of the key pair and the service address.
4. A missing generated module SHALL be normal rather than an error, because that is a development
   checkout, and the environment variable covers it.
5. Reading a baked constant SHALL never raise.
6. THE environment SHALL take precedence over the baked constant, so that the test suite can sign with a
   throwaway key and a staging service needs no rebuild.
7. A tool SHALL exist to issue a licence locally for testing, so that the activation path can be
   exercised without the live service.
8. THE build SHALL verify the produced bundle rather than assuming it, including that the licence
   modules are present.
9. Every lazily imported module SHALL appear in **both** the bundler's hidden-import list **and** the
   selftest's runtime module list, because a module behind a default-off toggle is invisible to the
   bundler's static graph *and* to the selftest, so it fails first in a user's frozen build.

### Requirement 14: Release publishing fails loudly

**User Story:** As a maintainer, I want a broken release to fail the build rather than publish nothing.

#### Acceptance Criteria

1. THE release workflow SHALL publish installers to a repository whose assets and API are readable
   **without credentials**, which is the public source repository itself.
2. THE reason SHALL be recorded: releases belong beside the commits they describe, so the changelog
   matches the build, there is one place a reviewer has to look, and no token spanning repositories
   has to be maintained.
3. THE workflow SHALL use the credential the platform injects rather than a stored token, so there is no
   secret that can be absent and none to rotate.
4. THE workflow SHALL check the exit status of every publishing call.
5. THE workflow SHALL read the published asset list back and confirm the expected installer is present.
6. THE reason SHALL be recorded: a publish step that reports success while uploading nothing is
   indistinguishable from a working release until someone tries to download.
7. THE installer SHALL not write any auto-start entry, and the consequence SHALL be recorded where it
   matters: every launch is therefore a person double-clicking a shortcut, which is why the window
   defaults to opening.
8. THE installer's update link SHALL point at the releases page rather than at a website path, and the
   defect SHALL be recorded: it pointed at a `/releases` route the site does not have, so the "check for
   updates" link in the operating system's own settings was a 404 for every installed copy.
9. THE workflow SHALL bake **both** the licence public key and the service address, and SHALL fail the
   build when either is unavailable rather than publishing.
10. THE defect SHALL be recorded, because it shipped. The workflow baked only the public key. A release
    checkout holds no generated module for the tool to preserve an address from, so the generated module
    was written **without one**, and the installed application fell through to the reserved default. The
    visible symptom was not a refused licence: it was the account link in the activation dialog opening
    a browser at a domain reserved for documentation. Every activation in that build would also have
    failed, at a host that cannot exist.
11. THE build SHALL verify the address after baking it and SHALL fail when it is the reserved default,
    because the two ways of getting this wrong, an unset variable and a tool invoked without the flag,
    both produce an installer that looks correct and cannot activate anybody.
12. THE reason for failing rather than warning SHALL be recorded: a warning had already been chosen for
    the public key, and a warning was missed, publishing an installer that refused every licence. An
    installer that cannot activate is not a release, so not publishing is the better outcome.
13. THE address SHALL come from repository configuration rather than from the source tree, for the same
    reason the key does: it varies per deployment, and a value committed beside the code is a value that
    is wrong for everyone who deploys somewhere else.

### Requirement 15: The update check reaches a repository it can actually read

**User Story:** As a user, I want to be told when a new version exists, so that I am not stuck on an old
build.

#### Acceptance Criteria

1. THE update check SHALL query a repository that is readable **with no credentials**, which is now the
   public source repository itself.
2. THE defect being fixed SHALL be recorded: it queried a *private* repository, which returns a
   not-found status to an unauthenticated request, and that was interpreted as "you are up to date" — so
   every user was silently told there were no updates, forever.
3. THE failure mode SHALL be recorded as the reason the target matters: an update check aimed somewhere
   it cannot read does not raise. It reads a 404 as "no newer release" and reports success, so there is
   nothing to notice and no error anywhere.
4. THE target SHALL be baked into every installer, so a wrong value is **not correctable** for anyone
   who already has one.
5. THE version comparison SHALL be a pure function, testable without a network.
6. A failed check SHALL be silent to the user and SHALL never block startup.

### Requirement 16: Password hashing has an explicit memory limit

**User Story:** As a tester creating an account, I want registration to work.

#### Acceptance Criteria

1. THE server-side password hash SHALL declare an explicit maximum memory parameter.
2. THE defect being fixed SHALL be recorded with its numbers: the chosen parameters need roughly 64
   megabytes, the runtime's default cap is roughly 32, so the call threw on **every** password until the
   limit was raised.
3. THE measured cost SHALL be recorded, so that the parameter choice is a known trade rather than a
   guess.
4. THE hash and verify paths SHALL both use the same parameters, or verification would fail against
   stored hashes.

### Requirement 17: The installer is signed

**User Story:** As someone who has just been sent this by a friend, I want Windows not to warn me that
the publisher is unknown, so that I install it instead of deleting it.

> **Not built — `T4-8`.** Recorded as low effort and blocked on cost: a code-signing certificate has to
> be bought and, for the version of the warning that actually disappears, the identity behind it
> validated. This is the one item in this spec whose blocker is money rather than a decision.

#### Acceptance Criteria

1. THE published installer SHALL be signed with a certificate whose subject identifies the publisher.
2. THE reason SHALL be recorded with what it costs: an unsigned installer makes SmartScreen warn every
   person who downloads it, and a warning shown to someone who has never heard of Nimbus is where most
   of them stop.
3. Signing SHALL happen in the release workflow rather than on a developer machine, so that a release
   cannot be published unsigned by forgetting a step.
4. THE signing credential SHALL be held as a workflow secret and SHALL never appear in the repository,
   under the same rule that already governs the licence signing key.
5. THE workflow SHALL fail loudly if the credential is absent, rather than publishing an unsigned
   installer, and this SHALL follow the existing rule from Requirement 14: a publish step that reports
   success while doing the wrong thing is indistinguishable from a working release.
6. THE existing post-publish verification SHALL be extended to confirm the published asset carries a
   valid signature, so that "it was signed" is read back rather than assumed.
7. THE bundle verification from Requirement 13.8 SHALL continue to pass on the signed artefact, because
   signing rewrites the file and a check that only ran before signing would prove nothing about what
   ships.
8. WHERE no certificate is configured, the build SHALL still produce a working unsigned installer for
   local testing, so that development does not depend on a paid credential.
