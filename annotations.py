"""Teaching-annotation tag grammar (draw-on-screen teaching mode).

The vision model appends shape tags to its spoken answer using a
dimension-labeled screenshot + [SHAPE:coords] tags. This module strips the
tags (so TTS never reads coordinates aloud) and returns shape objects in
screenshot-pixel space. Model-agnostic: any vision model that follows the
grammar works.

Grammar (coords are integer screenshot pixels, origin top-left):
    [ARROW:x1,y1->x2,y2]    arrow from (x1,y1) to (x2,y2)
    [CIRCLE:x,y,r:label]    circle center (x,y) radius r, optional :label
    [UNDERLINE:x,y,w]       horizontal underline at (x,y), width w
    [LABEL:x,y:text]        floating text at (x,y)

The same `[SHAPE:coords]` text-tag + regex pattern the `[POINT:x,y]` cursor
already uses (ai.parse_point_tag) — extending the cursor to shapes is just
more tag types.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Arrow:
    x1: int
    y1: int
    x2: int
    y2: int


@dataclass(frozen=True)
class Circle:
    x: int
    y: int
    r: int
    label: str = ""


@dataclass(frozen=True)
class Underline:
    x: int
    y: int
    w: int


@dataclass(frozen=True)
class Label:
    x: int
    y: int
    text: str


@dataclass(frozen=True)
class Rect:
    """Axis-aligned rectangle framing a UI control (T1-2).

    Top-left ``(x, y)`` plus ``w``/``h``, in screenshot pixels — the same
    position-plus-lengths shape ``Circle`` and ``Underline`` use, so
    ``overlay.annotations_to_local`` applies the identical rule: positions
    transform, lengths only scale.

    Added for structured ``box_2d`` output. A real bounding box frames a control
    correctly, where a ``Circle`` with a model-guessed radius either clips the
    control or swallows its neighbours.
    """
    x: int
    y: int
    w: int
    h: int
    label: str = ""

    @property
    def center(self) -> tuple[int, int]:
        """Centre point, for when a rectangle must degrade to a single point."""
        return (self.x + self.w // 2, self.y + self.h // 2)


@dataclass(frozen=True)
class StepBadge:
    """A numbered step marker, e.g. ① at a position (T3-5).

    The visual counterpart to a multi-step spoken answer: "first click here, then here"
    is ambiguous when both places are pointed at in sequence and the user looks away.
    Numbering makes order survivable.

    ``n`` is 1-based because it is read by a human, not indexed by code.
    """
    x: int
    y: int
    n: int
    label: str = ""


@dataclass(frozen=True)
class Highlight:
    """Dim the whole screen EXCEPT this rectangle (T3-5).

    Inverted geometry: every other shape draws *on* the target, this one draws everywhere
    else. In a dense UI -- an IDE, a timeline editor, a spreadsheet -- removing ninety
    competing elements is far clearer than adding a ninety-first.

    Cheap here specifically because the overlay is already full-screen, per-monitor and
    click-through. On any other architecture this would be the expensive shape.

    Deliberately position-plus-lengths like ``Rect`` so ``overlay.annotations_to_local``
    needs no special case: positions transform, lengths scale.
    """
    x: int
    y: int
    w: int
    h: int
    label: str = ""

    @property
    def center(self) -> tuple[int, int]:
        return (self.x + self.w // 2, self.y + self.h // 2)


# All shape regexes are case-INSENSITIVE + tolerate whitespace after '['.
# The annotation prompt asks for all-lowercase prose, so the model may well
# emit `[circle:...]` instead of `[CIRCLE:...]` — both must parse AND strip,
# or lowercase coordinates would leak to TTS (the never-speak-coords invariant).
# Note the `\s*` BEFORE each colon too — a model could emit `[circle : ...]`
# with a space before the colon; that variant must also parse AND strip so
# coordinates never reach TTS (the never-speak-coords invariant).
_ARROW_RE = re.compile(r"\[\s*ARROW\s*:\s*(\d+)\s*,\s*(\d+)\s*->\s*(\d+)\s*,\s*(\d+)\s*\]", re.IGNORECASE)
_CIRCLE_RE = re.compile(r"\[\s*CIRCLE\s*:\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?::([^\]]*))?\]", re.IGNORECASE)
_UNDERLINE_RE = re.compile(r"\[\s*UNDERLINE\s*:\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]", re.IGNORECASE)
_LABEL_RE = re.compile(r"\[\s*LABEL\s*:\s*(\d+)\s*,\s*(\d+)\s*:([^\]]*)\]", re.IGNORECASE)
# T3-5. RECT and HIGHLIGHT share the x,y,w,h shape; STEP carries a 1-based index.
_RECT_RE = re.compile(
    r"\[\s*RECT\s*:\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?::([^\]]*))?\]",
    re.IGNORECASE,
)
_HIGHLIGHT_RE = re.compile(
    r"\[\s*HIGHLIGHT\s*:\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?::([^\]]*))?\]",
    re.IGNORECASE,
)
_STEP_RE = re.compile(
    r"\[\s*STEP\s*:\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?::([^\]]*))?\]",
    re.IGNORECASE,
)

_SHAPE_KEYWORDS = "ARROW|CIRCLE|UNDERLINE|LABEL|RECT|HIGHLIGHT|STEP"
"""Every tag keyword, in ONE place (T3-5).

