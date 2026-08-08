# Requirements Document

## Introduction

Nimbus answers out loud. That is the interaction, and it is the right one — but speech is
unreviewable. A user cannot scroll back to what was said thirty seconds ago, cannot re-read a
shortcut they half-caught, and has no record at all once the session ends. The chat panel is the
readable counterpart: a floating transcript of the live conversation, plus durable sessions behind it.

**One technical decision dominates this feature.** A panel pinned to the top of the screen would be
captured in the next screenshot and fed back to the model, which would then see its own previous
answer rendered as user interface, might describe it, and might point at the panel instead of the
application underneath. Excluding the window from screen capture is what prevents that — and the
measurement that made it work also killed the translucent body the design asked for, because the two
are mutually exclusive on Windows.

The second theme is that **this panel must never cost the user an answer**. The pipeline emits into it
and moves on. If a render path throws — a malformed message, a deleted screenshot, a destroyed Qt
object — the user loses the chat panel for that turn, not the answer they asked for.

The third is that persistence is **purely additive**. Two new tables in the database that already holds
per-application memory and the review queue, with the same create-if-not-exists contract and no
alteration of anything existing. Users have live databases.

> **Provenance.** Consolidated into Kiro's spec format from `SHELL_AND_CHAT.md` §4 `S-6`, `S-6b`,
> `S-7`, `S-8`, `S-8b`, §4.1 and §6.1. Every measurement quoted — the affinity return values, the
> marker pixel counts, the stale effect, the three failed button attempts — is recorded there or in the
> module docstrings.

## Glossary

| Term | Meaning |
|---|---|
| **The panel** | The floating transcript window |
| **Capture exclusion** | A window attribute that keeps a window on screen while removing it from screen capture |
| **Layered window** | A window whose compositing is handled per-pixel, which translucency requires |
| **Space C** | The Nimbus declared-resolution coordinate space the model answers in |
| **Exchange** | One user message plus the reply that followed it |
| **Collapse** | The third state, between open and hidden: the bar stays, the body goes |
| **Delta** | A partial reply arriving during streaming, at sentence granularity |
| **Suppressed capture** | A turn where the privacy guard refused to take a screenshot |

## Requirements

### Requirement 1: The panel is never in a screenshot

**User Story:** As a user, I want Nimbus to answer about my application rather than about its own
panel, so that having a transcript on screen does not degrade the answers.

#### Acceptance Criteria

1. THE panel SHALL be excluded from screen capture while remaining visible on screen.
2. THE exclusion SHALL be applied on every show event rather than once at construction, because a
   window can lose it.
3. THE effectiveness SHALL be verified by **pixel count rather than by inspection**: with exclusion
   active, a grab SHALL contain zero marker pixels.
4. A control measurement SHALL be taken with exclusion **off** in the same run, and SHALL show a large
   non-zero count, because without the control a broken test passes silently.
5. THE alternative capture-hiding mode SHALL be named in the source and recorded as rejected: it
   hides the window but renders the region **black**, which is worse than the window itself, because
   the model then sees a black rectangle across the screen and has no way to know it is not part of
   the application.
6. WHERE capture exclusion is unavailable — on an operating system build too old to support it — THE
   system SHALL expose a query and hide-and-show methods so the existing overlay hide cycle can cover
   the panel too.
7. Those methods SHALL be no-ops when exclusion is active, so the calls can be made unconditionally.
8. THE exclusion call SHALL never raise, because a failure must degrade to the hide cycle rather than
   crash on every show.

### Requirement 2: Translucency loses to capture exclusion

**User Story:** As a user, I want the correct trade made without being asked, so that a cosmetic
choice does not silently break the thing that makes answers accurate.

#### Acceptance Criteria

1. THE panel body SHALL be **opaque**, not translucent.
2. THE measurement SHALL be recorded: the capture-exclusion call fails — returns zero, sets nothing —
   on a window carrying the layered extended style, which is exactly what a translucent background
   adds. Measured on a specific operating system build: an opaque frameless tool window returns
   success with the exclusion affinity set, and the same window with a translucent background returns
   failure with no affinity.
