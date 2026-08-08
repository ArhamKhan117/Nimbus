# Requirements Document

## Introduction

Nimbus is bring-your-own-key and provider-agnostic: five vision providers sit behind one `AIClient`
abstraction, and one of them is a fully-local Ollama stack that needs no network at all. The native
Gemini path is the default because it is the only one that returns geometry as a **typed function
call** rather than a tag parsed out of prose — which is the difference between a contract and a
convention, and the reason the pointer lands.

This spec covers the provider abstraction, the native Gemini path and its capabilities (structured
geometry, thinking budgets, context caching, search grounding, agentic vision), the Vertex AI
backend switch, and the grid-based fallback for models that cannot return coordinates at all.

> **Provenance.** Consolidated into Kiro's spec format from `IMPROVEMENTS.md` §0 (the strategic
> decision), §4 (Tier 1, items `T1-1` through `T1-9`) and §11 (measured results). Task IDs are
> preserved. That document also carries the honest accounting of which vendor recommendations were
> genuine improvements and which were positioning.

## Glossary

| Term | Meaning |
|---|---|
| **BYOK** | Bring your own key. The user's credential, the user's model calls, no proxy |
| **Structured geometry** | Coordinates returned as a declared function call, never in the text channel |
| **Split-role** | Two concurrent requests per turn: one for speech with no tools, one for geometry with tools only |
| **Thinking budget** | Per-request reasoning token allowance, tiered by question class |
| **Query class** | `locate`, `diagnostic` or `conceptual`, decided by a pure keyword function |
| **Agentic Vision** | The model inspecting and zooming into the screenshot itself, instead of Nimbus cropping |
| **Grid locator** | A two-stage numbered-grid fallback for providers that cannot return coordinates |

## Requirements

### Requirement 1: Provider abstraction

**User Story:** As a user, I want to choose my own model provider, so that I am not locked into one
vendor's pricing, privacy posture or availability.

#### Acceptance Criteria

1. THE system SHALL define one abstract client interface, and SHALL route to a concrete
   implementation from a single factory keyed on the model identifier's prefix.
2. THE factory SHALL dispatch local models first, because bare `llama*` and `qwen*` prefixes are
   unambiguous and no cloud provider ships models under those names.
3. IF a model identifier matches no known prefix THEN THE factory SHALL raise an error listing every
   supported prefix and stating how to add a provider.
4. THE system SHALL centralise the endpoint decision in one function, so that no provider can forget
   OpenRouter routing.
5. THE system SHALL adapt a model slug to the endpoint receiving it, because the two endpoints
   Nimbus can reach disagree on version punctuation.
6. WHEN a new capability is added to the interface THEN THE system SHALL give it a concrete default
   on the base class, so that every existing provider continues to work untouched.
7. THE pipeline SHALL contain no branch on provider identity, so that adding a provider is one class
   and one factory case.

### Requirement 2: Structured geometry instead of tag parsing

**User Story:** As a user, I want the pointer to land on the control I asked about, so that the
explanation and the thing explained are never in two different places.

#### Acceptance Criteria

1. WHERE a provider supports it, THE system SHALL declare a pointing function whose vertical and
   horizontal positions are **separately named integer fields**, so that the model cannot transpose
   them and the wire format is self-documenting.
2. THE system SHALL expose a capability flag for structured geometry, so that the caller can skip the
   tag-safety machinery entirely for providers that do not need it.
3. WHEN a tag-based prompt is passed to a structured provider THEN THE system SHALL substitute the
   structured equivalent, because the model otherwise obeys the prompt's instruction to append a tag
   and puts coordinates back into the speech channel.
4. THE prompt substitution SHALL match on **prefix, not equality**, so that a prompt carrying an
   appended per-app addendum is still recognised as ours.
5. THE system SHALL make the geometry decision and the prompt substitution agree by construction,
   rather than through two independent checks.
6. WHERE a genuinely custom prompt is supplied, THE system SHALL pass it through untouched, so that
   the refinement path which deliberately wants a text tag keeps working.
7. IF a structured geometry call is malformed THEN THE system SHALL drop the pointer and keep the
   spoken answer, because losing the pointer is strictly better than failing the interaction.

### Requirement 3: Split-role concurrent calls

**User Story:** As a user, I want both an answer I can hear and a pointer I can see, so that a turn
is never silent.

