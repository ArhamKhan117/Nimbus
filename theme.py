"""Nimbus design system: palette, elevation, type, motion (SHELL_AND_CHAT.md §2).

The single source of truth for every colour, radius, spacing step and animation duration in
the application shell, the chat HUD and the overlay. Two independent workstreams consume this
module, so a value invented locally instead of imported here is a defect: it is how a dark
theme ends up with four slightly different greys that nobody can see in review.

## Structure, and why it is this way

**Pure values and pure functions at module level; Qt behind lazy imports.** Everything above
`--- Qt helpers ---` imports nothing but the standard library. That keeps the palette, the
contrast maths and the stylesheet generation testable with no `QApplication`, which is the same
separation `capture.py` uses for its coordinate maths and the reason those tests are fast and
deterministic.

## The contrast audit is load-bearing

Every text colour here was measured against WCAG, not chosen by eye. The first draft used
`#6B6B75` for muted text, which measures **3.49:1** on `BG_ELEVATED` and fails AA for body text
-- while being destined for exactly the small secondary labels it fails for. It is now
`#8A8A94` at 5.38:1.

`tests/test_theme.py` pins every ratio, so a future palette tweak cannot quietly regress
readability. `TEXT_DISABLED` deliberately fails and has its own test saying so, because
disabled text must not carry information.
"""
from __future__ import annotations

# --- Surfaces: a five-step elevation ramp ------------------------------------
#
# Every step is very slightly COOL -- 2-3 points more blue than red. That is deliberate and it
# is the opposite of what the first draft of the design doc claimed.
#
# The accent is warm orange. A cool neutral is its complement, so orange reads as vivid against
# these surfaces; a warm grey would sit adjacent to the accent on the colour wheel and muddy
# it. The shared blue bias is also what makes five greys read as one material rather than five
# unrelated ones -- consistency of tint matters more than its direction.
#
# Asserted by `test_surfaces_share_a_consistent_cool_tint`, which caught the doc's original
# claim being false.

BG_BASE = "#0B0B0D"
"""Elevation 0. Window background."""

BG_SUNKEN = "#08080A"
"""Elevation -1. Input wells, code blocks, scroll troughs."""

BG_ELEVATED = "#141417"
"""Elevation 1. Cards, panels, the chat HUD body."""

BG_RAISED = "#191920"
"""Elevation 2. Popovers, menus, the session picker."""

BG_HOVER = "#1F1F26"
BG_ACTIVE = "#26262E"

# --- Gradient stops ----------------------------------------------------------
#
# A flat fill and a gradient fill are the difference between a panel and a surface. Each pair
# below straddles its base colour by 3-5 points of lightness -- light at the top, dark at the
# bottom, because that is where light comes from -- which is enough to read as a material and
# little enough that nobody can point at a "gradient". Anything wider looks like 2009.
#
# Every stop keeps the same cool bias as the ramp above, so a gradient cannot introduce a warm
# grey that fights the orange accent.

SURFACE_TOP = "#17171C"
SURFACE_BOTTOM = "#111115"
"""Card fill, straddling ``BG_ELEVATED``."""

RAISED_TOP = "#1D1D25"
RAISED_BOTTOM = "#16161C"
"""Popovers and menus, straddling ``BG_RAISED``."""

CHROME_TOP = "#121217"
CHROME_BOTTOM = "#0B0B0D"
"""Retained for anything that still wants a shaded chrome gradient."""

CHROME_FLAT = "#070709"
"""**The title bar and nav rail, flat.**

Both carried the diagonal accent wash that the cards use. On a card, sitting inside the content
area, that reads as lit. On the window's own chrome it read as a smear -- the frame is meant to
recede so the content can come forward, and a gradient on the frame competes with the thing it
frames.

Two points darker than ``BG_BASE`` rather than equal to it, so the chrome is separated from the
content by tone alone and needs no divider line to explain where one ends.

One constant to revert: put ``chrome_gradient()`` back in the two rules that reference this."""

CONTROL_TOP = "#1C1C23"
CONTROL_BOTTOM = "#15151A"
"""Ordinary buttons at rest -- enough shading to read as raised without competing with
``#Primary``."""

SURFACE_GLOW = "#241C1E"
"""``SURFACE_TOP`` with roughly 8% of the accent mixed in.

The **corner** stop for card surfaces: a card fades from this at its top-left down to
``SURFACE_BOTTOM`` at its bottom-right, so the light on it reads as warm light coming from the
brand colour rather than as a neutral grey ramp. At 8% it is a tint, not a colour -- put it next
to ``SURFACE_TOP`` and you can see it, look at one card alone and you would only say it looks
warmer than the window.

Computed, not eyeballed: ``#17171C`` blended 8% toward ``#FF7A1A``. Still cooler in blue than
red at the same value would suggest, which is why it does not read as brown."""

CHROME_GLOW = "#1A1518"
"""The same treatment for the title bar and nav rail, at a lower strength -- chrome carries the
tint across a much larger area, so the same 8% would read as a colour wash."""

ACCENT_LIGHT = "#FFA155"
ACCENT_DEEP = "#D9600A"
"""The metallic accent's highlight and shadow stops.

Three stops rather than two (``ACCENT_LIGHT`` -> ``ACCENT`` -> ``ACCENT_DEEP``) with the middle
one held at 45%: a two-stop orange gradient reads as a flat wash, while a mid stop above centre
puts a visible sheen line across the upper third, which is what makes it read as metal rather
than as a colour fade."""

PANEL_RAISED = "#232020"
"""A neutral raised chip on the panel -- the auto-hide pill, the window-control buttons.

Opaque for the same reason as ``PANEL_HOVER`` below: these sit on containers whose background Qt
may paint from the palette."""

PANEL_HOVER = "#33231A"
PANEL_HOVER_STRONG = "#452B1D"
"""Hover and pressed states on the chat panel, **pre-composited** over ``PANEL_TOP``.

Roughly 10% and 20% of the accent, written as opaque colours for the same reason
``SELECTION_ROW`` is: a translucent background on a widget inside a ``QScrollArea`` composites
over whatever the viewport painted, and an unstyled Qt viewport paints the palette's default --
near-white on Windows. A 10% orange wash over that is a white row you cannot read, which is
exactly what the session list did."""

PANEL_GLOW = "#2A1E17"
"""``PANEL_TOP`` with roughly 9% of the accent mixed in -- the panel's lit corner.

A shade stronger than ``SURFACE_GLOW``'s 8%, because the panel is small and seen against an
arbitrary background rather than a large neutral field, so the same proportion reads as less."""

PANEL_TOP = "#1C1714"
PANEL_BOTTOM = "#141110"
"""The chat panel's interior: the same elevation as a card, but **warm** rather than cool.

The five-step surface ramp is deliberately cool -- 2-3 points more blue than red -- because a
cool neutral is the accent's complement and makes orange read as vivid against it. That is right
for the shell, where cards sit inside a large neutral window.

The floating panel is a different problem. It is small, it has a warm tinted bezel a few pixels
away, and it is seen against whatever application is underneath. Against that bezel the cool
interior read as *blue-black* -- two different blacks a centimetre apart, which is worse than
either choice on its own. These are the same lightness as `SURFACE_TOP`/`SURFACE_BOTTOM` with the
bias flipped, so the panel is one warm object from its edge inwards."""

TINT_EDGE = "#241A16"
TINT_CHROME = "#161014"
"""Warm near-blacks, for the window's own frame and chrome.

The chrome is flat black, which is right -- a frame should recede. But *entirely* neutral black
next to an orange accent reads as absence rather than as a decision, and the brand disappears
everywhere except the 5% of surface the accent occupies.

These are the smallest amount of orange that survives being looked at: `TINT_EDGE` is roughly 9%
accent over `BG_BASE`, `TINT_CHROME` about 4%. Both stay darker than `BG_ELEVATED`, so the
content still sits forward of the frame. Anything stronger and the chrome starts competing with
the cards; anything weaker and it is indistinguishable from black on a cheap panel."""