3. THE conclusion SHALL be stated: the translucent body and capture exclusion are **mutually
   exclusive**, and exclusion wins, because a cosmetic alpha is not worth the model pointing at
   Nimbus's own panel.
4. THE fallback SHALL be identified as sanctioned rather than improvised: the design document's own
   verification step anticipated this and named the opaque body as the answer.
5. Rounded corners SHALL be achieved by clipping the window to a rounded region, which needs no
   layering.
6. THE region SHALL be verified not to disturb the capture affinity.
7. THE region SHALL be re-applied on every resize, because it is defined at a fixed size in window
   coordinates and a stale one would clip the new geometry.
8. Window-level opacity animation SHALL be prohibited, because setting a window opacity below full
   also forces the layered path, so a window-level fade would trade correct answers for polish.

### Requirement 3: There is no opacity animation anywhere

**User Story:** As a user, I want the panel to have a normal background when I reopen it, so that it
does not turn black.

#### Acceptance Criteria

1. THE panel SHALL have **no** opacity animation on the window or on any child widget.
2. THE entrance SHALL animate position instead, which needs no offscreen buffer.
3. THE bug SHALL be recorded in full: the reveal and dismiss paths animated an opacity effect on the
   body widget, attaching it for the duration of the fade because a permanent effect forces every
   repaint through an offscreen buffer. Dismiss detached it when its animation finished. **Reveal
   never did.**
4. THE measurement SHALL be recorded: after one reveal, the body still carried a live opacity effect
   at full opacity, so from the first time the panel appeared every repaint of the body went through
   that buffer for the rest of the session.
5. THE user-visible symptoms SHALL be recorded: a black background after reopening, and black bars
   down the sides when switching session — because a resize re-creates the buffer and whatever has
   not repainted into it yet is transparent black.
6. THE reveal path SHALL **clear** any graphics effect rather than trusting that none is attached,
   because that is precisely the state that caused the bug.
7. Dismissal SHALL be immediate, since there is no fade left to run.
8. THE reason no fade can be restored SHALL be recorded, referring to the layered-window
   incompatibility.
9. Each animation SHALL stop and dispose of its predecessor before starting, because two live
   animations on the same property fight and the loser wins intermittently.

### Requirement 4: The panel never takes focus and never blocks

**User Story:** As a user, I want the panel to appear without stealing my typing, so that it does not
interrupt the work I am asking about.

#### Acceptance Criteria

1. THE panel SHALL be frameless, always on top, and SHALL never accept focus.
2. Showing the panel SHALL not activate it.
3. THE panel SHALL need no keyboard-chord guard of its own, and the reason SHALL be recorded: it
   accepts no focus anywhere, so it never receives a key event at all.
4. Every public entry point SHALL be wrapped so that an exception degrades to "no chat panel", never
   to "no answer".
5. Swallowed exceptions SHALL be **logged**, because an invisible swallowed exception is how this
   feature would rot unnoticed.
6. THE pipeline SHALL be upstream of the panel and SHALL never learn that it exists in any way it can
   trip over.
7. All visual work SHALL happen on the main thread, and producers on the pipeline, listener and
   socket threads SHALL reach the panel only through inbound signals.

### Requirement 5: Geometry is measured, clamped and remembered

**User Story:** As a user, I want to resize the panel to suit my answer lengths, so that it is neither
a toy nor a window covering the application I am asking about.

#### Acceptance Criteria

1. THE default size SHALL be chosen for legibility rather than minimal coverage, and the revision
   SHALL be recorded: the first pass optimised only for covering as little of the user's work as
   possible and produced a panel whose header, transcript and footer were pressed together with no
   air anywhere. The extra size buys **margins**, not more rows.
2. THE panel SHALL be resizable from any edge and any corner, clamped to a minimum and a maximum in
   both axes.
3. THE minimum SHALL be justified as a real floor rather than a guess: below it the footer's status
   text and its two pills stop fitting on one line, which produced a visibly elided status string.
4. THE maximum SHALL be justified: an unbounded drag produces a panel covering the application the
   user is asking about, which defeats the point of the product.
5. THE chosen size SHALL be remembered per monitor.
6. THE resize border SHALL double as the visible bezel, and the reason SHALL be recorded: the inset is
   what leaves bare window under the pointer for the hit test, so a gutter narrower than the hit zone
   would create a ring that changes the cursor but is not grabbable.
