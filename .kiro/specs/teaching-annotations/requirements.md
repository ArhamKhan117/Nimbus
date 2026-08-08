# Requirements Document

## Introduction

Pointing is the smaller half of teaching. "Click the publish button, top right" is advice the user
still has to act on; a pointer landing on that button *while* they are told what publishing will do
is being taught. Some things words are simply the wrong tool for — a box round the control you need,
everything else dimmed, numbered order for a sequence, an arrow from the mistake to the fix.

This spec covers the visual layer: the per-monitor click-through overlay, the animated pointer, the
teaching-annotation vocabulary and its two input channels, and the design system every surface in the
product draws its values from.

> **Provenance.** Consolidated into Kiro's spec format from `IMPROVEMENTS.md` `T3-5` (richer
> annotations) and `SHELL_AND_CHAT.md` §2 (the design system) and §2.4 (retheming the overlay). Task
> IDs are preserved. The WCAG contrast audit and the six shading techniques live in §2.1 and §2.5 of
> that document.

## Glossary

| Term | Meaning |
|---|---|
| **Click-through** | The overlay is visible but receives no mouse input; clicks pass to the app beneath |
| **Annotation** | A teaching shape drawn on screen: arrow, circle, underline, label, rect, highlight, step |
| **Highlight** | Inverted geometry — dims the whole screen *except* one rectangle |
| **Step badge** | A numbered marker, 1-based, for an answer with an order |
| **Tag channel** | Shapes parsed out of the model's text, for providers with no structured output |
| **Tool channel** | Shapes arriving as typed function calls, for providers that support them |
| **Elevation ramp** | The five-step surface scale that makes a dark interface read as layered |

## Requirements

### Requirement 1: The overlay is invisible to input and to capture

**User Story:** As a user, I want Nimbus to draw over my work without interfering with it, so that I
can keep clicking while it points.

#### Acceptance Criteria

1. THE system SHALL create one overlay window per physical monitor, each covering exactly that
   monitor, rather than one window spanning the virtual desktop.
2. THE system SHALL apply the click-through window styles **after** the window is shown, because the
   native handle does not exist until then.
3. THE system SHALL read the current extended styles and **OR** the click-through bits into them,
   never assign, so that the toolkit's own flags are not wiped.
4. THE system SHALL force a frame recalculation after changing styles, because otherwise the change
   has no effect until the window is resized or moved.
5. IF the style change fails THEN THE system SHALL raise an error including the Win32 error detail,
   so that silently broken click-through has a diagnostic.
6. THE system SHALL achieve transparency through window attributes rather than a stylesheet, and
   SHALL never set a window opacity below one, because either forces the toolkit's own layered path
   and overrides the applied styles.
7. THE overlay SHALL be excluded from the taskbar and from the window-switcher list.
8. THE overlay SHALL never take focus, even when it receives an event.

### Requirement 2: Coordinate transforms are per-screen

**User Story:** As a user with two monitors at different scaling, I want the pointer to land in the
right place on both, so that a mixed-DPI setup is not a broken setup.

#### Acceptance Criteria

1. THE system SHALL convert a physical point to a screen-local logical point using **that screen's**
   device-pixel ratio, and SHALL never cache a global ratio.
2. THE system SHALL match a capture's monitor descriptor to a screen by comparing physical geometry,
   and SHALL fall back to the primary screen when no match is found.
3. WHEN transforming a shape, THE system SHALL transform positions and SHALL only divide lengths,
   applying no origin offset to a length.
4. THE transform SHALL return new shape objects and SHALL leave its inputs untouched.
5. THE transform SHALL handle every shape type in the vocabulary, and a shape type it does not handle
   SHALL be treated as a defect rather than silently dropped.
6. THE transform functions SHALL be pure, so that the coordinate maths is testable with no windowing
   toolkit running.

### Requirement 3: The pointer

**User Story:** As a user, I want to see where Nimbus is pointing without losing track of it, so that
I can follow the answer with my eyes.

#### Acceptance Criteria

1. THE pointer silhouette SHALL be **derived from the brand artwork** by a tool, not hand-authored,
   so that the flying pointer and the logo cannot drift apart.
2. THE pointer vertices SHALL be floating point, because the shape is DPI-scaled and pulsed during
   flight and integer rounding would surface as a wobbling edge.
3. WHEN pointing THEN THE pointer SHALL fly along a quadratic Bézier arc whose control point is the
   midpoint lifted proportionally to the distance.