SHEEN = "rgba(255,255,255,0.045)"
"""A brighter top edge for gradient surfaces than ``HIGHLIGHT_TOP``, for chrome that needs to
read as raised against the window rather than against a card."""

SCRIM = "rgba(0,0,0,0.28)"
"""Darkens a surface under an overlay or a pressed state."""

# --- Lines -------------------------------------------------------------------

BORDER = "#26262B"
BORDER_STRONG = "#33333A"

HIGHLIGHT_TOP = "rgba(255,255,255,0.055)"
"""A 1px lighter line on the TOP inside edge of a raised surface.

The highest-impact single technique in the whole design system. It simulates light falling
from above, which is how every physical object reads, and it is the difference between a card
looking like an object and looking like a hole punched in the page. If only one shading
technique survives implementation, it should be this one."""

# --- Text --------------------------------------------------------------------

TEXT_PRIMARY = "#F5F5F7"
"""16.9:1 on BG_ELEVATED."""

TEXT_SECONDARY = "#A1A1AA"
"""7.2:1 on BG_ELEVATED."""

TEXT_MUTED = "#8A8A94"
"""5.4:1 on BG_ELEVATED.

Was `#6B6B75` in the first draft, which measured 3.49:1 and failed WCAG AA for body text.
`#7C7C87` was also considered and misses at 4.46:1 -- the kind of near-miss that gets waved
through without measurement."""

TEXT_DISABLED = "#5A5A63"
"""Deliberately below AA (2.6:1). Decorative only, NEVER information.

Disabled text must not be readable as content; that is the point of it. Guarded by
`test_text_disabled_is_deliberately_below_aa` so nobody "fixes" this and makes disabled stop
looking disabled."""

# --- Accent ------------------------------------------------------------------

ACCENT = "#FF7A1A"
"""Nimbus orange. 7.1:1 on BG_ELEVATED.

Used on roughly 5% of any given surface. A dark theme stays legible because the accent is
scarce; accenting everything is how it stops meaning anything."""

ACCENT_HOVER = "#FF8F3D"
ACCENT_PRESS = "#E56A0F"

ACCENT_WASH = "rgba(255,122,26,0.10)"
"""Selected nav item, subtle fills."""

ACCENT_WASH_STRONG = "rgba(255,122,26,0.20)"
"""A selected **table row**, where 10% is not enough to see.

Also the fix for a real defect: with the table set to ``NoFocus``, Qt fell back to the palette's
*inactive* highlight for a selected row -- a pale grey wash that made the row's own text
unreadable, which is the opposite of what selecting it was for. Styling the inactive state
explicitly is what removes the palette from the decision."""

ACCENT_GLOW = "rgba(255,122,26,0.28)"
"""Blurred ambient bloom behind focused elements."""

ACCENT_HAIR = "rgba(255,122,26,0.45)"
"""1px accent edge on active surfaces, and the leading rule on Nimbus chat turns."""

ON_ACCENT = "#0B0B0D"
"""Text on an accent fill.

Dark on orange, not white. Measured: dark text on `ACCENT` beats white text on `ACCENT`, and
white-on-orange is a common and avoidable readability mistake."""

# --- State -------------------------------------------------------------------

SUCCESS = "#22C55E"
WARNING = "#F59E0B"
DANGER = "#EF4444"

# --- Geometry ----------------------------------------------------------------

RADIUS_CARD = 12
RADIUS_CONTROL = 8
RADIUS_PILL = 999

SPACE: tuple[int, ...] = (4, 8, 12, 16, 20, 24, 32, 48)
"""The only permitted spacing values.

An open-ended set of paddings is how a layout ends up with 13px in one place and 15px in
another. Guarded by `test_spacing_scale_is_the_only_source_of_spacing`."""

# --- Type --------------------------------------------------------------------

FONT_FAMILY = "Segoe UI"
"""Already used throughout overlay.py, so this is consistency rather than a new choice."""

SYMBOL_FONT_STACK = '"Segoe UI Symbol"'
"""Windows' symbol font, named as the tail of the text stack.

Only reached by characters the text fonts do not have, so it cannot affect a letter of ordinary
copy. It covers the handful of symbols this interface draws inline: the restart marker's circular
arrow, the chat panel's close and pin glyphs, the Journal's tick and cross.

**Named rather than left to the OS.** All of these were already coming from this font by fallback --
Qt finds *something* for every character, and which font it picks is not under our control. Putting
the family in the stack makes Qt's own ordered fallback decide instead, so the same glyph is used on
every machine. That is the fix for the class of bug ``shell/titlebar.py`` records for ``\\u2b1c``,
where Segoe UI renders a solid white block.

An icon font (Segoe Fluent Icons on 11, Segoe MDL2 Assets on 10) was here briefly, for the restart
marker's ``U+E72C``. It was removed with that glyph: measured, an icon-font glyph is 1.36-1.44x the
surrounding cap height, because icon fonts are drawn to fill the em box while a text character's
capitals occupy roughly 70% of it. Correct shape, wrong scale -- it read as oversized beside a
label, and there is no way to shrink one run of a plain-text label."""

FONT_STACK = ('"Segoe UI Variable Text", "Segoe UI", "Inter", "Helvetica Neue", '
              f'{SYMBOL_FONT_STACK}, sans-serif')
"""The stylesheet's font stack, in preference order.

``Segoe UI Variable Text`` is Windows 11's system font. It is genuinely better than Segoe UI at
small sizes -- it was designed for variable optical sizing, so 10pt labels keep their counters
instead of filling in -- and it is what a Windows 11 user's other applications use, which is
what makes an app look native rather than merely dark.

Windows 10 does not have it, and there is no version check here on purpose: CSS font fallback
already does exactly the right thing, and a runtime check would be a second source of truth for
the same question. ``FONT_FAMILY`` stays as the single name for ``QPainter`` callers in
``overlay.py``, which cannot take a stack.

Measured, so the fallback is not taken on faith: on this machine the first entry is **not
installed** and the interface renders in Segoe UI, resolved through ``QFont::setFamilies``. The
silent failure this guards against is substitution to Tahoma, which is what a bare
``QFont("Segoe UI Variable Text")`` gives.

``SYMBOL_FONT_STACK`` sits before ``sans-serif`` so an inline symbol is found in a named font before
any generic fallback gets a say."""

FONT_MONO = "Cascadia Mono, Consolas, monospace"
"""For paths, executable names and licence keys.

Not decoration: `orionflow.exe.md` and `kpm_viewer.exe` are strings the user must copy
accurately, and a proportional font makes character-level mistakes easy to miss."""

FONT_HERO = 26
"""One per screen at most: the push-to-talk state on Home, and nothing else.

A display size used twice stops being a hierarchy and becomes a texture."""

FONT_DISPLAY = 20
FONT_TITLE = 15
FONT_BODY = 11
FONT_SMALL = 10
FONT_MICRO = 9

WEIGHT_REGULAR = 400
WEIGHT_MEDIUM = 500
WEIGHT_SEMIBOLD = 600
WEIGHT_BOLD = 700
"""Reserved for headings that label a *container* -- group box titles. Semibold at small sizes was
quieter than the body text underneath, which is the wrong way round for a heading."""

LINE_HEIGHT = 1.45
"""Body line height. Dark themes need slightly more leading than light ones -- light text on
dark backgrounds blooms optically and tighter leading reads as cramped."""

# --- Motion ------------------------------------------------------------------

DUR_MICRO = 120
"""Hover, press, focus ring."""

DUR_STANDARD = 200
"""Expand/collapse, page change, nav slide."""

DUR_ENTRANCE = 260
"""A panel arriving."""

DUR_EXIT = 160
"""Anything leaving.

**Always faster than its entrance.** An element arriving deserves to be noticed; the same
element leaving is in the user's way. Equal durations make dismissal feel sluggish, and it is
the most common mistake in hand-rolled UI motion. Guarded by
`test_exit_durations_are_faster_than_entrances`."""