#### Acceptance Criteria

1. THE system SHALL issue the speech request with **no tools declared**, because measured across
   thinking budgets 0, 64, 128, 256 and 512, a tool-enabled call that chose to point emitted zero
   text.
2. THE system SHALL issue the geometry request with tools only, on a background thread, and SHALL
   harvest it when the final result is read.
3. THE two requests SHALL run concurrently, so that wall-clock cost is the maximum of the two rather
   than their sum.
4. THE system SHALL skip the geometry request entirely for a conceptual question, so that such a turn
   costs exactly one request.
5. WHERE annotation mode is active, THE system SHALL always attempt the geometry request, because a
   drawing instruction such as "circle the search bar" contains no directional word and would
   otherwise produce nothing.
6. THE geometry request SHALL be harvested exactly once, whichever of the two accessors is called
   first.
7. IF the geometry request times out or fails THEN THE system SHALL return no geometry and SHALL NOT
   propagate the failure into the speech path.
8. THE geometry request SHALL NOT receive the knowledge base, because locating a pixel is visual and
   application documentation cannot help with it.

### Requirement 4: Thinking budgets tiered by question class

**User Story:** As a user, I want a simple "where is it" question answered immediately, so that the
common case is not slowed down by reasoning it does not need.

#### Acceptance Criteria

1. THE system SHALL classify a transcript as `locate`, `diagnostic` or `conceptual` using a pure
   function with no network call.
2. THE classifier SHALL test for diagnostic intent **first**, because a question like "why is this
   button greyed out" contains a directional word yet needs real reasoning.
3. THE system SHALL assign a reasoning budget of zero to a locate question, a small budget to a
   conceptual one, and the largest budget to a diagnostic one.
4. IF the selected model rejects a zero budget THEN THE system SHALL silently raise it to that
   model's floor, so that the latency optimisation is safe to enable on any model.
5. THE geometry request SHALL always use the minimal budget, because locating a control is perception
   rather than reasoning.
6. WHERE agentic vision is enabled, THE system SHALL raise the geometry budget and append inspection
   guidance to the geometry prompt only, because the user never hears any of it.
7. THE directional word list SHALL be kept in sync with the pipeline's own copy by a test, so that
   the two cannot drift.

### Requirement 5: Knowledge-base context caching

**User Story:** As a user with a large set of notes, I want Nimbus to stop re-sending them on
every question, so that using the feature does not become expensive.

#### Acceptance Criteria

1. WHERE a knowledge-base payload exceeds the caching threshold, THE system SHALL create a
   provider-side cache and reference it instead of inlining the content.
2. THE cache key SHALL include a content hash, so that editing a note invalidates the cache
   immediately rather than serving stale documentation until it expires.
3. WHEN a cache is in use THEN THE system SHALL NOT also inline the content, so that the content is not sent twice.
4. WHEN a cache is in use THEN THE system SHALL omit the system instruction from the request, because
   the cache carries it and supplying both is rejected.
5. IF cache creation fails for any reason THEN THE system SHALL fall back to inline injection, so that
   the worst case is the previous behaviour.
6. THE cache SHALL apply to the speech request only, because the two requests use different prompts
   and different tools and one cache cannot serve both.
7. WHEN the client shuts down THEN THE system SHALL delete every live cache, because caches are billed
   for their storage duration.
8. THE system SHALL estimate token count locally rather than making a counting round trip, and SHALL
   over-estimate so that the threshold admits slightly more content rather than silently skipping it.

### Requirement 6: Grid fallback for models that cannot return coordinates

**User Story:** As a user running a small local model, I want pointing to work at all, so that the
fully-offline configuration is a real option rather than a checkbox.

#### Acceptance Criteria

1. WHERE the active provider cannot return structured geometry AND no coordinate was parsed from the
   text, THE system SHALL locate the target using a numbered-grid pass.
2. THE first pass SHALL overlay a coarse numbered grid and ask the model for a single cell number.
3. THE second pass SHALL crop to the chosen cell plus one cell of context, overlay a finer grid, and
   ask again.
4. IF the second pass fails THEN THE system SHALL fall back to the centre of the first pass's cell.
5. IF the model answers with the conceptual sentinel THEN THE system SHALL place no pointer.
6. THE system SHALL upscale a small crop before the second pass, so that grid labels stay legible to
   a weak model.
