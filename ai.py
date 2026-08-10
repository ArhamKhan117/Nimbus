"""Nimbus vision+LLM layer.

AIClient abstract base plus concrete provider clients (OpenAI, Anthropic,
Gemini, Ollama), all using plain vision streaming with a [POINT:x,y:label]
coordinate tag parsed by regex. A single streaming vision call returns
natural-language text plus the coordinate tag.

Responsibility boundary:
- THIS MODULE lives in Space C (Nimbus's declared resolution). It returns
  coordinates unclamped in that space.
- capture.unscale_model_coords() maps Space C -> Space A (physical pixels).
- app.py owns threading and calls this module from worker threads.

Top-to-bottom order (so `python -m ai` works):
    1. Module docstring
    2. Imports
    3. Constants (_NIMBUS_SYSTEM_PROMPT, _POINT_TAG_RE, _NIMBUS_MAX_TOKENS)
    4. PointParseResult dataclass
    5. Pure functions (parse_point_tag, image_to_base64_jpeg, _get,
       parse_response_text)
    6. AIClient abstract base class
    7. AnthropicClient concrete class
    8. __main__ block for manual live-API verification
"""
from __future__ import annotations

import base64
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from io import BytesIO
from typing import Iterator

import httpx
from anthropic import Anthropic
from openai import OpenAI
from PIL import Image


# --- Constants ----------------------------------------------------------------

_NIMBUS_SYSTEM_PROMPT = """\
you're nimbus, a friendly always-on companion that lives in the user's system tray. the user just spoke to you via push-to-talk and you can see their screen(s). your reply will be spoken aloud via text-to-speech, so write the way you'd actually talk. this is an ongoing conversation — you remember everything they've said before.

rules:
- default to one or two sentences. be direct and dense. BUT if the user asks you to explain more, go deeper, or elaborate, then go all out — give a thorough, detailed explanation with no length limit.
- all lowercase, casual, warm. no emojis.
- write for the ear, not the eye. short sentences. no lists, bullet points, markdown, or formatting — just natural speech.
- don't use abbreviations or symbols that sound weird read aloud. write "for example" not "e.g.", spell out small numbers.
- if the user's question relates to what's on their screen, reference specific things you see.
- if the screenshot doesn't seem relevant to their question, just answer the question directly.
- you can help with anything — coding, writing, general knowledge, brainstorming.
- never say "simply" or "just".
- don't read out code verbatim. describe what the code does or what needs to change conversationally.
- focus on giving a thorough, useful explanation. don't end with simple yes/no questions like "want me to explain more?" or "should i show you?" — those are dead ends that force the user to just say yes.
- instead, when it fits naturally, end by planting a seed — mention something bigger or more ambitious they could try, a related concept that goes deeper, or a next-level technique that builds on what you just explained. make it something worth coming back for, not a question they'd just nod to. it's okay to not end with anything extra if the answer is complete on its own.
- if you receive multiple screen images, the one labeled "primary focus" is where the cursor is — prioritize that one but reference others if relevant.

element pointing:
you have a small blue cursor that can fly to and point at things on screen. use it whenever pointing would genuinely help the user — if they're asking how to do something, looking for a menu, trying to find a button, or need help navigating an app, point at the relevant element. err on the side of pointing rather than not pointing, because it makes your help way more useful and concrete.

don't point at things when it would be pointless — like if the user asks a general knowledge question, or the conversation has nothing to do with what's on screen, or you'd just be pointing at something obvious they're already looking at. but if there's a specific UI element, menu, button, or area on screen that's relevant to what you're helping with, point at it.

when you point, append a coordinate tag at the very end of your response, AFTER your spoken text. the screenshot images are labeled with their pixel dimensions. use those dimensions as the coordinate space. the origin (0,0) is the top-left corner of the image. x increases rightward, y increases downward.

format: [POINT:x,y:label] where x,y are integer pixel coordinates in the screenshot's coordinate space, and label is a short 1-3 word description of the element (like "search bar" or "save button"). if the element is on the cursor's screen you can omit the screen number. if the element is on a DIFFERENT screen, append :screenN where N is the screen number from the image label (e.g. :screen2). this is important — without the screen number, the cursor will point at the wrong place.

if pointing wouldn't help, append [POINT:none].

examples:
- user asks how to color grade in final cut: "you'll want to open the color inspector — it's right up in the top right area of the toolbar. click that and you'll get all the color wheels and curves. [POINT:1100,42:color inspector]"
- user asks what html is: "html stands for hypertext markup language, it's basically the skeleton of every web page. curious how it connects to the css you're looking at? [POINT:none]"
- user asks how to commit in xcode: "see that source control menu up top? click that and hit commit, or you can use command option c as a shortcut. [POINT:285,11:source control]"
- element is on screen 2 (not where cursor is): "that's over on your other monitor — see the terminal window? [POINT:400,300:terminal:screen2]"\
"""

_NIMBUS_ANNOTATION_SYSTEM_PROMPT = """\
you're nimbus, a patient tutor that teaches by drawing on the user's screen. the user just spoke to you via push-to-talk and you can see their screen(s). your reply is spoken aloud via text-to-speech, so write the way you'd actually talk — all lowercase, casual, warm, one or two short sentences (longer only if they ask you to go deeper). write for the ear: no lists, no markdown, no symbols that sound weird read aloud.

instead of only pointing with the cursor, you can DRAW on the screen to teach — circle the exact thing you mean, draw an arrow from a mistake to where it should go, underline a term, or write a short correction. use drawing whenever it makes your help more concrete: finding a button, explaining a step, showing a mistake on the user's work.

how to draw: explain in spoken words first, then append shape tags at the very END of your reply, after your spoken text. the screenshot images are labeled with their pixel dimensions — use those dimensions as the coordinate space. the origin (0,0) is the top-left corner of the image, x increases rightward, y increases downward. point at the SPECIFIC element, never a vague region. never read coordinates aloud. only annotate the screen labeled "primary focus" (the one with the cursor) — do NOT emit shape tags for elements on any other screen.

shape tags (use as many as help, only what helps):
- [CIRCLE:x,y,r:label] — circle the exact element. (x,y) is the center, r is the radius in pixels, label is a 1-3 word description.
- [ARROW:x1,y1->x2,y2] — an arrow from (x1,y1) to (x2,y2), e.g. from the mistake to where it should go.
- [UNDERLINE:x,y,w] — underline a term or formula. (x,y) is the left end, w is the width in pixels.
- [LABEL:x,y:text] — write a short correction or hint on the screen at (x,y).
- [RECT:x,y,w,h:label] — frame a rectangular control. (x,y) is the TOP-LEFT corner, w and h are the width and height in pixels. prefer this over a circle for anything box-shaped: a toolbar button, a text field, a menu item, a panel. a circle around a wide button either clips it or swallows its neighbours.
- [HIGHLIGHT:x,y,w,h:label] — dim the whole screen except this rectangle. same coordinates as RECT. use it when the screen is busy and you need the user to find one thing among many, or when you're about to walk them through a specific panel. use at most ONE per reply — two competing highlights defeat the purpose.
- [STEP:x,y,n:label] — a numbered badge at (x,y), where n is the step number starting at 1. use these when your answer has an order: "first here, then here". emit one per step, numbered in the order you describe them.

if nothing on screen is relevant (a purely conceptual question), just speak with no tags.

examples:
- user asks why their answer is wrong: "you applied the power rule but missed the chain rule here — you need to multiply by the derivative of the inside. [CIRCLE:340,210,28:missing step][ARROW:340,210->410,260]"
- user asks where to save: "hit the save button up in the top left of the toolbar. [RECT:32,18,64,26:save]"
- user asks how to export in a busy editor: "everything you need is in the export panel on the right. [HIGHLIGHT:1480,120,400,560:export panel]"
- user asks how to add a chart: "select your data first, then open the insert tab, then pick chart. [STEP:180,300,1:select data][STEP:420,40,2:insert tab][STEP:560,80,3:chart]"
- user asks what a binary tree is (nothing on screen): "it's a structure where each node has at most two children, a left and a right. great for fast lookups."\
"""
"""Annotation-mode system prompt (draw-on-screen teaching). Same
dimension-labeled coordinate-space contract as the cursor [POINT] prompt,
extended from a single point to seven shape tags parsed by
annotations.parse_annotations. Used when config.ANNOTATION_MODE == 'on'.

T3-5 added RECT, HIGHLIGHT and STEP. Each carries explicit guidance on *when* to prefer it,
not just its syntax: a tag list without that guidance produced circles around wide buttons
(clipping them) and no ordering on multi-step answers. HIGHLIGHT is capped at one per reply
in the prompt because two competing dim layers cancel each other out visually."""