DUR_MAX = 300
"""Nothing may exceed this. Longer than ~300ms in a utility app reads as lag, not polish."""

EASE_STANDARD = (0.4, 0.0, 0.2, 1.0)
EASE_OUT = (0.0, 0.0, 0.2, 1.0)
EASE_IN = (0.4, 0.0, 1.0, 1.0)

# --- Shadows -----------------------------------------------------------------
#
# Cards get NO shadow: the top highlight and two-tone border already read as elevation, and
# shadowing every surface is how a dark theme starts looking like 2014 Material.

SHADOW_POPOVER = (0, 8, 24, "rgba(0,0,0,0.45)")
SHADOW_FLOATING = (0, 16, 48, "rgba(0,0,0,0.55)")
"""(x, y, blur, colour) for elevation 2 and 3 respectively."""

# --- Overlay state palette (SHELL_AND_CHAT.md §2.4) --------------------------

OVERLAY_STATE_RGB: dict[str, tuple[int, int, int]] = {
    "idle": (138, 138, 148),        # TEXT_MUTED — idle should recede, not glow
    "pointing": (255, 122, 26),     # ACCENT — the brand moment
    "listening": (34, 197, 94),     # green, deliberately unchanged
    "thinking": (245, 158, 11),     # amber, nudged toward accent
    "hidden": (138, 138, 148),
}
"""Overlay accent per interaction state, consumed by `overlay._STATE_ACCENT_RGB`.

`listening` stays green even though it is off-brand. Recording indicators are green
essentially everywhere, and overriding a learned signal for palette tidiness is a bad trade --
the user needs to know the microphone is live more than they need visual consistency."""


# --- Pure colour maths (no Qt) -----------------------------------------------

def parse_hex(colour: str) -> tuple[int, int, int]:
    """`"#RRGGBB"` -> `(r, g, b)`. Raises `ValueError` on anything else."""
    text = colour.strip().lstrip("#")
    if len(text) != 6:
        raise ValueError(f"expected #RRGGBB, got {colour!r}")
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def relative_luminance(colour: str) -> float:
    """WCAG relative luminance of a hex colour."""
    def channel(value: int) -> float:
        srgb = value / 255
        return srgb / 12.92 if srgb <= 0.04045 else ((srgb + 0.055) / 1.055) ** 2.4

    r, g, b = parse_hex(colour)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG contrast ratio between two hex colours. 1.0 (identical) to 21.0 (black on white).

    Exists so contrast is *asserted in tests* rather than eyeballed. It caught `TEXT_MUTED`
    shipping at 3.49:1 against a 4.5:1 requirement.
    """
    a, b = relative_luminance(foreground), relative_luminance(background)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


def meets_aa(foreground: str, background: str, large_text: bool = False) -> bool:
    """WCAG AA: 4.5:1 for body text, 3.0:1 for >=18.66px bold or >=24px."""
    return contrast_ratio(foreground, background) >= (3.0 if large_text else 4.5)


def rgba(colour: str, alpha: float) -> str:
    """Hex colour + alpha -> a Qt-stylesheet `rgba(...)` string."""
    r, g, b = parse_hex(colour)
    return f"rgba({r},{g},{b},{max(0.0, min(alpha, 1.0)):.3f})"


def blend(top: str, bottom: str, alpha: float) -> str:
    """`top` at `alpha` composited over `bottom`, as an opaque hex colour.

    For the cases where translucency is not actually available. Qt's item views paint the
    palette's `Highlight` role *underneath* a stylesheet's `background`, so a translucent
    selection wash ends up sitting on whatever the palette says -- which on Windows is
    `#0078d7` blue when the view has focus and `#f0f0f0` near-white when it does not. A 20%
    orange over near-white is a pale peach, which is exactly the "light blue/white shade"
    that got reported.

    Pre-blending removes the palette from the question: the result is one opaque colour that
    *looks* like a translucent wash because it was computed as one.
    """
    tr, tg, tb = parse_hex(top)
    br, bg, bb = parse_hex(bottom)
    ratio = max(0.0, min(alpha, 1.0))

    def mix(a: int, b: int) -> int:
        return max(0, min(255, round(b + (a - b) * ratio)))

    return f"#{mix(tr, br):02X}{mix(tg, bg):02X}{mix(tb, bb):02X}"


SELECTION_ROW = blend(ACCENT, SURFACE_TOP, 0.22)
"""A selected table row: 22% Nimbus orange over the card surface, pre-composited.

Derived rather than written, so it cannot drift from `ACCENT`. Declared here rather than with
the other colours because it needs `blend`, and a constant that lies about how it was produced
is worse than one in a slightly odd place.

22% is the point where the row is unmistakably selected while `TEXT_PRIMARY` on it still clears
WCAG AA comfortably -- asserted in `tests/test_theme.py`."""

NAV_LABEL = blend(TEXT_PRIMARY, TEXT_SECONDARY, 0.42)
"""Unselected nav labels: 42% of the way from ``TEXT_SECONDARY`` toward ``TEXT_PRIMARY``.

Secondary grey is the right weight for supporting copy and slightly too quiet for navigation, where
every item is a destination the user might want. Bright enough to read as a control, still clearly
behind the selected item, which is primary *and* semibold *and* carries the accent wash.

Defined here rather than beside ``TEXT_SECONDARY`` because ``blend`` is not defined until after the
palette -- the text constants are literals on purpose, so they can be read and checked by eye."""

SIDEBAR_SWITCH_GLOW = blend(ACCENT, CHROME_FLAT, 0.10)
SIDEBAR_SWITCH = CHROME_FLAT
SIDEBAR_SWITCH_HOVER = blend(ACCENT, CHROME_FLAT, 0.07)
SIDEBAR_SWITCH_ON_GLOW = blend(ACCENT, CHROME_FLAT, 0.20)
SIDEBAR_SWITCH_ON = blend(ACCENT, CHROME_FLAT, 0.12)
SIDEBAR_SWITCH_ON_DEEP = blend(ACCENT, CHROME_FLAT, 0.07)
SIDEBAR_SWITCH_ON_HOVER = blend(ACCENT, CHROME_FLAT, 0.26)
"""The nav rail's chat-panel switch, **pre-composited over ``CHROME_FLAT``**.

Off, the chip is the rail's own black with a warm top-left corner and the grain over it -- the same
treatment as a card, which is why it now reads as a surface rather than as a button. It was a flat
lift off the rail colour, which made it look like an empty box.

On, it is a *shaded* orange rather than a flat wash: 20% at the lit corner falling to 7%, averaging
below the 14% flat fill it replaced. Same family of colour, less of it in the eye at once, and the
corner falloff plus the grain is what stops it reading as a bright rectangle. Measured on the
rendered rail: mean brightness 24.7 off and 37.8 on, against 18.3 for the Privacy Guard chip
beneath it -- present without shouting.

