# Requirements Document

## Introduction

Before this feature Nimbus had no window. It lived in the tray, and everything configurable was
behind a modal dialog. That made two questions unanswerable without opening a menu: **is it on**, and
**what is it using?** For a background tool that sends screenshots to a model, the second one matters
rather a lot.

The shell is a frameless main window with a navigation rail and five pages. Its defining constraint is
that it is a **view**. The push-to-talk pipeline is untouched; nothing in the shell sits on its path;
there is no import of the application module anywhere in the package. Every data source arrives as an
injected callable and every action leaves as a signal. If a shell change ever required the pipeline
worker to be touched, the design would be wrong.

The second constraint is that this is **not a rewrite**. The existing settings implementation carries
the provider and model matrix, the key-reuse rule, keyring persistence, the hotkey capture widget, the
privacy group, the experimental group and the restart labels, along with 41 tests. A nicer
reimplementation would have silently dropped several of those. So the settings work is a pure
extraction whose acceptance criterion is that every pre-existing test keeps passing untouched.

Almost every non-obvious decision here came from a measurement that contradicted the plan. A frameless
window loses the style bit Windows needs to offer snap. A page crossfade left stale pixels from the
previous page visible inside the new one. The push-to-talk chord was pressing whatever button had
focus, and the button that had focus on a freshly opened window was the one that turns Nimbus off.

> **Provenance.** Consolidated into Kiro's spec format from `SHELL_AND_CHAT.md` §3 `S-1`–`S-5` and
> §2, plus `IMPROVEMENTS.md` `T2-7` and `T4-7`. Every measurement quoted below is recorded in those
> documents or in the module docstrings.

## Glossary

| Term | Meaning |
|---|---|
| **Provider** (in this document) | An injected callable supplying a number or a list the window will not invent |
| **Chrome** | The title bar, the navigation rail and the window frame |
| **Grip** | A small invisible child widget that hands a resize gesture to the operating system |
| **Snap** | The window manager's edge-docking behaviour, which the OS performs *to* a window |
| **Restart marker** | The glyph appended to a setting's label when it takes effect only on next start |
| **Restart-gated** | A setting read once at construction or import, so a change needs a restart |
| **Em dash placeholder** | What a number renders as when it has not been measured |

## Requirements

### Requirement 1: The shell is a view and nothing more

**User Story:** As a developer, I want the window to be constructible without the application, so that
the pipeline can never acquire a user-interface dependency.

#### Acceptance Criteria

1. THE window SHALL be constructible with no arguments and with no running application object.
2. THE shell package SHALL contain no import of the application module.
3. Every inbound data source SHALL be an injected callable, following the pattern the speech and
   realtime modules already use.
4. Every outbound action SHALL be a signal emitted on the main thread.
5. THE pipeline worker SHALL gain no dependency on the shell or the chat panel, so that it stays
   testable with no application instance.
6. IF a shell change would require the pipeline worker to be modified THEN THE design SHALL be
   reconsidered rather than the worker changed.
7. THE window SHALL be split across a package rather than one module, because a single module covering
   five pages plus a custom title bar becomes unreviewable.
8. THE package SHALL expose the window class through a lazy attribute hook, and every module in the
   package SHALL therefore be registered in **both** the frozen-build hidden-import list **and** the
   selftest's runtime module list.

### Requirement 2: The window is frameless and still behaves like a window

**User Story:** As a user, I want to drag, snap, maximise and resize from any edge, so that a custom
title bar does not cost me the behaviour every other window has.

#### Acceptance Criteria

1. THE window SHALL be frameless with a custom title bar.
2. Dragging the title bar SHALL hand the gesture to the operating system's own move loop rather than
   repositioning the window from mouse-move events.
3. Resizing SHALL hand the gesture to the operating system's own sizing loop from a border region.
4. THE reason SHALL be recorded: the operating system owning the gesture is what brings snap and
   window-shake back for free, and it means **no code in the shell converts a coordinate or caches a
   device-pixel ratio**, so dragging between monitors at different scaling is not something this
   feature can get wrong.
5. THE measurement that ruled out the message-interception approach SHALL be recorded: a frameless
   window's style word reads `0x96000000` against `0x96CF0000` for an ordinary one, so the sizing
   style bit is **absent**, and the operating system only runs its sizing loop for a window that has
   it. Returning a hit-test result would therefore do nothing until that bit is restored and the
   frame it brings back is suppressed — and that route also has to convert physical pixels per
   monitor, which is exactly the per-monitor scaling assumption the plan warned against.