4. THE flight duration SHALL scale with distance and be clamped to a range, so that a short hop is
   not sluggish and a cross-desktop flight is not frantic.
5. THE pointer SHALL NOT rotate along the flight tangent, because the shape is tip-anchored and the
   tip must keep pointing at the target throughout.
6. THE scale pulse SHALL be driven by **linear** progress while the position is driven by eased
   progress, so that the peak lands at the true mid-arc.
7. THE scale SHALL be applied about the tip, because scaling about any other point drifts the tip off
   the target.
8. WHEN the flight completes THEN THE pointer SHALL snap exactly to the target and reset its scale.
9. AFTER the dwell period, THE pointer SHALL fly back to the mouse and resume following it with a
   spring interpolation rather than snapping.
10. WHILE idle, THE pointer SHALL breathe within a restrained scale range, so that it reads as present
    rather than as a static decal.

### Requirement 4: Interaction state is visible at the cursor

**User Story:** As a user, I want to know whether Nimbus is listening, thinking or speaking, so that
I am not waiting on something that is not happening.

#### Acceptance Criteria

1. THE system SHALL define one central mapping from interaction state to accent colour, sourced from
   the design system rather than from literals.
2. WHILE recording, THE system SHALL replace the pointer with an audio-reactive waveform at the cursor.
3. THE waveform SHALL apply a dead zone below a small level, so that near-silent input does not
   flicker.
4. THE waveform SHALL never be fully flat, so that it reads as live rather than broken.
5. WHILE waiting for a response, THE system SHALL show a spinner at the cursor.
6. THE listening state SHALL remain green even though green is off-brand, because recording indicators
   are green essentially everywhere and overriding a learned signal costs the user certainty that the
   microphone is live.
7. THE pointing state SHALL use the brand accent.
8. THE idle state SHALL recede rather than glow.
9. THE pointer, the waveform and the spinner SHALL be mutually exclusive; exactly one is visible at a
   time.

### Requirement 5: Teaching annotation vocabulary

**User Story:** As a user learning something with an order to it, I want to see the sequence rather
than remember it, so that a multi-step answer survives me looking away.

#### Acceptance Criteria

1. THE system SHALL support arrow, circle, underline, label, rectangle, highlight and numbered step.
2. THE rectangle SHALL exist because a real bounding box frames a control correctly, where a circle
   with a model-guessed radius either clips the control or swallows its neighbours.
3. THE highlight SHALL dim the entire screen except its rectangle, because in a dense interface
   removing ninety competing elements is clearer than adding a ninety-first.
4. THE system SHALL draw highlights in a separate unconditional first pass rather than in list order,
   because the model controls that order and must not be able to break the visual.
5. THE step number SHALL be 1-based, because it is read by a human rather than indexed by code.
6. Every shape SHALL be an immutable value object, so that a transform cannot mutate shared state.
7. THE system SHALL fade annotations in quickly, hold them, and fade them out before the automatic
   clear, so that they neither appear abruptly nor vanish mid-read.
8. THE system SHALL clear annotations at the start of every turn, so that stale shapes never survive a
   no-speech, cancelled or errored previous turn.

### Requirement 6: Two input channels, one renderer

**User Story:** As a user on any provider, I want teaching mode to work, so that the feature is not
gated on my choice of model.

#### Acceptance Criteria

1. THE system SHALL accept shapes from a text-tag grammar for providers with no structured output.
2. THE system SHALL accept shapes as typed function calls where the provider supports them.
3. Both channels SHALL produce the same shape objects, so that the renderer knows nothing about the
   channel.
4. THE tag grammar SHALL be case-insensitive and tolerant of whitespace around every delimiter,
   because the prompt asks for lowercase prose and the model may well comply.
5. THE system SHALL strip every complete tag from the spoken text.
6. THE system SHALL strip an **unterminated** tag and everything after it, so that a response
   truncated mid-tag cannot have its coordinates read aloud.
7. THE tag keyword list SHALL exist in exactly one place, feeding both the complete-tag and
   unterminated-tag patterns, because forgetting the second would silently leak coordinates.
8. IF a tag is malformed THEN THE system SHALL drop it silently rather than raise, because a
   half-formed tag must never crash a turn mid-response.
9. Shapes SHALL be returned in order of appearance, so that the renderer can honour a described
   sequence.

### Requirement 7: One design system, no local values

**User Story:** As a user, I want the interface to look like one product, so that it reads as designed
rather than assembled.

#### Acceptance Criteria