Named constants rather than ``blend`` calls inline in the stylesheet for two reasons. The drift guard
in ``tests/test_theme.py`` requires every colour in the generated QSS to be a theme constant, and it
is right to -- a computed colour buried in an f-string is exactly as unreviewable as a typed one. And
the rail paints flat black behind this, where a translucent tint is the composite that has gone wrong
repeatedly in this file; resolving it here means it cannot."""

FEATURE_ROW = blend(ACCENT, BG_ELEVATED, 0.06)
"""The highlighted-setting panel behind teaching mode. 6% accent over a card: enough to separate it
from the checkbox column it sits in, not enough to compete with an actual accent control."""

SELECTION_ROW_HOVER = blend(ACCENT, SURFACE_TOP, 0.30)
"""Hovering an already-selected row. Enough of a step to feel responsive, not enough to read as
a different state."""


# --- Gradients ---------------------------------------------------------------
#
# Qt's stylesheet gradient syntax is verbose and easy to get subtly wrong -- an `x2: 1` instead
# of `y2: 1` gives a horizontal gradient that looks like a rendering bug. These build it once.


def gradient_v(top: str, bottom: str, mid: str = "", mid_at: float = 0.45) -> str:
    """A vertical `qlineargradient(...)` string, light at the top.

    Vertical and top-lit is not a style choice, it is the same claim `HIGHLIGHT_TOP` makes:
    light falls from above, so a surface lit at its top edge reads as an object. Reversing it
    reads as a hole.

    `mid` is what separates the metallic accent from a plain fade. Held above centre by default
    so the sheen line sits in the upper third, where a highlight on a convex surface would be.
    """
    stops = f"stop:0 {top}, stop:1 {bottom}"
    if mid:
        stops = f"stop:0 {top}, stop:{mid_at:.2f} {mid}, stop:1 {bottom}"
    return f"qlineargradient(x1:0, y1:0, x2:0, y2:1, {stops})"


def gradient_corner(near: str, mid: str, far: str, mid_at: float = 0.45) -> str:
    """A diagonal `qlineargradient(...)` running from the top-left corner to the bottom-right.

    Where `gradient_v` simulates a light source above a surface, this simulates one *beside* it
    -- a single warm source at the top-left, which is what makes a row of cards look lit by the
    same lamp rather than individually shaded. Diagonal is the whole point: a vertical gradient
    repeated down a page reads as banding, while a corner gradient gives each card a different
    slice of the same light.

    `mid` sits above centre so the falloff happens in the upper half and the bottom two-thirds
    settle into the neutral surface colour. Without it the tint spreads across the whole card and
    stops being a highlight.
    """
    return (f"qlineargradient(x1:0, y1:0, x2:1, y2:1, "
            f"stop:0 {near}, stop:{mid_at:.2f} {mid}, stop:1 {far})")


def gradient_h(near: str, far: str, mid: str = "", mid_at: float = 0.35) -> str:
    """A horizontal `qlineargradient(...)`, warm at the left edge and fading right.

    For chrome that needs a hint of brand without a gradient down its face. The title bar and the
    nav rail both anchor at the left -- the logo is there, the nav items are there -- so a wash
    that decays rightwards puts the warmth where the eye already is and leaves the rest black.

    Deliberately not radial. Qt stylesheets support `qradialgradient`, but a radial bloom on a
    48px-tall bar is a bright spot with visible falloff rings on an 8-bit panel -- the banding
    problem the grain overlay exists to solve. A linear fade over ~35% has none of it.
    """
    stops = f"stop:0 {near}, stop:1 {far}"
    if mid:
        stops = f"stop:0 {near}, stop:{mid_at:.2f} {mid}, stop:1 {far}"
    return f"qlineargradient(x1:0, y1:0, x2:1, y2:0, {stops})"


def chrome_tint() -> str:
    """The title bar and nav rail: a warm left edge decaying into flat black."""
    return gradient_h(TINT_CHROME, CHROME_FLAT, mid=CHROME_FLAT, mid_at=0.38)


def accent_rule() -> str:
    """A 1px divider starting at the accent and fading to the ordinary border colour.

    A full-width accent line would be a stripe; fading it inside the first quarter makes it read
    as the brand touching the edge of the chrome and then getting out of the way.
    """
    return gradient_h(ACCENT_HAIR, BORDER, mid=ACCENT_WASH, mid_at=0.22)


def surface_gradient() -> str:
    """The card fill: a warm accent-tinted corner falling to a cool neutral."""
    return gradient_corner(SURFACE_GLOW, SURFACE_TOP, SURFACE_BOTTOM)


def sidebar_switch_gradient(on: bool = False, hover: bool = False) -> str:
    """The nav rail's chat-panel chip: a warm lit corner falling into the rail's own black.

    Cornered like ``surface_gradient`` rather than flat, so the chip reads as a lit surface in both
    states. Off it is the rail colour with a 10% warm corner; on it is the same shape with more of
    the accent in it. A flat fill in either state read as a box drawn on top of the rail.
    """
    if on:
        near = SIDEBAR_SWITCH_ON_HOVER if hover else SIDEBAR_SWITCH_ON_GLOW
        return gradient_corner(near, SIDEBAR_SWITCH_ON, SIDEBAR_SWITCH_ON_DEEP, mid_at=0.42)
    near = SIDEBAR_SWITCH_HOVER if hover else SIDEBAR_SWITCH_GLOW
    return gradient_corner(near, SIDEBAR_SWITCH, SIDEBAR_SWITCH, mid_at=0.42)


def panel_gradient() -> str:
    """The floating chat panel's interior, and its popovers: warm all the way through.

    Distinct from `surface_gradient` on purpose -- see `PANEL_TOP`. A card lives inside a large
    neutral window; the panel floats with a warm bezel a few pixels from its own fill, and a cool
    interior next to that reads as blue-black.
    """
    return gradient_corner(PANEL_GLOW, PANEL_TOP, PANEL_BOTTOM)


def panel_frame_gradient() -> str:
    """The panel's bezel: warm at the top-left corner, settling into the darkest warm black.

    Ends on `CHROME_FLAT` rather than `BG_BASE`. `BG_BASE` is part of the cool ramp, and it was
    what made the frame read as blue-black at its far corner while the near corner was orange.
    """
    return gradient_corner(TINT_EDGE, TINT_CHROME, CHROME_FLAT, mid_at=0.30)


def raised_gradient() -> str:
    """Popovers and menus. Vertical, not cornered.

    A popover is small and transient, and a diagonal tint on something 200px wide reads as a
    gradient rather than as light. The cards it opens over carry the warmth.
    """
    return gradient_v(RAISED_TOP, RAISED_BOTTOM)


def chrome_gradient() -> str:
    """Title bar and nav rail, tinted from the top-left like the cards."""
    return gradient_corner(CHROME_GLOW, CHROME_TOP, CHROME_BOTTOM, mid_at=0.35)


def control_gradient() -> str:
    """An ordinary button at rest."""
    return gradient_v(CONTROL_TOP, CONTROL_BOTTOM)


def accent_gradient(light: str = ACCENT_LIGHT, mid: str = ACCENT,
                    deep: str = ACCENT_DEEP) -> str:
    """The metallic orange used on primary actions and the power control."""
    return gradient_v(light, deep, mid=mid)


# --- Qt helpers (lazy imports so the values above stay Qt-free) --------------

def qcolor(colour: str, alpha: int | None = None):
    """Hex or `rgba(...)` string -> `QColor`."""
    from PyQt6.QtGui import QColor

    text = colour.strip()
    if text.startswith("rgba"):
        parts = text[text.index("(") + 1:text.rindex(")")].split(",")
        r, g, b = (int(float(p)) for p in parts[:3])
        a = int(float(parts[3]) * 255) if len(parts) > 3 else 255
        return QColor(r, g, b, a if alpha is None else alpha)
    r, g, b = parse_hex(text)
    return QColor(r, g, b, 255 if alpha is None else alpha)


_animations_enabled: bool | None = None


def animations_enabled() -> bool:
    """Whether the user wants UI animation, honouring Windows and `REDUCE_MOTION`.

    Reads `SPI_GETCLIENTAREAANIMATION`. Vestibular sensitivity is real and Windows exposes the
    preference, so ignoring it is a genuine accessibility failure rather than a nicety.

    Cached after the first call: the setting changes rarely, and a syscall on the hover path
    would be wasteful. Fails **open** (animations on) because a broken syscall should not
    silently strip the interface of motion.
    """
    global _animations_enabled
    if _animations_enabled is not None:
        return _animations_enabled

    override = ""
    try:
        from config import resolve_setting
        override = resolve_setting("REDUCE_MOTION", default="auto").strip().lower()
    except Exception:
        pass
    if override == "on":
        _animations_enabled = False
        return False
    if override == "off":
        _animations_enabled = True
        return True

    try:
        import ctypes

        SPI_GETCLIENTAREAANIMATION = 0x1042
        enabled = ctypes.c_int(1)
        ok = ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(enabled), 0)
        _animations_enabled = bool(enabled.value) if ok else True
    except Exception:
        _animations_enabled = True
    return _animations_enabled


def duration(ms: int) -> int:
    """A motion duration, collapsed to 0 when the user has asked for reduced motion.

    Callers pass a `DUR_*` constant through this rather than using it directly, so honouring
    the accessibility preference is one call site per animation instead of a conditional.

    A 0ms `QPropertyAnimation` still emits `finished`, which matters because cleanup logic
    hangs off that signal -- verified by `test_zero_duration_animation_still_emits_finished`.
    Without that guarantee, disabling animation would silently break those paths.
    """
    return ms if animations_enabled() else 0


def easing(curve: tuple[float, float, float, float] | str = EASE_STANDARD):
    """A `QEasingCurve` from a cubic-bezier tuple, or a named curve like `"OutBack"`."""
    from PyQt6.QtCore import QEasingCurve

    if isinstance(curve, str):
        return QEasingCurve(getattr(QEasingCurve.Type, curve))
    result = QEasingCurve(QEasingCurve.Type.BezierSpline)
    x1, y1, x2, y2 = curve
    result.addCubicBezierSegment(
        _point(x1, y1), _point(x2, y2), _point(1.0, 1.0))
    return result


def _point(x: float, y: float):
    from PyQt6.QtCore import QPointF
    return QPointF(x, y)


def focus_visible_only(root) -> int:
    """Make every button under ``root`` reachable by Tab but never focused by a click.

    Returns how many widgets were changed, so a caller can assert it did something.

    ## The dotted white rectangle

    Clicking any control left a dotted white frame around its label that stayed until something
    else was clicked. It is ``QStyle::PE_FrameFocusRect``, drawn by the ``windowsvista`` style on
    whatever holds focus -- and Qt's default ``StrongFocus`` means a *click* gives a button focus,
    so every click parked that frame somewhere.

    It reproduces only when Windows has keyboard cues enabled. Measured with
    ``SPI_GETKEYBOARDCUES``: 0 on the development machine, which is why nothing showed up until the
    flag was forced on -- at which point one click on a nav item changed **186 bright pixels**, and
    0 after this. Windows turns cues on for the session the moment anyone presses Alt or Tab, and
    some accessibility settings leave them on permanently, so "it does not happen for me" is not
    evidence of anything.

    ## Why ``TabFocus`` rather than ``NoFocus`` or ``outline: none``

    ``NoFocus`` would fix the pixels by removing the control from the keyboard entirely -- a
    keyboard-only user could no longer reach the nav rail. ``outline: none`` depends on the style
    honouring the property for a widget it draws natively, which ``border: none`` controls like the
    nav items are; it is applied in ``build_qss`` as well, but it is not the load-bearing half.

    ``TabFocus`` keeps the widget in the tab chain and only stops a *mouse* click focusing it.
    Nothing can draw a focus frame on a widget that does not have focus, so this holds whatever the
    platform style and the cues setting do. It is the same rule as CSS ``:focus-visible``: show
    focus to the keyboard, not to the mouse. The themed ``:focus`` ring in ``build_qss`` is then
    only ever seen after Tab, which is exactly when it is wanted.

    Text entry is deliberately untouched. A ``QLineEdit`` or ``QComboBox`` must take click focus to
    be usable, and both are fully styled here, so they get the theme's focus border rather than a
    dotted frame.

    ## Checkable group boxes count as buttons, and missing that shipped the bug again

    A **checkable ``QGroupBox``** takes focus and draws the same frame, and it is not a
    ``QAbstractButton`` -- so the first version of this walked right past the "Experimental /
    developer options" toggle, which is exactly the one the user photographed still showing a
    dotted rectangle. Measured inside a real window afterwards: 0 buttons click-focusable, 1 group
    box still ``StrongFocus``.

    Lives in ``theme`` rather than ``shell.widgets`` because both settings hosts need it and
    ``settings_dialog`` must not import from ``shell`` -- the shell is one of its hosts.
    """
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QAbstractButton, QGroupBox

    changed = 0
    for widget in (*root.findChildren(QAbstractButton), *root.findChildren(QGroupBox)):
        if widget.focusPolicy() == Qt.FocusPolicy.StrongFocus:
            widget.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            changed += 1
    return changed


# --- Grain (SHELL_AND_CHAT.md §2.5) -----------------------------------------

GRAIN_TILE = 128
GRAIN_ALPHA = 10
"""Maximum per-pixel alpha out of 255, so roughly 4% at the brightest speck and 0% at the
dimmest.

