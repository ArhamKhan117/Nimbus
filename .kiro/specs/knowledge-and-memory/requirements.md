# Requirements Document

## Introduction

Three stores sit behind every answer Nimbus gives, and all three are **files the user can open**.

`memory.py` remembers what happened per application, so the model has context on the second
question about a program rather than starting cold. `kb.py` lets the user supply documentation for
software the model has never seen — the only mechanism that closes the in-house-software gap, because
no amount of screen-reading recovers a convention that exists only in a company wiki. `review.py`
turns that accumulated history into something for the *user* rather than the model: the things they
asked about come back on a schedule, so they learn the software instead of re-asking next month.

The design commitment shared by all three is **no vector database, no embeddings, no retrieval
pipeline**. Plain Markdown the user can read, edit and delete, plus a small index for counting. That
is not a shortcut taken for time; it is the transparency contract. A user can open a memory file in
Notepad and see exactly what Nimbus knows about them, which is what makes persistent memory read as
benign rather than as surveillance. A keyword relevance score is inspectable in a way a cosine
distance is not.

The journal's differentiator is worth stating plainly: because Nimbus can see the screen, a review
item can be **positional** — *"show me where the export button is"* — and graded against a real
grounding call. No flashcard tool can ask that question, because none of them know what is on screen.

> **Provenance.** Consolidated into Kiro's spec format from `IMPROVEMENTS.md` `T3-2` and `T3-3`, plus
> the module contracts in `memory.py`, `kb.py`, `review.py` and `debug_log.py`. The sanitiser drift
> measurement, the ladder rationale, the false-positive table and the live in-house-software
> verification are all recorded there.

## Glossary

| Term | Meaning |
|---|---|
| **Recall** | Reading stored context before a request is sent to the model |
| **Record** | Appending an interaction after a response has already been delivered |
| **Sanitised name** | An application's executable basename normalised for use as a filename and database key |
| **Flat layout** | A single Markdown file named for the application |
| **Folder layout** | A directory named for the application, holding any number of documents |
| **Section** | A Markdown heading plus its body — the unit relevance ranking operates on |
| **Ladder** | The fixed sequence of review intervals in days |
| **Ease** | A per-item multiplier that stretches or tightens the ladder |
| **Positional item** | A review item carrying the on-screen element it was about |

## Requirements

### Requirement 1: Memory is plain files a person can read

**User Story:** As a user, I want to be able to open what Nimbus remembers about me and read it, so
that persistent memory feels benign rather than opaque.

#### Acceptance Criteria

1. THE system SHALL store per-application memory as one Markdown file per application, in a folder
   under the user's home directory.
2. THE Markdown files SHALL be the source of truth, and any database SHALL be a denormalised cache
   derived from them.
3. Each memory file SHALL carry a header stating when the application was first seen, how many
   interactions have been recorded, and that the file may be deleted to reset memory for that
   application.
4. THE system SHALL NOT use embeddings, a vector store or a retrieval pipeline for memory, so that
   what Nimbus knows stays inspectable without tooling.
5. THE rationale SHALL be recorded in the module: a modern model reads brief per-application
   summaries directly at this scale, so the complexity buys nothing and costs transparency.
6. WHEN the user deletes a memory file THEN THE system SHALL treat that application as unseen and
   recreate the file cleanly on the next interaction.
7. THE memory folder SHALL be created lazily on first record rather than at startup, so that a user
   who never asks a question gets no folders in their home directory.

### Requirement 2: Recall is a bounded tail read

**User Story:** As a user, I want past context included without it crowding out my actual question,
so that answers stay about what I asked.

#### Acceptance Criteria

1. THE system SHALL return the last N characters of the memory file, where N is a configured budget.
2. WHERE the file is shorter than the budget, THE system SHALL return the whole file.
3. IF no memory file exists THEN THE system SHALL return an empty string rather than raising.
4. IF the budget is zero or negative THEN THE system SHALL return an empty string, because a tail
   slice of zero length returns the **entire** string in Python and a misconfigured override would
   otherwise send the whole file.
5. THE recall SHALL apply no filtering and no relevance scoring, and the rationale SHALL be recorded
   as a deliberate wait-for-the-data decision rather than an oversight.

### Requirement 3: One name, resolved in one place

**User Story:** As a user, I want the name I read out of my memory folder to be the name my knowledge
base file needs, so that following the documented instructions works.

#### Acceptance Criteria

1. THE system SHALL normalise an application name by lowercasing it, stripping surrounding
   whitespace, and replacing every character Windows forbids in a filename with an underscore.
