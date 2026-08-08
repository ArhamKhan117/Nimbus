# Design Document

## Overview

The valuable asset here is the seam, not any one provider. `AIClient` plus `create_ai_client()` makes
a new provider **one class and one factory case**, and every capability below is cheap *because* of
that. The rule that protects it: no `if provider == ...` in `_pipeline_worker`, ever.

The native Gemini path is the default (`config.DEFAULT_LLM_PROVIDER = "gemini-native"`) for one
load-bearing reason and three supporting ones. The load-bearing one is that geometry arrives as a
typed function call, so coordinates never share a channel with speech. The supporting ones are
thinking budgets, explicit context caching, and the fact that these three together are why three
months of development on a product that sends a screenshot with every interaction cost under twenty
dollars in model calls.

> Consolidated from `IMPROVEMENTS.md` §0 and §4. Read §0.3 for the honest accounting of which vendor
> recommendations were genuine improvements (structured output, split-role, measurement) and which
> were parity or convenience (caching, grounding).

## Architecture

```
                        create_ai_client(model_id, api_key, base_url, ollama_host)
                                          │
        ┌──────────────┬──────────────┬────┴─────────┬──────────────────┐
        │              │              │              │                  │
   ollama/ llama*   anthropic/     google/        openai/          (no match)
   qwen* llava*     claude*        gemini*                              │
        │              │              │              │             ValueError
   OllamaClient   Anthropic      ┌────┴────┐   OpenAIVision      listing every
   raw httpx      Client         │         │    Client            prefix
   grid locator   slug adapt   direct    sk-or-  max_completion
   fallback       per endpoint  key or   key     _tokens
                                project    │
                                   │       └─→ GeminiClient (OpenAI-compat shim)
                                   │
                        GeminiNativeClient  ← THE DEFAULT
                                   │
                    ┌──────────────┴──────────────┐
              genai.Client(api_key)      genai.Client(vertexai=True,
              consumer API                          project, location)
              zero config for one person   ADC, no key held at all
```

Per turn, the native client issues two concurrent requests:

```
ask_stream(images, transcript, history, system_prompt, kb_content, annotation_mode)
   │
   ├─ speech call ─ generate_content_stream, NO tools, budget by query class,
   │                cached_content when the KB cache resolved
   │                └─→ text deltas ─→ sentence flush ─→ TTS
   │
   └─ geometry call ─ thread nimbus-gemini-geometry, generate_content,
                      tools ONLY, minimal budget, NO knowledge base
                      └─→ harvested once in final_result() / geometry()

   wall clock = max(speech, geometry)
   skipped entirely for a conceptual question outside annotation mode
```

**Why the split is forced rather than chosen.** Measured across thinking budgets 0, 64, 128, 256 and
512: whenever the model chose to call the pointing function it emitted **zero text**. A single
tool-enabled call produced a pointer and total silence. Silence is a correctness failure — the user
held a hotkey and asked a question.

## Components and Interfaces

### `ai.AIClient`

```python
class AIClient(ABC):
    @abstractmethod
    def ask(self, image, transcript, history, declared_w, declared_h) -> dict: ...

    # Concrete defaults, so every existing provider keeps working untouched.
    def supports_structured_geometry(self) -> bool: return False
    def supports_thinking_budget(self) -> bool: return False
```

The de-facto contract every provider also implements, and the one the pipeline actually calls:

```python
ask_stream(images, transcript, history, system_prompt=..., max_tokens=1024,
           kb_content="", kb_app_name="") -> context manager
    .text_deltas() -> Iterator[str]
    .final_result() -> PointParseResult
```

`GeminiNativeClient.ask_stream` adds `annotation_mode: bool = False` and also exposes `geometry()`,
`citations()`, `search_queries()`, `uses_vertex()`, `supports_agentic_refinement()` and `close()`.

### Endpoint and slug adaptation

`_provider_base_url(api_key, base_url, openrouter_url, native_url=None)`: an explicit override wins;
an `sk-or-` key routes to OpenRouter; otherwise the provider's native endpoint or the SDK default.
Centralised because OpenAI's branch once forgot it and an OpenRouter key was sent to the wrong host
and rejected.

`_anthropic_model_for_endpoint(model_id, base_url)`: the two endpoints disagree on version
punctuation — native wants `claude-sonnet-4-6`, OpenRouter wants `anthropic/claude-sonnet-4.6`. The
native dash form is canonical and this converts on the way out.

### Tool declarations

Always declared: **`point_at`** with `y`, `x` (each normalised 0–1000, separately named integers) and
a short `label`. Only in annotation mode: `draw_box` and `highlight_region`, sharing one
`[ymin, xmin, ymax, xmax]` schema declared once so the y-first note lives in a single place; and
`mark_step`, adding a 1-based step number.

