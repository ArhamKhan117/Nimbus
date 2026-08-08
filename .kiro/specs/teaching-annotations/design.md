# Design Document

## Overview

Three concerns, deliberately separated: `overlay.py` owns the window and the painting, `annotations.py`
owns the shape vocabulary and its text grammar, and `theme.py` owns every value either of them uses.

The separation matters most at the coordinate boundary. `overlay.py` lives in Space A (physical) and
Space B (per-screen logical) and owns the transform between them; `capture.py` owns Space A to Space C.
Nothing else converts a coordinate, and nothing anywhere caches a device-pixel ratio.

> Consolidated from `IMPROVEMENTS.md` `T3-5` and `SHELL_AND_CHAT.md` §2. The WCAG audit table and the
> six shading techniques are in §2.1 and §2.5 of that document.

## Architecture

```
model
  ├─ tag channel  ─→ annotations.parse_annotations(text)
  │                    seven regexes, IGNORECASE, whitespace-tolerant
  │                    → (spoken_text_with_tags_stripped, [shape, ...])
  │
  └─ tool channel ─→ GeminiNativeClient.geometry()
                       draw_box → Rect · highlight_region → Highlight
                       mark_step → StepBadge · point_at → Circle
                       → [shape, ...] in Space C

                       ↓ both produce the SAME immutable shapes

app._annotations_to_physical(shapes, capture)      Space C → Space A
      positions: clamp → × scale → + monitor origin
      lengths:   × scale only, no origin, no clamp
                       ↓
      sig_show_annotations(shapes, monitor)        crosses the thread boundary
                       ↓
OverlayController.show_annotations
      screen_for_monitor  → match physical geometry, primary fallback
      annotations_to_local(shapes, screen)         Space A → Space B
            positions: − screen origin, ÷ devicePixelRatio
            lengths:   ÷ devicePixelRatio only
      route to ONE overlay, clear the rest
                       ↓
OverlayWindow.paintEvent
      pass 1: highlights (unconditional, first — the model must not control this)
      pass 2: the rest, in order of appearance
      then:   the pointer, if visible
```

Per monitor there is one `OverlayWindow`, constructed with its screen, shown, and then given its
click-through styles — in that order, because the native handle does not exist until `show()`.

## Components and Interfaces

### `overlay.py`

```python
# Win32
_CLICKTHROUGH_FLAGS = 0x080800A8   # LAYERED | TRANSPARENT | TOPMOST | NOACTIVATE | TOOLWINDOW
apply_clickthrough_styles(hwnd)    # GetWindowLong → OR → SetWindowLong → SetWindowPos(FRAMECHANGED)

# Pure coordinate maths — no toolkit needed
screen_for_monitor(monitor, screens) -> screen
physical_to_local_logical(px, py, screen) -> (lx, ly)
annotations_to_local(shapes, screen) -> list           # positions transform, lengths scale

# Pure animation maths
_bezier_position(t, p0, p1, p2)      _smoothstep(t)        _flight_duration_s(distance)
_scale_pulse(linear_t)               _idle_breath_scale(elapsed)
_ease_out_cubic(t)                   _annotation_opacity(elapsed)
_curved_arrow_control(x1, y1, x2, y2)  _spinner_angle_deg(elapsed)
_spinner_tail_segments(angle)        _waveform_bar_height(index, level, phase)

class OverlayController:
    def __init__(self, overlay_factory=None, screens=None, cursor_pos_fn=None)
```

Three injection seams on the controller mean the whole coordinate and routing layer tests against mock
screens and mock windows, with no real toolkit and no real display.

`_CLICKTHROUGH_FLAGS` is pinned by a test asserting **both** the OR expression and the literal, so a
typo in one constant cannot silently break click-through. The layered bit is required for the
transparent bit to function on a top-level window — which is also why capture exclusion is impossible
here, and why the hide-before-grab cycle is permanent.

### The pointer

The silhouette is 11 vertices **traced from the artwork** by `tools/trace_cursor.py`: alpha threshold,
Moore-neighbour boundary tracing, Ramer-Douglas-Peucker simplification, then normalised so the tip
lands on the origin. Deriving it rather than eyeballing it matters more than it sounds — the
proportions are easy to guess and the *character* is not. The traced shape is 23.7 × 27.9, an aspect of
0.85, far broader than the 0.6 of the hand-authored polygon it replaced, with the heel two thirds of
the way down rather than halfway. Guessing produced a generic operating-system arrow, which is exactly
what the brand's pointer is not.

Painting order, and each element's reason: flight trail (only while flying, tip anchors so the effect
introduces no transform), shadow, accent glow, then the polygon — **black outline stroked first at a
wider pen, under a radial fill**, so the visible border stays a crisp hairline. The outline is black
rather than white because white was chosen when the pointer was blue: against a light accent it lowers
contrast at exactly the boundary that defines the shape, and washes out entirely on a light background.
Black at high alpha is what Windows and macOS both use, for the same reason.

The fill's highlight and lower edge are **derived** from the state accent by `_tint` and `_shade`. They
were literals once — a pale blue highlight and a navy edge — which is why the pointer still read as
blue after the palette moved to orange: the accent was correct and a cool highlight on top was
cancelling the hue.

