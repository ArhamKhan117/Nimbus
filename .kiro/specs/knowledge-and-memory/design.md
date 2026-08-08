# Design Document

## Overview

Three stores, one shared database, one shared name-resolution function, and no retrieval
infrastructure at all.

`memory.py` owns per-application history: Markdown files as the source of truth, a small table as a
denormalised counter cache. `kb.py` owns user-supplied documentation: text extraction plus keyword
ranking, no index. `review.py` owns spaced repetition: pure scheduling functions plus one more table in
the database `memory.py` already created. `debug_log.py` records what happened, per turn, on disk.

The design commitment that unifies them is that **every store is a file the user can open**. Memory
files are Markdown. The knowledge base is the user's own documents. Insights are Markdown. Diagnostics
are a folder of a log and some JPEGs. Nothing here needs Nimbus running to be understood, which is what
makes a background tool with persistent memory read as benign.

Responsibility boundary, stated because it has to hold: these modules own the read and write API. They
do **not** decide when to record, what to inject into a prompt, or which application is active. The
pipeline calls recall before a request and record after a response, and is the single writer.

> Consolidated from `IMPROVEMENTS.md` `T3-2` and `T3-3`.

## Architecture

```
                              foreground app name (e.g. "MYAPP.EXE")
                                        │
                          memory._sanitize_app_name        ← ONE function
                        lowercase · strip · replace <>:"/\|?*
                                        │
                        ┌───────────────┴───────────────┐
                        ↓                               ↓
            ~/.nimbus/memory/<name>.md        Documents/Nimbus Wiki/
                        │                        ├── <name>.md        (flat, read FIRST)
                        │                        └── <name>/          (folder, recursive)
                        │                              ├── 01-*.md
                        │                              ├── 02-*.docx  ← tables extracted
                        │                              └── 03-*.pdf
                        │                               │
                  recall(tail)                    recall(ranked)
                  MEMORY_RECALL_MAX_CHARS          KB_RECALL_MAX_CHARS = 60,000
                  no filtering, no scoring         split on headings → score by DISTINCT
                        │                          terms → keep best → re-emit in
                        │                          DOCUMENT order
                        └───────────────┬───────────────┘
                                        ↓
                          injected as strings into the system prompt
                            (provider-agnostic: works on Ollama too)
                                        ↓
                                 answer delivered
                                        ↓
              ┌─────────────────────────┼─────────────────────────┐
              ↓                         ↓                         ↓
    memory.record()            review.add()              debug session closed
    append block               swallowed + logged        ~/.nimbus/debug/<ts>_<app>/
    rewrite header             next_review = tomorrow      interaction.log  (+Nms)
    upsert apps row                                        screenshot.jpg   (+marker)
              │                         │
              └──────────┬──────────────┘
                         ↓
              ~/.nimbus/index.db   (WAL, create-if-not-exists, one table each)
                 apps  ────────────  review_queue
                 PRIMARY KEY         AUTOINCREMENT id
                 app_name            idx on next_review
```

**Journal intent is classified before capture.** A transcript that is predominantly *"quiz me"* never
reaches the capture path at all — less work, and one less privacy exposure for a question that is about
the user's own data rather than their screen.

## Components and Interfaces

### `memory.py`