The y-first convention and the separate integer fields are both there for the same reason: a
transposition would be silent and systematic.

### Thinking budgets

```python
THINKING_BUDGET_BY_CLASS = {"locate": 0, "conceptual": 512, "diagnostic": 4096}
_PRO_MIN_BUDGET = 128        # pro models return 400 "Budget 0 is invalid"
_AGENTIC_THINKING_BUDGET = 2048
```

Measured: dropping to a zero budget moved time-to-first-token from 3.97 s to 1.18 s. The per-model
floor is load-bearing, not defensive.

### `gemini_cache.KBCacheManager`

Threshold, TTL and estimate: 4096 tokens, 900 seconds, four characters per token. The estimate is
deliberately pessimistic — measured, this content runs about six characters per token, so using four
over-estimates and admits slightly more content rather than silently skipping it. Verified live: a
60,000-character knowledge base is 10,002 tokens, and caching served 10,008 of 10,013 prompt tokens
from cache. Every failure path returns `None`, which the caller treats as "inline as before".

### `locator.py`

Locked tunables: a 12×8 first pass (96 cells), a 6×6 second pass (36 sub-cells), ±1 cell of context,
1280 px maximum inference width, 900 px refinement crop at native resolution. The transform chain is
inference → JPEG → physical → virtual → logical, and the crop is centred then clamped inside the
image so it never runs off an edge.

Documented accuracy: the grid locator lands within roughly 25–50 px on a 1080p screen, against about
5 px for a native structured point. It exists so the offline path works, not because it is good.

## Data Models

```python
@dataclass class PointParseResult:          # not frozen - see ai.py
    spoken_text: str
    coordinate: tuple[int, int] | None      # Space C, unclamped by contract
    element_label: str | None
    screen_number: int | None
    malformed_tags: tuple[str, ...] = ()
```

The geometry channel is **not** a declared type. `_GeometryWorker.result()` returns
`list[tuple[str, dict]]` — the tool name paired with its raw arguments — where the name is one of
`point_at`, `draw_box`, `highlight_region` or `mark_step` and the arguments carry normalised
0–1000 integers plus a label. Kept untyped deliberately: the shape is whatever the SDK handed back,
and asserting a `TypedDict` over it would be a claim this module cannot enforce.

Settings, all resolved through `config.resolve_setting`. Restart-gated:
`LLM_PROVIDER`, `GEMINI_NATIVE_MODEL`, `SEARCH_GROUNDING`, `AGENTIC_VISION`, `CODE_EXECUTION`,
`KB_CACHE`, `GROUNDING_REFINEMENT`. **Not** restart-gated: `GOOGLE_CLOUD_PROJECT` and
`GOOGLE_CLOUD_LOCATION`, which `ai.vertex_settings()` resolves fresh on every client construction —
deliberately, so switching to a cloud project needs no relaunch.

## Correctness Properties

### Property 1: Factory routing is total and deterministic

For any non-empty model identifier, `create_ai_client` either returns a concrete client or raises a
`ValueError` naming every supported prefix. The same identifier always produces the same class.
Generator: arbitrary strings, plus the cross product of every known prefix with arbitrary suffixes
and arbitrary casing.

**Validates: Requirements 1.1, 1.2, 1.3**

### Property 2: Endpoint choice is a pure function of three inputs

`_provider_base_url` depends only on the explicit override, the key prefix and the supplied defaults.
An explicit override always wins; an `sk-or-` key always routes to OpenRouter. Generator: the cross
product of override present/absent, key prefixes, and native URL present/absent.

**Validates: Requirements 1.4, 1.5**

### Property 3: Prompt substitution preserves any suffix

For any Nimbus base prompt and any suffix, substitution replaces exactly the base and returns the
suffix unchanged. For any prompt that is not one of ours, substitution is the identity. Generator:
each base prompt concatenated with arbitrary text, plus arbitrary unrelated prompts.

**Validates: Requirements 2.3, 2.4, 2.6, 7.2**

### Property 4: The geometry decision agrees with the prompt substitution

For any prompt, "this is one of ours" is answered identically by the substitution function and the
geometry-wanted decision. These previously disagreed via two independent equality checks, which
silently disabled pointing once an addendum was appended.

**Validates: Requirements 2.5**

### Property 5: Geometry is harvested exactly once

For any call order and any repetition of `final_result()` and `geometry()`, the worker is joined once
and its calls appear once in the accumulated list. Generator: every permutation and repetition of the
two accessors, with and without a worker present.

**Validates: Requirements 3.6**

### Property 6: Query classification is total, pure and diagnostic-first

For any string, `classify_query` returns one of exactly three values, with no I/O. Any string
containing both a diagnostic phrase and a directional phrase classifies as diagnostic. Blank input
classifies as conceptual.