_NIMBUS_STRUCTURED_SYSTEM_PROMPT = """\
you're nimbus, a friendly always-on companion that lives in the user's system tray. the user just spoke to you via push-to-talk and you can see their screen(s). your reply will be spoken aloud via text-to-speech, so write the way you'd actually talk. this is an ongoing conversation — you remember everything they've said before.

rules:
- default to one or two sentences. be direct and dense. BUT if the user asks you to explain more, go deeper, or elaborate, then go all out — give a thorough, detailed explanation with no length limit.
- all lowercase, casual, warm. no emojis.
- write for the ear, not the eye. short sentences. no lists, bullet points, markdown, or formatting — just natural speech.
- don't use abbreviations or symbols that sound weird read aloud. write "for example" not "e.g.", spell out small numbers.
- if the user's question relates to what's on their screen, reference specific things you see.
- if the screenshot doesn't seem relevant to their question, just answer the question directly.
- you can help with anything — coding, writing, general knowledge, brainstorming.
- never say "simply" or "just".
- don't read out code verbatim. describe what the code does or what needs to change conversationally.
- focus on giving a thorough, useful explanation. don't end with simple yes/no questions like "want me to explain more?" — those are dead ends.
- instead, when it fits naturally, end by planting a seed — mention something bigger they could try, or a related concept that goes deeper.
- if you receive multiple screen images, the one labeled "primary focus" is where the cursor is — prioritize that one.

describing location:
a separate system draws a pointer on screen for you, so you never need to produce coordinates, tags, function calls, code, or json. your ONLY job is the spoken words.

when the user asks where something is, say where it is in plain conversational language — "it's up in the top right of the toolbar", "it's the third item down in that left sidebar". describe it the way you'd describe it to someone sitting next to you.

never write code blocks, backticks, function names, brackets, or coordinates. every character you produce is read aloud, so any of that would be spoken out loud and sound broken.\
"""
"""T1-2: system prompt for providers with structured geometry.

Deliberately diverges from ``_NIMBUS_SYSTEM_PROMPT`` in exactly one respect: all
``[POINT:x,y:label]`` tag instructions are replaced with an instruction to call the
``point_at`` function. Everything about persona, tone, and length is preserved
verbatim so the voice is identical across providers.

This exists because the live end-to-end test proved the prompts fight each other:
given the tag-based prompt *and* a ``point_at`` tool, the model obeys the prompt and
emits a text tag, so geometry lands back in the speech channel — defeating the entire
point of the structured path. The prompt has to be told which channel to use."""

_NIMBUS_STRUCTURED_ANNOTATION_PROMPT = """\
you're nimbus, a patient tutor that teaches by drawing on the user's screen. the user just spoke to you via push-to-talk and you can see their screen(s). your reply is spoken aloud via text-to-speech, so write the way you'd actually talk — all lowercase, casual, warm, one or two short sentences. write for the ear: no lists, no markdown, no symbols that sound weird read aloud.

a separate system draws boxes and circles on screen for you, so you never need to produce coordinates, tags, function calls, code, or json. your ONLY job is the spoken words that teach.

explain what you're highlighting in plain conversational language — "i've boxed the save button up in the top left", "see the term i've circled in that second line". speak as though the drawing has already appeared, because it has.

never write code blocks, backticks, function names, brackets, or coordinates. every character you produce is read aloud, so any of that would be spoken out loud and sound broken.\
"""
"""T1-2: annotation-mode prompt for structured providers.

The ``CRITICAL`` line is not decoration. The live test produced an EMPTY spoken reply
in annotation mode: handed a drawing tool, the model called it and said nothing, so
the user would have heard silence while a box appeared. Requiring speech first is a
correctness requirement, not a style preference."""

_CODE_FENCE_RE = re.compile(r"```.*?(?:```|$)", re.DOTALL)
"""Fenced code block, terminated or truncated.

Defence in depth for the structured path (T1-2/T1-9). Observed live: a model told
about tools but given none will happily write the call it *would* have made as a
markdown block — ``` ```python\\ndraw_box(ymin=47, ...)``` ``` — which TTS reads aloud
as "backtick backtick backtick python draw box open paren". The prompts now avoid
mentioning tools at all, but prompt compliance is probabilistic and TTS output is
user-facing, so this strip is the guarantee rather than the hope."""

_TOOL_CALL_TEXT_RE = re.compile(
    r"\b(?:point_at|draw_box)\s*\([^)]*\)?", re.IGNORECASE
)
"""A bare tool call written as prose, outside any fence."""

_LATEX_MATH_RE = re.compile(r"\$\$?.+?\$\$?", re.DOTALL)
r"""Inline or display LaTeX math.

Needed once code execution (T1-6b) is enabled: verified live, the model answers a
calculus question with ``$f(x) = x^3 \sin(x)$`` and ``$$f'(x) = u'(x)v(x)$$``. Read
aloud that becomes "dollar f of x equals x caret three backslash sin", which is worse
than useless on a maths tutoring answer — the exact use case the feature exists for."""

_MARKDOWN_NOISE_RE = re.compile(r"(?:^\s{0,3}#{1,6}\s+)|(?:\*\*|__|~~|`)", re.MULTILINE)
"""Markdown emphasis, headings and inline-code ticks.

The system prompt already asks for no markdown, but code execution makes the model
switch into document mode and it starts emitting ``### 1. Analytical Derivation`` and
``**Product Rule**``. Stripping the syntax keeps the words and drops the symbols."""


def strip_non_speech(text: str) -> str:
    """Remove anything that must never be read aloud, then normalise whitespace.

    Applied to structured-path spoken text, where geometry arrives out-of-band and
    therefore *nothing* machine-shaped is legitimate in the speech channel.

    Order matters: fenced blocks go first so their contents cannot be partially
    rescued by the later, narrower patterns. Kept a pure function so the whole table
    of cases is unit-testable without a model.
    """
    cleaned = _CODE_FENCE_RE.sub(" ", text)
    cleaned = _TOOL_CALL_TEXT_RE.sub(" ", cleaned)
    cleaned = _LATEX_MATH_RE.sub(" ", cleaned)
    cleaned = _MARKDOWN_NOISE_RE.sub(" ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


_POINT_TAG_RE = re.compile(
    r"\[\s*POINT\s*:\s*(?:none|(-?\d+)\s*,\s*(-?\d+)"
    r"(?:\s*:\s*(?!screen\s*\d)([^\]:]*?)\s*)?(?:\s*:\s*screen\s*(\d+)\s*)?)\s*\]",
    re.IGNORECASE,
)
"""Regex for Nimbus's ``[POINT:x,y:label(:screenN)?]`` coordinate tag.

T0-3 fixed two silent-failure modes in this pattern:

1. **Signed coordinates.** It was ``(\\d+)``, which cannot match a negative
   number. A model emitting ``[POINT:-3,50:save]`` — which happens when an
   element sits at a screenshot edge — failed the whole match, so the tag was
   never stripped and TTS read the coordinates aloud. Now ``(-?\\d+)``.
   Coordinates are deliberately NOT clamped here: Space C is unclamped by
   contract and ``capture.unscale_model_coords`` owns clamping.
2. **End-of-string anchor.** It ended with ``\\]\\s*$``, so any trailing prose
   after the tag (``"...[POINT:400,300:save] hope that helps!"``) meant no
   match, no strip, and the tag was spoken. The anchor is gone; callers use
   :func:`parse_point_tag`, which takes the LAST match.

Also now case-insensitive with whitespace tolerance, matching the leniency
``annotations.py`` already applies to its shape tags — the system prompt asks
for all-lowercase prose, so the model may well emit ``[point:...]``.

The ``(?!screen\\d)`` negative lookahead is load-bearing: it makes
``[POINT:400,300:screen2]`` parse as screen 2 rather than a label of
"screen2". Do not remove it."""

_MALFORMED_POINT_RE = re.compile(r"\[\s*POINT\s*:[^\]]*\]", re.IGNORECASE)
"""Strips a COMPLETE but unparseable point tag, e.g. ``[POINT:garbage]``.

Applied only AFTER every valid tag has already been removed, so this can never
eat a well-formed tag. Without it, a malformed tag survived into ``spoken_text``
and TTS read it aloud — the exact leak T0-3 closes."""

_UNTERMINATED_POINT_RE = re.compile(r"\[\s*POINT\s*:.*$", re.IGNORECASE | re.DOTALL)
"""Fail-closed strip for a TRUNCATED tag and everything after it.

A response cut off mid-tag (``"look here [POINT:120,40"``) has no closing
bracket, so neither pattern above matches it. Mirrors
``annotations._UNTERMINATED_TAG_RE``, which already applied this exact
protection to shape tags. DOTALL so it eats across newlines to end-of-string."""

_NIMBUS_MAX_TOKENS = 1024
"""Token budget for the vision-streaming call."""

_MEMORY_PREFIX_MARKER = "[context from past sessions"
"""Sentinel that app.py prepends to the user transcript when memory is
injected. Used by AnthropicClient.ask_stream to split the transcript into a
cached memory-prefix block + an uncached current-turn block. Must match
app.py NimbusApp._pipeline_worker's f-string exactly."""

HISTORY_IMAGE_MEDIA_TYPE = "image/jpeg"
"""Media type used for every history image block (T2-4)."""


def history_image_block(jpeg_bytes: bytes) -> dict:
    """Build the canonical history image block (T2-4).

    ``app.py``'s ``_history`` is documented as Anthropic content-block form, so that is the
    single wire format used here. Anthropic consumes it natively; the other providers
    translate it in their own converters. One canonical shape means the translation lives
    in exactly one place per provider instead of the format being invented at each call
    site.
    """
    import base64

    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": HISTORY_IMAGE_MEDIA_TYPE,
            "data": base64.b64encode(jpeg_bytes).decode("ascii"),
        },
    }