6. THE system SHALL restore the sizing, maximise and minimise style bits after the window is shown, so
   that the operating system will offer snap, top-edge maximise and taskbar minimise.
7. THE caption style bit SHALL deliberately **not** be restored, because that one really would bring
   back a title bar.
8. THE style change SHALL be applied with a frame-changed flag, because without it the operating
   system does not re-ask for the frame calculation and the new style has no visible effect until the
   next resize.
9. THE system SHALL verify that suppressing the returning frame is unnecessary rather than assuming
   it, and SHALL pin that measurement with a test, so that a future toolkit version which stops
   handling it is a failing test rather than a returning frame.
10. THE style call SHALL declare explicit argument and return types, because an undeclared window
    handle is marshalled as a 32-bit integer, which truncates a 64-bit handle and fails silently
    against a handle that does not exist.
11. THE style word SHALL be read back after writing rather than trusting the return value, because the
    write returns the *previous* style and a legitimate call can therefore return zero.
12. THE style call SHALL return a boolean and SHALL never raise, so that the fallback is "no snap"
    rather than a window that will not open.
13. A message-handler override SHALL be recorded as a dead end: calling the base implementation from
    this binding crashes the process with an access violation on the first message the window
    receives. IF a handler is ever genuinely needed THEN it SHALL return the unhandled result directly
    rather than delegating.
14. A native corner grip SHALL remain as the visible affordance and as the fallback where there is no
    native handle to hand the gesture to.

### Requirement 3: Each resize edge owns its own cursor

**User Story:** As a user, I want my cursor to go back to normal when I move off the window edge, so
that it does not get stuck as a resize arrow.

#### Acceptance Criteria

1. THE resize region SHALL be implemented as eight small child widgets, each carrying its own edge and
   its own cursor.
2. THE previous implementation SHALL be recorded as the defect: the window set the resize cursor on
   **itself** from a mouse-move handler, and a cursor set on a parent applies to every child that has
   not set its own, so the resize cursor was inherited by every card and label on the page.
3. THE reason it stayed stuck SHALL be recorded: clearing it needed another move event over the
   *window*, and a move from the border into the content lands on a child, so the window never saw the
   pointer leave. One brush past an edge left every page with a resize cursor.
4. Per-widget cursors SHALL be relied upon because the toolkit sets them on enter and restores them on
   leave, with no state of ours to get stuck.
5. THE corner regions SHALL be larger than the edge border, because two thin crossing strips leave a
   corner too small to hit.
6. THE grips SHALL be transparent to painting but not to the mouse, so the bezel underneath shows
   through unchanged.
7. All grips SHALL be hidden while the window is maximised.
8. THE dead hit-test code SHALL be deleted along with the mechanism it served, and the tests that
   referenced it SHALL be replaced with geometry assertions against the code that actually runs.

### Requirement 4: Sizes adapt to the screen rather than being constants

**User Story:** As a user on a small or heavily scaled display, I want the window to fit on my screen,
so that a size chosen on someone else's hardware is not a trap.

#### Acceptance Criteria

1. THE window SHALL open at a natural size clamped to a fraction of the available screen geometry,
   reusing the existing dialog's approach rather than reinventing it.
2. THE minimum size SHALL also be clamped to a fraction of the available geometry.
3. THE reason SHALL be recorded: at high scaling a full-resolution panel reports a logical size
   *below* the intended floor, so an unclamped window would open unable to fit on its own screen and
   unable to shrink.
4. THE minimum SHALL be recomputed when the window moves, so that a floor measured on a large panel
   does not follow the window onto a small one.
5. Each page except the settings page SHALL sit in its own scroll area, so that a window too short for
   a page scrolls rather than clipping.
6. THE measurement that allowed the floor to be lowered SHALL be recorded: the layout's own minimum
   was 810 by 646 while the explicit floor said 1040 by 680, so the floor was 230 pixels wider and 34
   taller than anything the content needed — the user was being stopped by a constant. Of that 646,
   one page accounted for 549 pixels of height with no way to give less.
7. THE consequence SHALL be stated: the failure mode on a small or heavily scaled screen becomes a
   scrollbar rather than an unreachable control.
8. THE settings page SHALL be the documented exception, because it brings its own scroll area with the
   save action pinned outside it, and wrapping it again would nest one scrolling region inside another
   and put the save action back below the fold.