2. THE normalisation SHALL be implemented once and **delegated to** by every other module that needs
   it, rather than reimplemented.
3. THE reason SHALL be recorded: two copies existed, one claiming in its docstring to mirror the
   other exactly, and measurement across fifteen inputs found **seven disagreeing** — one stripped
   whitespace and replaced nine reserved characters, the other stripped nothing and replaced three.
4. THE user-visible consequence SHALL be recorded: users are instructed to read the canonical name
   out of the memory folder and name their knowledge-base file to match, so for an application whose
   name needed stripping the memory folder showed one name while the knowledge base silently looked
   for another and found nothing.
5. THE fix SHALL be delegation rather than re-synchronising two copies, so that the two folders can
   never disagree again.
6. THE delegation SHALL be pinned by a parametrised drift test covering every measured divergence.
7. IF normalisation rejects a name THEN THE knowledge-base lookup SHALL treat it as "no knowledge
   base" rather than propagating the error, while memory SHALL raise, because an empty name reaching
   memory indicates a real defect in the caller.

### Requirement 4: The record block survives hostile content

**User Story:** As a user, I want a question containing code or newlines to be remembered correctly,
so that one awkward interaction does not corrupt the file.

#### Acceptance Criteria

1. THE system SHALL write each interaction as a fixed-shape block: a heading carrying the timestamp
   and the question, then the window title, the response, and what was pointed at, one field per
   line.
2. THE system SHALL replace triple-backtick fences in any embedded text with a plain ASCII
   substitute, because a model response containing a code fence would otherwise break the block
   shape a parser expects.
3. THE system SHALL replace embedded newlines in single-line fields with a visible marker rather than
   deleting them, so that the user can still see where the original line break was.
4. THE system SHALL strip carriage returns from single-line fields.
5. WHERE no coordinate was produced, THE system SHALL write an explicit text-only placeholder rather
   than an empty field, so that the block shape is stable.
6. THE header SHALL be assembled by string interpolation rather than by a format-template call, so
   that an application name containing a literal brace cannot raise.
7. WHEN a record is appended THEN THE system SHALL rewrite the header so the interaction count is
   current, and preserve every existing block from the first block heading to the end of the file.
8. IF the file exists but contains no block heading — because it was hand-edited or corrupted — THEN
   THE system SHALL rewrite it cleanly rather than failing.
9. All timestamps written within one record call SHALL come from a single clock reading, so that the
   file header and the index cannot skew apart and make ordering non-deterministic.

### Requirement 5: The index is a cache with an idempotent schema

**User Story:** As a developer, I want counters available without reading every Markdown file, so that
the Home page and insights are cheap to render.

#### Acceptance Criteria

1. THE system SHALL maintain a table keyed on the sanitised application name, holding first seen,
   last seen, an interaction count and the Markdown path.
2. THE schema SHALL be created with create-if-not-exists semantics so that construction is idempotent
   and no migration step is needed.
3. THE database SHALL use write-ahead logging so concurrent reads are clean.
4. Each method SHALL open and close its own short-lived connection rather than holding one, so that
   background reads need no lock coordination with the UI thread.
5. THE single-writer model SHALL be documented, and writes SHALL come from one thread only.
6. THE system SHALL expose every known application sorted most-recently-seen first.
7. THE index parent directory SHALL be created eagerly, because the first connection needs it.

### Requirement 6: The knowledge base accepts real documents

**User Story:** As a user with an internal tool nobody has documented publicly, I want to give Nimbus
the actual documentation I have, so that it stops guessing.

#### Acceptance Criteria

1. THE system SHALL support a flat layout — one Markdown file named for the executable — and this
   SHALL continue to work unchanged for existing users.
2. THE system SHALL support a folder layout — a directory named for the executable holding any number
   of documents, read recursively.
3. WHERE both layouts exist for one application, THE system SHALL read the flat file **first**, so
   that a user who later adds a folder keeps their original file as the leading context rather than
   having it reordered.
4. THE system SHALL read Markdown, plain text, PDF and Word documents.
5. THE system SHALL extract table cells from Word documents as well as paragraphs, because software
   documentation puts keyboard shortcuts, field definitions and option matrices in tables and those
   are exactly the content worth having.
6. THE system SHALL ignore unsupported files silently rather than erroring, because users keep
   images and spreadsheets alongside their notes.
7. THE system SHALL read folder contents in a stable order derived from the relative path, so that
   two identical questions can never select different content.
8. THE system SHALL cap the number of files read from one application folder, applied **before**
   extraction so the cost is bounded regardless of file sizes.