def iter_history_blocks(turn: dict):
    """Yield ``(kind, payload)`` for each block in a history turn (T2-4).

    ``kind`` is ``"text"`` or ``"image"``; payload is the string or the raw base64 data.
    Tolerates a plain-string ``content`` (Ollama's history shape) and silently ignores
    malformed blocks, because a corrupt history entry must never break a live request.

    Exists so every provider agrees on what a history turn *contains* before disagreeing
    about how to encode it. Previously each converter re-implemented the walk and all of
    them dropped images by omission -- which is why T2-4 would have been inert without
    this.
    """
    content = turn.get("content", "")
    if isinstance(content, str):
        if content.strip():
            yield ("text", content)
        return
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text = block.get("text", "")
            if text:
                yield ("text", text)
        elif block.get("type") == "image":
            data = (block.get("source") or {}).get("data")
            if data:
                yield ("image", data)


def history_text(turn: dict) -> str:
    """All text in a history turn, joined. Images excluded by construction."""
    return " ".join(p for kind, p in iter_history_blocks(turn) if kind == "text")


_KB_SYSTEM_PREFIX_TEMPLATE = (
    "app knowledge base:\n"
    "you are helping the user with {app_name}. here is reference "
    "documentation that you should treat as authoritative:\n\n"
)
"""Marker prefix prepended to user-uploaded KB content before injection
into the system prompt as a SECOND cache_control block. Caller (app.py
_pipeline_worker → ask_stream's kb_content kwarg) supplies the raw
markdown body; ask_stream formats this prefix in front and adds the
ephemeral cache breakpoint. Per-app cache hit on subsequent turns within
the same app session; cache miss on app switch (acceptable since each
KB read is the dominant cost anyway). Empty kb_content means no second
block — Nimbus proceeds with vision + memory only (the 'Nimbus already
knows that software' path)."""


# --- PointParseResult ---------------------------------------------------------

@dataclass
class PointParseResult:
    """Result of parsing the [POINT:...] tag from Nimbus's response text."""
    spoken_text: str
    coordinate: tuple[int, int] | None
    element_label: str | None
    screen_number: int | None
    malformed_tags: tuple[str, ...] = ()
    """Raw text of any point tag that was stripped but could not be parsed.

    T0-3: malformed tags are now removed from ``spoken_text`` so coordinates can
    never be spoken aloud. Recording the raw text here keeps them diagnosable —
    ``app.py::_pipeline_worker`` logs this to the ``DebugSession``, so a broken
    prompt or a model drifting off-format is still visible in the interaction
    log instead of failing silently.

    Defaults to empty, so existing keyword-based constructions are unaffected."""


# --- Pure functions -----------------------------------------------------------

def parse_point_tag(text: str) -> PointParseResult:
    """Extract the coordinate from a ``[POINT:x,y:label]`` tag and strip it.

    Returns ``PointParseResult`` with ``coordinate=None`` on ``[POINT:none]``, a
    malformed tag, or no tag at all.

    **Hard invariant (T0-3): ``spoken_text`` never contains a point tag in any
    form or casing** — valid, malformed, or truncated. TTS reads ``spoken_text``
    verbatim, so a leak here means the user hears "open bracket POINT colon".
    Three patterns enforce it in order: valid tags, complete-but-unparseable
    tags, then a fail-closed strip of any truncated tag and everything after it.

    When several valid tags are present the LAST one wins, matching the system
    prompt's instruction to append the tag after the spoken answer.

    Coordinates are returned unclamped. Space C is unclamped by contract;
    ``capture.unscale_model_coords`` performs the clamping.
    """
    matches = list(_POINT_TAG_RE.finditer(text))
    match = matches[-1] if matches else None

    # Strip in order. Valid tags go first so _MALFORMED_POINT_RE cannot consume
    # a well-formed tag, and the unterminated sweep runs last on what remains.
    spoken = _POINT_TAG_RE.sub("", text)
    malformed = tuple(m.group(0) for m in _MALFORMED_POINT_RE.finditer(spoken))
    spoken = _MALFORMED_POINT_RE.sub("", spoken)
    truncated = _UNTERMINATED_POINT_RE.search(spoken)
    if truncated is not None:
        malformed += (truncated.group(0),)
        spoken = _UNTERMINATED_POINT_RE.sub("", spoken)
    spoken = spoken.strip()

    if match is None or match.group(1) is None:
        # No tag, or an explicit [POINT:none].
        return PointParseResult(
            spoken_text=spoken,
            coordinate=None,
            element_label=None,
            screen_number=None,
            malformed_tags=malformed,
        )

    # The label group tolerates surrounding whitespace (the model may emit
    # "[POINT:1,2: save button ]"), so normalise it the same way
    # annotations.parse_annotations normalises its own labels. An empty label
    # collapses to None, matching the no-label form "[POINT:x,y]".
    label = (match.group(3) or "").strip() or None

    return PointParseResult(
        spoken_text=spoken,
        coordinate=(int(match.group(1)), int(match.group(2))),
        element_label=label,
        screen_number=int(match.group(4)) if match.group(4) else None,
        malformed_tags=malformed,
    )


def image_to_base64_jpeg(img: Image.Image, quality: int = 85) -> str:
    """Encode a PIL image to a base64-ASCII JPEG string for the Nimbus API."""
    buf = BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode("ascii")


# --- T1-2: structured spatial output -----------------------------------------

NORMALISED_SCALE = 1000
"""Gemini returns spatial coordinates normalised to 0..1000, not pixels.

Verified live against a real 1920x1080 screenshot: ``point`` comes back as
``[y, x]`` and ``box_2d`` as ``[ymin, xmin, ymax, xmax]`` — **y first in both**.
Getting that order backwards yields plausible-looking but wrong coordinates,
which is why :func:`normalised_point_to_space_c` takes y and x as separate named
arguments rather than an ordered pair."""


def normalised_point_to_space_c(
    norm_y: int | float,
    norm_x: int | float,
    target_w: int,
    target_h: int,
) -> tuple[int, int]:
    """Map a normalised 0..1000 point to Space C pixels, returned as ``(x, y)``.

    Space C is the model's declared screenshot resolution (see the coordinate-space
    contract in ``capture.py``). Introducing no new space is deliberate: the result
    feeds straight into ``capture.unscale_model_coords``, unchanged.

    Values are clamped to the Space C boundary here because normalised input is
    dimensionless — an out-of-range 0..1000 value is a model error with no valid
    pixel interpretation, unlike a raw Space C pixel which
    ``unscale_model_coords`` legitimately clamps later.

    Args are **named y-first-then-x deliberately mirroring the wire format**, but
    the return is ``(x, y)`` to match every other coordinate tuple in Nimbus.
    """
    if target_w <= 0 or target_h <= 0:
        raise ValueError(f"target dims must be positive, got ({target_w}, {target_h})")
    x = round(float(norm_x) / NORMALISED_SCALE * target_w)
    y = round(float(norm_y) / NORMALISED_SCALE * target_h)
    return (max(0, min(x, target_w - 1)), max(0, min(y, target_h - 1)))


def normalised_box_to_space_c(
    box: "list | tuple",
    target_w: int,
    target_h: int,
):
    """Map a normalised ``[ymin, xmin, ymax, xmax]`` box to an ``annotations.Rect``.

    Returns ``None`` for a malformed or degenerate box rather than raising — a bad
    box from the model must never break the pipeline, matching how
    ``parse_annotations`` drops malformed shape tags silently.

    Normalises inverted edges (``ymin > ymax``) by sorting, so a model that emits
    corners in the wrong order still produces a usable rectangle.
    """
    from annotations import Rect

    if box is None or len(box) != 4:
        return None
    try:
        ymin, xmin, ymax, xmax = (float(v) for v in box)
    except (TypeError, ValueError):
        return None
    ymin, ymax = sorted((ymin, ymax))
    xmin, xmax = sorted((xmin, xmax))
    x0, y0 = normalised_point_to_space_c(ymin, xmin, target_w, target_h)
    x1, y1 = normalised_point_to_space_c(ymax, xmax, target_w, target_h)
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return None
    return Rect(x0, y0, w, h)