9. THE scroll areas SHALL NOT be tab stops, because a page-sized container with nothing to do would
   otherwise take focus and draw a focus frame around the whole page. The wheel SHALL still scroll
   them and tabbing between a page's own controls SHALL still scroll those into view.

### Requirement 5: Closing hides, and never quits

**User Story:** As a user, I want closing the window to leave Nimbus listening, so that closing a
window does not silently disable the product.

#### Acceptance Criteria

1. WHEN the window is closed THEN THE system SHALL hide it and SHALL NOT quit.
2. THE system SHALL emit a signal so a tray notification can be shown once, so that a user who closed
   the window is not left wondering where Nimbus went.
3. Quitting SHALL be available from the tray and from the account page, and both SHALL route through
   **one** shutdown path.
4. THE title bar SHALL emit intent rather than acting on the window directly, so that a title bar
   cannot bypass the hide-to-tray behaviour by closing the window itself.
5. THE close button's tooltip SHALL say that it closes to the tray, so the behaviour is stated before
   it happens rather than discovered.

### Requirement 6: The push-to-talk chord must not press buttons

**User Story:** As a user, I want pressing my push-to-talk chord with the window open to ask a
question, not to toggle whatever control happens to have focus.

#### Acceptance Criteria

1. THE window SHALL install a shortcut on the configured push-to-talk chord whose handler does
   nothing.
2. THE defect SHALL be recorded with its cause: the global hook is deliberately non-suppressing, so
   the chord reaches the focused widget as well as Nimbus; and the toolkit's button base class
   activates on the space key **without looking at modifiers**, so a focused button treats the chord as
   a click.
3. THE three measured consequences SHALL be recorded: with the power control focused the chord paused
   Nimbus at the moment the user asked it to listen; with a folder button focused it opened the file
   manager; with a navigation item focused it changed page.
4. THE severity SHALL be recorded: focus on activation landed on the power control, so this fired on
   the very first question after opening the window.
5. THE guard SHALL be a shortcut rather than an application-wide event filter, because the toolkit's
   shortcut map runs **before** a key event reaches the focus widget, which is the only place this can
   be stopped cleanly.
6. THE handler SHALL do nothing, because the global hook already handles the chord and this window
   must not become a second push-to-talk path.
7. THE guard SHALL be built from the **configured** chord rather than a literal, so a user who
   remapped push-to-talk is protected by the same guard.
8. IF the chord cannot be parsed THEN THE system SHALL fall back to guarding the default rather than
   installing no guard, because an unparseable chord should cost the guard's precision, not the guard.
9. THE guard SHALL be scoped to this window, and the chat panel SHALL need no equivalent because it
   accepts no focus and therefore receives no key events.
10. THE guard SHALL be liftable while the settings page is recording a new hotkey, because otherwise
    the window swallows the chord the user is currently bound to and the capture button appears to
    ignore it.
11. THE window itself SHALL take the initial focus rather than letting it fall to the first widget in
    the tab order, so that no control is armed until the user presses Tab.
12. THE measurement SHALL be recorded: setting a focus policy alone was insufficient — the toolkit
    still handed focus to the first tab-chain widget on activation, which was the control that turns
    Nimbus off, complete with the platform's focus frame around it.

### Requirement 7: Navigation is one list

**User Story:** As a developer, I want a navigation entry without a page to be impossible, so that a
dead link cannot ship.

#### Acceptance Criteria

1. THE navigation entries SHALL be defined as one ordered list of page name and label pairs.
2. THE rail SHALL build its buttons from that list and THE window SHALL build its page stack from that
   list, so that a mismatch is impossible by construction rather than by vigilance.
3. THE correspondence SHALL be pinned by a test.
4. Each navigation entry SHALL carry its own page name, so that no part of the shell maps an index
   back to a page — index lookups are what break when the list is reordered.
5. THE rail SHALL emit a page request on a **user click only**; the programmatic selection path SHALL
   be silent, so that a page change cannot echo back into another page change.
6. Switching to an unknown page name SHALL be ignored rather than raising, because the call is
   reachable from a signal and a typo must not be able to take the window down.
7. IF a page's refresh raises THEN navigation SHALL still complete.
8. THE navigation side SHALL be configurable with the default on the left, and the reasoning SHALL be
   recorded: every desktop application the user already has puts primary navigation on the left,
   reading order makes the left edge the cheapest place to scan, and a right rail conventionally holds
   *contextual* content. The disagreement with the original brief SHALL cost exactly one value to
   reverse.