Large low-contrast gradients on dark backgrounds **band** -- visible stepped stripes, worst on
cheap panels and worse again after Windows' colour management. A faint noise tile destroys
banding completely and reads as texture rather than noise. It is the difference between a dark
theme that looks designed and one that looks flat."""


def grain_pixmap(seed: int = 0x4E494D42):
    """A tileable noise `QPixmap` for the window-level grain overlay.

    **Varies alpha, not colour, and this matters.** The first implementation used a fixed
    low alpha with a varying white level, then premultiplied -- which quantised
    ``(value * 6) // 255`` down to seven distinct output values across the whole tile. Seven
    levels is far too coarse to break banding, which is the texture's only job. Varying alpha
    over white in a non-premultiplied format keeps ``GRAIN_ALPHA + 1`` genuinely distinct
    levels.

    Fixed seed so the texture is byte-identical between runs and machines: a grain layer that
    changed per launch would make screenshots and visual diffs useless.

    Drawn once as a window-level overlay, never per widget -- per-widget grain tiles
    inconsistently across boundaries and costs a composite on every repaint.
    """
    import random

    from PyQt6.QtGui import QImage, QPixmap

    rng = random.Random(seed)
    image = QImage(GRAIN_TILE, GRAIN_TILE, QImage.Format.Format_ARGB32)
    image.fill(0)
    for y in range(GRAIN_TILE):
        for x in range(GRAIN_TILE):
            alpha = rng.randint(0, GRAIN_ALPHA)
            image.setPixel(x, y, (alpha << 24) | 0x00FFFFFF)
    return QPixmap.fromImage(image)


# --- Stylesheet generation ---------------------------------------------------

def build_qss() -> str:
    """The application stylesheet, generated from the constants above.

    **Generated, not a static `.qss` file.** A hand-written stylesheet is where literal colours
    creep back in, and a literal `#1a1a1a` next to `BG_ELEVATED = "#141417"` is invisible in
    review and impossible to grep for. Generating it makes that class of drift impossible;
    `test_qss_references_no_literal_colours` proves it.

    `overlay.py` is deliberately excluded -- it is a click-through translucent window doing
    60Hz `QPainter` animation, and a stylesheet has nothing to offer it. It consumes the
    constants directly instead.
    """
    return f"""
* {{
    font-family: {FONT_STACK};
    font-size: {FONT_BODY}pt;
    color: {TEXT_PRIMARY};
}}

QWidget#Root, QDialog {{
    background: {BG_BASE};
}}

QFrame#Card {{
    background: {surface_gradient()};
    border: 1px solid {BORDER};
    border-top: 1px solid {HIGHLIGHT_TOP};
    border-radius: {RADIUS_CARD}px;
}}

QFrame#Popover {{
    background: {raised_gradient()};
    border: 1px solid {BORDER_STRONG};
    border-top: 1px solid {SHEEN};
    border-radius: {RADIUS_CARD}px;
}}

QLabel#Hero      {{ font-size: {FONT_HERO}pt; font-weight: {WEIGHT_SEMIBOLD}; }}
QLabel#Display   {{ font-size: {FONT_DISPLAY}pt; font-weight: {WEIGHT_SEMIBOLD}; }}
QLabel#Title     {{ font-size: {FONT_TITLE}pt;   font-weight: {WEIGHT_SEMIBOLD}; }}
QLabel#Secondary {{ color: {TEXT_SECONDARY}; }}
QLabel#Muted     {{ color: {TEXT_MUTED}; font-size: {FONT_SMALL}pt; }}
QLabel#Mono      {{ font-family: {FONT_MONO}; color: {TEXT_SECONDARY}; }}
QLabel#CardHeader {{
    color: {TEXT_SECONDARY};
    font-size: {FONT_SMALL}pt;
    font-weight: {WEIGHT_SEMIBOLD};
    letter-spacing: 0.6px;
}}
QLabel#PageTitle {{
    font-size: {FONT_DISPLAY}pt;
    font-weight: {WEIGHT_SEMIBOLD};
    letter-spacing: -0.2px;
}}