7. THE bezel width SHALL match the shell window's, so the two surfaces look related rather than
   coincidental, and the earlier wider value SHALL be recorded as reading like a second frame around
   the panel — a box inside a box.
8. THE corner hit region SHALL be substantially larger than the edge border, and the defect SHALL be
   recorded: two thin strips crossing leave a corner of a few dozen pixels, and one pixel outside it
   the user silently gets a single-axis resize instead of the diagonal they aimed for. That is exactly
   what "the corner cursor never shows up" was.
9. THE corner target SHALL be close to what the platform's own frames use, and SHALL cost nothing
   visually because it changes only the hit test.
10. THE panel SHALL open at the top centre of the available geometry of its screen, in that screen's
    logical coordinates.
11. THE header, footer and state strip heights SHALL be taller than their first values, and the strip
    in particular SHALL be recorded as having read like a rendering artefact rather than a deliberate
    indicator at its original height.

### Requirement 6: Collapse is a third state

**User Story:** As a user, I want to know Nimbus is there and which conversation I am in, without a
transcript over my work.

#### Acceptance Criteria

1. THE panel SHALL support a collapsed state that hides everything below the bar while keeping the
   panel's width and position.
2. THE justification SHALL be recorded: minimising shrinks to a small pill and loses the session name;
   collapsing keeps the bar exactly where it was and drops only the body.
3. THE collapsed height SHALL be **derived from the live layouts** rather than written as a literal.
4. THE defect SHALL be recorded: it was previously a sum including a constant standing for the body's
   margins, and adding the resize gutter put more space between the window edge and the header, so the
   collapsed window came out short — the body could not fit the header, and the header spilled past
   the body's bottom edge and clipped the buttons sitting in it.
5. THE height SHALL be driven by an explicit fixed height, because a frameless window keeps its old
   height if nothing tells it otherwise, which would leave an empty rectangle behind.
6. **Collapsing SHALL never move the bar.** It SHALL stay exactly where the panel's top edge was, so
   that the thing the user clicked is still under their pointer afterwards.
7. THE earlier behaviour SHALL be recorded as rejected: a bar that walks away from the click that
   collapsed it is disorienting even when the arithmetic is right.
8. THE expansion direction SHALL be decided **before** anything moves and remembered, so that
   expanding reverses exactly what collapsing did.
9. THE reason SHALL be recorded: deciding again on expand would let a panel dragged near a screen edge
   mid-collapse expand the other way and jump.
10. THE direction SHALL depend on where the panel is: a panel in the lower half of the screen SHALL
    expand upwards, because expanding downwards would run the transcript off the bottom edge or, with
    clamping, appear to teleport the whole panel.
11. WHERE no screen is available, THE direction SHALL default to downwards, because a panel that opens
    down and is clipped is still usable while one that opens off the top is not.
12. THE previous height SHALL be recorded **before** the children are hidden, and the defect SHALL be
    recorded: hiding them makes the layout recalculate immediately and shrink the window to its
    minimum, so reading the height afterwards returns the minimum rather than the size the user chose,
    and expanding then "restored" the panel to a fraction of its size.
13. Signals SHALL be blocked while syncing the collapse control, and the defect SHALL be recorded:
    setting the checked state re-emitted the toggle, which re-entered the method — the inner call
    collapsed the panel and *then* the outer call recorded the height, by which point it was the bar
    height.
14. THE collapse control's glyph SHALL be an arrow pointing **where the body will go**, not a state
    indicator.

### Requirement 7: The transcript row is a frame, not a button

**User Story:** As a user, I want session titles and their subtitles fully legible, so that descenders
are not clipped.

#### Acceptance Criteria

1. A session row SHALL be built from a plain frame containing its own labels rather than from a
   button.
2. THE three failed attempts SHALL be recorded, each with its distinct cause:
   a button with a two-line label plus a minimum height, where the application stylesheet's own
   minimum **overrides** the widget property; the same with the minimum raised in the button's own
   stylesheet, where the minimum governs the content box and the arithmetic never agreed with the
   layout; and a button containing a layout of two labels, where a styled button computes its size
   hint from the style's contents size and **ignores a child layout**, squeezing the labels to a few
   pixels each.