9. THE system SHALL label each file inline with its relative path, so that the model knows a shortcut
   list and a troubleshooting guide are separate documents and the user can tell which of their files
   an answer came from.
10. IF no knowledge base matches THEN THE system SHALL return empty and THE pipeline SHALL answer
    from vision and memory, which is the common case rather than an error.

### Requirement 7: Extraction degrades one file at a time

**User Story:** As a user, I want one corrupt PDF to cost me that one file, so that it does not cost
me the rest of my knowledge base.

#### Acceptance Criteria

1. Extraction dependencies SHALL be imported lazily and SHALL be individually optional.
2. IF a format's dependency is unavailable THEN THE system SHALL skip files of that format with a log
   entry rather than failing the knowledge base, mirroring how the local speech and speech-synthesis
   providers degrade.
3. THE extraction entry point SHALL never raise: a corrupt document, an exotic encoding or a file
   locked by another program SHALL yield empty text for that file only.
4. THE reason SHALL be recorded: the pipeline already wraps the lookup, and this keeps one bad file
   from also costing the user the rest of their documents.
5. Text files SHALL be read with replacement on undecodable bytes rather than raising.
6. THE system SHALL skip files whose extracted text is empty or whitespace-only rather than emitting
   an empty labelled section.

### Requirement 8: Over-budget content is selected, not blindly truncated

**User Story:** As a user with fifty pages of documentation, I want the part that answers my question
to be the part Nimbus reads, so that a large knowledge base is better than a small one rather than
worse.

#### Acceptance Criteria

1. THE system SHALL enforce a character budget on the combined knowledge-base content.
2. WHERE the content fits the budget, THE system SHALL return it unchanged and SHALL NOT reorder it,
   because a document's own order carries meaning.
3. WHEN the content exceeds the budget AND the user's question is available THEN THE system SHALL
   split the content into sections on Markdown headings, score each section, and keep the
   highest-scoring sections until the budget fills.
4. THE kept sections SHALL be re-emitted in **original document order**, so that headings still read
   in sequence rather than by score.
5. Scoring SHALL count **distinct** query terms present in a section, not total occurrences, because
   a section repeating one word fifty times is not more relevant than one covering five of the
   question's words and counting occurrences would let a glossary entry outrank the page that
   answers the question.
6. Query terms shorter than a minimum length SHALL be dropped, because they match nearly everything
   and therefore contribute no ranking signal.
7. WHERE the content is over budget and no usable question is supplied, THE system SHALL fall back to
   tail truncation, reproducing the previous behaviour exactly, so that this change is additive.
8. WHERE the content has no headings and therefore only one section, THE system SHALL fall back to
   tail truncation rather than cutting mid-sentence at an arbitrary point.
9. Once at least one scoring section has been kept, THE system SHALL stop admitting zero-scoring
   sections, because irrelevant content is not worth remaining budget.
10. THE previous behaviour SHALL be recorded as the defect being fixed: an over-budget file was
    tail-truncated, so everything before the last portion was silently discarded and a question about
    the discarded part was answered from nothing.
11. Ranking SHALL be keyword-based rather than embedding-based, and the standing judgement against
    premature vector-database complexity SHALL be cited, along with the point that a keyword score is
    inspectable where a cosine distance is not.
12. IF the budget is zero or negative THEN THE system SHALL return empty, for the same tail-slice
    reason as memory recall.

### Requirement 9: The knowledge base is discoverable

**User Story:** As a new user, I want to find out that this feature exists and how to name my files,
so that it is not invisible.

#### Acceptance Criteria

1. THE system SHALL write a guide into the knowledge-base folder when the guide is absent.
2. THE system SHALL write it only when absent, so that a user's own edits are never overwritten.
3. THE guide SHALL state the naming convention, both layouts, the supported formats, the budget, the
   file cap, and that nothing is uploaded except as part of a question the user asks.
4. THE guide SHALL tell the user to read the canonical application name out of their memory folder,
   because that is the one place the exact name is visible.
5. THE guide text SHALL be embedded as a string in the module rather than shipped as a bundled data
   file, because a data file needs frozen-versus-source path resolution that is a recurring source of
   works-in-development, missing-in-the-installer defects, and a few kilobytes of text in a module
   cannot go missing.
6. THE seeding call SHALL never raise, because a read-only or redirected folder must not prevent
   Nimbus starting and a guide is help text rather than functionality.
7. THE system SHALL also offer a Settings action that opens the folder, re-seeding first so that
   folders created by an earlier version are covered.
8. THE Settings action SHALL report failures, because the user clicked something, whereas the startup
   call SHALL stay silent.