QPushButton {{
    background: {control_gradient()};
    border: 1px solid {BORDER};
    border-top: 1px solid {HIGHLIGHT_TOP};
    border-radius: {RADIUS_CONTROL}px;
    padding: 7px 14px;
    min-height: 20px;
    color: {TEXT_PRIMARY};
}}
QPushButton:hover  {{
    background: {gradient_v(BG_HOVER, CONTROL_TOP)};
    border-color: {BORDER_STRONG};
}}
QPushButton:pressed{{
    /* Gradient inverted on press: the lit edge moves to the bottom, so the control reads as
       pushed in rather than merely darker. Costs nothing and it is the whole tactile cue. */
    background: {gradient_v(SURFACE_BOTTOM, BG_ACTIVE)};
    border-color: {BORDER_STRONG};
}}
QPushButton:disabled {{
    background: {BG_SUNKEN};
    color: {TEXT_DISABLED};
    border-color: {BORDER};
}}

QPushButton#Primary {{
    background: {accent_gradient()};
    border: 1px solid {ACCENT_DEEP};
    border-top: 1px solid {ACCENT_LIGHT};
    color: {ON_ACCENT};
    font-weight: {WEIGHT_SEMIBOLD};
    padding: 8px 18px;
}}
QPushButton#Primary:hover {{
    background: {accent_gradient(ACCENT_LIGHT, ACCENT_HOVER, ACCENT)};
    border-color: {ACCENT};
}}
QPushButton#Primary:pressed {{
    background: {gradient_v(ACCENT_PRESS, ACCENT_DEEP)};
    border-color: {ACCENT_DEEP};
}}
QPushButton#Primary:disabled {{
    background: {BG_SUNKEN};
    color: {TEXT_DISABLED};
    border-color: {BORDER};
}}

QPushButton#Ghost {{
    background: transparent;
    border: 1px solid transparent;
    color: {TEXT_SECONDARY};
}}
QPushButton#Ghost:hover {{ background: {BG_HOVER}; color: {TEXT_PRIMARY}; }}
QPushButton#Ghost:pressed {{ background: {BG_ACTIVE}; }}

QPushButton#Danger {{ color: {DANGER}; border-color: {rgba(DANGER, 0.35)}; }}
QPushButton#Danger:hover {{ background: {rgba(DANGER, 0.10)}; border-color: {DANGER}; }}

/* Nav labels, given an actual typographic treatment.
   They were the interface font at its default weight and `TEXT_SECONDARY`, which is the same
   treatment as body copy -- so five one-word labels in a rail read as a list of paragraphs rather
   than as navigation. Three changes, none of them a font swap:
   * **semibold throughout.** Short words in a narrow column need stem weight to hold their shape;
     regular at 11pt in a 216px rail looks thin and slightly washed out. The selected item does not
     get *heavier* -- it is already distinguished by colour, the accent wash and the sliding bar, and
     a fourth signal would be noise.
   * **letter-spacing.** Navigation is read as shapes, not sentences. A third of a pixel of tracking
     is what separates "Knowledge" into a word you recognise rather than parse.
   * **a brighter resting colour.** `NAV_LABEL` sits between primary and secondary, so an unselected
     item is legible in its own right and the selected one is still clearly ahead of it.

   Two things measured rather than assumed. `FONT_STACK` leads with "Segoe UI Variable Text", which is
   *not installed* on this machine -- Qt honours the rest of the list through `QFont::setFamilies`, so
   the interface renders in Segoe UI. And Segoe UI has no 500: asking for it resolves to 400, silently.
   Available weights are 300, 400, 600 and 700, which is why this says semibold and not medium -- the
   first version of this rule set 500 and changed nothing at all. */
QPushButton#NavItem {{
    background: transparent;
    border: none;
    border-radius: {RADIUS_CONTROL}px;
    padding: 10px 12px;
    text-align: left;
    color: {NAV_LABEL};
    font-size: {FONT_BODY}pt;
    font-weight: {WEIGHT_SEMIBOLD};
    letter-spacing: 0.3px;
    min-height: 20px;
}}
QPushButton#NavItem:hover   {{ background: {BG_HOVER}; color: {TEXT_PRIMARY}; }}
QPushButton#NavItem:checked {{
    background: {ACCENT_WASH};
    color: {TEXT_PRIMARY};
    font-weight: {WEIGHT_SEMIBOLD};
}}

/* Focus: no native frame, one themed ring.

   The platform style draws `PE_FrameFocusRect` -- a dotted white rectangle around the label -- on
   whatever holds focus, and it looked like a rendering fault sitting inside a rounded orange pill.
   It appears only when Windows has keyboard cues on, which it turns on for the session as soon as
   anyone presses Alt or Tab; measured with SPI_GETKEYBOARDCUES = 0 on the development machine,
   which is why it went unseen until a user reported it.

   `outline: none` covers the controls this stylesheet draws itself. The load-bearing half of the
   fix is `shell.widgets.focus_visible_only`, which stops a mouse click focusing a button at all --
   nothing can draw a frame on a widget that is not focused. Together they mean the ring below is
   only ever seen after Tab, which is the CSS `:focus-visible` rule and the accessible outcome:
   visible to the keyboard, invisible to the mouse. */
QPushButton, QCheckBox, QRadioButton, QToolButton, QAbstractSpinBox, QComboBox,
QLineEdit, QPlainTextEdit, QTextEdit, QTabBar::tab {{
    outline: none;
}}
QPushButton:focus {{ border: 1px solid {ACCENT_HAIR}; }}
QPushButton#NavItem:focus {{
    /* No border here: a 1px ring inside a 6px-radius pill that already carries the accent wash
       reads as a misaligned second outline. The wash brightening is the cue. */
    border: none;
    background: {ACCENT_WASH_STRONG};
}}
QCheckBox:focus, QRadioButton:focus {{
    /* These have no border to tint, so the label carries the state. */
    color: {TEXT_PRIMARY};
}}

QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {BG_SUNKEN};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_CONTROL}px;
    padding: 6px 10px;
    min-height: 20px;
    selection-background-color: {ACCENT};
    selection-color: {ON_ACCENT};
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {{
    border-color: {BORDER_STRONG};
}}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: {TEXT_DISABLED}; }}
QComboBox QAbstractItemView {{
    background: {BG_RAISED};
    border: 1px solid {BORDER_STRONG};
    selection-background-color: {ACCENT_WASH};
    outline: none;
}}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {BORDER_STRONG};
    border-radius: 4px;
    background: {BG_SUNKEN};
}}
QCheckBox::indicator:hover   {{ border-color: {ACCENT_HAIR}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QCheckBox:disabled {{ color: {TEXT_DISABLED}; }}

/* Group boxes, and the title that was sitting on the border.

   Measured with `QStyle.subControlRect(CC_GroupBox, ..., SC_GroupBoxLabel)`: the label occupied
   y=0..19 while the frame started at y=10, so the lower half of every heading was drawn across
   the top border. "Privacy", "Interface" and the experimental toggle all read as half in and half
   out of their own box -- and the experimental one looked like a loose checkbox floating between
   two panels, because that is exactly what a checkable group box's title indicator looks like
   when the title is outside the frame.

   `margin-top` has to be at least the label's height, since `subcontrol-origin: margin` places
   the label in that margin. It was 10px against a 20px label. The title is also now the size and
   weight of `QLabel#CardHeader`, so a group box and a card label the same way -- the group titles
   were rendering at body size, which is why they looked oversized against everything else. */
QGroupBox {{
    background: {BG_ELEVATED};
    border: 1px solid {BORDER};
    border-top: 1px solid {HIGHLIGHT_TOP};
    border-radius: {RADIUS_CARD}px;
    margin-top: {SPACE[4]}px;
    padding: {SPACE[3]}px {SPACE[3]}px {SPACE[3]}px {SPACE[3]}px;
    font-weight: {WEIGHT_SEMIBOLD};
}}
/* The title reads as a heading: bold, uppercase-weight colour, letter-spaced -- the same treatment
   as `QLabel#CardHeader`, because a group box and a card are the same kind of container and were
   labelling themselves differently. `TEXT_PRIMARY` rather than secondary: at this size, semibold
   secondary grey was quieter than the body text underneath it, which is the wrong way round for a
   heading. */
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: {SPACE[2]}px;
    padding: 0 {SPACE[0]}px;
    color: {TEXT_PRIMARY};
    font-size: {FONT_SMALL}pt;
    font-weight: {WEIGHT_BOLD};
    letter-spacing: 0.7px;
}}