7. THE system SHALL distinguish three failure modes in its diagnostics — image decode failure,
   transport failure, and a reply that could not be parsed — because previously all three looked
   identical.
8. THE grid fallback SHALL be skipped entirely when the turn has been cancelled, because its two
   model calls take 5–10 seconds on a local model and would emit side effects for an abandoned turn.

### Requirement 7: Per-application prompt addenda

**User Story:** As a user asking about code, I want Nimbus to talk about my code rather than about
the editor's toolbar, so that the answer is about the thing I care about.

#### Acceptance Criteria

1. WHERE the foreground application matches a known entry, THE system SHALL append guidance to the
   system prompt.
2. THE system SHALL **append, never substitute**, because the base prompt carries the persona, the
   write-for-the-ear contract and the pointing rules.
3. THE lookup keys SHALL be produced by the same name-sanitising function the memory folder uses, so
   that the keys match the folder names users already see and the two cannot drift.
4. THE lookup SHALL return an empty string rather than a null value, so that callers can concatenate
   without a branch.
5. IF foreground detection fails THEN THE system SHALL return no addendum rather than raising, because
   a detection hiccup must never break the prompt.
6. THE system SHALL expose one entry point that performs the append, so that the append-never-replace
   rule is structural rather than a convention someone has to remember.

### Requirement 8: Vertex AI backend switch

**User Story:** As an institution, I want inference to run inside my own cloud project, so that
authentication, billing, auditing and data residency are mine.

#### Acceptance Criteria

1. WHERE a Google Cloud project is configured, THE system SHALL construct the client against Vertex
   AI and SHALL pass no API key.
2. WHERE no project is configured, THE system SHALL construct the client against the consumer API
   with the user's key, which remains the zero-configuration path for an individual.
3. THE project setting SHALL be checked **before** the key-shape check, so that an institution which
   has deliberately configured Vertex is never silently downgraded to a leftover pasted key.
4. THE system SHALL read the project and region fresh rather than caching them at import, so that
   switching a deployment does not require a restart.
5. IF the region is blank THEN THE system SHALL substitute the global endpoint, because the SDK's
   empty-region error names neither the setting nor the fix.
6. THE speech-to-speech path SHALL apply the identical switch, so that voice and text cannot land on
   different backends.
7. THE system SHALL expose which backend is live, so that "which endpoint am I billing against" is
   not a question answered by reading configuration.
8. Everything downstream — tool declarations, thinking budgets, caching, the split-role calls —
   SHALL be unchanged by the switch, because that equivalence is why it is a setting rather than a
   fork.

### Requirement 9: Experimental capabilities, honestly labelled

**User Story:** As a user, I want to know what a switch will actually cost me before I flip it, so
that I do not blame the app for a choice it nudged me into.

#### Acceptance Criteria

1. THE system SHALL default every experimental capability to OFF, except where enabling it changes no
   observable behaviour and degrades to the current path on any failure.
2. THE system SHALL make each capability reachable from the Settings interface rather than leaving it
   as an unreachable scaffold.
3. THE system SHALL state each capability's trade-off in its own description, including where a
   capability measured **worse** than the default.
4. WHERE a capability requires a specific provider, THE system SHALL say so in its description.
5. THE system SHALL write an explicit on or off value rather than deleting the setting when off, so
   that a deliberate refusal is distinguishable from "never configured".
6. WHERE search grounding is enabled, THE system SHALL collect citations for the diagnostic log and
   SHALL NOT include them in spoken text.

### Requirement 10: Graceful degradation is a gate, not a goal

**User Story:** As a user with no internet and no API keys, I want Nimbus to work, so that the
privacy claim is real rather than aspirational.

#### Acceptance Criteria

1. THE fully-local configuration SHALL remain functional after every model-layer change.
2. WHERE a capability is unsupported by the active provider, THE system SHALL ignore its setting
   rather than failing.
3. IF a model request fails THEN THE system SHALL raise an error naming the model and listing the
   three things to check: the key, access to that model, and connectivity.
4. THE system SHALL probe a local server's version and SHALL warn — without blocking — when the
   selected model needs a newer version than is installed.
5. IF the local server cannot be reached THEN THE system SHALL treat that as "could not check" rather
   than as "incompatible", so that a stopped server does not produce a misleading warning.
