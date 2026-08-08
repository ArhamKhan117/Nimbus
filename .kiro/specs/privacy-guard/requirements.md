# Requirements Document

## Introduction

Nimbus captures every monitor on every push-to-talk. Before this feature it did so with no content
awareness at all, which means a password manager, a banking page or an open `.env` file could be sent
to a third-party model provider because the user happened to ask a question with it on screen.

The Settings dialog says *"Nothing leaves your machine."* That is true of **credentials** — they live
in Windows Credential Manager under DPAPI — and a user will reasonably read it as being about **screen
contents**, where on a cloud provider it was not. This feature makes the existing claim honest.

The design principle throughout: **enforce, do not promise, and show the count.** A running total of
suppressions is an observation the user can check. A privacy policy paragraph is not.

> **Provenance.** Consolidated into Kiro's spec format from `IMPROVEMENTS.md` `T2-1`. The blocklist
> derivation, the two bugs the tests caught and the fail-open reasoning are all recorded there.

## Glossary

| Term | Meaning |
|---|---|
| **Suppression** | A turn where no screenshot was taken because the guard refused |
| **Voice-only turn** | A turn that proceeds with a question and no image |
| **Fail open** | On detection failure, permit capture rather than block it |
| **Choke point** | The single function every capture in the product goes through |
| **Sentinel** | The `"unknown"` value foreground detection returns when Win32 calls fail |

## Requirements

### Requirement 1: Policy is a pure function

**User Story:** As a user, I want the privacy decision to be something that can be exhaustively
verified, so that "protected" is a fact rather than a hope.

#### Acceptance Criteria

1. THE decision function SHALL perform no input or output, consult no clock, and hold no global state
   beyond its configured lists.
2. THE decision function SHALL return both a boolean and a reason.
3. THE decision function SHALL be given the same inputs from every call site, so that the same
   situation always produces the same answer.
4. THE policy SHALL live in its own module, separate from the module that captures pixels, so that a
   blocklist is not embedded in the highest-risk geometry code in the project.
5. THE decision function SHALL evaluate the enabled flag first, then the application name, then the
   window title.
6. WHERE an application matches, THE system SHALL suppress capture regardless of the window title.

### Requirement 2: Detection failure fails open

**User Story:** As a user, I want Nimbus to keep working when Windows briefly cannot tell it what is in
front, so that a privacy feature does not read as random breakage.

#### Acceptance Criteria

1. IF the foreground application name is empty or equals the unknown sentinel THEN THE system SHALL NOT
   suppress capture on the basis of the application name.
2. THE system SHALL suppress only on positive identification.
3. THE system SHALL NOT suppress on an empty or whitespace-only window title.
4. THE rationale SHALL be recorded in the module, because failing closed is the intuitive choice and the
   wrong one: detection fails transiently during window transitions, on elevation prompts and against
   elevated processes, and failing closed would make Nimbus stop working at random.

### Requirement 3: The blocklists

**User Story:** As a user, I want the obvious sensitive contexts covered without my having to configure
anything, so that the protection is on before I think about it.

#### Acceptance Criteria

1. THE system SHALL block a list of password-manager and credential-tooling executables by exact
   lowercase basename, not by substring.
2. Entries verified present on a real machine SHALL be distinguished in the source from entries added
   from general knowledge, because a non-matching entry is inert while a missing one is a real gap.
3. THE system SHALL block window titles matching patterns across four groups: authentication surfaces,
   finance, secret-bearing filenames, and private browsing.
4. THE private-browsing patterns SHALL be included because the user has already signalled that they do
   not want a record kept.
5. THE title patterns SHALL be matched case-insensitively as a search rather than a full match.
6. THE blocklists SHALL be extensible from Settings, and user entries SHALL be **added** to the
   defaults rather than replacing them, so that pinning one extra application cannot discard the
   built-in list.
7. IF a user-supplied pattern is invalid THEN THE system SHALL discard that pattern and keep the rest,
   so that one bad expression degrades the guard rather than breaking capture on every turn.

### Requirement 4: Blocking is narrow enough to be useful

**User Story:** As a user reading documentation about passwords, I want Nimbus to still help me, so
that the guard protects me without getting in the way.

#### Acceptance Criteria

1. THE system SHALL NOT suppress capture on a title merely containing the word "password" without
   credential context.
2. THE system SHALL suppress on a title that begins with the word, because real password dialogs are
   often titled with nothing else.
3. THE system SHALL suppress on the word in credential context — preceded by a verb such as enter,
   new, current, confirm, master, forgot, reset or change, or followed by manager, vault, store, safe
   or generator.