QScrollArea {{ background: transparent; border: none; }}
/* The scrolled widget, and why it needs naming.
   Styling a `QAbstractScrollArea` makes Qt set `autoFillBackground` on the widget inside its
   viewport, and that fill comes from the **palette**, not from this stylesheet. Measured: the
   shell Settings page rendered rgb(240,240,240) edge to edge -- the Windows default window
   colour -- with `page.form.autoFillBackground()` reporting True even though nothing here asked
   for it. `QAbstractScrollArea::viewport` below does not help, because `viewport` is not a real
   Qt sub-control and that rule has always been a no-op.
   Two selectors because the widget sits one level below the viewport, which is itself an
   unnamed `QWidget` child, and Qt has no portable name for it in a stylesheet. */
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 2px;
}}
/* Grey at rest, accent while in use.
   A scrollbar is furniture until you reach for it, at which point it is the thing you are
   manipulating -- so the accent is spent on the moment of interaction rather than paid permanently.
   `:pressed` is listed after `:hover` because Qt applies the later rule when both match, and a drag
   is always also a hover. */
QScrollBar::handle:vertical {{
    background: {BORDER_STRONG};
    border-radius: 5px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: {ACCENT}; }}
QScrollBar::handle:vertical:pressed {{ background: {ACCENT_PRESS}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    height: 0; background: none;
}}
QScrollBar:horizontal {{ height: 0; }}

QToolTip {{
    background: {BG_RAISED};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER_STRONG};
    border-radius: {RADIUS_CONTROL}px;
    padding: 6px 9px;
}}

/* Tables.
   `QAbstractScrollArea` and `QHeaderView` are styled explicitly, not just
   `QHeaderView::section`. Windows' native style paints the header *widget* background from the
   palette, which is light -- so styling only the sections leaves a white strip above and to the
   right of them, and a light band across the top of a table on a near-black card is the single
   most obvious "unfinished" tell in the whole interface. Same reason the viewport is named: an
   unstyled `QAbstractScrollArea` viewport shows the palette base colour through any gap. */
QTableView, QTreeView, QListView {{
    background: transparent;
    border: none;
    outline: none;
    gridline-color: transparent;
    alternate-background-color: {rgba(BG_HOVER, 0.35)};
}}
QAbstractScrollArea {{ background: transparent; }}
QAbstractScrollArea::viewport {{ background: transparent; }}
/* Horizontal padding only.
   Vertical padding on an item inside a fixed-height row does not add height -- it eats into the
   text's own rectangle, and the glyphs get clipped through the middle. That is what produced
   half-height text in the first column of every styled table. Row height is set on the vertical
   header instead (`shell.widgets.TABLE_ROW_HEIGHT`), which is the only thing that actually
   reserves space. */
QTableView::item, QTreeView::item, QListView::item {{
    padding: 0px 10px;
    border: none;
    border-bottom: 1px solid {rgba(BORDER, 0.55)};
}}
QTableView::item:hover, QTreeView::item:hover, QListView::item:hover {{
    background: {BG_HOVER};
}}
/* Selection.
   `selection-background-color` on the *view* is the one that actually wins, and the colour is
   pre-blended (`SELECTION_ROW`) rather than translucent. Measured: Qt paints the palette's
   `Highlight` role under a stylesheet `background`, and on Windows that role is the system blue
   when the view has focus and a near-white when it does not -- so a 20% orange wash composited
   onto it gave the pale blue-white row that got reported. `shell.widgets.style_table` also
   blanks the palette role, so neither path can reintroduce it.
   `:!active` is still listed because the shell's tables are `NoFocus` and therefore never
   "active" by Qt's reckoning. */
QTableView, QTreeView, QListView {{
    selection-background-color: {SELECTION_ROW};
    selection-color: {TEXT_PRIMARY};
}}
QTableView::item:selected, QTreeView::item:selected, QListView::item:selected,
QTableView::item:selected:active, QTableView::item:selected:!active,
QTreeView::item:selected:!active, QListView::item:selected:!active {{
    background: {SELECTION_ROW};
    color: {TEXT_PRIMARY};
}}
QTableView::item:selected:hover, QTreeView::item:selected:hover,
QListView::item:selected:hover {{
    background: {SELECTION_ROW_HOVER};
}}
QHeaderView {{
    background: transparent;
    border: none;
}}
/* Same rule as the items: horizontal padding only, and the height comes from
   `QHeaderView.setFixedHeight`. The header was 24px tall carrying 16px of vertical padding,
   which left 8px for 9pt uppercase text -- so every column title was sliced through the
   middle. */
QHeaderView::section {{
    background: {rgba(BG_SUNKEN, 0.55)};
    color: {TEXT_MUTED};
    border: none;
    border-bottom: 1px solid {BORDER_STRONG};
    padding: 0px 10px;
    font-size: {FONT_MICRO}pt;
    font-weight: {WEIGHT_SEMIBOLD};
    letter-spacing: 0.7px;
}}
QHeaderView::section:first {{ border-top-left-radius: {RADIUS_CONTROL}px; }}
QHeaderView::section:last  {{ border-top-right-radius: {RADIUS_CONTROL}px; }}
QTableCornerButton::section {{
    background: {rgba(BG_SUNKEN, 0.55)};
    border: none;
    border-bottom: 1px solid {BORDER_STRONG};
}}

/* Menus and popovers use the **warm** panel fill, not the cool `BG_RAISED` ramp.
   A context menu opened on the chat panel sat directly on top of it, and a cool-tinted menu on a
   warm-black panel read as two different blacks a centimetre apart. Menus in the shell sit over
   neutral surfaces where either would do, so warm is the one that works in both places. */
QMenu {{
    background: {panel_gradient()};
    border: 1px solid {BORDER_STRONG};
    border-top: 1px solid {SHEEN};
    border-radius: {RADIUS_CONTROL}px;
    padding: 5px;
}}
QMenu::item {{
    padding: 8px 16px 8px 14px;
    border-radius: 6px;
    color: {TEXT_PRIMARY};
}}
QMenu::item:selected {{ background: {ACCENT_WASH_STRONG}; color: {TEXT_PRIMARY}; }}
QMenu::item:disabled {{ color: {TEXT_DISABLED}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 8px; }}

/* Window chrome. Lives here rather than in `shell/titlebar.py` so the whole application
   shares one definition, and so the close button's red hover cannot drift from `DANGER`. */
/* Chrome: still essentially black, with a warm left edge.
   Flat neutral black next to an orange accent reads as absence rather than as a decision -- the
   brand vanishes from everything except the 5% of surface the accent occupies. `chrome_tint`
   fades out inside the first ~38%, so the warmth sits behind the logo and the nav items and the
   rest of the bar is the black it was.
   No bottom border here: the accent divider under the title bar is a real 1px widget
   (`AccentRule`, below), because Qt cannot put a gradient on a single border edge and
   `border-image` on one side is unreliable across styles. The chat panel draws its dividers the
   same way, so both surfaces divide their chrome identically. */