**Validates: Requirements 4.1, 4.2**

### Property 7: A clamped budget is never rejected

For any budget and any model identifier, the clamped budget is either greater than zero or zero on a
model known to accept zero. No combination yields a value the provider refuses. Generator: budgets
including negatives, across identifiers containing and not containing the pro fragment.

**Validates: Requirements 4.4, 4.5**

### Property 8: Caching is invisible on failure

For any knowledge-base content and any cache outcome (created, hit, skipped, failed), the assembled
request contains that content exactly once — inlined when there is no cache, referenced when there
is, never both and never neither.

**Validates: Requirements 5.1, 5.3, 5.5**

### Property 9: Content identity drives cache identity

For any application name and any two contents, the cache keys are equal if and only if both the name
and the content are equal. A single-character edit produces a different key.

**Validates: Requirements 5.2**

### Property 10: The grid transform is monotonic and in bounds

For any chosen cell in a grid of any size, the resulting logical point lies inside the target
monitor, and a higher cell index never yields a smaller coordinate along the scan direction.
Generator: arbitrary grid dimensions, image sizes, monitor origins and DPI scales.

**Validates: Requirements 6.2, 6.3, 6.4**

### Property 11: Addendum lookup is total and non-destructive

For any application name, including the unknown sentinel and the empty string, the lookup returns a
string, and applying it yields a result that starts with the original prompt.

**Validates: Requirements 7.1, 7.2, 7.4, 7.5, 7.6**

### Property 12: The backend switch changes construction and nothing else

For any settings combination, the tool declarations, thinking budgets, prompt selection and split-role
structure are byte-identical between the two backends; only the client construction and whether a key
is passed differ. A non-empty project always selects Vertex regardless of key shape.

**Validates: Requirements 8.1, 8.2, 8.3, 8.6, 8.8**

## Error Handling

| Failure | Response | Why |
|---|---|---|
| Unknown model prefix | `ValueError` listing every prefix and how to add one | A silent mis-route is worse than a refusal |
| Request setup fails | `RuntimeError` with a three-point checklist | The three real causes are key, access and connectivity |
| Malformed geometry call | Drop the pointer, keep the answer | The spoken answer is already correct and useful |
| Geometry worker times out | Return `[]` after 8 s | Never propagate into the speech path |
| Cache creation fails | Return `None`, inline as before | Caching is a cost optimisation, not a feature |
| Cache deletion fails at shutdown | Continue to the next | Best effort; TTL will collect it |
| Grid pass: image decode fails | Log that specific cause, return `None` | Three failure modes previously looked identical |
| Grid pass: transport fails | Log that specific cause, return `None` | "Ollama down" is not "the model said no" |
| Grid pass: reply unparseable | Log with a 200-character preview | The only way to see a model drifting off-format |
| Refinement out of crop bounds | Keep the original coordinate | An uncertain correction is worse than none |
| Local server unreachable | Treat as "could not check", emit no warning | A stopped server is not an incompatible model |
| Search grounding returns no citations | Not an error | Grounding is advisory |

## Testing Strategy

- **Pure functions exhaustively, with no network**: prefix routing (`test_ai.py`, 109 tests),
  endpoint selection, slug adaptation per endpoint, query classification as a full table, budget
  clamping per model family, cache key derivation and the token estimate, the grid transform chain.
- **The regression that names its bug**: the classic here is that this provider returns **normalised
  0–1000** numbers inside a text tag as well, not pixels — measured on 900×900, 1920×1080, 600×400 and
  400×1200 images, where a dead-centre target returned the same value every time. Assuming pixels
  inflated every refined point and pushed a pixel-perfect seed about 50 px off target on the 900 px
  crop, making refinement actively worse. The test names that.
- **Structured geometry with a fake client**: `test_gemini_native.py` (60 tests) injects a client
  factory and asserts the split-role structure, the once-only harvest, the prompt prefix matching, and
  that a tool-enabled speech call is never issued.
- **Caching** in `test_gemini_cache.py` (59 tests) including the race where two threads create
  concurrently and the loser's cache is left to expire.
- **The Vertex switch** in `test_vertex_backend.py` (11 tests), all by construction and unit test —
  this path has never been executed against a live Vertex project, and that limitation is recorded
  rather than papered over.
- **Sync guards**: the directional word list exists in two modules to avoid an import cycle, and a
  test asserts the two copies match.
- **Grounding accuracy** is measured, not asserted: `tools/bench_grounding.py` scores hit rate and
  pixel error from a box centre against labelled fixtures produced by `tools/label_fixtures.py`.
  The harness is complete; the comparative run for agentic vision has not been done, which is why
  that capability ships off with its description saying so.