9. An unrecognised side value SHALL resolve to the default rather than producing a third layout.
10. THE divider hairline SHALL sit on the edge facing the content, so that moving the rail does not
    leave a border floating at the window edge.
11. THE selection marker SHALL be a child of the rail rather than of an item, so that it can travel
    between items.
12. THE marker SHALL jump rather than animate when the layout has not run or the target is unchanged,
    so that it does not slide in from a corner the first time the window appears.

### Requirement 8: Home answers the two questions the tray could not

**User Story:** As a user, I want to see at a glance whether Nimbus is listening and what model it is
using, so that I do not have to open a menu to find out.

#### Acceptance Criteria

1. THE home page SHALL make the power state visually dominant, because "is it on?" is the question a
   tray-only application cannot answer.
2. THE home page SHALL name the provider and the model in use.
3. THE home page SHALL show the configured hotkey.
4. THE home page SHALL show recent interactions with the application, the time and what was pointed
   at.
5. THE rail SHALL carry an always-visible status footer, so that the questions it answers need no
   click.
6. THE footer SHALL carry the privacy guard state permanently, because "is my screen leaving this
   machine?" is the one question a tray-only application cannot otherwise answer without opening a
   menu.
7. THE footer chip's **label SHALL never change**; only its indicator colour SHALL, because a control
   whose text changes also changes width and a rail that reflows on every settings change is its own
   small distraction — and a colour is quicker to take in than a two-letter suffix.
8. THE indicator SHALL use the danger colour rather than a warning colour when the guard is off,
   because with the guard off every question captures whatever is in front, and that is worth being
   blunt about.
9. THE tooltip SHALL say what the state means and where to change it, so that it informs rather than
   nags.
10. THE footer SHALL carry one chip rather than a list of dots, because the provider is already named
    on the home page where there is room to say the model too.
11. THE rail SHALL NOT repeat the product name, because the title bar directly above already says it
    and repeating it puts the name on screen twice in one glance while stealing the vertical space
    that made the first navigation item sit low.
12. Certain pages SHALL be deliberately absent, with reasons recorded: a log viewer is a large amount
    of interface for something used a handful of times and Explorer already serves it; a memory
    browser would weaken the plain-Markdown contract rather than strengthen it, so the folder is
    linked instead; and a chart dashboard would be decoration pretending to be information, because
    Nimbus's numbers are incidental rather than the product.

### Requirement 9: A number is measured or it is absent

**User Story:** As a user, I want a count I can trust, so that a zero means zero rather than "not
wired up".

#### Acceptance Criteria

1. WHERE a number has no provider, THE system SHALL render an em dash rather than zero.
2. THE reason SHALL be recorded: a measured zero and an unmeasured one are different claims, and the
   privacy count is the most trust-building item on the page precisely because it is an observation.
3. THE window SHALL never poll; every number SHALL come from an injected callable, refreshed on page
   change and on an explicit refresh call.
4. IF a provider raises THEN THE page SHALL render the placeholder rather than propagating the error.
5. THE integration requirements for each number SHALL be documented in the module, including which
   existing data is insufficient and why, so that a blank number is honestly blank rather than
   mysteriously blank.

### Requirement 10: One source of truth, three views

**User Story:** As a user, I want the window, the tray menu item and the tray icon to agree about
whether Nimbus is listening, so that I can trust any of them.

#### Acceptance Criteria

1. THE listening state SHALL live in one place: the hotkey listener's enabled flag.
2. THE window SHALL never write that state itself; it SHALL emit a request and the application SHALL
   be the only writer.
3. All three views SHALL be driven by one change signal, so that flipping the state anywhere updates
   everywhere.
4. No view SHALL hold its own copy of the boolean.
5. THE window's own accessor SHALL read through to the provider rather than caching, so that a caller
   cannot make a view show something the source disagrees with.
6. WHERE a provider is wired up, an inbound set call SHALL be honoured only as a refresh, so the
   source wins.
7. Toggling SHALL take effect immediately and SHALL NOT require a restart, and the verification SHALL
   be recorded: the enabled flag gates callbacks without uninstalling the listener, so the hook stays
   installed and the toggle is instant.
8. WHEN pausing THEN THE application SHALL also stop any speech in progress, mirroring the existing
   pause behaviour.