9. THE folder opened SHALL be derived from the path the seeding call returns rather than read
   independently, because reading the setting twice allowed seeding one folder and opening another.
10. THE guide filename SHALL be one a flat lookup can never mistake for content, and the reason SHALL
    be recorded: a flat lookup reads only a name with the executable suffix, and foreground detection
    returns executable basenames, so a collision is unreachable.

### Requirement 10: Review scheduling is a pure, explainable ladder

**User Story:** As a learner, I want items to come back at sensible intervals I could predict, so
that the schedule feels like a plan rather than a black box.

#### Acceptance Criteria

1. THE system SHALL use a fixed ladder of intervals in days, indexed by position.
2. THE ladder SHALL be preferred over a computed interval formula, and the reason SHALL be recorded:
   the classic formula needs both a per-item ease and a repetition count to produce sensible numbers
   and misbehaves on small datasets, which is exactly what a personal journal is.
3. THE ladder SHALL be capped, and the reason SHALL be recorded: software changes, so an item last
   reviewed months ago may be about an interface that no longer exists.
4. A per-item ease factor SHALL scale the ladder rather than replace it, so that a consistently easy
   item stretches out and a hard one stays tight while the ladder keeps the numbers sane.
5. THE ease SHALL be clamped to a minimum and a maximum, and the floor SHALL be justified: an item
   the user keeps failing must not collapse to a zero-day interval and be asked forever.
6. A correct answer SHALL advance one rung, capped at the top of the ladder.
7. An incorrect answer SHALL reset to the beginning of the ladder rather than stepping back one rung,
   and the reason SHALL be recorded: stepping back would keep a genuinely unknown item circulating at
   week-long gaps, while resetting shows it tomorrow, which is what not knowing something warrants.
8. Ease adjustment SHALL be asymmetric, with failure moving ease further than success, because
   getting something right once is weak evidence of knowing it and getting it wrong is strong
   evidence of not knowing it.
9. THE computed interval SHALL never be less than one day, or the item would be due again in the same
   session and the user would be asked the same thing repeatedly.
10. Scheduling SHALL be exposed as one entry point that returns the new ladder position, the new ease
    and the day count together, so that position and ease can never be updated inconsistently.
11. THE scheduling functions SHALL take numbers and return numbers, so that the whole algorithm is
    exhaustively testable with no database and no clock.

### Requirement 11: Review items can be positional

**User Story:** As a learner, I want to be asked *where* something is and be checked against the real
screen, so that reviewing teaches me the interface rather than a sentence about it.

#### Acceptance Criteria

1. A review item SHALL carry an optional label naming the on-screen element it concerns.
2. THE label SHALL be populated from the element that was pointed at, which costs nothing because it
   is already present in the result.
3. THE label SHALL make the item askable as a positional question and gradable against a real
   grounding call.
4. THE differentiator SHALL be recorded: no flashcard tool can ask a positional question, because
   none of them can see the screen.

### Requirement 12: Journal navigation is local and cannot hijack a question

**User Story:** As a user, I want asking about my own journal to be instant and free, and I want a
real question that happens to mention reviewing to still be answered.

#### Acceptance Criteria

1. THE system SHALL classify journal commands locally with no model call, because navigating one's
   own journal should be free and instant.
2. THE system SHALL recognise three intents: being quizzed, asking what is due, and asking for a
   recap of what was covered.
3. Classification SHALL require the transcript to be **predominantly** the command rather than merely
   to contain it.
4. THE system SHALL reject any transcript longer than a small word cap as a real question.
5. THE reason SHALL be recorded: a false positive silently replaces a genuine answer with a quiz,
   which is a far worse failure than a missed shortcut.
6. WHERE more than one trigger phrase matches, THE longest phrase SHALL win, so that a
   multi-word command cannot be shadowed by a bare word inside it.
7. THE system SHALL normalise case, question marks and whitespace before matching.
8. Journal commands SHALL be checked **before** capture, so that a question about the user's own
   journal takes no screenshot — less work and one less privacy exposure.
9. THE guard SHALL be pinned by tests over the dangerous near-misses, not only over the phrases that
   should match.

### Requirement 13: The journal is additive and never costs a turn

**User Story:** As an existing user, I want a new feature not to disturb the memory I already have,
and I want a journal failure to be invisible.

#### Acceptance Criteria

1. THE journal SHALL add a table to the existing database rather than creating a new one.
2. THE addition SHALL be purely additive: create-if-not-exists semantics, the same journal-mode
   pragma, and no alteration of the existing table.