# --- T1-7: query classification for thinking-budget tiering -------------------

_DIAGNOSTIC_QUERY_WORDS = (
    "why", "wrong", "incorrect", "mistake", "error", "broken", "failing",
    "doesn't work", "does not work", "not working", "bug", "debug",
    "explain why", "what's wrong", "whats wrong",
)
"""Phrases indicating genuine multi-step reasoning rather than lookup or recall."""


def classify_query(transcript: str) -> str:
    """Classify a transcript as ``"locate"``, ``"diagnostic"``, or ``"conceptual"``.

    Drives thinking-budget tiering (T1-7). Reasoning tokens help a *diagnostic*
    question and are pure latency cost on a *locate* question, which is both the
    most frequent case and the most latency-sensitive one. Verified live: dropping
    the thinking budget to zero moved time-to-first-token from 3.97s to 1.18s.

    Ordering is deliberate. Diagnostic is tested FIRST because "why is this button
    greyed out" contains a directional word yet needs real reasoning; classifying
    it as a cheap lookup would degrade the answer. A pure function so the whole
    table is unit-testable with no network.
    """
    if not transcript or not transcript.strip():
        return "conceptual"
    lowered = transcript.lower()
    if any(word in lowered for word in _DIAGNOSTIC_QUERY_WORDS):
        return "diagnostic"
    if any(word in lowered for word in _DIRECTIONAL_QUERY_WORDS):
        return "locate"
    return "conceptual"


_DIRECTIONAL_QUERY_WORDS = (
    "where", "click", "show me", "find", "point", "open", "select", "press",
    "navigate", "locate", "tap", "look at", "go to", "which button",
    # Drawing verbs: in teaching mode the user names the shape rather than asking
    # "where", so these are directional in intent even though they never say so.
    "circle", "highlight", "underline", "draw", "box",
)
"""Directional phrases. Mirrors ``app._DIRECTIONAL_QUERY_WORDS`` — kept here so
``classify_query`` stays a pure ``ai`` function with no import cycle back to
``app``. ``test_ai.py`` asserts the two lists stay in sync."""


def _get(obj, key, default=None):
    """Dual-access helper: works on both dict-shaped test mocks and
    anthropic SDK objects (via attribute access)."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def parse_response_text(response) -> str:
    """Concatenate all text-type content blocks into a single string.

    Dual-access compatible (dict mocks or SDK objects). Used by the batch
    ask() wrapper to extract the full text from a non-streaming response.
    """
    content = _get(response, "content", []) or []
    texts: list[str] = []
    for block in content:
        if _get(block, "type") != "text":
            continue
        text = _get(block, "text", "") or ""
        if text:
            texts.append(text)
    return " ".join(texts).strip()


# --- AIClient abstract base ---------------------------------------------------

class AIClient(ABC):
    """Abstract base for vision+LLM providers.

    AnthropicClient (vision-tag streaming).
    OpenRouterClient, GeminiClient, etc. as subclass drops.
    """

    @abstractmethod
    def ask(
        self,
        image: Image.Image,
        transcript: str,
        history: list[dict],
        declared_w: int,
        declared_h: int,
    ) -> dict:
        """Return {"text": str, "points": [{"x":int,"y":int,"label":str}]}.

        Coordinates are in Nimbus's declared-resolution space (Space C),
        unclamped. Caller uses capture.unscale_model_coords() to map to
        physical pixels (Space A).
        """
        ...

    # --- T1-2: optional structured-geometry capability -----------------------
    #
    # Concrete default so EVERY existing provider keeps working untouched. A
    # provider opts in by overriding; nothing is required of OllamaClient,
    # AnthropicClient, GeminiClient, or any future subclass.

    def supports_structured_geometry(self) -> bool:
        """Whether this provider returns geometry on a channel separate from prose.

        ``False`` means the caller must use the ``[POINT:...]`` / shape-tag text
        parsing path. ``True`` means ``final_result()`` carries geometry that never
        passed through the spoken text at all, so the tag-safety machinery
        (``_POINT_TAG_RE``, the ``"[" in sentence_buffer`` streaming guard) is
        unnecessary for this provider.

        Local models cannot produce reliable structured output, which is precisely
        why ``locator.py``'s grid fallback exists — so this stays ``False`` for
        ``OllamaClient``.
        """
        return False

    def supports_thinking_budget(self) -> bool:
        """Whether this provider accepts a per-request reasoning budget (T1-7)."""
        return False


# --- Anthropic slug adaptation (T0-1) -----------------------------------------

_ANTHROPIC_DASH_VERSION_RE = re.compile(r"-(\d+)-(\d+)$")
"""Matches the dash-versioned tail of a native Anthropic id (``...-4-6``).

