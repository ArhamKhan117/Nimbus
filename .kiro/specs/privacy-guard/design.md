# Design Document

## Overview

Two modules and one function call. `privacy.py` is the policy: a pure function over two strings and two
lists. `app._capture_screens_guarded` is the mechanism: the single place in the product where a screen
capture happens, so the policy is consulted exactly once per turn and a call site added later inherits
the guard for free.

That split is the whole design. Policy has no I/O, so it can be exhaustively tested — which matters
more here than anywhere else in the codebase, because **a silently broken blocklist is worse than no
blocklist**: the user believes they are protected.

> Consolidated from `IMPROVEMENTS.md` `T2-1`.

## Architecture

```
press ─→ get_foreground_app()  → recorded as (app, title) for the whole turn
                                  ↓ the window the user was ACTUALLY looking at
                          _privacy_verdict()
                                  │  merges defaults + user extras from settings
                                  ↓
              privacy.should_skip_capture(app, title, enabled, apps, titles)
                                  │  pure: no I/O, no clock, no globals
                    ┌─────────────┴─────────────┐
              (True, reason)               (False, "")
                    │                            │
        ┌───────────┴───────────┐      hide overlay
        │ log                   │      DwmFlush()
        │ append in-memory      │      mss.grab()          ← the only grab
        │ store.record_skip()   │      show overlay (finally)
        │   (never raises)      │                │
        │ toast with the reason │                ↓
        │ return []             │         list[LabeledCapture]
        └───────────┬───────────┘
                    ↓
            voice_only = True
              · append "you cannot see the screen" to the prompt
              · discard any coordinate the model returns
              · discard annotations, but still strip their tags
              · skip the grid locator
              · NO overlay hide/show cycle at all

durable count:  privacy_skips table in the shared database
                → count_privacy_skips_since(week_cutoff)
                → Home renders the number, or an em dash if unreadable
                → sidebar chip: green on, RED off
```

Four call sites collapse into that one helper: press-time capture, release-time re-capture, the
speech-to-speech path, and the re-point path. The audit that specified this feature said there were
two. Verification found three, and a fourth arrived later — **which is exactly the argument for a single
choke point** rather than applying the gate at each site by hand.

## Components and Interfaces

### `privacy.py`

```python
DEFAULT_BLOCKED_APPS: tuple[str, ...]              # exact lowercase basenames
DEFAULT_BLOCKED_TITLE_PATTERNS: tuple[str, ...]    # regex sources, four groups
_UNKNOWN_APP = "unknown"                           # the foreground-detection sentinel

def should_skip_capture(
    app_name: str,
    window_title: str,
    enabled: bool = True,
    blocked_apps=DEFAULT_BLOCKED_APPS,
    blocked_title_patterns=DEFAULT_BLOCKED_TITLE_PATTERNS,
) -> tuple[bool, str]: ...
```

Exactly three possible returns:

| Return | Condition |
|---|---|
| `(False, "")` | guard disabled, or nothing matched |
| `(True, "a password manager is open")` | application-name match |
| `(True, "this window looks like it holds sensitive information")` | title-pattern match |

The blocklists are passed as parameters with defaults rather than read from configuration inside the
function. That is what keeps it pure and what lets the caller merge user extras without the policy
knowing that settings exist.

`_compile(patterns)` discards invalid expressions rather than raising. These lists are user-editable
from Settings, and one bad expression must not take down the capture path on every interaction — the
remaining patterns keep working, so the guard degrades rather than fails.

### The title patterns, by group

**Authentication.** Sign-in and log-in forms; passkeys; credentials; two-factor and multi-factor;
one-time codes; verification codes; authentication; recovery codes, keys and phrases; seed phrases;
private keys.

**Finance.** Banking, named banks, checkout, billing, card numbers, wallets.

**Secret-bearing filenames.** The environment-file extension including a further suffix; SSH private
key filenames; secrets and credentials files in the common serialisation formats; certificate and key
store extensions.

**Private browsing.** The three vendors' terms. Included because the user has already signalled that
they do not want a record.

### `app._privacy_verdict` and `app._capture_screens_guarded`

The verdict function merges the defaults with comma-separated user extras and reads the foreground
application **recorded at press time**. The guarded helper is the choke point: on a suppression it logs,
counts in memory, records durably inside a try/except, toasts, and returns an empty list. Otherwise it
emits the hide signal, waits for the compositor, grabs, and emits the show signal in a `finally`.

The `finally` matters independently of privacy: without it, an exception during a grab leaves the user
with a permanently invisible pointer for the rest of the session.

### Durable counting

The count lives in a `privacy_skips` table in the database that already exists, alongside per-app
memory and the review queue. It is a table rather than a file because that database already exists, is
already in the right folder, and is already pruned.

It had to become durable for a specific reason: an in-memory count meant the Home card said "this week"
while counting only since the last restart — a label that is wrong most of the time it is read, since
Nimbus is a background tool people leave running for a day and restart the next. **A trust-building
number that quietly resets to zero undermines the thing it is there to build.**

## Data Models

```sql
CREATE TABLE IF NOT EXISTS privacy_skips (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    reason     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_privacy_skips_created ON privacy_skips(created_at);
```

Settings, all restart-gated: `PRIVACY_GUARD` (default **on**), `PRIVACY_GUARD_APPS` and
`PRIVACY_GUARD_TITLES` (both empty, comma-separated, **additive**). All three are wiped by "clear local
data", which restores the on default.

## Correctness Properties

### Property 1: The policy is a pure function

For any inputs, two calls return equal results, and no call performs I/O, reads a clock or mutates
state. Generator: arbitrary strings for both name and title, arbitrary list contents, both boolean
values for the enabled flag, invoked repeatedly.

**Validates: Requirements 1.1, 1.2, 1.3**