QFrame#TitleBar {{
    background: {chrome_tint()};
    border: none;
}}

/* The 1px accent-led divider, used under the shell's title bar and inside the chat panel. */
QFrame#AccentRule {{
    background: {accent_rule()};
    border: none;
}}
QLabel#WordMark {{
    font-size: {FONT_TITLE}pt;
    font-weight: {WEIGHT_SEMIBOLD};
    color: {TEXT_PRIMARY};
    letter-spacing: 0.3px;
}}
/* Window buttons were `TEXT_SECONDARY` on a transparent fill, which on a near-black title bar
   is close to invisible -- the first thing every reviewer said. Now: a visible resting chip,
   primary-colour glyphs, and a hover state per button rather than one shared grey. */
QPushButton#WindowButton, QPushButton#WindowButtonClose {{
    background: {rgba(BG_HOVER, 0.55)};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_CONTROL}px;
    color: {TEXT_PRIMARY};
    font-size: {FONT_SMALL}pt;
    font-weight: {WEIGHT_SEMIBOLD};
    padding: 0px;
}}
QPushButton#WindowButton:hover {{
    background: {BG_ACTIVE};
    border-color: {BORDER_STRONG};
}}
QPushButton#WindowButton:pressed {{ background: {BG_SUNKEN}; }}
QPushButton#WindowButtonClose:hover {{
    background: {DANGER};
    border-color: {DANGER};
    color: {TEXT_PRIMARY};
}}
QPushButton#WindowButtonClose:pressed {{ background: {rgba(DANGER, 0.75)}; }}

QFrame#Sidebar {{ background: {chrome_tint()}; }}
QFrame#NavMarker {{ background: {ACCENT}; border-radius: 2px; }}

/* The selected nav item gets the accent leading in from its left edge, matching the title bar's
   rule and the marker beside it, instead of a flat 10% wash across the whole row. */
QPushButton#NavItem:checked {{
    background: {gradient_h(ACCENT_WASH_STRONG, rgba(ACCENT, 0.04), mid=ACCENT_WASH,
                            mid_at=0.35)};
}}

/* The status chip in the sidebar footer. A bordered pill rather than a bullet and a caption:
   a lone coloured dot has to be learned, whereas a pill with a state colour and a word reads
   at a glance and survives being the only thing down there.

   The **same warm-cornered fill as the switch above it**, and grained by `GrainedFrame` for the same
   reason: these are the rail's two permanent components, and one textured chip beside one flat one
   read as an accident. It was a 55% translucent BG_SUNKEN tint over flat black, which is the kind of
   composite that has gone wrong repeatedly in this file; the gradient resolves it up front. */
QFrame#StatusChip {{
    background: {sidebar_switch_gradient()};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_CONTROL}px;
    padding: 2px;
}}

/* The sidebar's chat-panel switch. Sibling to the status chip above it, so the rail's two
   permanent facts -- what is captured, and whether the transcript shows itself -- look like a set.

   Opaque fills, not `rgba`, in both states. The rail paints `CHROME_FLAT` behind this and a
   translucent tint over flat black is the composite that has gone wrong repeatedly elsewhere in
   this file; `blend` resolves it here instead of at paint time. The `on` state is a 14% accent wash
   with an accent hairline, which is enough to read from the corner of the eye without competing
   with the nav rail's selected item. */
QFrame#SidebarSwitch {{
    background: {sidebar_switch_gradient()};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_CONTROL}px;
}}
QFrame#SidebarSwitch:hover {{
    background: {sidebar_switch_gradient(hover=True)};
    border-color: {BORDER_STRONG};
}}
QFrame#SidebarSwitch[on="true"] {{
    background: {sidebar_switch_gradient(on=True)};
    border: 1px solid {ACCENT_HAIR};
}}
QFrame#SidebarSwitch[on="true"]:hover {{
    background: {sidebar_switch_gradient(on=True, hover=True)};
}}
QLabel#SidebarSwitchLabel {{
    color: {TEXT_SECONDARY};
    font-size: {FONT_SMALL}pt;
    font-weight: {WEIGHT_MEDIUM};
}}
QFrame#SidebarSwitch[on="true"] QLabel#SidebarSwitchLabel {{ color: {TEXT_PRIMARY}; }}

/* A highlighted setting: one accent-edged panel for the capability worth noticing.

   Teaching mode was a single long checkbox label in a column of other checkboxes, with its
   explanation hidden in a tooltip -- the most capable thing in the Settings form described in the
   least prominent way available. The accent left edge is the same device the chat panel uses to
   mark a Nimbus turn, so it reads as "this one is different" without a colour nobody else uses. */
QFrame#FeatureRow {{
    background: {FEATURE_ROW};
    border: 1px solid {BORDER};
    border-left: 2px solid {ACCENT_HAIR};
    border-radius: {RADIUS_CONTROL}px;
}}
QCheckBox#FeatureToggle {{
    color: {TEXT_PRIMARY};
    font-size: {FONT_TITLE}pt;
    font-weight: {WEIGHT_SEMIBOLD};
}}
QLabel#FeatureBlurb {{
    color: {TEXT_SECONDARY};
    font-size: {FONT_SMALL}pt;
    padding-left: 24px;
}}
""".strip()


def _colorref(hex_colour: str) -> int:
    """``#RRGGBB`` to a Win32 ``COLORREF``, which is ``0x00BBGGRR`` -- byte order reversed.

    Getting this backwards is the classic Win32 colour bug and it fails silently: you get a
    plausible wrong colour rather than an error, so the blue and red channels swap and nobody
    notices until someone asks why the orange tint looks blue.
    """
    value = hex_colour.lstrip("#")
    red, green, blue = (int(value[index:index + 2], 16) for index in (0, 2, 4))
    return (blue << 16) | (green << 8) | red


def apply_dark_titlebar(widget, colour: str = TINT_EDGE) -> bool:
    """Make a native Windows title bar dark, and tinted where Windows allows it.

    ## Why this is needed at all

    Every window Nimbus draws itself is frameless, so this had never come up. The licence gate is a
    plain ``QDialog`` with the system frame, which means a **white** title bar with a white close
    button sitting on top of a near-black dialog -- and it is the first thing a new user sees.

    ## What actually works, per Windows version

    Two different DWM attributes, and only one of them is widely available:

    * ``DWMWA_USE_IMMERSIVE_DARK_MODE`` (20) makes the caption dark. Windows 10 build 19041 and
      later, so it works on the 19045 machines this is being tested on.
    * ``DWMWA_CAPTION_COLOR`` (35) sets an exact caption colour, which is what gives the warm
      near-black. **Windows 11 (build 22000) only** -- on Windows 10 the call simply fails, so it is
      attempted and its result ignored rather than depended on.

    So: dark everywhere, orange-tinted on Windows 11. Both are better than white, and pretending
    otherwise by hand-drawing a frame would mean reimplementing snap, drag and the close button.

    Returns whether the dark caption was accepted, for tests. Never raises: a title bar is not worth
    failing a launch over.
    """
    try:
        import ctypes
        from ctypes import wintypes

        handle = wintypes.HWND(int(widget.winId()))
        dwm = ctypes.windll.dwmapi
        dwm.DwmSetWindowAttribute.argtypes = [
            wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
        ]
        dwm.DwmSetWindowAttribute.restype = ctypes.c_long

        dark = ctypes.c_int(1)
        accepted = dwm.DwmSetWindowAttribute(
            handle, 20, ctypes.byref(dark), ctypes.sizeof(dark)) == 0

        # Windows 11 only. Ignored on 10, which is why `accepted` above is the return value.
        tint = ctypes.c_uint(_colorref(colour))
        dwm.DwmSetWindowAttribute(handle, 35, ctypes.byref(tint), ctypes.sizeof(tint))

        return accepted
    except Exception:
        return False