4. THE system SHALL suppress on a filename carrying the environment-file extension, including with a
   further suffix.
5. THE system SHALL NOT suppress on prose containing the word "environment", because the literal dot
   is what keeps that pattern tight.
6. THE system SHALL be tested against a table of ordinary titles that must **not** suppress, alongside
   the table that must.

### Requirement 5: Suppression is counted at one place

**User Story:** As a user, I want to see how often Nimbus chose not to look at my screen, so that the
privacy claim is something I can check rather than something I am told.

#### Acceptance Criteria

1. THE system SHALL route every capture in the product through one guarded helper.
2. THE guarded helper SHALL count a suppression exactly once, so that the number shown is a count of
   actual suppressions rather than an estimate assembled from log lines.
3. THE count SHALL be written to durable storage, so that "this week" means this week rather than
   "since the last restart".
4. IF the durable write fails THEN THE system SHALL continue the turn, because a counter must never
   cost the user their answer.
5. WHERE no durable store is available, THE system SHALL fall back to an in-memory count for the
   session.
6. THE system SHALL display the count on the Home page.
7. WHERE the count cannot be read at all, THE system SHALL render a placeholder rather than zero,
   because an unmeasured zero is a false claim.
8. THE system SHALL show an always-visible on/off indicator for the guard, so that its state needs no
   click to confirm.
9. THE indicator SHALL use the danger colour when the guard is off rather than a gentler warning
   colour, because with the guard off every question captures whatever is in front — including a
   password manager — and that is worth being blunt about.

### Requirement 6: A suppressed turn still answers

**User Story:** As a user, I want an answer even when Nimbus refuses to look, so that protecting me is
not the same as failing me.

#### Acceptance Criteria

1. WHEN capture is suppressed THEN THE system SHALL return an empty capture list rather than raising.
2. An empty capture list SHALL mean "no screenshot this turn", never "abort".
3. THE system SHALL tell the model plainly that the screen was withheld, so that it does not answer as
   though it can see and describe a screen it was never shown.
4. THE system SHALL discard any coordinate returned on a suppressed turn, because a model given no
   image can still emit one and placing a pointer from it would be invention.
5. THE system SHALL discard any annotations on a suppressed turn, while still stripping their tags from
   the spoken text.
6. THE system SHALL skip the grid locator on a suppressed turn.
7. THE system SHALL skip the overlay hide-and-show cycle entirely on a suppressed turn, so that there
   is no flicker for a capture that is not happening.
8. THE system SHALL show a toast naming the reason and stating that the question is being answered
   without seeing the screen.

### Requirement 7: The guard is on by default

**User Story:** As a user, I want the protection active before I know it exists, so that I am covered
on my first question rather than after reading the settings.

#### Acceptance Criteria

1. THE guard SHALL default to on.
2. THE deviation from the rule that a new setting must reproduce existing behaviour SHALL be recorded
   with its justification: here the existing behaviour is the defect, not a preference.
3. WHEN local data is cleared THEN THE system SHALL restore the on default rather than leaving the
   guard off from a previous session, because a wipe must not silently weaken privacy.
4. THE setting SHALL persist an explicit on or off value rather than being deleted when off, so that a
   deliberate refusal is distinguishable from "never configured" and cannot be re-defaulted later.
5. Turning the guard off SHALL restore the previous unconditional capture behaviour exactly.

### Requirement 8: The decision concerns the window the user was looking at

**User Story:** As a user, I want the guard to judge the window I asked about, so that alt-tabbing
mid-question does not defeat it.

#### Acceptance Criteria

1. THE system SHALL evaluate the guard against the foreground application recorded **at press time**,
   not against a fresh query at capture time.
2. THE rationale SHALL be recorded: by the time a capture thread runs, the foreground window may have
   changed, and the decision must be about the window the user was actually looking at when they asked.

### Requirement 9: Every capture path is covered

**User Story:** As a user, I want the guard to apply to everything Nimbus does with my screen, so that
there is no side door.

#### Acceptance Criteria

1. THE guard SHALL apply to the press-time capture.
2. THE guard SHALL apply to the release-time re-capture.
3. THE guard SHALL apply to the speech-to-speech path, because a voice turn is no less capable of
   sending a password manager to a cloud provider.
4. THE guard SHALL apply to the re-point path, so that re-pointing does not become a way to photograph
   a password manager.
5. A capture call site added later SHALL inherit the guard without being changed, because there is only
   one place to add it.
6. THE reason strings SHALL contain no regular expression, no file path and no executable name, because
   the toast is shown on screen and may itself be captured in a screenshot or a screen recording.