```python
_WINDOWS_RESERVED_CHARS = set('<>:"/\\|?*')

def _sanitize_app_name(name: str) -> str: ...          # raises ValueError on empty
def _escape_markdown_fences(text: str) -> str: ...     # ``` → '''
def _escape_single_line(text: str) -> str: ...         # \n → ' ↵ ', \r → ''

class MemoryStore:
    def __init__(self, memory_dir=MEMORY_DIR, index_db_path=INDEX_DB_PATH) -> None: ...
    def recall(self, app_name, max_chars=MEMORY_RECALL_MAX_CHARS) -> str: ...
    def record(self, app_name, window_title, user_question,
               model_response, pointer_targets: list[tuple[int, int]]) -> None: ...
    def list_known_apps(self) -> list[dict]: ...
```

The constructor creates the **index** parent directory eagerly, because the first connection needs it,
and leaves the **memory** directory to the first `record()`. A user who installs Nimbus and never asks
a question gets no folders.

Every method opens its own connection and closes it before returning. There is one writer — the UI
thread — so no in-process locking is needed beyond the journal mode.

**The block shape, exactly:**

```markdown
# MYAPP.EXE — Nimbus Memory

First seen: 2026-08-09 14:22
Interactions: 3
_(This file is human-readable. Delete it to reset memory for this app.
  Nimbus reads the tail of this file to remember past interactions.)_

## 2026-08-09 14:31 — "how do I freeze panes"
Window: Sales Q1 - Excel
Response: Click View tab, then Freeze Panes → Freeze Top Row.
Pointed at: (1245, 82)
```

Three escaping decisions sit behind that. Triple backticks become `'''` because a model response
containing a code fence would otherwise break the block shape — plain ASCII, human-readable, impossible
to confuse with a real fence; zero-width-space insertion was considered and rejected as too clever.
Newlines in single-line fields become a visible `↵` rather than being deleted, so the user can still see
where the break was. And the header is built by f-string rather than `.format()`, so an application name
containing a literal `{` cannot raise a `KeyError` on a code path that runs after the user already has
their answer.

Preservation is a single rule: **everything from the first `## ` to end of file is the body**. A file
with no `## ` — hand-edited, truncated, corrupted — yields an empty body and a clean rewrite rather than
an error.

One `datetime.now()` per call, shared by the header and the index row. Two readings would let the
Markdown and the database skew by microseconds, which makes sort-by-last-seen non-deterministic under
rapid-fire tests.

### `kb.py`

```python
TEXT_SUFFIXES  = {".md", ".markdown", ".txt", ".text"}
PDF_SUFFIXES   = {".pdf"}
DOCX_SUFFIXES  = {".docx"}
MAX_FILES_PER_APP = 40
_MIN_TERM_LENGTH  = 3
_SECTION_SPLIT_RE = re.compile(r"\n(?=#{1,6}\s)")
GUIDE_FILENAME    = "README.md"
_GUIDE_TEXT       = """..."""            # embedded, NOT a PyInstaller datas entry

def ensure_guide(kb_dir=None) -> Path | None: ...        # never raises
def _sanitize_app_name(app_name) -> str: ...             # DELEGATES to memory's
def extract_pdf_text(path) -> str: ...                   # "" if pypdf missing
def extract_docx_text(path) -> str: ...                  # paragraphs AND table cells
def extract_text(path) -> str: ...                       # never raises
def iter_kb_files(folder) -> list[Path]: ...             # stable order, capped
def query_terms(query) -> set[str]: ...
def split_sections(text) -> list[str]: ...
def score_section(section, terms) -> int: ...            # DISTINCT terms
def rank_and_truncate(text, query, max_chars) -> str: ...
def recall(app_name, kb_dir=None, max_chars=..., query="") -> tuple[str, str]: ...
```

**The delegation is the interesting line in this module.** It previously had its own copy of the
sanitiser whose docstring claimed to mirror memory's "exactly". It did not. Measured across fifteen
inputs, seven disagreed:

| Input | `memory` | `kb` before |
|---|---|---|
| `"  spaced.exe  "` | `spaced.exe` | `  spaced.exe  ` |
| `"app?.exe"` | `app_.exe` | `app?.exe` |
| `"pipe\|app.exe"` | `pipe_app.exe` | `pipe\|app.exe` |

The reserved-character rows are theoretical — those characters are illegal in filenames anyway. **The
whitespace row broke the documented mental model.** Users are told to read the canonical name out of
`~/.nimbus/memory/` and name their knowledge-base file to match; for such an application memory showed
`spaced.exe` while the knowledge base looked for `  spaced.exe  ` and silently found nothing. Fixed by
delegating rather than re-synchronising, so the two folders can never disagree again.

**No Files API, and that is a decision rather than an omission.** The plan specified PDFs through the
Gemini Files API. `kb_content` is a **string** injected into the system prompt, flowing
provider-agnostically through the whole pipeline; routing one format through a Gemini-only file
reference would fracture that contract and break PDFs on the fully-local path that is a standing
regression gate. Local extraction keeps one code path across five providers and stays inspectable. The
Files API item is therefore **unnecessary rather than outstanding**.

`MAX_FILES_PER_APP` is applied by slicing the sorted list *before* extraction, so a user pointing Nimbus
at a directory of thousands of files costs a directory walk rather than thousands of PDF parses on the
hot path.

### Ranking, in order

1. Under budget → return unchanged. Ranking must not reorder content that all fits.
2. No usable terms, or only one section → tail-truncate. Identical to the old behaviour.
3. Score every section by **distinct** terms present.
4. Sort by score descending, index ascending — earlier sections win ties, since a document's opening
   usually carries the overview.
5. Admit sections while they fit. Once anything scoring has been admitted, stop at the first
   zero-scoring section: irrelevant content is not worth remaining budget.
6. Nothing admitted → tail-truncate.
7. Re-sort the kept sections by original index, join, and hard-slice to the budget.

### `review.py`

```python
INTERVALS_DAYS = (1, 3, 7, 14, 30, 60, 120)
MIN_EASE, DEFAULT_EASE, MAX_EASE = 1.3, 2.5, 2.8
EASE_CORRECT_BONUS, EASE_INCORRECT_PENALTY = 0.10, 0.25
_MAX_INTENT_WORDS = 6

def next_interval_index(interval_index, correct) -> int: ...
def adjust_ease(ease, correct) -> float: ...
def next_interval_days(interval_index, ease) -> int: ...
def schedule(interval_index, ease, correct) -> tuple[int, float, int]: ...
def classify_review_intent(transcript) -> str | None: ...   # "quiz" | "due" | "recap" | None

class ReviewQueue:
    def add(self, app_name, question, answer, target_label="", today=None) -> int | None: ...
    def due(self, today=None, limit=10) -> list[dict]: ...
    def grade(self, item_id, correct, today=None) -> dict | None: ...
    def recap(self, app_name=None, since=None, limit=10) -> list[dict]: ...
    def stats(self) -> dict: ...

def format_recap_for_speech(items) -> str: ...
def write_insights(path, stats, due_count) -> Path: ...
```

`schedule()` exists so that ladder position and ease cannot be updated inconsistently by a caller that
remembers one and forgets the other. Every scheduling function is numbers-in, numbers-out, so the
algorithm is exhaustively testable with no database and no clock.

The constants each carry their reason. A **fixed ladder** rather than a computed interval, because the
classic formula needs both a per-item ease and a repetition count to behave and misbehaves on small
datasets — which is exactly what a personal journal is. **Capped at 120 days** because software changes:
an item from four months ago may describe an interface that no longer exists. **Ease scales the ladder**
rather than replacing it, so an easy item stretches and a hard one stays tight while the ladder keeps
the numbers sane. **The ease floor** stops an item the user keeps failing from collapsing to a zero-day
interval and being asked forever. **Asymmetric adjustment** because getting something right once is weak
evidence of knowing it and getting it wrong is strong evidence of not knowing it. **A wrong answer resets
to zero** rather than stepping back a rung, because stepping back keeps a genuinely unknown item
circulating at week-long gaps.

### The false-positive guard

This is the part of the journal most likely to make Nimbus feel broken, so it is the part with the
tightest constraint. Classification is local — no model call, because navigating one's own journal
should be free — but a false positive silently replaces a genuine answer with a quiz. That is worse than
not shipping the feature.

Two rules make it safe. The transcript must be **predominantly** the command: normalise case and
punctuation, and reject anything over six words outright. And when several phrases match, **the longest
wins**, so `"what should i review"` cannot be shadowed by a bare `"review"`.

| Transcript | Result |
|---|---|
| "quiz me" | `quiz` |
| "how would you quiz me on this spreadsheet formula" | `None` — real question |
| "what did we cover in the meeting about the quarterly budget review" | `None` |

### `debug_log.py`

`DebugSession.start()` returns a `_NullDebugSession` when diagnostics are off or the folder cannot be
written. Same three methods, all no-ops. Callers therefore need no branch and no error path, which is
the only way an optional feature stays optional in practice.

`log()` prefixes every line with milliseconds since session start, and swallows write failures.
`save_screenshot()` optionally draws a red circle-and-crosshair at Nimbus's coordinate, which turns "the
pointer was off" from an assertion into an image. Pruning runs at session start, deletes only expired
directories beneath Nimbus's own diagnostics folder, and treats a locked file as a skip.

## Data Models

```sql
-- memory.py
CREATE TABLE IF NOT EXISTS apps (
    app_name          TEXT PRIMARY KEY,     -- sanitised
    first_seen        TEXT NOT NULL,
    last_seen         TEXT NOT NULL,
    interaction_count INTEGER NOT NULL DEFAULT 0,
    md_path           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_apps_last_seen ON apps(last_seen DESC);

-- review.py — additive, same database, no ALTER on apps
CREATE TABLE IF NOT EXISTS review_queue (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    app_name       TEXT NOT NULL,
    question       TEXT NOT NULL,
    answer         TEXT NOT NULL,
    target_label   TEXT NOT NULL DEFAULT '',   -- makes the item POSITIONAL
    first_learned  TEXT NOT NULL,
    next_review    TEXT NOT NULL,
    interval_index INTEGER NOT NULL DEFAULT 0,
    ease           REAL NOT NULL DEFAULT 2.5,
    times_correct  INTEGER NOT NULL DEFAULT 0,
    times_wrong    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_review_next ON review_queue(next_review);
```

Dates are stored as ISO strings, which sort correctly as text, so `next_review <= ?` needs no date
functions and the index is a plain B-tree.

On disk:

| Path | Owner | Lifetime |
|---|---|---|
| `~/.nimbus/memory/<app>.md` | `memory.py` | forever, user-deletable |
| `~/.nimbus/index.db` | `memory.py` + `review.py` | forever |
| `~/.nimbus/insights.md` | `review.py` | rewritten each pass |
| `~/.nimbus/debug/<ts>_<app>/` | `debug_log.py` | pruned after retention |
| `Documents/Nimbus Wiki/` | the user | the user's |

Settings: `MEMORY_RECALL_MAX_CHARS` (1500), `KB_RECALL_MAX_CHARS` (60,000), `KNOWLEDGE_JOURNAL`
(default **on**), `DIAGNOSTIC_CAPTURE`, `DIAGNOSTIC_RETENTION_DAYS`.

## Correctness Properties

### Property 1: One sanitiser, no drift

For any string, the knowledge base's name resolution agrees with memory's, except that where memory
raises the knowledge base returns empty. Generator: the fifteen measured inputs plus arbitrary strings
containing reserved characters, leading and trailing whitespace, and mixed case.

**Validates: Requirements 3.1, 3.2, 3.6, 3.7**

### Property 2: Sanitisation is idempotent and case-stable

For any name that sanitises successfully, sanitising the result again returns it unchanged, and any two
names differing only in case sanitise to the same value.

**Validates: Requirements 3.1**

### Property 3: A non-positive budget yields nothing

For memory recall and for knowledge-base recall, a budget of zero or less returns empty. This is
asserted rather than assumed because a tail slice of length zero returns the **whole** string in Python,
so the natural implementation fails in the worst possible direction.

**Validates: Requirements 2.4, 8.12**

### Property 4: Recall is a suffix

For any file and any positive budget, the returned string is a suffix of the file contents and is no
longer than the budget. Generator: files above, below and exactly at the budget, including multi-byte
characters straddling the boundary.

**Validates: Requirements 2.1, 2.2**

### Property 5: The block shape survives any input

For arbitrary question, window title and response strings — including triple backticks, newlines,
carriage returns, braces and every reserved character — the written file parses back into the same number
of blocks, each with exactly the four expected fields, and the header contains no line break.

**Validates: Requirements 4.1, 4.2, 4.3, 4.4, 4.6**

### Property 6: Recording preserves every prior block

For any sequence of records, the resulting file contains one block per record in order, and the header
count equals the number of blocks. For a file whose header has been hand-edited away, the next record
still succeeds.

**Validates: Requirements 4.7, 4.8**

### Property 7: One clock reading per record

Within any single record, the timestamp in the header, the timestamp in the block heading and the value
written to the index are equal.

**Validates: Requirements 4.9**

### Property 8: The index agrees with the files

For any sequence of records across any set of applications, each index row's count equals the number of
blocks in the corresponding file, and listing returns applications sorted most-recently-seen first.

**Validates: Requirements 5.1, 5.6**

### Property 9: Schema creation is idempotent and additive

For any number of constructions against the same database, in any order across both stores, the schema
is unchanged and no existing row is modified. Generator: a database pre-populated with the older
single-table schema, then opened by both stores repeatedly.

**Validates: Requirements 5.2, 13.1, 13.2, 13.4**

### Property 10: The flat layout is unchanged

For any application with only a flat file, the content returned is exactly what the previous
implementation returned. This is the backward-compatibility gate — existing users have these files.

**Validates: Requirements 6.1**

### Property 11: Folder reads are deterministic and bounded

For any folder, two reads return byte-identical content, the file count never exceeds the cap, and the
order depends only on relative paths rather than on filesystem enumeration order. Generator: nested
folders, mixed cases, mixed suffixes, more files than the cap.

**Validates: Requirements 6.7, 6.8**

### Property 12: One bad file costs only itself

For any folder containing at least one readable and at least one unreadable file, every readable file's
content appears in the result and the call does not raise. Generator: truncated PDFs, zero-byte files,
invalid UTF-8, and files whose extraction dependency is absent.

**Validates: Requirements 7.2, 7.3, 7.5, 7.6**

### Property 13: Under budget is identity

For any content shorter than or equal to the budget and any query, the result equals the input exactly —
same bytes, same order. Ranking must never reorder content that all fits.

**Validates: Requirements 8.2**

### Property 14: Ranking output is ordered, bounded and a subset

For any over-budget content with a usable query, the result is no longer than the budget, consists only
of sections present in the input, and those sections appear in their original relative order.

**Validates: Requirements 8.3, 8.4**

### Property 15: Scoring counts sections, not repetitions

For any section and term set, the score is bounded by the number of terms. A section containing one term
repeated any number of times never outranks a section containing two distinct terms.

**Validates: Requirements 8.5, 8.6**

### Property 16: No query means the old behaviour, exactly

For any over-budget content and an empty query, the result equals the final budget-length slice of the
input. Same for content with no headings.

**Validates: Requirements 8.7, 8.8**

### Property 17: The guide is written once and never overwritten

For any folder, seeding when the guide is absent creates it; seeding when it is present leaves the bytes
untouched. For an unwritable folder, seeding returns a failure signal and does not raise.

**Validates: Requirements 9.1, 9.2, 9.6**

### Property 18: The interval is always in range

For any ladder position, any ease within bounds and either outcome, the resulting day count is at least
one and at most the top of the ladder. The zero case is asserted explicitly: an item due again in the
same session would be asked repeatedly.

**Validates: Requirements 10.3, 10.9**

### Property 19: Ease stays clamped under any history

For any sequence of outcomes of any length, the ease remains within its bounds. Generator: a thousand
consecutive failures, a thousand consecutive successes, and random alternations.

**Validates: Requirements 10.5**

### Property 20: Wrong resets, right advances by one

For any ladder position, an incorrect answer yields position zero and a correct answer yields exactly
one rung higher, saturating at the top.

**Validates: Requirements 10.6, 10.7**

### Property 21: Ease moves asymmetrically

For any ease strictly inside its bounds, the magnitude of the decrease on failure strictly exceeds the
magnitude of the increase on success.

**Validates: Requirements 10.8**

### Property 22: Scheduling is atomic

For any input, the single entry point returns a position, an ease and a day count that are mutually
consistent — the day count is exactly what the returned position and ease produce.

**Validates: Requirements 10.10, 10.11**

### Property 23: A long transcript is never a command

For any transcript exceeding the word cap, classification returns nothing, whatever phrases it contains.
Generator: every trigger phrase embedded in longer genuine questions, including the two recorded
near-misses.

**Validates: Requirements 12.3, 12.4, 12.9**

### Property 24: The longest match wins

For any transcript matching more than one phrase, the intent returned is the one belonging to the
longest matching phrase. Case, question marks and repeated whitespace do not change the result.

**Validates: Requirements 12.6, 12.7**

### Property 25: A journal failure never costs an answer

For any failure of any journal write — an exception, a locked database, a missing table — the turn still
completes and still produces a spoken answer. An empty question or answer is skipped silently rather
than raising.

**Validates: Requirements 13.6, 13.7, 13.8**

### Property 26: Diagnostics are invisible when broken

For any diagnostic failure — the folder unwritable, the file locked, the image unsaveable, capture
disabled — every method returns normally and the turn is unaffected. The null session and the real
session expose the same interface.

**Validates: Requirements 14.4, 14.5**

### Property 27: Pruning is bounded to Nimbus's own folder and forgiving

For any diagnostics folder, pruning removes only directories older than the retention window, removes
nothing outside the diagnostics root, and completes when any entry is locked.

**Validates: Requirements 14.6, 14.7, 14.8**

### Property 28: Skills are off until explicitly enabled

For any skills directory in any state — absent, empty, full of valid files, full of files that raise on
import — no skill is imported while the feature is disabled. Asserted with the import machinery
instrumented, because "did not run" and "was never imported" are different claims and only the second
one is safe.

**Validates: Requirements 15.5**

### Property 29: One failing skill costs only itself

For any set of skill files where an arbitrary subset raises on import, every non-raising skill loads and
each failure appears in the log. Generator: subsets of raising files, including all of them and the
first one. This is the same contract a corrupt knowledge-base document already has.

**Validates: Requirements 15.6, 15.8**

### Property 30: No skill can reach past the guard

Static analysis finds no capture path available to a skill that bypasses the Privacy Guard's single choke
point. Asserted structurally rather than behaviourally, because a guarantee that depends on skills
behaving is not a guarantee.

**Validates: Requirements 15.9**

### Property 31: Nothing is fetched

Static analysis finds no remote fetch in the skills loader, and no code path that writes an executable
file into the skills directory. Asserted as a test, because a marketplace is one commit away from
turning a careless install into a compromise.

**Validates: Requirements 15.4**

## Error Handling

| Failure | Response | Why |
|---|---|---|
| Empty application name reaching memory | Raise | Indicates a real defect in the caller; must surface loudly |
| Empty application name reaching the knowledge base | Return "no knowledge base" | A lookup miss is a normal outcome, not an error |
| Memory file missing | Return empty string | First interaction with an application is the common case |
| Memory file hand-edited past recognition | Clean rewrite | The user is allowed to edit their own files |
| Non-positive recall budget | Return empty | A zero-length tail slice would return everything |
| `pypdf` or `python-docx` absent | Skip that format, log | Mirrors how the local speech providers degrade |
| Corrupt or locked knowledge-base file | Empty text for that file only | One bad file must not cost the rest |
| Unsupported file in a knowledge folder | Ignore silently | Users keep images and spreadsheets beside their notes |
| More files than the cap | Read the first N in stable order | Bounds hot-path cost regardless of file sizes |
| Guide folder unwritable | Return a failure signal, do not raise | Help text must not block startup |
| Guide already present | Leave it alone | Never overwrite the user's edits |
| Journal insert with empty question or answer | Skip, return no id | Called after success; must not be able to fail a turn |
| Any journal write failing | Swallow and log | Losing an entry is invisible; raising is not |
| Grading an absent item | Return nothing | The user may have deleted it |
| Nothing graded yet | Report "not yet reviewed" | A zero percentage would be a false claim |
| Diagnostics folder unwritable | Substitute the null session | Optional logging must never fail an interaction |
| Locked file during pruning | Skip and continue | Retention is best effort |
| Privacy-suppressed capture | Never written to diagnostics | The guard is not undone by a debug path |

## Testing Strategy

Everything worth testing here is either a pure function or a file on disk, and both are cheap. `tmp_path`
throughout — no test touches the real `~/.nimbus/`. `tests/test_memory.py`, `tests/test_kb.py`,
`tests/test_review.py`.

- **The drift guard is the highest-value test in this feature.** A fifteen-case parametrised comparison
  between the two name resolvers, seeded with the seven inputs that actually disagreed. It is the test
  that would have caught the original defect, which is the only real evidence a guard works.
- **Backward compatibility, asserted twice.** `test_flat_file_still_works` for the knowledge base, and
  `test_existing_memory_and_review_tables_untouched` for the database. Both against fixtures built to
  look like a real user's existing data, because both features shipped to users who already had files.
- **Both directions of the intent guard.** The phrases that must match, and the near-misses that must
  not. The second table is the one that matters: a guard tested only for what it catches has not been
  checked for what it wrongly catches.
- **Scheduling exhaustively, with no database.** Every ladder position against both outcomes, ease
  driven to both bounds by long runs, and the never-zero and never-above-cap boundaries asserted
  directly.
- **Ranking against a real over-budget document,** not a synthetic one. The check that matters is that a
  question about content near the *start* of a large file is answerable — which is precisely what
  tail-truncation broke.
- **Extraction verified against real files.** A hand-built PDF containing actual text, and a Word
  document with a real table. A blank PDF would only have proven "does not raise".
- **`test_unreadable_file_skipped_not_fatal`** and **`test_journal_failure_falls_back_to_the_pipeline`**
  — the two degradation paths, both named for the failure they prevent.
- **Live verification, recorded.** A knowledge base for a fictional internal tool built from a Markdown
  overview, a Word shortcut table and a PDF, then the same question asked twice:

  | | Answer |
  |---|---|
  | **Without** the knowledge base | *"shift plus command plus r"* — hallucinated, and `command` is not even a Windows key |
  | **With** it | *"control shift q"* — correct, extracted from the Word **table**, and it volunteered `F9` from the same table |

  That single comparison is the whole justification for the feature: public tools are in the training
  data, internal tools are not.
- **Manual verification entry points.** `python -m memory` seeds three synthetic interactions against a
  reserved application name and prints the recalled text, so the human-readable claim can be checked by
  eye rather than asserted. `python -m kb` prints the resolved folder and what each probe matched.
  Discoverability was verified by deleting the guide, confirming `--selftest` does **not** seed it — it
  only imports — then launching and confirming it reappeared.