### Property 2: The return shape is one of exactly three values

For any inputs, the result is `(False, "")`, or `(True, r)` for exactly one of the two defined reason
strings. No other pairing is reachable.

**Validates: Requirements 1.2**

### Property 3: Disabled is absorbing

For any name, title and lists, when the guard is disabled the result is `(False, "")`. No blocklist
entry can override it.

**Validates: Requirements 1.5, 7.5**

### Property 4: Application match dominates title

For any name in the blocked list and any title whatsoever, the result carries the
application-name reason. The title is never consulted once the name matches.

**Validates: Requirements 1.6**

### Property 5: The sentinel and the empty name never block on name

For the unknown sentinel and for any whitespace-only name, no application-name suppression occurs
regardless of list contents. Title matching still applies independently.

**Validates: Requirements 2.1, 2.2**

### Property 6: An empty title never blocks

For any whitespace-only title and any pattern list, no title suppression occurs.

**Validates: Requirements 2.3**

### Property 7: Application matching is exact, not substring

For any blocked basename `b` and any string strictly containing `b` as a proper substring, the longer
string does not match on name. Case is normalised on both sides. Generator: each default entry with
arbitrary prefixes and suffixes attached.

**Validates: Requirements 3.1**

### Property 8: User patterns are additive, never replacing

For any user-supplied list, every default entry still suppresses what it suppressed before. Adding an
entry can only ever increase the suppressed set.

**Validates: Requirements 3.6**

### Property 9: One invalid pattern degrades rather than fails

For any list containing at least one valid and at least one invalid pattern, the function returns
normally and every valid pattern still matches what it matched. For a list of only invalid patterns,
the function returns normally and suppresses nothing.

**Validates: Requirements 3.7**

### Property 10: The narrow-versus-broad boundary holds both ways

Every title in the must-suppress table suppresses, and every title in the must-not-suppress table does
not. Both tables are asserted, because a guard tested only for what it catches is a guard nobody has
checked for what it wrongly catches. This is the property that caught documentation pages about
passwords being suppressed, and configuration files with the environment extension not being.

**Validates: Requirements 4.1, 4.2, 4.3, 4.4, 4.5, 4.6**

### Property 11: A reason string is safe to display

For every reachable reason string, the text contains no regular-expression metacharacter, no executable
extension, no drive-letter path fragment and no alternation. The toast may itself be captured.

**Validates: Requirements 9.6**

### Property 12: An empty capture list never aborts a turn

For any suppression, the turn reaches a spoken answer, records a turn, and emits neither a coordinate
nor annotations. Generator: a suppressed turn across every model response shape, including one that
returns a coordinate anyway.

**Validates: Requirements 6.1, 6.2, 6.4, 6.5, 6.6**

### Property 13: Counting is exactly once per suppression

For any sequence of turns, the durable count increases by exactly one per suppressed turn and by zero
per permitted turn, whichever call site initiated the capture.

**Validates: Requirements 5.1, 5.2**

### Property 14: A counter failure never costs an answer

For any failure of the durable write — an exception, a missing store, a locked database — the turn still
completes and still produces a spoken answer.

**Validates: Requirements 5.4, 5.5**

## Error Handling

| Failure | Response | Why |
|---|---|---|
| Invalid user regex | Discard that pattern, keep the rest | One bad expression must not break capture on every turn |
| Foreground detection fails | Fail open; do not suppress on name | Transient during transitions, on elevation prompts, against elevated processes |
| Durable count write fails | Swallow, continue the turn | A counter must never cost the user their answer |
| Count unreadable at display time | Render an em dash, never zero | An unmeasured zero is a false claim |
| Capture raises after the guard permits | Restore the overlay in `finally`, abort the turn | Otherwise the pointer is invisible for the rest of the session |
| Session store absent entirely | In-memory count for the session | A session-scoped number beats none |

## Testing Strategy

The whole policy is a pure function over two strings, so it is tested **exhaustively rather than
representatively** — which is the point of having extracted it. 33 tests in `tests/test_privacy.py`.

- **Both tables, always.** `test_blocklisted_titles_skip` and `test_ordinary_titles_do_not_skip`. The
  second is the one that matters: it is where a documentation page about password hashing lives, and
  where a page about environment variables lives. A guard tested only for what it catches has not been
  checked for what it wrongly catches.
- **`test_function_is_pure`** — asserts no I/O and no state, so the property above is not merely a
  claim in a document.
- **`test_blocked_app_names_are_lowercase`** — an uppercase entry could never match, so it would be a
  silent gap in the list.
- **`test_every_default_title_pattern_compiles`** — a pattern that cannot compile is a pattern that
  protects nothing.
- **`test_malformed_user_regex_is_ignored_not_fatal`** and
  **`test_only_malformed_patterns_degrades_gracefully`** — both directions of the degradation.
- **`test_reason_string_is_user_presentable`** — asserts the reason contains no word-boundary escape,
  no executable extension, no non-capturing group, no module prefix, no drive path and no alternation.
- **Integration**, in `tests/test_integration.py` and `tests/test_app.py`: a suppressed turn still
  produces a spoken answer, emits no coordinate even when the model returns one, writes no screenshot
  to disk, and increments the durable count exactly once.
- **`test_repoint_respects_the_privacy_guard`** — because re-pointing must not become a way to
  photograph a password manager.
- **Manual verification** with a real password manager in front: the toast appears, the answer is
  spoken, no screenshot lands in the diagnostics folder, and the Home count increases.

The two bugs found by these tests **before shipping**, both in the narrow-versus-broad boundary: a bare
word match suppressed any page merely mentioning passwords — exactly the kind of page Nimbus is most
useful for — and the environment-file pattern missed the extension when it followed a filename. Both
are now rows in the tables above.