3. THE reason a frame works SHALL be recorded: its layout's size hint is its size hint, the labels
   report their own heights, and the row is exactly as tall as its content.
4. THE hover attribute SHALL be set explicitly, because the stylesheet hover rule does not fire on a
   plain frame without it.
5. Clicking SHALL be a single press handler, which is less code than any of the three attempts.

### Requirement 8: The system role explains an absence

**User Story:** As a user, I want to be told when Nimbus deliberately did not do something, so that
protection does not look like malfunction.

#### Acceptance Criteria

1. THE message model SHALL carry three roles: the user, Nimbus, and the system.
2. THE system role SHALL be justified as load-bearing rather than padding: it is how the panel explains
   an **absence** — a screenshot skipped because a password manager was open, a cancelled turn, a new
   chat started.
3. THE consequence of omitting it SHALL be recorded: without a role for those, a privacy-suppressed
   turn looks indistinguishable from Nimbus malfunctioning, and the user's conclusion is that the
   application is broken rather than that it protected them.
4. System messages SHALL be dropped when history is rebuilt, because they were never sent to the model
   and replaying them would put interface copy into the conversation as if the user or Nimbus had said
   it.

### Requirement 9: Persistence is additive and shares one database

**User Story:** As an existing user, I want a new feature not to disturb the memory and review data I
already have.

#### Acceptance Criteria

1. THE system SHALL add tables to the **existing** database, alongside the per-application memory and
   review tables.
2. THE addition SHALL use the same create-if-not-exists contract, the same journal-mode pragma, and no
   alteration of any existing table.
3. A test SHALL assert that the existing tables are untouched, as the gate on that promise.
4. Structure SHALL mirror the existing store deliberately: autocommit, row access by name, a
   connection opened and closed per method, nothing held across turns.
5. Three writers now share one database, which is acceptable under the single-writer journal model
   **provided every write happens on the main thread**.
6. THE structural guarantee SHALL be stated: the panel — not the pipeline worker — is what calls the
   message write, and the panel lives on the main thread by definition, so the invariant is
   structural rather than a comment someone has to remember.
7. A foreign key SHALL be deliberately omitted, and the reason recorded: the database does not enforce
   one without a per-connection pragma, and neither existing store sets it, so a constraint that looks
   enforced but is not is worse than none because the next reader trusts it.
8. Deletion cascades SHALL therefore be explicit, and SHALL also remove the screenshot folder that no
   constraint could.
9. THE screenshot root SHALL be derived from the database path rather than declared independently, so
   that pointing the database elsewhere moves the images with it instead of scattering test images
   into a real profile.
10. A reply SHALL be extended by an update per delta rather than one write at the end, so that a crash
    mid-reply leaves the partial answer in the transcript instead of losing the turn.
11. THE cost SHALL be bounded and stated: deltas arrive at sentence granularity from the speech split,
    not per token, so this is a handful of small writes per turn rather than hundreds.
12. THE panel SHALL re-read a message immediately after writing it, so that the rendered row carries
    the identifier and screenshot path the store actually assigned rather than what the panel assumed
    — which is precisely where a screenshots-off setting would get quietly ignored and a thumbnail
    rendered for a file that was never written.

### Requirement 10: A suppressed screenshot never reaches the disk

**User Story:** As a user, I want the privacy guard to mean what it says, so that a transcript feature
does not quietly undo it.

#### Acceptance Criteria

1. Screenshot saving SHALL refuse in three cases, in priority order.
2. THE **first** refusal SHALL be the suppression flag, because the guard's entire purpose is that
   those pixels are not retained, and writing them here would quietly undo it — which is worse than
   never having had the guard, because the user believes they are protected.
3. THE flag passed in SHALL be the **same** boolean the guard returned, or the protection is only
   decorative.
4. THE **second** refusal SHALL be the storage setting, which SHALL default **off**.
5. THE default SHALL be justified: screen contents on disk is a materially bigger privacy commitment
   than a transcript, and it deserves an explicit yes rather than being inherited from switching the
   panel on for an unrelated reason.
