# Writing for the ear, and the interface rules that follow from it

Everything Nimbus says is **heard, not read**. That single fact drives more of this codebase than any
other design decision, because it means the speech channel has to be clean by construction rather
than by the model behaving well.

## The speech channel

**One or two sentences by default.** The persona is a companion sitting next to you, not a document.
Lowercase prose, no lists, no numbering, no markdown, no headings.

**Nothing machine-shaped ever reaches text-to-speech.** Not because it looks untidy, but because TTS
reads it out: "backtick backtick backtick python draw box open paren" is what a fenced code block
sounds like. Four scrubbers run in order in `ai.strip_non_speech`, and the order matters — fences
first, so their contents cannot be partially rescued by the narrower patterns that follow:

1. `_CODE_FENCE_RE` — fenced blocks, terminated or truncated
2. `_TOOL_CALL_TEXT_RE` — a bare `point_at(...)` written as prose
3. `_LATEX_MATH_RE` — inline and display maths (`$f'(x) = u'(x)v(x)$`), which code execution provokes
4. `_MARKDOWN_NOISE_RE` — headings, emphasis, inline ticks

**Coordinates are never spoken, and the stripping is fail-closed.** Three regexes per tag family: the
valid form, the complete-but-unparseable form, and an unterminated tail that eats to end-of-string.
A response truncated mid-tag (`"look here [POINT:120,40"`) matches only the third, and without it the
coordinates would be read aloud.

**Sentence-level streaming is the largest latency win in the product.** The answer is flushed to TTS
at each `[.!?]\s` boundary, so sentence one is playing while sentence three is still being
generated — about 2 s of perceived latency. The tag-safety guard is what makes it safe: flushing
stops permanently for the turn the moment a `[` appears, and the tail is flushed from the
already-stripped text.

**Do not read things out character by character.** Per-app addenda in `prompts.py` say this
explicitly for each context: never spell a URL (name the site), never read a long path, never read a
line number (say what the line does), never read a long code fragment (describe it).

**Speak first, then draw.** In annotation mode the structured prompt requires speech and describes
the drawing as already present — *"I've boxed the save button up in the top left"*. This is a
correctness requirement, not a style preference: the live test produced an **empty** spoken reply,
because a model handed a drawing tool called it and said nothing. Silence is a failure, not a
cosmetic issue — the user held a hotkey and asked a question.

## The visual interface

**No emoji.** Enforced by a test over string literals in every widget-building module. Segoe UI Emoji
is a separate font with its own metrics — one glyph silently changes a label's line height — and its
own palette, which ignores the theme entirely. Monochrome text-presentation glyphs (`✕` close,
`⚑` pin, `✓`/`✗` in the Journal) are fine and the guard is narrowed to let them through.

**Draw a glyph rather than typing one where the shape matters.** `\u2b1c` renders as a *solid white
block* in Segoe UI, so the maximise button paints a rectangle outline instead. The power symbol on
the switch is an arc plus a line for the same reason: an icon-font glyph measures 1.36–1.44× the
surrounding cap height, because icon fonts fill the em box while text capitals occupy about 70% of it.

**A measured zero and an unmeasured one are different claims.** Home renders `—` when a provider is
absent, never `0`. The Privacy Guard's suppression count is the most trust-building number in the
interface *precisely because* it is an observation, so claiming zero suppressions when nobody counted
would undermine the thing it exists to build.

**Say what to do, not only where you are.** "PAUSED" tells the user their state and nothing about how
to leave it. The status line under the switch reads *"The hotkey is ignored while paused. Nothing is
listening."*

**Explain an absence.** The `system` message role exists so the transcript can say *"Screenshot
skipped — a password manager was open"*. Without it a privacy-suppressed turn is indistinguishable
from Nimbus malfunctioning, and the user's conclusion is that the app is broken rather than that it
protected them.

**Tooltips carry the *why*; labels carry the *what*.** A four-word status can only be a label. The
sentence explaining what it means belongs on hover, not permanently in a 216px rail.

**Never disguise a manual process as automatic.** A page that says "we will confirm this by hand,
usually within a few hours" and then does is better than a spinner that lies.

**Motion communicates state; it is not decoration.** Exits are always faster than entrances
(`DUR_EXIT` 160 vs `DUR_ENTRANCE` 260) — an element arriving deserves to be noticed, the same element
leaving is in the user's way. Nothing exceeds `DUR_MAX` 300 ms. Every duration passes through
`theme.duration()`, which collapses to 0 when Windows' `SPI_GETCLIENTAREAANIMATION` says so.

**Error copy names the number and the remedy.** Not "device limit reached" but *"This licence is already
on 2 devices. Open Nimbus on one of them and use Account → Deactivate this device."* Someone who knows
they are on 2 of 2 can act; someone told "limit reached" has to come and ask.