9. THE chat panel's visibility switch SHALL follow the same arrangement, because three things move
   that panel: the switch, a keyboard shortcut, and an idle auto-hide.
10. THE switch SHALL be re-read after a request, so that if the application declines, the switch snaps
    back.

### Requirement 11: Settings is re-hosted, not rewritten

**User Story:** As a user, I want every setting I had, so that a nicer-looking page does not lose the
provider matrix or my saved keys.

#### Acceptance Criteria

1. THE settings content SHALL be extracted into a plain widget with no host of its own, and the
   extraction SHALL be a **pure refactor**.
2. THE acceptance criterion SHALL be that every pre-existing settings test keeps passing untouched.
3. THE reason SHALL be recorded: the existing implementation carries the provider, model and key
   matrix, the key-reuse rule, keyring persistence, the hotkey capture widget, the privacy group, the
   experimental group and the restart labels, and a reimplementation would have silently dropped
   several of those.
4. THE widget SHALL be hosted by both the first-launch modal and the shell page, so that there is one
   implementation with two hosts.
5. THE widget SHALL contain **no** scroll area and **no** button box, both of which belong to the host.
6. THE hosts SHALL communicate with it through signals for validity, save success and local-data
   clearing, plus a save call returning whether anything was written.
7. IF the save call returns that nothing was written — an invalid hotkey, or a declined compatibility
   warning — THEN THE host SHALL NOT close.
8. THE shell SHALL **react** to local data being cleared rather than merely recording it.

### Requirement 12: The settings form fits a small laptop

**User Story:** As a first-time user on a 1366 by 768 laptop, I want to reach the save button, so that
setup is possible at all.

#### Acceptance Criteria

1. THE host SHALL wrap the form in a scroll area with the button box **outside** it.
2. THE placement SHALL be recognised as the load-bearing detail: a fully scrolled dialog can still
   hide the save action below the fold, whereas a pinned button box cannot, however many settings are
   added later.
3. THE measurement SHALL be recorded: the form wanted 744 pixels of content, 783 with the window
   frame, against 728 usable — so the save button would have been off-screen on a dialog that is
   **modal at first launch**, meaning setup could not have been completed.
4. THE cause SHALL be attributed honestly: the growth came from features added across several tiers,
   and the knowledge-base button was the last straw rather than the cause. It was invisible during
   development because the development machine has 1040 usable pixels.
5. Scrolling alone SHALL be recognised as insufficient: a scrollable dialog opens at its **minimum**
   size, which measured about 111 pixels — a letterbox.
6. THE opening size SHALL therefore be derived from the **page's** natural height clamped to a
   fraction of the screen, and the reason SHALL be recorded: asking the dialog's own layout returned
   426 pixels, because a scroll area reports its own small hint rather than its child's.
7. THE fit SHALL be guarded by a parametrised check across several common screen heights.
8. An inventory test SHALL assert the full set of controls, so that the refactor cannot silently drop
   a widget.
9. A test SHALL assert the settings page has **exactly one** scroll area, so that nesting cannot be
   reintroduced.

### Requirement 13: Settings that need a restart say so

**User Story:** As a user, I want to know when a toggle will not take effect until I restart, so that
I do not conclude the feature is broken.

#### Acceptance Criteria

1. THE system SHALL maintain an explicit set of settings that take effect only after a restart.
2. Each such setting's label SHALL carry a marker glyph.
3. THE marker SHALL be a symbol rather than the word "(restart)", so that it survives being appended
   to already-long checkbox labels.
4. A single note near the save action SHALL explain the marker, and it SHALL be **built from** the
   marker constant, so that the legend cannot end up explaining a symbol the labels no longer use.
5. THE marker lookup SHALL be a pure function, so that labelling is testable without constructing the
   dialog and a setting cannot be marked inconsistently in two places.
6. Coverage SHALL be asserted: every restart-requiring setting carries the marker.
7. THE reason for caching rather than live-reloading SHALL be recorded: resolving a setting writes to
   the credential store whenever a value came from the environment, so re-resolving per interaction
   would put a credential-store write on the hottest path in the application. Removing the cache would
   be the wrong fix, so honesty is the minimum viable version.
8. API keys SHALL be deliberately absent from the set, because they are read per request and a new key
   works immediately.
9. THE glyph choice SHALL be recorded as a measurement rather than a preference, comparing ink height
   against the surrounding text's cap height at three sizes across four candidates, and naming the
   symbol font explicitly rather than leaving it to fallback.