### `annotations.py`

Seven frozen dataclasses: `Arrow`, `Circle`, `Underline`, `Label`, `Rect` (+`center`), `StepBadge`,
`Highlight` (+`center`). Seven case-insensitive regexes tolerant of whitespace around `[`, every `:`
and every `,`.

```python
_SHAPE_KEYWORDS = "ARROW|CIRCLE|UNDERLINE|LABEL|RECT|HIGHLIGHT|STEP"
_ANY_TAG_RE          = rf"\[\s*(?:{_SHAPE_KEYWORDS})\s*:[^\]]*\]"
_UNTERMINATED_TAG_RE = rf"\[\s*(?:{_SHAPE_KEYWORDS})\s*:.*$"      # IGNORECASE | DOTALL
```

The single keyword constant is the fix for a real class of mistake: the list was written out twice, so
adding a shape meant remembering both, and forgetting the second would let a truncated tag's
coordinates be read aloud.

`parse_annotations` collects `(offset, shape)` from seven passes, sorts by offset, then strips complete
tags, then strips any unterminated tail, then collapses whitespace.

### `theme.py`

Pure values and pure functions above the Qt line; everything Qt-dependent behind a lazy import. That
separation is what makes the palette, the contrast maths and the stylesheet generation testable with no
toolkit — the same split `capture.py` uses, for the same reason.

The measured contrast audit, on the elevated surface:

| Colour | Ratio | Verdict |
|---|---|---|
| primary text | 16.9 | AA |
| secondary text | 7.2 | AA |
| muted text | 5.4 | AA |
| accent | 7.1 | AA |
| disabled text | 2.6 | **deliberately fails** |

The muted value is the one worth recording. The first draft used a colour measuring **3.49:1**, which
fails AA for body text — while being destined for exactly the small secondary labels it fails for. A
second candidate missed at 4.46:1, which is the kind of near-miss that gets waved through without
measurement. Both are named in the docstring so neither is tried again.

`build_qss()` generates the stylesheet from those constants. Generated rather than hand-written because
a literal colour next to a named one is invisible in review and impossible to grep for, and a test
asserts no literal colour appears in the output.

## Data Models

```python
@dataclass(frozen=True) class Arrow:     x1: int; y1: int; x2: int; y2: int
@dataclass(frozen=True) class Circle:    x: int; y: int; r: int; label: str = ""
@dataclass(frozen=True) class Underline: x: int; y: int; w: int
@dataclass(frozen=True) class Label:     x: int; y: int; text: str
@dataclass(frozen=True) class Rect:      x: int; y: int; w: int; h: int; label: str = ""
                                         @property center -> (x + w//2, y + h//2)
@dataclass(frozen=True) class StepBadge: x: int; y: int; n: int; label: str = ""   # n is 1-based
@dataclass(frozen=True) class Highlight: x: int; y: int; w: int; h: int; label: str = ""
                                         # inverted: dims everything EXCEPT this rect

class _OverlayState(Enum): IDLE, POINTING, LISTENING, THINKING, HIDDEN
```

`Rect` and `Highlight` are deliberately position-plus-lengths, exactly like `Circle` and `Underline`,
so the transform needs no special case for them.

## Correctness Properties

### Property 1: The click-through bit pattern is exact

The OR of the five named Win32 constants equals the declared literal. Any single-bit drift in any
constant fails. This is pinned as a literal *and* as an expression, because the two failing together is
the only case that would go unnoticed.

**Validates: Requirements 1.3**

### Property 2: Positions transform, lengths only scale

For any shape, any screen origin including negative, and any device-pixel ratio, every positional field
has the origin subtracted and is divided by the ratio, while every length field is only divided by the
ratio. Generator: each of the seven shape types across arbitrary origins and ratios in
{1.0, 1.25, 1.5, 2.0, 2.5}.

**Validates: Requirements 2.1, 2.3**

### Property 3: The transform is non-destructive and total

For any list of shapes, the transform returns new objects, leaves the inputs unchanged, and returns
exactly as many shapes as it received. A dropped shape is a defect — this property is what caught
`Rect` being silently discarded by both transforms, so `draw_box` had never rendered anything.

**Validates: Requirements 2.4, 2.5**

### Property 4: Screen matching is total

For any monitor descriptor and any non-empty screen list, exactly one screen is returned: the geometric
match if one exists, otherwise the primary. Generator: descriptors that match, that match on some
fields only, and that match nothing.

**Validates: Requirements 2.2**

### Property 5: The Bézier flight is continuous and endpoint-exact

For any two points, the flight passes through the start at progress zero and the end at progress one,
and its sampled path is continuous with no jump larger than the frame budget. The scale pulse peaks at
linear progress one half, not at eased progress one half.

**Validates: Requirements 3.3, 3.6, 3.8**

### Property 6: The tip is invariant under scaling

For any scale factor and any pointer position, the tip vertex after the scale transform is the same
point it was before. Scaling about any other centre violates this and drifts the tip off target.

**Validates: Requirements 3.7**