Previously the keyword list was written out separately in the complete-tag and
unterminated-tag regexes. Adding a shape then meant remembering to update both, and
forgetting the second would let a truncated tag's coordinates be read aloud -- a silent
violation of the never-speak-coordinates invariant. Single source of truth removes that
class of mistake entirely."""

# Strips any COMPLETE shape tag from the spoken text. Narrow to the known
# keywords so we never eat unrelated bracketed text.
_ANY_TAG_RE = re.compile(rf"\[\s*(?:{_SHAPE_KEYWORDS})\s*:[^\]]*\]", re.IGNORECASE)

# Fail-closed strip: removes an UNTERMINATED shape tag (and everything after
# it) — e.g. a truncated `look here [CIRCLE:120,40,15` with no closing `]`.
# Without this, a malformed/truncated tag would survive into the spoken text
# and TTS would read the coordinates aloud, violating the hard invariant that
# coordinates are never spoken. Matches the pipeline's "stop at the first '['"
# streaming guard. DOTALL so it eats across newlines to end-of-string.
_UNTERMINATED_TAG_RE = re.compile(
    rf"\[\s*(?:{_SHAPE_KEYWORDS})\s*:.*$", re.IGNORECASE | re.DOTALL
)


def parse_annotations(text: str) -> tuple[str, list]:
    """Return ``(spoken_text_with_tags_stripped, [Annotation, ...])``.

    Annotations are returned in their order of appearance so the overlay can
    render them in a sensible sequence. Malformed tags are dropped silently —
    a half-formed tag must never crash the pipeline mid-response.
    """
    found: list[tuple[int, object]] = []

    for m in _ARROW_RE.finditer(text):
        found.append((m.start(), Arrow(
            int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)),
        )))
    for m in _CIRCLE_RE.finditer(text):
        found.append((m.start(), Circle(
            int(m.group(1)), int(m.group(2)), int(m.group(3)),
            (m.group(4) or "").strip(),
        )))
    for m in _UNDERLINE_RE.finditer(text):
        found.append((m.start(), Underline(
            int(m.group(1)), int(m.group(2)), int(m.group(3)),
        )))
    for m in _LABEL_RE.finditer(text):
        found.append((m.start(), Label(
            int(m.group(1)), int(m.group(2)), m.group(3).strip(),
        )))
    # T3-5
    for m in _RECT_RE.finditer(text):
        found.append((m.start(), Rect(
            int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)),
            (m.group(5) or "").strip(),
        )))
    for m in _HIGHLIGHT_RE.finditer(text):
        found.append((m.start(), Highlight(
            int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)),
            (m.group(5) or "").strip(),
        )))
    for m in _STEP_RE.finditer(text):
        found.append((m.start(), StepBadge(
            int(m.group(1)), int(m.group(2)), int(m.group(3)),
            (m.group(4) or "").strip(),
        )))

    found.sort(key=lambda pair: pair[0])
    annotations = [ann for _, ann in found]

    # Strip complete tags, then fail-closed-strip any unterminated tag tail so
    # a truncated `[CIRCLE:120,40,15` can never be spoken aloud.
    spoken = _ANY_TAG_RE.sub("", text)
    spoken = _UNTERMINATED_TAG_RE.sub("", spoken)
    spoken = re.sub(r"\s+", " ", spoken).strip()
    return spoken, annotations