1. THE system SHALL define every colour, radius, spacing step, type size and animation duration in one
   module.
2. A value invented locally instead of imported SHALL be treated as a defect.
3. THE surface scale SHALL have five monotonic elevation steps that share a consistent tint, so that
   they read as one material rather than as unrelated greys.
4. THE spacing scale SHALL be a fixed set, and it SHALL be the only permitted source of spacing.
5. THE system SHALL provide exactly one accent hue, used on a small fraction of any surface.
6. Every text colour SHALL be **measured** against WCAG contrast rather than chosen by eye, and the
   ratios SHALL be pinned by tests.
7. THE disabled text colour SHALL deliberately fail the contrast requirement, and a test SHALL say so,
   because disabled text must not be readable as content.
8. THE stylesheet SHALL be **generated** from the constants rather than hand-written, so that a
   literal colour cannot creep back in.
9. THE overlay SHALL be excluded from the stylesheet and SHALL consume the constants directly, because
   it is a translucent click-through surface doing per-frame painting and a stylesheet has nothing to
   offer it.

### Requirement 8: Motion communicates state

**User Story:** As a user, I want the interface to explain itself through movement without slowing me
down, so that motion is information rather than decoration.

#### Acceptance Criteria

1. THE system SHALL define durations for micro, standard, entrance and exit transitions, and a cap
   that nothing may exceed.
2. An exit SHALL always be faster than its matching entrance, because an element arriving deserves to
   be noticed while the same element leaving is in the user's way.
3. THE system SHALL prefer animating opacity and position over width and height, because animating
   layout forces a relayout of every child per frame.
4. All animation SHALL run on the main thread.
5. THE system SHALL honour the operating system's reduced-motion preference, and SHALL provide an
   explicit override for a user whose system setting and app preference differ.
6. WHERE reduced motion is in effect, every duration SHALL collapse to zero.
7. A zero-duration animation SHALL still emit its completion signal, because cleanup logic depends on
   it and otherwise disabling motion would silently break those paths.
8. THE reduced-motion check SHALL be resolved once and cached, and SHALL **fail open**, because a
   broken system call must not silently strip the interface of motion.

### Requirement 9: Dark interfaces need shading, not just dark colours

**User Story:** As a user, I want to be able to tell what is on top of what, so that the interface has
structure rather than being grey rectangles on slightly different grey rectangles.

#### Acceptance Criteria

1. THE system SHALL draw a one-pixel lighter line along the top inside edge of a raised surface,
   because that simulates light from above and is the difference between a card reading as an object
   and reading as a hole.
2. THE system SHALL tint each elevation step slightly rather than only lightening it.
3. THE system SHALL apply an ambient accent bloom behind a focused element, offset to zero because it
   is ambient light rather than a directional shadow.
4. THE bloom SHALL be used on **static** elements only, because the effect forces the widget through
   a software offscreen buffer.
5. WHERE a surface repaints every frame, THE system SHALL paint its bloom with a gradient in the paint
   handler instead.
6. THE system SHALL overlay a low-opacity noise texture at window level, because large low-contrast
   gradients on dark backgrounds band visibly.
7. THE noise texture SHALL vary alpha rather than colour, so that it retains enough distinct levels to
   actually break banding.
8. THE noise texture SHALL use a fixed seed, so that it is byte-identical between runs and screenshots
   remain comparable.
9. THE noise overlay SHALL be transparent to the mouse, and there SHALL be exactly one per window.
10. Cards SHALL have no drop shadow, because the top highlight and the two-tone border already read as
    elevation.

### Requirement 10: Brand artwork is loaded once and trimmed

**User Story:** As a user, I want the logo to look the same size everywhere it appears, so that the
product looks finished.

#### Acceptance Criteria

1. THE system SHALL load brand artwork through one module rather than at each site.
2. THE system SHALL crop artwork to its alpha bounding box before scaling, because the source canvas
   carries substantial transparent padding.
3. THE alpha scan SHALL be cached per asset rather than per requested size, because the crop is a
   property of the artwork and the scan is not free.
4. IF an asset is missing or unreadable THEN THE loader SHALL return an empty image rather than raise,
   because a missing logo must cost a logo and not a window.
5. A brand label SHALL be transparent to the mouse, because it sits inside a drag handle and would
   otherwise punch a dead spot in it.
6. THE artwork bounding boxes SHALL be asserted by tests, so that a re-exported asset with different
   padding fails a test rather than silently shrinking the logo.