10. THE reasoning SHALL be recorded: the first glyph shipped and was reported as pixelated; a straight
    arrow is crisp but does not say "reloads on next start"; the icon-font glyph is the right shape
    but measured about 40% larger than the letters beside it, because an icon font fills the em box
    while a text character's capitals occupy roughly 70% of it — and since there is no way to shrink
    one run of a plain-text label, and the control carrying nine of these markers does not support
    rich text, a smaller size was not available.
11. THE chosen glyph SHALL be verified to be a real glyph rather than a missing-character box, by
    comparing its ink against a codepoint guaranteed to be absent, and SHALL be verified not to clip
    below the baseline.

### Requirement 14: Clearing local data is scoped and reports failures

**User Story:** As a user, I want to wipe what Nimbus has stored about me without losing the documents
I exported, so that "clear data" means app state rather than my files.

#### Acceptance Criteria

1. THE system SHALL clear the contents of Nimbus's data root and knowledge-base folder while
   preserving the folders themselves, so that a running process can recreate a database or diagnostics
   folder cleanly.
2. User-created exports SHALL be excluded, because they are explicit documents rather than application
   state.
3. THE system SHALL delete the enumerated local credential-store entries, and SHALL treat a missing
   entry or a locked store as non-fatal.
4. THE system SHALL return the list of failures rather than raising, so that a partial result is still
   useful and reportable.
5. Clearing SHALL restore the privacy guard's on default rather than leaving it off from a previous
   session, because a wipe must not silently weaken privacy.
6. Symbolic links SHALL NOT be followed when removing directories.

### Requirement 15: The tray stays, trimmed

**User Story:** As a user, I want the tray to keep the one action worth having in a single click, so
that Nimbus still feels like a background utility.

#### Acceptance Criteria

1. THE tray SHALL remain, because it is the only surface available when the window is closed.
2. Left-clicking the tray icon SHALL show and raise the window.
3. THE tray menu SHALL keep show, pause and quit.
4. Items that now have a better home in the window SHALL leave the tray menu, because a menu that
   duplicates the window is two places to keep in sync and two places to fix a bug.
5. Pause SHALL stay in the tray, because it is the one action whose whole value is being reachable in
   one click without opening anything.
6. THE tray's pause item and the window's toggle SHALL both read the same source and write it only
   through their own callbacks, with no second boolean anywhere.
7. Actions inherited from the trimmed tray menu SHALL be raised as signals by the window, because only
   the application can service them.

### Requirement 16: Motion communicates state, and glyphs are drawn

**User Story:** As a user, I want animation that tells me something changed, not decoration, and I
want the window buttons to look right on every machine.

#### Acceptance Criteria

1. Animation SHALL be used to communicate state change rather than for decoration, and every duration
   SHALL come from one place.
2. A reduced-motion preference SHALL be honoured, following the operating system by default with an
   explicit override available.
3. A zero-duration animation SHALL be verified to still emit its completion signal, because cleanup
   hangs off it and a silently non-firing signal would break the reduced-motion path.
4. THE page crossfade SHALL be **removed**, and the reason recorded: an effect renders its target into
   an offscreen buffer, and the pages contain exactly the widgets that go wrong there — scroll areas
   and tables with transparent viewports. The result was stale pixels from the *previous* page visible
   inside the new one for the fade's duration, worst on the page where a table occupies most of the
   card. A transition whose whole job is to feel smooth cannot leave visible tearing.
5. THE alternatives SHALL be recorded as rejected: painting every viewport opaque defeats the card
   gradient showing through, and animating a real overlay widget is a lot of machinery for a fraction
   of a second.
6. THE removal SHALL be recorded rather than silently dropped, because "the design says crossfade" is
   otherwise a reasonable thing to re-add.
7. THE page stack SHALL be given an explicit opaque fill rather than transparency, because that is
   what stops a half-painted child leaving remnants on a page change — the same class of artefact that
   retired the crossfade.
8. Window-button glyphs SHALL be **painted** rather than typed, and the reason recorded: the maximise
   character renders as a **solid white block** in the system font, and every substitute has the same
   class of problem on some fallback — one is too small against the wordmark, another is a ballot box
   with different metrics. Two strokes and a rectangle outline cost less than picking a font-safe
   character and look identical on every machine.
9. THE painted button SHALL remain the standard button type so that the whole stylesheet still applies
   to its background; only the glyph SHALL be custom.