### Property 7: Waveform height is bounded and never flat

For any audio level in the unit interval, any bar index and any phase, the bar height lies within the
declared range and is strictly greater than zero. A level below the dead zone yields the idle pulse
only, so the bars keep moving without reacting to noise.

**Validates: Requirements 4.3, 4.4**

### Property 8: Tag stripping is complete under adversarial input

For any text containing any mixture of the seven tag types — well-formed, malformed, truncated,
mixed-case, with arbitrary internal whitespace — the returned spoken text matches none of the tag
patterns. Generator: tags assembled from arbitrary integers, arbitrary labels, arbitrary delimiter
whitespace and arbitrary truncation points.

**Validates: Requirements 6.4, 6.5, 6.6, 6.7**

### Property 9: Parsing is order-preserving and total

For any text, shapes are returned in ascending order of their position in the text, malformed tags
contribute nothing, and no input raises.

**Validates: Requirements 6.8, 6.9**

### Property 10: Both channels converge

For any geometry expressible in both channels, the tag path and the tool path produce equal shape
objects. Generator: the same coordinates encoded as tags and as normalised tool arguments.

**Validates: Requirements 6.3**

### Property 11: Elevation is monotonic and consistently tinted

The five surface steps are strictly increasing in relative luminance, and every step's blue channel
exceeds its red channel by a similar margin. A warm grey among cool ones fails. This caught the design
document's original claim about the tint direction being false.

**Validates: Requirements 7.3**

### Property 12: Every text colour meets its stated ratio

For each declared text colour and each surface it is used on, the computed contrast ratio meets AA —
except the disabled colour, which must fail. Both directions are asserted.

**Validates: Requirements 7.6, 7.7**

### Property 13: The generated stylesheet contains no literal colour

For the whole generated stylesheet, every colour token is traceable to a constant in the design system.
A hex literal or an `rgba(...)` not produced by the helpers fails.

**Validates: Requirements 7.8**

### Property 14: Exits are faster than entrances, and nothing exceeds the cap

For every entrance and exit pair, the exit duration is strictly smaller. For every declared duration,
the value is at most the cap.

**Validates: Requirements 8.1, 8.2**

### Property 15: Reduced motion collapses every duration and still completes

When reduced motion is in effect, every duration passed through the helper returns zero, and a
zero-duration animation still emits its completion signal.

**Validates: Requirements 8.6, 8.7**

### Property 16: The noise tile is deterministic and has enough levels

For a fixed seed, the tile is byte-identical between runs. The number of distinct alpha values it
contains is at least the declared maximum plus one. This caught an implementation that premultiplied
and quantised down to seven levels — far too coarse to break banding, which is the texture's only job.

**Validates: Requirements 9.7, 9.8**

## Error Handling

| Failure | Response |
|---|---|
| Style change fails | Raise with the Win32 error detail, because silent click-through breakage has no other signal |
| No screen matches a monitor | Fall back to the primary; mss state can be stale after a display change |
| No overlay matches a screen | Fall back to the first overlay rather than dropping the pointer |
| Malformed tag | Drop it silently; a half-formed tag must not crash a turn mid-response |
| Unhandled shape type in a transform | A defect, not a runtime concern — every type has a branch and a test |
| Brand asset missing | Return an empty image. A missing logo costs a logo, not a window |
| Noise tile generation fails | Skip the texture; a repaint of the interface is worth more than a texture |
| Reduced-motion query fails | Fail **open**: animations on |
| Marker animation before layout | Skip it; animating to an unlaid-out position would slide the marker in from a corner |

## Testing Strategy

- **Coordinate maths at 100% and 200% scaling, on primary and offset screens**, with mock screens and
  no toolkit. The transform is the highest-risk code in the project and the cheapest to test.
- **The bit-pattern guard**, asserting both the expression and the literal.
- **Animation maths as pure functions**: Bézier endpoints, smoothstep, the distance-to-duration clamp,
  waveform bar heights across the level range, spinner angle wrapping.
- **The full tag grammar** as a table: every shape, every casing, every whitespace variant, plus the
  truncated and complete-but-unparseable cases, asserting both the parsed shapes and that the spoken
  text is clean.
- **Palette and motion drift guards** in `test_theme.py` (56 tests): computed contrast ratios, one
  accent hue, monotonic elevation, consistent tint, ascending unique spacing, exits faster than
  entrances, nothing over the cap, disabled text below AA on purpose, no literal colour in the
  stylesheet, and a proof that the prose-stripping helper the source-reading guards depend on can
  itself fail.
- **Artwork bounding boxes** in `test_brand.py`, so a re-exported asset fails a test rather than
  quietly shrinking the logo.
- **Regenerate, do not hand-edit.** `tools/trace_cursor.py` regenerates the pointer vertices from the
  artwork and `tools/make_icons.py` regenerates every icon from one source. After changing artwork, run
  them; do not adjust the numbers.
- **Visual review** through `tools/preview_ui.py`, which opens the real overlay, shell and panel with
  sample data and every path redirected into a temporary folder, so it writes nothing and needs no
  microphone, network or pipeline.