6. THE **third** refusal SHALL be a missing image or a failed write, returning empty so the caller
   records a turn with no screenshot rather than a dangling path.
7. Saving SHALL never raise, because a thumbnail is a nicety and the transcript is the feature.
8. THE image and the suppression flag SHALL be excluded from persistence and from equality
   comparison, carrying the pixels only as far as the main-thread write call.
9. THE stored screenshot SHALL carry a marker drawn at the coordinate, at the same radius and stroke
   width as the diagnostic screenshot's, so that a thumbnail and a diagnostic image show the user the
   same marker for the same coordinate.
10. THE marker drawing SHALL be reimplemented rather than reused, and the reason recorded: the
    existing method is bound to a diagnostic session's folder and gated on a diagnostics setting, so
    there is no way to reach the drawing without also creating a diagnostic session the user did not
    ask for.
11. A deleted session SHALL have its screenshots removed, and the reason recorded: a "deleted"
    conversation that leaves the screen contents on disk is the one failure here that matters.
12. Retention SHALL prune sessions untouched for a configured number of days, SHALL be best effort and
    SHALL never raise, because it runs at startup and a locked image file must not stop Nimbus from
    launching.
13. THE retention default SHALL be justified with an estimate of unbounded growth, so that it is a
    bounded cost rather than a hypothetical one.

### Requirement 11: A new chat clears the history, in the same call

**User Story:** As a user, I want a new chat to actually start fresh, so that "no context" is true
rather than merely visual.

#### Acceptance Criteria

1. Starting a new session SHALL clear the caller's in-memory history **in place**, as part of the same
   call.
2. THE reason SHALL be recorded: a new chat that starts a fresh visual thread while still sending the
   model the last ten exchanges is a lie, and the way that lie happens is a caller creating the
   session and forgetting the clear. Making it one operation removes the opportunity.
3. THE mutation SHALL be in place rather than returning a new list, because the same object is handed
   to the pipeline and rebinding it would leave the worker holding the old one.
4. Switching session SHALL rebuild the history in place under the same contract, and SHALL also return
   the rebuilt value so a caller with no list of its own can use it.
5. THE persistent record SHALL be a *record* of the conversation rather than its source of truth, and
   nothing SHALL read back from it per turn, because that would put a database read on the hot path
   and couple the pipeline worker to the interface.

### Requirement 12: History rebuild matches what the pipeline produces

**User Story:** As a user reopening an old conversation, I want the model to have the same context it
would have had live, so that continuing a conversation works.

#### Acceptance Criteria

1. THE rebuild SHALL produce exactly the shape the pipeline worker appends, and the reason SHALL be
   recorded: anything else would work until the first provider that actually reads history, which is
   the worst time to find out.
2. THE rebuild SHALL apply the same exchange window the pipeline uses.
3. THE window constant SHALL be duplicated rather than imported, and the reason recorded: importing the
   orchestrator here would drag the whole application — and a running toolkit instance — into a module
   that must be testable on its own.
4. A test SHALL pin the two constants together so they cannot drift silently.
5. THE image budget SHALL be read at call time rather than import time, so a settings change applies
   without a restart.
6. Images SHALL be allocated to the **newest** turns, and the reason recorded: an old screenshot is
   actively misleading, because the user has moved on and the model would answer about a window that
   is no longer there.
7. THE budget SHALL be applied before any blocks are built, so that it is honoured newest-first
   regardless of where the screenshots happen to sit.
8. A message SHALL be treated as having no coordinate unless **both** coordinate columns are present,
   and the reason recorded: a half-null pair treated as a coordinate with a zero component would fly
   the cursor somewhere the model never suggested.

### Requirement 13: A session boundary needs two reasons

**User Story:** As a user, I want alt-tabbing for ten seconds not to fragment my conversation, and I
want a genuinely different task to get its own thread.

#### Acceptance Criteria

1. An automatic new session SHALL require **both** a change of foreground application **and** an idle
   period.
2. Each condition SHALL be justified as guarding the other's failure mode: per-application memory
   already exists, so a session spanning two unrelated applications is muddled context — but
   alt-tabbing to a browser for ten seconds must not fragment one conversation into three, and an hour
   of continuous work in one application is still one conversation.