10. THE maximise glyph SHALL change to a restore glyph when the window is maximised, so that the
    button describes what it will do next.
11. Window buttons SHALL be inset chips with a border and a resting background rather than full-height
    transparent hit zones, and the reason recorded: the original was close to invisible against a
    near-black title bar and was the first thing anyone remarked on.
12. THE texture overlay SHALL cover the content area only, and the reason recorded: it exists to stop
    large low-contrast gradients banding, the remaining gradients are on the cards, and noise over
    flat black reads *as* noise — which is why the chrome was still reading as textured.
13. THE texture overlay SHALL be transparent to mouse events, verified by hit-testing a control's
    centre: without the flag the hit resolves to the overlay, with it to the control.
14. Every colour SHALL come from the theme module, pinned by a test asserting the generated stylesheet
    contains no literal colour.
15. THE focus frame SHALL be shown for keyboard focus only, so that a mouse click does not leave a
    dotted outline behind, without taking the navigation rail away from the keyboard.

### Requirement 17: A setting resolves through one chain, and the chain is written down

**User Story:** As a developer, I want one documented answer to "where does this value come from", so
that a setting cannot behave differently depending on which module read it.

> **Specified after the fact.** Requirement 13 depends on this chain — restart-gating exists *because*
> of the write-through described below — but the chain itself had no owning requirement. It is recorded
> here because it is the reason a whole class of settings cannot be reloaded live, and that reason was
> only written in a comment.

#### Acceptance Criteria

1. Every setting SHALL resolve in the order **environment, then credential store, then declared
   default**, through one function, with no module reading a setting any other way.
2. THE resolver SHALL **write through** to the credential store whenever the value came from the
   environment, and the purpose SHALL be recorded: it is a one-shot migration out of a dotfile, so
   that a value set once in an environment file survives without that file.
3. THE consequence SHALL be recorded rather than discovered: because the write-through touches the
   credential store, re-resolving on every read would put a credential-store write on the hottest path
   in the application.
4. THEREFORE a setting read at import time SHALL be cached, and SHALL appear in the restart set from
   Requirement 13 so that its label is marked.
5. API keys SHALL resolve through a separate path that is read per request rather than cached, so that
   a newly entered key works immediately and without a restart.
6. THE resolver SHALL never raise: an unreadable credential store SHALL fall through to the declared
   default.
7. THE declared default SHALL be the value in the code, not the value currently stored, so that a test
   can assert what the product ships with rather than what the developer's machine happens to hold.
8. A test fixture SHALL exist that reloads the settings module with the environment cleared and the
   credential store stubbed empty, and SHALL restore both on teardown, so that no test inherits a
   stubbed configuration.
9. THE reason for that fixture SHALL be recorded: asserting a default by reading the imported module
   tests the machine the suite is running on rather than the code.

### Requirement 18: A setting can be changed without restarting

**User Story:** As a user, I want a toggle to take effect when I flip it, so that trying a different
model does not cost me a restart and the loss of my session.

> **Not built — `T4-7b`, the remaining half of `T4-7`.** The labelling half is done and shipped
> (Requirement 13): every restart-gated setting says so. Actually reloading mid-session is open. The
> recorded constraint is that this is **not** a matter of deleting the cache — see Requirement 17.3 —
> so the work is deciding which settings are genuinely safe to swap mid-session and giving only those
> a reload path.

#### Acceptance Criteria

1. THE system SHALL classify each cached setting as safe or unsafe to change mid-session, and that
   classification SHALL be one explicit set rather than a judgement made per call site.
2. WHERE a setting is classified safe, changing it SHALL take effect on the next turn without a
   restart, and its label SHALL lose the restart marker in the same change.
3. THE reload path SHALL NOT re-resolve through the write-through resolver on the hot path, because
   that reintroduces the credential-store write Requirement 17.3 exists to avoid.
4. WHERE a setting is classified unsafe, behaviour SHALL be exactly as it is today: cached, marked, and
   applied on the next start.
5. THE classification SHALL be justified per setting rather than in bulk, because "safe to swap" for a
   provider that holds an open socket is a different question from one that is read once per request.
6. Requirement 13's coverage assertion SHALL be extended so that a setting cannot be simultaneously
   marked as restart-requiring and classified as live-reloadable.
7. A setting moved from unsafe to safe SHALL be accompanied by a test proving the next turn observes
   the new value, so that the marker is removed on evidence rather than on intent.