3. THE reason SHALL be recorded: users have live databases and this must not disturb them.
4. THE backward-compatibility gate SHALL be an explicit test asserting the existing table is
   untouched.
5. THE journal SHALL default to on, and the deviation from the rule that a new setting reproduces
   existing behaviour SHALL be justified: the feature is purely additive and writes only after an
   interaction has already succeeded.
6. A queue write SHALL silently skip an empty question or answer rather than raising.
7. Every journal write SHALL be swallowed and logged, and the reason SHALL be recorded: the journal
   is written **after** the user already has their answer, so losing an entry is invisible whereas
   raising would surface as a failed interaction.
8. A broken journal SHALL degrade to a normal answer, asserted by a named test.
9. THE system SHALL write a human-readable progress summary to a plain Markdown file, honouring the
   same transparency contract as memory.
10. WHERE nothing has been graded yet, THE summary SHALL say so rather than printing a zero
    percentage.
11. Spoken recap SHALL be written for the ear with no lists, no numbering and no markup, and SHALL
    cap the number of topics named, because a spoken list beyond that is unfollowable — the cap is a
    speech constraint rather than a data one.

### Requirement 14: Every interaction can be reconstructed afterwards

**User Story:** As a developer diagnosing a bad answer, I want the exact inputs and timings of that
one interaction, so that I can tell which stage was wrong.

#### Acceptance Criteria

1. THE system SHALL write one folder per interaction, containing a log with millisecond offsets and
   the screenshots the model was given.
2. THE log SHALL record the application and window title at the start.
3. WHERE a coordinate was produced, THE system SHALL be able to save the screenshot with a marker
   drawn at that coordinate, so that a pointing error is visible rather than inferred.
4. WHERE diagnostics are disabled or unavailable, THE system SHALL substitute a no-op session
   exposing the identical interface, so that callers need no special error path.
5. Diagnostic writes SHALL never raise, because the interaction must not fail on optional logging.
6. THE system SHALL prune expired session folders on startup.
7. IF a file within an expired folder is locked THEN THE system SHALL skip it and continue, because
   retention is best effort and a locked image must not block Nimbus.
8. Pruning SHALL apply only beneath Nimbus's own diagnostics folder.
9. A privacy-suppressed screenshot SHALL never be written to a diagnostics folder.

### Requirement 15: Skills are user code, and the trust model is explicit

**User Story:** As a power user, I want to drop a Python file into a folder and have Nimbus be able to
do something new, so that extending it does not mean forking it.

> **Not built — `T4-3`.** The shape is deliberately the same as the knowledge base: a folder the user
> owns, plain files, no registry and no build step. The recorded risk is what makes this different from
> the knowledge base and why it is still unbuilt: **executing user Python is a security surface**, and
> the trust model has to be written down before a single line runs. A Markdown file the user drops in
> can at worst mislead the model. A Python file the user drops in can do anything the process can.

#### Acceptance Criteria

1. THE system SHALL discover skills as plain Python files in a documented directory under the user's
   Nimbus folder, mirroring the knowledge base's drop-in-a-folder pattern.
2. THE trust model SHALL be documented **before** any execution path exists, and SHALL state plainly
   that a skill runs with the full privileges of the Nimbus process.
3. THE documentation SHALL state what a skill can therefore reach: the user's files, their credential
   store, their network, and their screen.
4. THE system SHALL NOT execute a skill downloaded on the user's behalf, and SHALL NOT fetch skills
   from any remote source, because a marketplace turns one careless install into a compromise.
5. Skills SHALL be disabled by default, and enabling them SHALL be an explicit action with the trust
   model shown at that moment rather than buried in documentation.
6. THE loader SHALL import each file in isolation, and IF a skill raises on import THEN THE system SHALL
   log it, skip that skill, and continue loading the rest — one bad file costs that file only, the same
   contract extraction already has for a corrupt document.
7. A skill SHALL declare its trigger and its callable through a documented contract, so that discovery
   does not depend on inspecting arbitrary module contents.
8. THE system SHALL log which skills loaded and which were skipped, so that "why did my skill not run"
   is answerable without a debugger.
9. A skill SHALL NOT be able to suppress the Privacy Guard, and that SHALL be enforced by the guard's
   existing single choke point rather than by asking skills to behave.
10. THE seeded example SHALL be inert — it SHALL demonstrate the contract without touching the network,
    the filesystem outside its own folder, or the credential store.
11. THE decision to build this SHALL record what it costs: every skill is code the maintainer did not
    write running inside a process that holds the user's keys, and no amount of documentation makes
    that reversible after the fact.