3. THE comparison SHALL be case-insensitive and whitespace-tolerant.
4. IF either application name is empty, or the timestamp does not parse, THEN THE system SHALL NOT
   start a new session.
5. THE session title SHALL be derived from the first user message with **no model call**, and the
   reason recorded: a title is cosmetic, spending a request and a round trip on one is not justified,
   and the first thing the user said is a better label than a generated summary because it is what
   they will search for later.
6. THE title SHALL truncate on a word boundary where one is available, so that it never ends mid-word.
7. THE title SHALL be set at the first user message, because that is the earliest moment it is
   knowable and it costs nothing.
8. Sessions SHALL be listed most-recently-used first, with a search over the title and the application
   name.
9. THE search SHALL be justified as not premature: sessions accumulate silently, a few weeks of normal
   use is hundreds, and a flat list stops being navigable well before that. The title and the
   application name are the two things a user remembers about a conversation they are trying to find.

### Requirement 14: The panel's own numbers come from the same data

**User Story:** As a user, I want the home page and the transcript to agree about what happened, so
that neither is misleading.

#### Acceptance Criteria

1. Suppression counts SHALL be recorded durably from the single capture choke point, so that the
   number shown is a count of actual suppressions rather than an estimate assembled from log lines.
2. THE suppression write SHALL never raise, because a status card is not worth failing an interaction
   for.
3. Question counts SHALL be derived from the stored messages rather than kept as a separate tally, so
   that the home page's count and the transcript cannot disagree about what happened.
4. Recent activity SHALL be read from the same store, and the defect being fixed SHALL be recorded: it
   previously read an in-memory list, so the table was empty after every restart even for a user with
   a week of conversations behind them.
5. Only user messages SHALL be counted or listed as questions, because a question is what the column
   shows.
6. THE reply SHALL NOT be joined in, and the reason recorded: pairing a question with the reply that
   followed it needs a correlated subquery for a column nobody displays.
7. A timestamp SHALL be returned as a real date-time where it parses, so relative formatting is
   possible, and SHALL be passed through as its stored string where it does not, rather than guessed
   at.
8. IF a counter query fails THEN THE system SHALL return zero or an empty list rather than raising,
   because a status table is not worth taking the window down for.

### Requirement 15: Marking an answer wrong does something

**User Story:** As a learner, I want flagging a wrong answer to stop it being taught back to me, so
that the flag is not theatre.

#### Acceptance Criteria

1. Flagging a reply as wrong SHALL delete the matching review-queue row.
2. THE reason SHALL be recorded: reviewing a known-wrong answer for a month would actively teach the
   user the wrong thing.
3. A system note SHALL be added to the transcript, so that the flag is visible next time the session
   is opened.
4. THE deletion SHALL be guarded on the review table existing, so that a database predating the
   journal is unaffected.
5. THE choice of direct SQL over calling into the review store SHALL be recorded with its reason: the
   store exposes no delete, and adding one would mean editing a module this workstream must not touch.

### Requirement 16: The panel replaces, rather than duplicates, captions

**User Story:** As a user, I want one copy of the words on screen, not two.

#### Acceptance Criteria

1. WHILE the panel is showing the transcript, THE existing caption overlay SHALL be suppressed.
2. THE reason SHALL be recorded: two copies of the same words on one screen is noise.
3. THE panel SHALL expose a query for whether it is showing the transcript, so the decision can be
   made by the caller rather than guessed.
4. THE panel SHALL be toggleable by keyboard, and SHALL also auto-hide after a configurable idle
   period with zero meaning never.
5. THE keyboard shortcuts SHALL be routed through the existing global listener rather than a second
   low-level hook, and SHALL not go through the chord parser, which deliberately rejects chords it
   cannot own.
6. THE panel's visibility SHALL have one writer in the application, with the shell's switch and the
   keyboard shortcut both asking rather than setting, because three things move this panel.
7. THE panel SHALL be pinnable so that the idle timer does not dismiss it.
8. THE empty state SHALL explain the core interaction using the **real** configured chord, and the
   reason SHALL be recorded: it is the only surface where the core interaction can be explained at the
   moment it is relevant, so telling a user who remapped the hotkey the wrong chord is worse than
   saying nothing.