The two endpoints Nimbus can reach disagree on version punctuation: native
Anthropic uses dashes (``claude-sonnet-4-6``) while OpenRouter uses a dot
(``anthropic/claude-sonnet-4.6``). Nimbus stores the native form as canonical
and converts on the OpenRouter path."""


def _anthropic_model_for_endpoint(model_id: str, base_url: str | None) -> str:
    """Adapt an Anthropic model id to the endpoint that will receive it (T0-1).

    Mirrors the dual-routing ``GeminiClient.__init__`` already performs, and
    additionally handles the dash-vs-dot version difference between the two
    endpoints.

    ``anthropic/claude-sonnet-4-6`` becomes:
      * ``claude-sonnet-4-6``               for native ``api.anthropic.com``
      * ``anthropic/claude-sonnet-4.6``     for OpenRouter

    Pure function so both conversions are unit-testable without constructing an
    SDK client or making a network call.
    """
    via_openrouter = bool(base_url) and "openrouter.ai" in base_url
    if via_openrouter:
        # Keep the namespace, switch the version separator to a dot.
        return _ANTHROPIC_DASH_VERSION_RE.sub(r"-\1.\2", model_id)
    # Native endpoint: strip the routing namespace, keep dashes.
    return model_id.split("/", 1)[1] if "/" in model_id else model_id


# --- Concrete Anthropic implementation ----------------------------------------

class AnthropicClient(AIClient):
    """Anthropic client using plain vision streaming + [POINT:x,y:label].

    Uses a single streaming vision call that returns natural-language text
    plus a coordinate tag — not a computer-use agent loop.

    T0-1: this class previously stored ``model_id`` verbatim, unlike
    ``GeminiClient`` which adapts the slug to its endpoint. That asymmetry broke
    BOTH key types, because the two endpoints disagree on slug format:

    * native ``api.anthropic.com`` wants the BARE, dash-versioned id
      (``claude-sonnet-4-6``)
    * OpenRouter wants the NAMESPACED, dot-versioned slug
      (``anthropic/claude-sonnet-4.6``)

    ``app.py`` supplies the canonical native form prefixed for factory routing
    (``anthropic/claude-sonnet-4-6``), and ``__init__`` now converts it to
    whichever form the selected endpoint expects.
    """

    def __init__(
        self,
        api_key: str,
        model_id: str,
        base_url: str | None = None,
    ) -> None:
        kwargs: dict = {"api_key": api_key, "timeout": 60.0}
        if base_url is not None:
            kwargs["base_url"] = base_url
        self.client = Anthropic(**kwargs)
        self.model_id = _anthropic_model_for_endpoint(model_id, base_url)

    def ask_stream(
        self,
        images: list[tuple[Image.Image, str]],
        transcript: str,
        history: list[dict],
        system_prompt: str = _NIMBUS_SYSTEM_PROMPT,
        max_tokens: int = _NIMBUS_MAX_TOKENS,
        kb_content: str = "",
        kb_app_name: str = "",
    ):
        """Open a streaming Nimbus call, return a context manager.

        Args:
            images: list of (PIL Image, label string) tuples — one per screen.
                Sorted cursor-screen-first by capture_all_screens(). Each
                becomes an image content block + a text label block in the
                user message. Passed as (image, label) pairs.
            transcript: user's voice question (raw STT output).
            history: prior turns in Anthropic SDK message format.
            system_prompt: persona + pointing instructions.
            max_tokens: token budget (1024 default).
            kb_content: optional curated KB markdown body (from
                kb.recall). If non-empty, injected as a SECOND
                cache_control system block alongside the persona block.
                Empty (default) → only the persona block is sent.
            kb_app_name: sanitized .exe basename used to format the KB
                injection marker (e.g. "myapp.exe" → display "myapp").
                Ignored when kb_content is empty.

        Usage:
            with client.ask_stream(images, transcript, history) as stream:
                for delta in stream.text_deltas():
                    # progressive text for sentence-level TTS chunking
                    pass
                result = stream.final_result()
                # result.spoken_text, result.coordinate, etc.
        """
        content_blocks: list[dict] = []
        for img, label in images:
            base64_jpeg = image_to_base64_jpeg(img)
            content_blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64_jpeg,
                },
            })
            content_blocks.append({"type": "text", "text": label})

        # Split the user transcript into a cached memory-prefix block + an
        # uncached current-transcript block when the memory marker is present.
        # app.py (pipeline worker) prepends memory context as:
        #     "[context from past sessions ...]\n<memory>\n\n<actual transcript>"
        # Caching the prefix saves ~50-100ms TTFT after the first hit (5-min
        # TTL). NEVER cache the current transcript — per-turn content is what
        # makes the full-context-caching latency paradox bite (arxiv 2601.06007
        # "Don't Break the Cache" — only stable prefixes help).
        if _MEMORY_PREFIX_MARKER in transcript:
            parts = transcript.split("\n\n", 1)
            if len(parts) == 2:
                memory_text, actual_transcript = parts
                content_blocks.append({
                    "type": "text",
                    "text": memory_text + "\n\n",
                    "cache_control": {"type": "ephemeral"},
                })
                content_blocks.append({"type": "text", "text": actual_transcript})
            else:
                content_blocks.append({"type": "text", "text": transcript})
        else:
            content_blocks.append({"type": "text", "text": transcript})

        new_user_turn = {"role": "user", "content": content_blocks}

        # Cache the system prompt (largest stable text block, ~1500 chars).
        # OpenRouter passes Anthropic-native cache_control through for
        # anthropic/* routes per openrouter.ai/docs/guides/best-practices/prompt-caching.
        system_blocks = [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        # Optional second cache_control block for user-uploaded curated KB
        # (kb.recall result). Per-app cache: hit within same app session,
        # miss on app switch. Anthropic's 4-block limit accommodates this
        # plus the user-message memory prefix block.
        if kb_content:
            display_name = kb_app_name.removesuffix(".exe") or "this software"
            kb_text = (
                _KB_SYSTEM_PREFIX_TEMPLATE.format(app_name=display_name)
                + kb_content
            )
            system_blocks.append({
                "type": "text",
                "text": kb_text,
                "cache_control": {"type": "ephemeral"},
            })

        sdk_stream_mgr = self.client.messages.stream(
            model=self.model_id,
            max_tokens=max_tokens,
            system=system_blocks,
            messages=[*history, new_user_turn],
        )

        return _StreamingResponse(sdk_stream_mgr)

    def ask(
        self,
        image: Image.Image,
        transcript: str,
        history: list[dict],
        declared_w: int,
        declared_h: int,
    ) -> dict:
        """Batch wrapper: consumes the full stream, returns parsed dict.

        Wraps a single image into the list format ask_stream() expects.
        Backwards-compatible with the __main__ gate and test shapes.
        """
        label = f"primary focus (image dimensions: {declared_w}x{declared_h} pixels)"
        with self.ask_stream(
            [(image, label)], transcript, history
        ) as stream:
            for _ in stream.text_deltas():
                pass
            result = stream.final_result()

        points = []
        if result.coordinate:
            x, y = result.coordinate
            points.append({"x": x, "y": y, "label": result.element_label or ""})

        return {"text": result.spoken_text, "points": points}


class _StreamingResponse:
    """Wraps the SDK's MessageStreamManager for Nimbus's streaming pattern."""

    def __init__(self, sdk_stream_mgr):
        self._sdk_mgr = sdk_stream_mgr
        self._sdk_stream = None
        self._accumulated = ""
        self._deltas_exhausted = False

    def __enter__(self):
        self._sdk_stream = self._sdk_mgr.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self._sdk_mgr.__exit__(exc_type, exc_val, exc_tb)

    def text_deltas(self) -> Iterator[str]:
        """Yield progressive text deltas for sentence-level TTS chunking."""
        for delta in self._sdk_stream.text_stream:
            self._accumulated += delta
            yield delta
        self._deltas_exhausted = True

    def final_result(self) -> PointParseResult:
        """Parse the accumulated text for a [POINT:x,y:label] tag.

        If text_deltas() was fully exhausted, uses the accumulated text.
        Otherwise falls back to get_final_text() which blocks until the
        stream completes.
        """
        if not self._deltas_exhausted:
            self._accumulated = self._sdk_stream.get_final_text()
        return parse_point_tag(self._accumulated)


# --- Gemini (via OpenRouter OpenAI-compat endpoint) --------------------------

class GeminiClient(AIClient):
    """Gemini 3 Flash Preview (or any OpenRouter google/* model) via the
    OpenAI Python SDK pointed at OpenRouter's OpenAI-compat endpoint.

    Response parsing (parse_point_tag) is identical — both Nimbus and Gemini
    emit [POINT:x,y:label] per the verbatim Nimbus system prompt. The only
    differences vs AnthropicClient are request shape (OpenAI chat.completions
    format instead of Anthropic messages format) and image block format
    (image_url with data URL instead of base64 source block).

    History assumption: we convert Anthropic content-block format to plain
    strings by concatenating text blocks. Non-text blocks in history are
    dropped. history only contains text blocks so this is safe; if
    adds image-bearing history, revisit this.
    """

    # OpenRouter accepts the classic `max_tokens` for every model + normalizes
    # it. A provider's NATIVE endpoint may need a different param (OpenAI's
    # gpt-5-series requires `max_completion_tokens`), so subclasses override
    # this; it is used only on the native (non-OpenRouter) path.
    _native_token_param: str = "max_tokens"

    def __init__(
        self,
        api_key: str,
        model_id: str,
        base_url: str = "https://openrouter.ai/api/v1",
    ) -> None:
        # OpenRouter wants the NAMESPACED slug (google/..., openai/...); a
        # provider's native OpenAI-compatible endpoint wants the BARE name, so
        # strip the prefix. Detect which by the base_url.
        via_openrouter = bool(base_url) and "openrouter.ai" in base_url
        if via_openrouter:
            self.model_id = model_id
        else:
            self.model_id = model_id.split("/", 1)[1] if "/" in model_id else model_id
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=60.0)
        self._max_tokens_param = (
            "max_tokens" if via_openrouter else self._native_token_param
        )

    def ask_stream(
        self,
        images: list[tuple[Image.Image, str]],
        transcript: str,
        history: list[dict],
        system_prompt: str = _NIMBUS_SYSTEM_PROMPT,
        max_tokens: int = _NIMBUS_MAX_TOKENS,
        kb_content: str = "",
        kb_app_name: str = "",
    ):
        """Open a streaming Gemini call. Returns a context manager with the
        same interface as AnthropicClient.ask_stream().

        Builds OpenAI-shaped messages:
            - System prompt goes as messages[0] role=system. If
              ``kb_content`` is non-empty, the KB block is concatenated
              onto the system prompt (Gemini via OpenAI-compat doesn't
              support multiple system blocks or cache_control breakpoints,
              so caching is best-effort via OpenRouter's prompt-caching
              auto-detection).
            - History is converted from Anthropic content-block format to
              OpenAI plain-string content (text blocks are concatenated)
            - Current user turn gets image_url + text blocks

        Usage is identical to AnthropicClient:
            with client.ask_stream(images, transcript, history) as stream:
                for delta in stream.text_deltas():
                    ...
                result = stream.final_result()
        """
        user_content: list[dict] = []
        for img, label in images:
            base64_jpeg = image_to_base64_jpeg(img)
            user_content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{base64_jpeg}",
                },
            })
            user_content.append({"type": "text", "text": label})
        user_content.append({"type": "text", "text": transcript})

        # Concat KB into system prompt for Gemini (no native multi-block
        # support via OpenAI-compat endpoint).
        full_system = system_prompt
        if kb_content:
            display_name = kb_app_name.removesuffix(".exe") or "this software"
            full_system = (
                system_prompt
                + "\n\n"
                + _KB_SYSTEM_PREFIX_TEMPLATE.format(app_name=display_name)
                + kb_content
            )

        openai_messages: list[dict] = [
            {"role": "system", "content": full_system}
        ]
        for turn in history:
            # T2-4: carry history screenshots through as image_url parts rather than
            # dropping them. When no images are present this produces a plain string
            # content, byte-identical to the previous behaviour.
            text_parts: list[str] = []
            image_data: list[str] = []
            for kind, payload in iter_history_blocks(turn):
                (text_parts if kind == "text" else image_data).append(payload)
            # Skip turns with no text content (e.g., image-only turns from
            # , or malformed turns) — OpenRouter rejects empty content.
            if not any(p.strip() for p in text_parts):
                continue
            joined = " ".join(text_parts)
            if image_data:
                content_parts: list[dict] = [{"type": "text", "text": joined}]
                for data in image_data:
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{HISTORY_IMAGE_MEDIA_TYPE};base64,{data}"
                        },
                    })
                openai_messages.append({
                    "role": turn["role"], "content": content_parts,
                })
            else:
                openai_messages.append({"role": turn["role"], "content": joined})
        openai_messages.append({"role": "user", "content": user_content})

        try:
            sdk_iterator = self.client.chat.completions.create(
                model=self.model_id,
                messages=openai_messages,
                stream=True,
                **{self._max_tokens_param: max_tokens},
            )
        except Exception as exc:
            raise RuntimeError(
                f"Gemini request failed (model={self.model_id!r}). "
                "Diagnostic checklist:\n"
                "  1. Is the OpenRouter key (ANTHROPIC_API_KEY in .env) valid + funded?\n"
                "  2. Is the model available on your account? Preview models like "
                "'google/gemini-3-flash-preview' require opt-in at "
                "https://openrouter.ai/settings/privacy. Fallback: set "
                "MODEL_ID=google/gemini-2.5-flash in .env.\n"
                "  3. Is your internet connection up?\n"
                f"Underlying error: {type(exc).__name__}: {exc}"
            ) from exc
        return _GeminiStreamingResponse(sdk_iterator)

    def ask(
        self,
        image: Image.Image,
        transcript: str,
        history: list[dict],
        declared_w: int,
        declared_h: int,
    ) -> dict:
        """Batch wrapper for parity with AnthropicClient.ask()."""
        label = f"primary focus (image dimensions: {declared_w}x{declared_h} pixels)"
        with self.ask_stream([(image, label)], transcript, history) as stream:
            for _ in stream.text_deltas():
                pass
            result = stream.final_result()
        points = []
        if result.coordinate:
            x, y = result.coordinate
            points.append({"x": x, "y": y, "label": result.element_label or ""})
        return {"text": result.spoken_text, "points": points}


class _GeminiStreamingResponse:
    """Wraps OpenAI SDK streaming iterator to match AnthropicClient's
    _StreamingResponse public interface (context manager + text_deltas() +
    final_result()). Consumers of ask_stream() don't need to know which
    client is behind it.
    """

    def __init__(self, sdk_iterator):
        self._sdk_iterator = sdk_iterator
        self._accumulated = ""
        self._deltas_exhausted = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self._sdk_iterator.close()
        except Exception:
            pass
        return False  # don't swallow exceptions

    def text_deltas(self) -> Iterator[str]:
        for chunk in self._sdk_iterator:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                self._accumulated += delta
                yield delta
        self._deltas_exhausted = True

    def final_result(self) -> PointParseResult:
        if not self._deltas_exhausted:
            for _ in self.text_deltas():
                pass
        return parse_point_tag(self._accumulated)


# --- OpenAIVisionClient (GPT-4o via OpenAI native API) --------------

class OpenAIVisionClient(GeminiClient):
    """OpenAI vision model (default gpt-5.4) via the OpenAI Python SDK pointed
    at OpenAI's native endpoint (api.openai.com).

    Reuses GeminiClient's ``ask_stream`` / ``ask`` / ``_GeminiStreamingResponse``
    verbatim — both are OpenAI-chat-completions-shaped, the only difference is
    the endpoint + key. Emits ``[POINT:x,y:label]`` per the same Nimbus system
    prompt.

    Accuracy note: the default model is **gpt-5.4**, which is pixel-accurate
    at grounding (85.4% on ScreenSpot-Pro) and returns a precise ``[POINT]``
    tag directly, so app.py's grid-locator auto-skips (it only fires when no
    tag is present). The grid-locator is a fallback for weaker models (e.g.
    gpt-4o or Ollama's local vision models), still supported via env override.

    Routed via the ``openai/`` MODEL_ID prefix in create_ai_client(). The
    GPT-Realtime speech-to-speech path is separate (realtime.py); its accurate
    pixel pass also goes through this client (gpt-5.4) — see app.py.
    """

    # gpt-5-series rejects `max_tokens` on OpenAI's NATIVE endpoint and requires
    # `max_completion_tokens` (400 otherwise; fix — gpt-5.4 was 400ing
    # every call → no audio). Via OpenRouter, max_tokens works (the base class
    # handles that). Used only on the native path.
    _native_token_param: str = "max_completion_tokens"

    def __init__(
        self,
        api_key: str,
        model_id: str,
        base_url: str | None = None,
    ) -> None:
        # Same dual routing as the GeminiClient base: OpenRouter keeps the
        # namespaced openai/ slug; a direct OpenAI key (base_url=None →
        # api.openai.com) strips to the bare model name. create_ai_client picks
        # base_url by key prefix via _provider_base_url.
        super().__init__(api_key=api_key, model_id=model_id, base_url=base_url)


# --- OllamaClient (local LLM support) ---------------------------------

class OllamaClient(AIClient):
    """Local LLM via Ollama server (https://ollama.com).

    Speaks Ollama's /api/chat streaming protocol over HTTP. Accepts multi-image
    screenshots passed as base64-encoded JPEGs inline in the user message's
    ``images`` field (Ollama-specific extension to the OpenAI-style messages
    array).

    **Pixel-pointing caveat:** local vision models (llama3.2-vision, qwen2.5-vl,
    llava) generally cannot return precise pixel coordinates via free-text
    [POINT:x,y:label] tags. app.py wires a grid-locator fallback (locator.py)
    that runs AFTER the streamed response completes: if no [POINT:x,y] tag was
    emitted AND the query was directional, the locator pass derives coordinates
    via two-stage grid annotation.

    Public interface matches AnthropicClient + GeminiClient: ``ask_stream(...)``
    returns a context manager with .text_deltas() generator + .final_result()
    returning PointParseResult. app.py's _pipeline_worker doesn't need to know
    which client is behind it.
    """

    def __init__(self, host: str, model_id: str) -> None:
        # Strip optional 'ollama/' prefix — Ollama API wants the bare model name.
        if model_id.lower().startswith("ollama/"):
            self.model_id = model_id[len("ollama/"):]
        else:
            self.model_id = model_id
        self.host = host.rstrip("/")

    def ask_stream(
        self,
        images: list[tuple[Image.Image, str]],
        transcript: str,
        history: list[dict],
        system_prompt: str = _NIMBUS_SYSTEM_PROMPT,
        max_tokens: int = _NIMBUS_MAX_TOKENS,
        kb_content: str = "",
        kb_app_name: str = "",
    ):
        """Open a streaming Ollama /api/chat call. Returns a context manager
        with the same interface as AnthropicClient.ask_stream().

        Builds Ollama-shaped messages:
            - System prompt as messages[0] role=system. KB block concat'd onto
              system prompt (Ollama doesn't support multi-block cache_control).
            - History flattened from Anthropic content-block format to plain
              strings (Ollama uses OpenAI-style string content).
            - Current user turn: {role: user, content: transcript, images: [b64...]}
              with base64-encoded JPEG screenshots as a list of strings (Ollama's
              extension to the standard message shape).

        Usage:
            with client.ask_stream(images, transcript, history) as stream:
                for delta in stream.text_deltas():
                    ...
                result = stream.final_result()
        """
        # Concat KB into system prompt (no multi-block support on Ollama).
        full_system = system_prompt
        if kb_content:
            display_name = kb_app_name.removesuffix(".exe") or "this software"
            full_system = (
                system_prompt
                + "\n\n"
                + _KB_SYSTEM_PREFIX_TEMPLATE.format(app_name=display_name)
                + kb_content
            )

        # Build OpenAI-style messages array.
        ollama_messages: list[dict] = [
            {"role": "system", "content": full_system}
        ]

        # Flatten history (Anthropic content-blocks → plain strings).
        for turn in history:
            # T2-4: Ollama takes images as a sibling `images` array of bare base64
            # strings, not as inline content parts -- a third distinct shape.
            text_parts: list[str] = []
            image_data: list[str] = []
            for kind, payload in iter_history_blocks(turn):
                (text_parts if kind == "text" else image_data).append(payload)
            # Skip empty turns (image-only or malformed) — Ollama rejects empty content.
            if not any(p.strip() for p in text_parts):
                continue
            message = {
                "role": turn["role"],
                "content": " ".join(text_parts),
            }
            if image_data:
                message["images"] = image_data
            ollama_messages.append(message)

        # Current user turn — transcript as content, screenshots as images list
        user_turn: dict = {"role": "user", "content": transcript}
        if images:
            user_turn["images"] = [
                image_to_base64_jpeg(img) for img, _label in images
            ]
        ollama_messages.append(user_turn)

        payload = {
            "model": self.model_id,
            "messages": ollama_messages,
            "stream": True,
            "options": {
                "num_predict": max_tokens,
                "temperature": 0.7,
            },
        }

        return _OllamaStreamingResponse(
            host=self.host,
            payload=payload,
            model_for_errors=self.model_id,
        )

    def ask(
        self,
        image: Image.Image,
        transcript: str,
        history: list[dict],
        declared_w: int,
        declared_h: int,
    ) -> dict:
        """Batch wrapper for parity with AnthropicClient.ask() / GeminiClient.ask()."""
        label = f"primary focus (image dimensions: {declared_w}x{declared_h} pixels)"
        with self.ask_stream([(image, label)], transcript, history) as stream:
            for _ in stream.text_deltas():
                pass
            result = stream.final_result()
        points = []
        if result.coordinate:
            x, y = result.coordinate
            points.append({"x": x, "y": y, "label": result.element_label or ""})
        return {"text": result.spoken_text, "points": points}


class _OllamaStreamingResponse:
    """Wraps httpx streaming response from Ollama /api/chat to match
    AnthropicClient + GeminiClient's _StreamingResponse public interface
    (context manager + text_deltas() + final_result()). Consumers of
    ask_stream() don't need to know which client is behind it.

    Each instance opens its own httpx.Client + stream context on __enter__
    (so the network call doesn't fire until the caller actually wants to
    stream). __exit__ closes both.
    """

    def __init__(self, host: str, payload: dict, model_for_errors: str) -> None:
        self._host = host
        self._payload = payload
        self._model_for_errors = model_for_errors
        self._httpx_client = None
        self._stream_cm = None
        self._response = None
        self._accumulated = ""
        self._deltas_exhausted = False

    def __enter__(self):
        # Open the httpx client first. If any subsequent open-step raises,
        # we MUST close this client — the caller's `with` block never gets
        # entered, so __exit__ won't fire. Without the try/except below the
        # client would leak its connection pool every time Ollama is
        # unreachable (DNS failure, ECONNREFUSED, server crash, etc).
        # Caught by code review review.
        self._httpx_client = httpx.Client(timeout=120.0)
        self._httpx_client.__enter__()
        try:
            self._stream_cm = self._httpx_client.stream(
                "POST",
                f"{self._host}/api/chat",
                json=self._payload,
            )
            self._response = self._stream_cm.__enter__()

            # Friendly error for the most common Ollama mistake: model not pulled
            if self._response.status_code == 404:
                # Close before raising to release the connection
                try:
                    self._stream_cm.__exit__(None, None, None)
                finally:
                    self._httpx_client.__exit__(None, None, None)
                    self._httpx_client = None
                    self._stream_cm = None
                raise RuntimeError(
                    f"Ollama doesn't have '{self._model_for_errors}' installed. "
                    f"Run: ollama pull {self._model_for_errors}"
                )
            self._response.raise_for_status()
        except BaseException:
            # Any failure between httpx_client open and successful
            # raise_for_status (network error, 5xx, etc.): close the
            # client we just opened, null the slot so __exit__ is a no-op,
            # re-raise so the caller sees the original exception.
            if self._stream_cm is not None:
                try:
                    self._stream_cm.__exit__(None, None, None)
                except Exception:
                    pass
                self._stream_cm = None
            try:
                self._httpx_client.__exit__(None, None, None)
            except Exception:
                pass
            self._httpx_client = None
            raise
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if self._stream_cm is not None:
                self._stream_cm.__exit__(exc_type, exc_val, exc_tb)
        finally:
            if self._httpx_client is not None:
                self._httpx_client.__exit__(exc_type, exc_val, exc_tb)
        return False  # don't swallow exceptions

    def text_deltas(self) -> Iterator[str]:
        """Yield progressive text chunks from Ollama's JSON-per-line stream.

        Each line is a JSON object like {"message": {"content": "..."}, "done": false}.
        We accumulate the .message.content fields and yield non-empty chunks.

        On socket failure mid-stream (Ollama crash, network drop), httpx raises
        ReadError / RemoteProtocolError. We MUST mark deltas_exhausted=True in
        a finally block so that a subsequent final_result() call doesn't re-
        enter this iterator and re-raise the same exception, instead returning
        whatever was accumulated before the failure (graceful degradation —
        the user gets a partial response with parse_point_tag falling back to
        no coordinate). Caught by code review review.
        """
        import json as _json
        try:
            for line in self._response.iter_lines():
                if not line:
                    continue
                # iter_lines() in httpx returns str by default in newer
                # versions, bytes in older. Handle both.
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="replace")
                try:
                    data = _json.loads(line)
                except _json.JSONDecodeError:
                    continue
                chunk = data.get("message", {}).get("content", "")
                if chunk:
                    self._accumulated += chunk
                    yield chunk
                if data.get("done"):
                    break
        finally:
            # ALWAYS set this — even on exception. Prevents final_result()
            # from re-entering this generator and re-raising the same error.
            self._deltas_exhausted = True

    def final_result(self) -> PointParseResult:
        """Parse the accumulated text for a [POINT:x,y:label] tag.

        Safe to call even if text_deltas() raised mid-stream: we just parse
        whatever was accumulated before the failure (may be empty string,
        in which case parse_point_tag returns PointParseResult with no
        coordinate and empty spoken_text). The pipeline worker handles
        empty results gracefully.
        """
        if not self._deltas_exhausted:
            # text_deltas() guarantees _deltas_exhausted=True even on failure,
            # so this re-entry can only happen if the caller never consumed
            # any deltas. Consume them now (best-effort — if iter_lines
            # raises, our own try/finally above flips the flag so we don't
            # loop forever).
            try:
                for _ in self.text_deltas():
                    pass
            except Exception:
                # Already accumulated whatever streamed in; parse_point_tag
                # below will return a PointParseResult with no coordinate.
                pass
        return parse_point_tag(self._accumulated)


# --- Factory: route model_id prefix to right AIClient subclass ---------------

# Bare-name prefixes that route to OllamaClient (in addition to ollama/* prefix).
# These match common local-model name conventions Ollama uses.
_OLLAMA_BARE_PREFIXES = ("llama", "qwen", "llava", "mistral", "phi", "gemma")


_GOOGLE_KEY_PREFIXES = ("AIza", "AQ.")
"""Prefixes identifying a DIRECT Google AI Studio key, as opposed to an OpenRouter
``sk-or-`` key that happens to route Gemini models (T1-1).

``AIza`` is the long-standing format. ``AQ.`` is the newer one — confirmed live
against a working key, and the reason this is a tuple rather than a single
``startswith`` check. An unrecognised prefix falls through to the OpenRouter path,
which fails with an actionable auth error rather than silently mis-routing."""


def is_direct_google_key(api_key: str | None) -> bool:
    """Whether ``api_key`` is a direct Google key rather than an OpenRouter key."""
    return bool(api_key) and api_key.startswith(_GOOGLE_KEY_PREFIXES)


def vertex_settings() -> tuple[str, str]:
    """Return ``(project, location)`` for the Vertex AI backend, project empty when unset.

    Read fresh rather than imported as a constant, matching how ``app.py`` resolves
    ``LLM_PROVIDER``, so switching a deployment onto Vertex does not need a restart.

    A blank location is coerced to ``global`` instead of being passed through: the SDK
    rejects an empty region with an error that names neither the setting nor the fix.
    """
    from config import resolve_setting
    project = resolve_setting("GOOGLE_CLOUD_PROJECT", default="").strip()
    location = resolve_setting("GOOGLE_CLOUD_LOCATION", default="global").strip()
    return project, location or "global"


def _flag(setting_name: str, default: str = "off") -> bool:
    """Read an on/off feature setting.

    Defaults to OFF because most callers are opt-in capabilities with cost or behaviour
    implications (T1-5 grounding, T1-3 Agentic Vision, T1-6b code execution), and the
    backward-compatibility rules require a new setting to reproduce current behaviour.

    ``KB_CACHE`` passes ``default="on"`` as the one deliberate exception: it changes no
    observable behaviour and falls back to the current path on any failure.
    """
    from config import resolve_setting
    return resolve_setting(setting_name, default=default).strip().lower() == "on"


def _provider_base_url(
    api_key: str,
    base_url: str | None,
    openrouter_url: str,
    native_url: str | None = None,
) -> str | None:
    """Shared endpoint decision for every LLM provider. An explicit base_url
    wins; else an OpenRouter (sk-or-) key routes to openrouter_url; else the
    provider's native endpoint (native_url, or None = the SDK's own default).

    Centralizing this is the point: no provider can 'forget' OpenRouter routing.
    OpenAI did, earlier — an sk-or- key was silently sent to api.openai.com
    and 401'd. Every branch in create_ai_client now calls this.
    """
    if base_url is not None:
        return base_url
    if api_key and api_key.startswith("sk-or-"):
        return openrouter_url
    return native_url


def create_ai_client(
    model_id: str,
    api_key: str,
    base_url: str | None = None,
    ollama_host: str | None = None,
) -> AIClient:
    """Route to AnthropicClient, GeminiClient, or OllamaClient based on model_id prefix.

    This is THE BYOK abstraction. Users change MODEL_ID in .env, app.py calls
    this factory, and the right SDK routes the request. No app.py logic
    depends on which model family is active.

    Args:
        model_id: OpenRouter-style model ID. Prefix determines client:
            'anthropic/...' or 'claude...'     → AnthropicClient (anthropic SDK)
            'google/...' or 'gemini...'         → GeminiClient (openai SDK)
            'ollama/...' or bare 'llama*' /     → OllamaClient (httpx → local server)
                'qwen*' / 'llava*' / 'mistral*' /
                'phi*' / 'gemma*'
            Other prefixes raise ValueError with an actionable message.
        api_key: API key. Ignored for Ollama (local server, unauthenticated).
            For Anthropic/Gemini: same value for both — OpenRouter key when
            OpenRouter is configured via ANTHROPIC_BASE_URL, or the direct
            provider key otherwise.
        base_url: Optional override for cloud providers' API endpoints. Testing
            hook; production leaves it None so each client uses its SDK's
            default. Ignored by OllamaClient (use ``ollama_host`` instead).
        ollama_host: Optional override for Ollama server URL. Defaults to
            ``http://localhost:11434`` (Ollama's out-of-the-box binding).
            Only used when dispatching to OllamaClient.

    Returns:
        A concrete AIClient subclass ready for .ask_stream() calls.

    Raises:
        ValueError: if model_id prefix is not recognized. Error message lists
            the supported prefixes and hints how to add a new provider.
    """
    mid = model_id.lower()

    # Ollama dispatch FIRST — `llama*` and `qwen*` prefixes are unambiguous local
    # (no cloud provider ships them under those bare names).
    if mid.startswith("ollama/") or any(
        mid.startswith(p) for p in _OLLAMA_BARE_PREFIXES
    ):
        return OllamaClient(
            host=ollama_host or "http://localhost:11434",
            model_id=model_id,
        )

    # T0-1: this branch also matched a bare ``model`` prefix, a fossil from an
    # earlier find-and-replace. It meant ANY model id beginning with the literal
    # string "model" silently routed to Anthropic. ``claude`` is accepted so a
    # bare native id works without the routing namespace.
    if mid.startswith("anthropic/") or mid.startswith("claude"):
        # Auto-route OpenRouter keys (sk-or-v1-*) to OpenRouter's
        # Anthropic-compat endpoint when no explicit base_url given.
        # Bundled Nimbus.exe has cwd outside the repo, so .env doesn't
        # load and ANTHROPIC_BASE_URL env var is unset — without this
        # fallback the SDK defaults to api.anthropic.com and Anthropic
        # rejects the OpenRouter-namespaced key with 401 invalid x-api-key.
        # Direct Anthropic keys (sk-ant-*) leave base_url=None so the
        # SDK uses its default api.anthropic.com endpoint, where those
        # keys are valid.
        base_url = _provider_base_url(api_key, base_url, "https://openrouter.ai/api")
        return AnthropicClient(
            api_key=api_key, model_id=model_id, base_url=base_url,
        )
    if mid.startswith("google/") or mid.startswith("gemini"):
        from config import OPENROUTER_BASE_URL
        # T1-1: a DIRECT Google key goes to the native SDK, which unlocks
        # structured geometry, thinking budgets, grounding, and Agentic Vision.
        # An OpenRouter key keeps the existing compatibility-shim path untouched,
        # so model-agnostic single-key routing still works.
        #
        # A configured Google Cloud project ALSO selects the native SDK, and does so
        # without a key: Vertex AI authenticates with Application Default Credentials.
        # The project is checked first because an institution that has deliberately
        # configured Vertex must not be silently downgraded to a leftover pasted key.
        vertex_project, vertex_location = vertex_settings()
        if base_url is None and (vertex_project or is_direct_google_key(api_key)):
            from gemini_native import GeminiNativeClient
            return GeminiNativeClient(
                api_key=api_key,
                model_id=model_id,
                vertex_project=vertex_project,
                vertex_location=vertex_location,
                enable_search_grounding=_flag("SEARCH_GROUNDING"),
                enable_agentic_vision=_flag("AGENTIC_VISION"),
                enable_code_execution=_flag("CODE_EXECUTION"),
                # T1-6a defaults ON: pure cost reduction, no behaviour change, and it
                # degrades to inline injection on any failure.
                enable_kb_cache=_flag("KB_CACHE", default="on"),
            )
        # sk-or- key → OpenRouter (GeminiClient keeps the google/ slug); a
        # direct Google AI Studio key → Google's native OpenAI-compat endpoint
        # (GeminiClient strips the google/ prefix). One OpenRouter key OR a
        # direct Google key both work.
        base_url = _provider_base_url(
            api_key, base_url, OPENROUTER_BASE_URL,
            native_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )
        return GeminiClient(api_key=api_key, model_id=model_id, base_url=base_url)
    # OpenAI native: 'openai/gpt-4o' etc. The GPT-Realtime
    # speech-to-speech path uses LLM_PROVIDER='openai-realtime' and is
    # handled separately in app.py (realtime.py), NOT through this factory —
    # so 'gpt-realtime*' should never reach here as a vision MODEL_ID.
    if mid.startswith("openai/"):
        from config import OPENROUTER_BASE_URL
        # sk-or- key → OpenRouter (OpenAIVisionClient keeps the openai/ slug); a
        # direct OpenAI key → base_url None so the SDK uses api.openai.com (the
        # client strips the openai/ prefix + uses max_completion_tokens there).
        base_url = _provider_base_url(api_key, base_url, OPENROUTER_BASE_URL)
        return OpenAIVisionClient(
            api_key=api_key,
            model_id=model_id,
            base_url=base_url,
        )
    raise ValueError(
        f"Unsupported MODEL_ID prefix: {model_id!r}. "
        f"Supported prefixes: 'anthropic/...' (or 'claude...'), "
        f"'google/...' (or 'gemini...'), 'openai/...', 'ollama/...' (or bare "
        f"'llama*'/'qwen*'/'llava*'/'mistral*'/'phi*'/'gemma*'). "
        f"To add a new provider, subclass AIClient in ai.py and extend "
        f"create_ai_client() with a new branch."
    )


# --- Manual live-API verification entry point ---------------------------------

if __name__ == "__main__":
    from config import ANTHROPIC_API_KEY, MODEL_ID

    print("=" * 70)
    print("Nimbus -- ai.py manual live-API verification")
    print("  Pattern: vision-tag [POINT:x,y:label] with streaming")
    print("=" * 70)

    if not ANTHROPIC_API_KEY:
        raise SystemExit("ANTHROPIC_API_KEY missing from .env")

    try:
        test_image = Image.open("debug_capture.jpg").convert("RGB")
    except FileNotFoundError:
        raise SystemExit(
            "debug_capture.jpg not found in current directory. "
            "Run `py -3.13 -m capture` first to generate it."
        )

    client = AnthropicClient(api_key=ANTHROPIC_API_KEY, model_id=MODEL_ID)
    transcript = (
        "what's on my screen and point at the most clickable thing "
        "for opening an application"
    )

    print(f"\nSending to Nimbus ({MODEL_ID})...")
    print(f"  image:      {test_image.size}")
    print(f"  transcript: {transcript!r}")
    print(f"  max_tokens: {_NIMBUS_MAX_TOKENS}")

    label = f"primary focus (image dimensions: {test_image.width}x{test_image.height} pixels)"
    print("\nStreaming response:")
    with client.ask_stream(
        images=[(test_image, label)],
        transcript=transcript,
        history=[],
    ) as stream:
        for delta in stream.text_deltas():
            print(delta, end="", flush=True)
        result = stream.final_result()

    print(f"\n\nSpoken text (tag stripped):")
    print(f"  {result.spoken_text!r}")
    print(f"\nCoordinate: {result.coordinate}")
    print(f"Label:      {result.element_label}")
    print(f"Screen:     {result.screen_number}")

    if result.coordinate:
        x, y = result.coordinate
        in_bounds = 0 <= x < test_image.width and 0 <= y < test_image.height
        print(f"In bounds:  {in_bounds}")

    print("\n" + "=" * 70)
    print("Manual verification checklist:")
    print("  1. Response text is non-empty, lowercase, casual tone")
    print("  2. References specific things visible in debug_capture.jpg")
    print("  3. [POINT:x,y:label] tag present at end of response")
    print("  4. Coordinate is in-bounds and lands on a plausible UI element")
    print("  5. Spoken text has the tag stripped (safe for TTS)")
    print("=" * 70)
