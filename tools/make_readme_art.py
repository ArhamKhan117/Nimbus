"""Generate the README's hero logo and architecture diagrams, dark and light.

    python -m tools.make_readme_art            write every image
    python -m tools.make_readme_art --check    report what would change, write nothing

Output lands in ``assets/readme/`` and is committed, because GitHub renders a README from the
repository rather than from a build. Regenerate whenever the palette or the architecture moves.

## Why generate rather than draw

Two reasons, and the second is the one that matters.

**The palette is already a single source of truth.** ``theme.py`` holds every Nimbus colour, measured
against WCAG. An image drawn by hand in a design tool is a second copy of those values that nothing
checks, and the first thing to drift after a palette change. Here the diagram imports the same constants
the application imports, so a colour cannot disagree with the product.

**Text has to be measured, not estimated.** Every box below sizes itself from the *rendered* width and
height of its own content, wrapping at real font metrics, and the column gutters are sized from the
rendered width of the arrow labels that have to fit inside them. That is the whole reason these read
cleanly: nothing is positioned by eyeballing a magic number, so nothing overlaps when a label gets a
word longer. A fixed-height box with a hardcoded gap is exactly how a diagram ends up with clipped
descenders and two labels on top of each other.

The first draft of this file got that wrong in a way worth recording. ``measure()`` and ``_panel()``
were two copies of the same cursor arithmetic, and they used different padding: the unscaled constant
in one, the scaled one in the other. So every panel was measured 16 device pixels shorter than it drew
and its text column 32 pixels wider, which is overflowing text and clipped descenders. The fix was not
a test asserting the two agree. It was ``flow()``: one pass that returns every line's position, which
``measure()`` sums and ``_panel()`` draws. Two things that must agree should be one thing.

## Light and dark, and where each is used

**The hero is rendered twice and is transparent.** GitHub serves a different image per colour scheme
through ``<picture>`` and ``prefers-color-scheme``, so the hero is rendered from one layout pass with
the palette swapped, and everything outside its rounded card is transparent. That is what stops a
white rectangle appearing around it on a dark page.

**The three diagrams are rendered once, light, on an opaque white page.** They are dense: five to
seven panels of small type with borders, connectors and a tinted band. A diagram like that reads better
on white in both colour schemes than it does adapting to each, and a reader who follows a figure across
a theme switch should not have to re-learn it. So they deliberately do not follow the page.

``Palette`` still carries a dark scheme, and ``layout()`` is palette-independent, so rendering the
diagrams dark again is a one-line change in ``main`` rather than a rewrite.

## Sizing for GitHub

A README's content column is roughly 900 px wide, and an image wider than that is downscaled by the
browser -- which shrinks the text with it. So the nominal width here stays under that, and the crispness
comes from ``SCALE`` supersampling rather than from a large canvas. A 1600 px diagram would look sharp
opened on its own and illegible where it is actually read.
"""
from __future__ import annotations

import argparse
import colorsys
import sys
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import theme  # noqa: E402  -- after the path insert, deliberately

OUT_DIR = ROOT / "assets" / "readme"
MARK = ROOT / "assets" / "nimbus_mark_256.png"

SCALE = 2
"""Render at 2x and downsample. Pillow has no antialiased primitives, so a 1px border drawn directly
comes out either hard or blurred depending on where it lands relative to the pixel grid. Drawing double
size and reducing with LANCZOS gives clean edges everywhere for one multiply."""

FIGURE_WIDTH = 960
"""Nominal width of a figure, in the pixels GitHub will display. See "Sizing for GitHub" above."""

FONTS = {
    "regular": "C:/Windows/Fonts/segoeui.ttf",
    "semibold": "C:/Windows/Fonts/seguisb.ttf",
    "bold": "C:/Windows/Fonts/segoeuib.ttf",
    "mono": "C:/Windows/Fonts/CascadiaMono.ttf",
}


# --------------------------------------------------------------------------- palette


@dataclass(frozen=True)
class Palette:
    """Everything a figure needs to draw itself, in one object per colour scheme.

    Dark values come straight from ``theme.py``. The light scheme is derived rather than invented: the
    same cool tint, the same accent, inverted lightness. Keeping the accent recognisably the same
    across both is what makes the two renders read as one brand instead of two themes.
    """

    name: str
    page: str
    surface: str
    surface_alt: str
    border: str
    border_strong: str
    text: str
    text_secondary: str
    text_muted: str
    accent: str
    accent_text: str
    band: str
    """The highlighted-subtree band, **pre-composited into an opaque colour**.

    It was ``(255, 122, 26, 20)`` drawn straight onto the canvas, and that was a real defect rather
    than a style question. ``ImageDraw`` replaces the destination for shape primitives instead of
    compositing over it, even in ``"RGBA"`` mode, so a translucent fill does not tint the panel: it
    overwrites the panel's alpha with its own. Measured on the output, every pixel across the full
    width of the banded rows came back at **alpha 24**, meaning those rows of the card were punched
    almost transparent and GitHub's background would have shown through them.

    This is the same trap ``theme.py`` records four times over for Qt, where a translucent wash
    composites against whatever the viewport happened to paint. The answer there is the answer here:
    resolve the blend yourself and hand the drawing code one opaque colour.
    """
    rule: str


def readable_accent(accent: str, *surfaces: str) -> str:
    """The nearest accent to ``accent`` that clears WCAG AA for body text on **every** surface given.

    Hue and saturation are held and only value moves, in 2% steps, so the result is recognisably the
    same orange. It is *derived* from ``theme.ACCENT`` rather than picked beside it, which is the whole
    argument for generating these images: change the brand orange and this follows.

    Needed because the accent marks 9pt item labels, and 9pt is body text. ``ACCENT_DEEP`` (#D9600A)
    is the deepest orange in ``theme.py`` and still reaches only 3.58:1 on a near-white card.

    **Two corrections worth recording, because both were live bugs.**

    It took one surface. Then the highlighted subtree got a tinted band behind it, which is a second
    surface, and the colour derived for the plain card measured **4.12:1** on the band. A near miss
    caused by deriving against the wrong background, which is the same defect one argument up.

    It only ever searched *downward*. That is right on a light card and exactly wrong on a dark one,
    where darkening the ink reduces contrast: the loop would have walked the accent to near-black and
    then returned the original unchanged, reporting success by doing nothing. It now searches outward
    in both directions and returns the first passing candidate, which is the nearest one.
    """
    hue, saturation, value = colorsys.rgb_to_hsv(*[c / 255 for c in theme.parse_hex(accent)])

    def at(v: float) -> str:
        return "#%02X%02X%02X" % tuple(
            round(c * 255) for c in colorsys.hsv_to_rgb(hue, saturation, v))

    def passes(colour: str) -> bool:
        return all(theme.meets_aa(colour, surface) for surface in surfaces)

    if passes(accent):
        return accent
    for step in range(1, 46):
        for direction in (-1, 1):
            v = value + direction * step * 0.02
            if 0.0 <= v <= 1.0 and passes(at(v)):
                return at(v)
    return accent


# The two surfaces an accent label can land on in each scheme: the plain card, and the tinted band
# behind a highlighted subtree. Declared before the palettes because the accent is derived from both.
DARK_SURFACE = theme.BG_ELEVATED
DARK_BAND = theme.blend(theme.ACCENT, DARK_SURFACE, 0.08)
LIGHT_SURFACE = "#FAFAFB"
LIGHT_BAND = theme.blend(theme.ACCENT, LIGHT_SURFACE, 0.10)


DARK = Palette(
    name="dark",
    page=theme.BG_BASE,
    surface=DARK_SURFACE,
    surface_alt=theme.BG_SUNKEN,
    border=theme.BORDER,
    border_strong=theme.BORDER_STRONG,
    text=theme.TEXT_PRIMARY,
    text_secondary=theme.TEXT_SECONDARY,
    text_muted=theme.TEXT_MUTED,
    accent=theme.ACCENT,
    accent_text=readable_accent(theme.ACCENT, DARK_SURFACE, DARK_BAND),
    band=DARK_BAND,
    rule=theme.BORDER,
)

LIGHT = Palette(
    name="light",
    page="#FFFFFF",
    surface=LIGHT_SURFACE,
    surface_alt="#F2F2F4",
    border="#DFDFE4",
    border_strong="#B9B9C2",
    text="#131316",
    text_secondary="#43434E",
    text_muted="#5F5F6B",
    accent=theme.ACCENT_DEEP,                              # borders and dots: decorative
    accent_text=readable_accent(theme.ACCENT, LIGHT_SURFACE, LIGHT_BAND),
    band=LIGHT_BAND,
    rule="#E4E4E9",
)


def font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONTS[kind], size * SCALE)


def px(value: int) -> int:
    """A nominal length in device pixels. Every geometric constant goes through this."""
    return value * SCALE


def blank(palette: Palette, size: tuple[int, int]) -> Image.Image:
    """A fully transparent canvas, for the hero.

    The hero is transparent outside its rounded card, so it sits on GitHub's background rather than on
    a rectangle of ours that nearly matches it. In dark mode that near-match was a visible panel edge
    around the whole image; in light mode it was a white band.

    The first version of this filled the RGB channels with ``palette.page`` at alpha zero, on the
    theory that a LANCZOS reduction of straight alpha mixes the colour of invisible pixels into the
    fringe and produces a dark halo around every glyph. **Measured, that is not what Pillow 12 does.**
    An opaque white block on a black-transparent field and the same block on a white-transparent field
    both reduce to a boundary pixel of ``(255, 255, 255, 13)``, which only happens if the resample is
    premultiplied. Text drawn on transparency and halved keeps a fringe within three levels of its own
    ink colour. So the surround colour cannot reach the fringe, and setting it was a fix for a bug
    this version of Pillow does not have.

    ``palette`` stays in the signature because the caller reads better for it and because a future
    Pillow could regress this.
    """
    return Image.new("RGBA", size, (0, 0, 0, 0))


# --------------------------------------------------------------------------- text


def wrap(text: str, f: ImageFont.FreeTypeFont, max_width: int,
         draw: ImageDraw.ImageDraw) -> list[str]:
    """Wrap on real measured widths. An estimate here is what produces a clipped last word."""
    if not text:
        return []
    lines: list[str] = []
    words = text.split()
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if draw.textlength(candidate, font=f) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def line_height(f: ImageFont.FreeTypeFont) -> int:
    ascent, descent = f.getmetrics()
    return int((ascent + descent) * 1.32)


# --------------------------------------------------------------------------- model


@dataclass
class Item:
    """One entry inside a panel: a label, an optional note under it."""

    label: str
    note: str = ""
    mono: bool = False
    accent: bool = False


@dataclass
class Panel:
    title: str
    subtitle: str = ""
    items: list[Item] = field(default_factory=list)
    emphasis: bool = False
    height: int = 0          # every field below is computed by Figure.layout
    x: int = 0
    y: int = 0
    width: int = 0


# --------------------------------------------------------------------------- drawing


class Figure:
    """A canvas of measured panels. Layout runs once; both palettes reuse the same numbers."""

    PAD = 30             # canvas margin
    PANEL_PAD = 15       # panel interior
    ITEM_GAP = 10        # between items in a panel
    PANEL_GAP = 14       # between stacked panels in a column
    MIN_GUTTER = 26      # between columns, before arrow labels widen it
    BULLET_COLUMN = 13   # indent from the panel's text edge to an item's text

    def __init__(self, title: str, subtitle: str, width: int = FIGURE_WIDTH) -> None:
        self.title = title
        self.subtitle = subtitle
        self.width = width
        self.columns: list[list[Panel]] = []
        self.arrows: list[tuple[str, str, str]] = []
        self.gutter = px(self.MIN_GUTTER)
        self._probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))

    # -- one place for every font and every measurement ---------------------
    @property
    def f_title(self) -> ImageFont.FreeTypeFont:
        return font("semibold", 12)

    @property
    def f_sub(self) -> ImageFont.FreeTypeFont:
        return font("regular", 9)

    @property
    def f_item(self) -> ImageFont.FreeTypeFont:
        return font("semibold", 10)

    @property
    def f_note(self) -> ImageFont.FreeTypeFont:
        return font("regular", 9)

    @property
    def f_arrow(self) -> ImageFont.FreeTypeFont:
        return font("regular", 8)

    def pad(self) -> int:
        """The panel's interior padding, in device pixels. Measurement and rendering share this."""
        return px(self.PANEL_PAD)

    def text_width(self, panel_width: int) -> int:
        """The width available to a panel's title and subtitle."""
        return panel_width - self.pad() * 2

    def item_width(self, panel_width: int) -> int:
        """The width available to an item's label and note, inside the bullet indent."""
        return self.text_width(panel_width) - px(self.BULLET_COLUMN)

    # -- layout: one pass, consumed by both measurement and rendering -------
    def flow(self, panel: Panel, width: int) -> list[tuple[int, int, ImageFont.FreeTypeFont,
                                                           str, str, bool]]:
        """Every line in a panel as ``(dx, dy, font, text, role, bullet)``, relative to its padding.

        Measurement and rendering both read this list, so they cannot disagree about where a line
        sits or how tall the panel is. The first draft of this file had them as two copies of the
        same arithmetic that used different padding, which measured every panel 16 device pixels
        shorter than it drew. Sharing the walk is the fix; asserting the two agree would only have
        been a test for a duplication that did not need to exist.
        """
        rows: list[tuple[int, int, ImageFont.FreeTypeFont, str, str, bool]] = []
        indent = px(self.BULLET_COLUMN)
        y = 0

        rows.append((0, y, self.f_title, panel.title, "title", False))
        y += line_height(self.f_title)

        if panel.subtitle:
            y += px(2)
            for line in wrap(panel.subtitle, self.f_sub, self.text_width(width), self._probe):
                rows.append((0, y, self.f_sub, line, "subtitle", False))
                y += line_height(self.f_sub)

        if panel.items:
            y += px(9)

        for index, item in enumerate(panel.items):
            f_label = font("mono", 9) if item.mono else self.f_item
            role = "label-accent" if item.accent else "label"
            for first, line in enumerate(
                wrap(item.label, f_label, self.item_width(width), self._probe)
            ):
                rows.append((indent, y, f_label, line, role, first == 0))
                y += line_height(f_label)
            if item.note:
                y += px(1)
                for line in wrap(item.note, self.f_note, self.item_width(width), self._probe):
                    rows.append((indent, y, self.f_note, line, "note", False))
                    y += line_height(self.f_note)
            if index != len(panel.items) - 1:
                y += px(self.ITEM_GAP)

        return rows

    def measure(self, panel: Panel, width: int) -> int:
        """The exact height ``_panel`` will consume for this content at this width."""
        _, last_y, last_font, *_ = self.flow(panel, width)[-1]
        return self.pad() + last_y + line_height(last_font) + self.pad()

    def header_left(self) -> int:
        """Where the header's title and subtitle start: past the mark, which sits in the margin."""
        return self.PAD * SCALE + px(30) + px(12)

    def subtitle_width(self) -> int:
        return self.width * SCALE - self.header_left() - self.PAD * SCALE

    def header_height(self) -> int:
        """Measured, not assumed. A subtitle that wraps to two lines has to push the rule down."""
        lines = max(1, len(wrap(self.subtitle, self.f_sub, self.subtitle_width(), self._probe)))
        return (self.PAD * SCALE + line_height(font("bold", 18))
                + lines * line_height(self.f_sub) + px(20))

    def measure_gutter(self) -> int:
        """Widen the column gap until the widest arrow label fits with its shaft either side.

        The label sits on the connector, so the gutter has to hold the label plus a readable run of
        line on both sides. Measured from the rendered label, because "screen and question" is more
        than twice the width of "release" and a single constant cannot serve both.
        """
        needed = px(self.MIN_GUTTER)
        for _, _, label in self.arrows:
            if not label:
                continue
            width = self._probe.textlength(label, font=self.f_arrow)
            needed = max(needed, int(width) + px(26))
        return needed

    def layout(self) -> int:
        """Position every panel and return the canvas height, guaranteed even for the downsample."""
        self.gutter = self.measure_gutter()
        count = len(self.columns)
        usable = self.width * SCALE - self.PAD * SCALE * 2 - self.gutter * (count - 1)
        column_width = usable // count

        header = self.header_height()
        tallest = 0
        for index, column in enumerate(self.columns):
            x = self.PAD * SCALE + index * (column_width + self.gutter)
            y = header
            for panel in column:
                panel.x, panel.y, panel.width = x, y, column_width
                panel.height = self.measure(panel, column_width)
                y += panel.height + px(self.PANEL_GAP)
            tallest = max(tallest, y - px(self.PANEL_GAP))

        height = tallest + self.PAD * SCALE
        return height + (height % SCALE)   # even, or the resize scales x and y differently

    # -- rendering ----------------------------------------------------------
    def render(self, palette: Palette, height: int) -> Image.Image:
        # Opaque, unlike the hero. See "Light and dark, and where each is used" at the top of the
        # file: these are rendered light on white and used in both colour schemes.
        image = Image.new("RGB", (self.width * SCALE, height), palette.page)
        draw = ImageDraw.Draw(image, "RGBA")

        self._header(draw, image, palette)
        for column in self.columns:
            for panel in column:
                self._panel(draw, panel, palette)
        self._arrows(draw, palette)

        return image.resize((self.width, height // SCALE), Image.LANCZOS)

    def _header(self, draw: ImageDraw.ImageDraw, image: Image.Image,
                palette: Palette) -> None:
        f_h1 = font("bold", 18)
        left = self.PAD * SCALE
        top = self.PAD * SCALE

        # The mark sits top-left with the title beside it, not under it, so the first line of the
        # figure is brand and subject together.
        mark_size = px(30)
        mark = Image.open(MARK).convert("RGBA").resize((mark_size, mark_size), Image.LANCZOS)
        image.paste(mark, (left, top), mark)
        text_left = self.header_left()

        draw.text((text_left, top - px(2)), self.title, font=f_h1, fill=palette.text)
        y = top - px(2) + line_height(f_h1)
        for line in wrap(self.subtitle, self.f_sub, self.subtitle_width(), draw):
            draw.text((text_left, y), line, font=self.f_sub, fill=palette.text_muted)
            y += line_height(self.f_sub)

        rule_y = self.header_height() - px(11)
        draw.line([(left, rule_y), (self.width * SCALE - left, rule_y)],
                  fill=palette.rule, width=SCALE)

    def _panel(self, draw: ImageDraw.ImageDraw, panel: Panel, palette: Palette) -> None:
        draw.rounded_rectangle(
            (panel.x, panel.y, panel.x + panel.width, panel.y + panel.height),
            radius=px(theme.RADIUS_CARD),
            fill=palette.surface,
            outline=palette.accent if panel.emphasis else palette.border,
            width=SCALE * (2 if panel.emphasis else 1),
        )

        origin_x = panel.x + self.pad()
        origin_y = panel.y + self.pad()
        colours = {
            "title": palette.accent_text if panel.emphasis else palette.text,
            "subtitle": palette.text_muted,
            "label": palette.text,
            "label-accent": palette.accent_text,
            "note": palette.text_secondary,
        }

        for dx, dy, f, text, role, bullet in self.flow(panel, panel.width):
            y = origin_y + dy
            if bullet:
                dot = y + line_height(f) // 2 - px(2)
                draw.ellipse([origin_x + px(2), dot, origin_x + px(6), dot + px(4)],
                             fill=palette.accent)
            draw.text((origin_x + dx, y), text, font=f, fill=colours[role])

    def _arrows(self, draw: ImageDraw.ImageDraw, palette: Palette) -> None:
        """Connectors between adjacent columns, at the vertical centre of the panel they leave.

        The label sits *above* the line rather than on it. It used to sit on the line with a
        page-coloured rectangle knocked out underneath so the connector did not strike through it,
        which worked only while the canvas was opaque. On a transparent canvas that rectangle is an
        opaque box floating in the gutter, visible against any background but ours. Putting the label
        above the line needs no knockout at all, and the connector stays unbroken.
        """
        lookup = {panel.title: panel for column in self.columns for panel in column}
        f = self.f_arrow
        for source, target, label in self.arrows:
            a, b = lookup[source], lookup[target]
            y = a.y + a.height // 2
            x1, x2 = a.x + a.width, b.x
            draw.line([(x1 + px(4), y), (x2 - px(7), y)], fill=palette.border_strong, width=SCALE)
            draw.polygon(
                [(x2 - px(7), y - px(4)), (x2 - px(1), y), (x2 - px(7), y + px(4))],
                fill=palette.border_strong,
            )
            if not label:
                continue
            width = int(draw.textlength(label, font=f))
            mid = (x1 + x2) // 2
            draw.text((mid - width // 2, y - line_height(f) - px(2)), label, font=f,
                      fill=palette.text_muted)


# --------------------------------------------------------------------------- tree


@dataclass
class Row:
    """One line of a file tree: how deep, what it is called, and what it does."""

    depth: int
    name: str
    note: str = ""
    directory: bool = False
    highlight: bool = False
    """Draw a faint accent band behind this row. Contiguous highlighted rows share one band."""


class TreeFigure(Figure):
    """A file tree drawn with real connector lines rather than box-drawing characters.

    The reason this is not a fenced code block full of ``|--`` and ``` `-- ``` is not decoration.

    Those glyphs come from the reader's monospace font, and a font that lacks them substitutes,
    which is how an ASCII tree arrives on someone's machine as a column of empty rectangles. The
    same class of bug ``theme.py`` records for ``\\u2b1c``. Lines that are actually lines cannot
    be substituted, and they let the descriptions sit in an aligned second column, which a code
    block cannot do without padding every row by hand to the width of the longest name.

    The name column is measured from the widest rendered name at its own indent, so adding a
    deeply nested entry moves the description column rather than colliding with it.
    """

    INDENT = 16      # per depth level
    STUB = 8         # length of the horizontal tick into a name
    NAME_GAP = 20    # between the widest name and the description column
    ROW_GAP = 6      # between rows

    def __init__(self, title: str, subtitle: str, width: int = FIGURE_WIDTH) -> None:
        super().__init__(title, subtitle, width)
        self.rows: list[Row] = []

    @property
    def f_name(self) -> ImageFont.FreeTypeFont:
        return font("mono", 9)

    # -- geometry -----------------------------------------------------------
    def panel_box(self) -> tuple[int, int]:
        left = self.PAD * SCALE
        return left, self.width * SCALE - left

    def name_column(self) -> int:
        """Width of the name column, measured from the widest name at its own indent."""
        return max(
            px(self.INDENT) * row.depth + int(self._probe.textlength(row.name, font=self.f_name))
            for row in self.rows
        ) + px(self.NAME_GAP)

    def note_width(self) -> int:
        left, right = self.panel_box()
        return right - left - self.pad() * 2 - self.name_column()

    def row_height(self, row: Row) -> int:
        note_lines = len(wrap(row.note, self.f_note, self.note_width(), self._probe))
        return max(line_height(self.f_name), note_lines * line_height(self.f_note))

    def last_child(self, index: int) -> bool:
        """True when no later row is a sibling of this one, which is what closes an elbow."""
        depth = self.rows[index].depth
        for row in self.rows[index + 1:]:
            if row.depth < depth:
                return True
            if row.depth == depth:
                return False
        return True

    def continues(self, index: int, depth: int) -> bool:
        """True when the ancestor at ``depth`` has another child below this row."""
        for row in self.rows[index + 1:]:
            if row.depth < depth:
                return False
            if row.depth == depth:
                return True
        return False

    def layout(self) -> int:
        height = (self.header_height() + self.pad() * 2
                  + sum(self.row_height(r) for r in self.rows)
                  + px(self.ROW_GAP) * (len(self.rows) - 1)
                  + self.PAD * SCALE)
        return height + (height % SCALE)

    # -- rendering ----------------------------------------------------------
    def render(self, palette: Palette, height: int) -> Image.Image:
        image = Image.new("RGB", (self.width * SCALE, height), palette.page)
        draw = ImageDraw.Draw(image, "RGBA")
        self._header(draw, image, palette)

        left, right = self.panel_box()
        top = self.header_height()
        draw.rounded_rectangle((left, top, right, height - self.PAD * SCALE),
                              radius=px(theme.RADIUS_CARD), fill=palette.surface,
                              outline=palette.border, width=SCALE)

        origin_x = left + self.pad()
        note_x = origin_x + self.name_column()
        y = top + self.pad()

        # Bands first, so every connector and glyph lands on top of them.
        band_start: int | None = None
        cursor = y
        for index, row in enumerate(self.rows):
            tall = self.row_height(row)
            if row.highlight and band_start is None:
                band_start = cursor
            if band_start is not None and (not row.highlight or index == len(self.rows) - 1):
                end = cursor + (tall if row.highlight else 0)
                draw.rounded_rectangle(
                    (left + self.pad() // 2, band_start - px(3),
                     right - self.pad() // 2, end + px(3)),
                    radius=px(6), fill=palette.band,
                )
                band_start = None
            cursor += tall + px(self.ROW_GAP)

        for index, row in enumerate(self.rows):
            tall = self.row_height(row)
            mid = y + line_height(self.f_name) // 2

            if row.depth:
                # The elbow for this row, plus a pass-through line for every ancestor that still
                # has children below. This is what makes a deep entry read as belonging to its
                # parent rather than floating.
                stub_x = origin_x + px(self.INDENT) * row.depth - px(self.STUB)
                draw.line([(stub_x, y - px(self.ROW_GAP)), (stub_x, mid)],
                          fill=palette.border_strong, width=SCALE)
                draw.line([(stub_x, mid), (stub_x + px(self.STUB) - px(3), mid)],
                          fill=palette.border_strong, width=SCALE)
                if not self.last_child(index):
                    draw.line([(stub_x, mid), (stub_x, y + tall + px(self.ROW_GAP))],
                              fill=palette.border_strong, width=SCALE)
                for depth in range(1, row.depth):
                    if self.continues(index, depth):
                        through = origin_x + px(self.INDENT) * depth - px(self.STUB)
                        draw.line([(through, y - px(self.ROW_GAP)),
                                   (through, y + tall + px(self.ROW_GAP))],
                                  fill=palette.border_strong, width=SCALE)

            draw.text((origin_x + px(self.INDENT) * row.depth, y), row.name, font=self.f_name,
                      fill=palette.accent_text if row.directory else palette.text)

            note_y = y + (line_height(self.f_name) - line_height(self.f_note)) // 2
            for line in wrap(row.note, self.f_note, self.note_width(), draw):
                draw.text((note_x, note_y), line, font=self.f_note, fill=palette.text_secondary)
                note_y += line_height(self.f_note)

            y += tall + px(self.ROW_GAP)

        return image.resize((self.width, height // SCALE), Image.LANCZOS)


def figure_tree() -> TreeFigure:
    fig = TreeFigure(
        "Every file that matters",
        "Directories in orange. The Kiro directory is banded because it is the part of this "
        "repository that is unusual.",
    )
    fig.rows = [
        Row(0, "Nimbus/", "the desktop application, its backend, and the specs that directed both",
            directory=True),
        Row(1, "app.py", "the pipeline: hotkey, speech, screen, model, then voice and pointer"),
        Row(1, "ai.py", "provider abstraction (BYOK). create_ai_client routes by model id"),
        Row(1, "gemini_native.py", "the default path: structured geometry, thinking budgets, "
                                   "context caching"),
        Row(1, "locator.py", "two-stage grid fallback for providers that cannot return coordinates"),
        Row(1, "capture.py", "screen, cursor, DPI and resolution picking. Three coordinate spaces"),
        Row(1, "overlay.py", "per-monitor, click-through pointer and teaching annotations"),
        Row(1, "annotations.py", "the teaching-annotation tag grammar: box, arrow, label, step"),
        Row(1, "privacy.py", "Privacy Guard. Refuses to screenshot a password manager, and counts it"),
        Row(1, "kb.py", "your own docs per application, treated as authoritative for that program"),
        Row(1, "memory.py", "per-app memory. Plain Markdown you can edit, with a SQLite index"),
        Row(1, "review.py", "the Knowledge Journal: spaced repetition over what you were taught"),
        Row(1, "licensing.py", "Ed25519, verified offline. Seven day trial, fourteen day grace"),
        Row(1, "theme.py", "the design system. Every colour measured against WCAG, not chosen"),
        Row(1, "chat_hud.py", "the chat panel, excluded from screen capture at the OS level"),
        Row(1, "shell/", "the windowed application", directory=True),
        Row(2, "window.py", "the frameless main window and the pages it hosts"),
        Row(2, "nav.py", "the sidebar and the always-visible status footer"),
        Row(2, "titlebar.py", "drag, minimise, maximise, close, done by hand"),
        Row(2, "widgets.py", "design-system primitives, shared by every page"),
        Row(1, "web/", "Next.js 15 on Vercel, Postgres behind it", directory=True),
        Row(2, "src/app/api/", "23 routes: accounts, trial, activate, refresh, deactivate, health",
            directory=True),
        Row(2, "src/lib/licence.ts", "the security core. node:crypto only, zero dependencies"),
        Row(2, "prisma/schema.prisma", "accounts, licences, devices, payments"),
        Row(1, "service/", "an earlier Python licence service, superseded by web/ and kept honest",
            directory=True),
        Row(1, "tests/", "2,030 tests, and the interesting ones guard intent rather than behaviour",
            directory=True),
        Row(1, "tools/", "build_release, verify_bundle, make_icons, make_readme_art",
            directory=True),
        Row(1, "installer/", "Inno Setup, producing Nimbus-Windows-Setup.exe", directory=True),
        Row(1, ".kiro/", "how this was directed. Committed, not gitignored",
            directory=True, highlight=True),
        Row(2, "specs/", "9 features, each requirements + design + tasks + .config.kiro",
            directory=True, highlight=True),
        Row(3, "voice-screen-pipeline/", "15 requirements. The spec that shaped the other eight",
            directory=True, highlight=True),
        Row(2, "steering/", "8 always-on files: invariants, refusals, conventions",
            directory=True, highlight=True),
        Row(2, "hooks/", "16 agent hooks, mostly guards against a decision being reversed",
            directory=True, highlight=True),
        Row(2, "settings/mcp.json", "4 MCP servers. The three that touch credentials are off",
            highlight=True),
    ]
    return fig


# --------------------------------------------------------------------------- hero


def hero(palette: Palette) -> Image.Image:
    """The README's opening card: mark, wordmark, one line. Rounded, bordered, faint gradient.

    **The card is the edge of the image, and outside it is transparent.**

    The first version painted ``palette.page`` around the card and shipped it as an opaque PNG. Read
    on GitHub that is a white rectangle with a rounded rectangle inside it: the corners of the file
    are visible, and the light render sits in a white box that does not match the page it is on. A
    transparent surround is the only version that works in both themes, because whatever GitHub puts
    behind the image becomes the surround.

    The remaining inset is two nominal pixels, just enough for the border stroke to be drawn and
    antialiased rather than clipped at the canvas edge. It is not margin.
    """
    width, height = px(960), px(232)
    inset = px(2)
    # Minus one, not minus SCALE: PIL's rectangle includes its far coordinate, so subtracting the
    # scale factor leaves one more device pixel outside on the right and bottom than on the left and
    # top. Measured as a 3px frame against a 2px one, which is exactly the kind of near-miss that
    # never gets noticed and always looks slightly wrong.
    card = (inset, inset, width - inset - 1, height - inset - 1)
    radius = px(22)

    # A vertical gradient, light at the top, because that is where light comes from. Three points of
    # lightness at most: anything wider reads as a gradient rather than as a surface.
    gradient = Image.new("RGB", (width, height), palette.surface)
    fill = ImageDraw.Draw(gradient)
    top = tuple(int(palette.surface[i:i + 2], 16) for i in (1, 3, 5))
    bottom = tuple(int(palette.page[i:i + 2], 16) for i in (1, 3, 5))
    for row in range(height):
        t = row / max(1, height - 1)
        fill.line([(0, row), (width, row)],
                  fill=tuple(int(top[c] + (bottom[c] - top[c]) * t) for c in range(3)))

    # The rounded rectangle is an alpha mask, not a crop. Everything outside it stays at alpha 0, so
    # the corners of the PNG are transparent and the shape of the file is the shape of the card.
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle(card, radius=radius, fill=255)

    image = blank(palette, (width, height))
    image.paste(gradient, (0, 0), mask)
    draw = ImageDraw.Draw(image, "RGBA")
    draw.rounded_rectangle(card, radius=radius, outline=palette.border_strong, width=SCALE)

    f_word = font("bold", 60)
    f_tag = font("regular", 14)
    word = "Nimbus"
    tag = "Ask about anything on your screen. Out loud."

    # Lay the hero out from measured ink, not from bounding boxes.
    #
    # The mark is a 256px square with 24px of transparent padding above and below its artwork, and a
    # glyph's box is taller than its letterforms. Centring the boxes therefore does not centre what
    # anyone can see: the first version of this looked 23px bottom-light because the mark's padding
    # pushed its visible top down while the accent rule ended exactly on its own edge. So every
    # position below comes from an alpha bbox or a textbbox.
    # Sized so the artwork stands a little taller than the wordmark's capitals, which is what makes
    # a mark-plus-word lockup read as one object rather than two things placed near each other.
    mark_size = px(124)
    source = Image.open(MARK).convert("RGBA")
    art = source.getchannel("A").getbbox()
    mark = source.resize((mark_size, mark_size), Image.LANCZOS)
    scale = mark_size / source.height
    art_top = round(art[1] * scale)
    art_left = round(art[0] * scale)
    art_height = round((art[3] - art[1]) * scale)
    art_width = round((art[2] - art[0]) * scale)

    word_box = draw.textbbox((0, 0), word, font=f_word)
    tag_box = draw.textbbox((0, 0), tag, font=f_tag)
    rule_width, rule_height = px(46), px(3)

    gap = px(22)                                        # mark artwork to wordmark
    tag_gap = px(18)                                    # wordmark row to tagline
    rule_gap = px(20)                                   # tagline to accent rule

    block_height = (art_height + tag_gap + (tag_box[3] - tag_box[1]) + rule_gap + rule_height)
    interior = height - inset * 2
    art_y = inset + (interior - block_height) // 2      # top of the visible artwork

    block_width = art_width + gap + (word_box[2] - word_box[0])
    art_x = (width - block_width) // 2

    image.paste(mark, (art_x - art_left, art_y - art_top), mark)
    # The wordmark's ink centre lines up with the artwork's, which is what "beside" means optically.
    draw.text((art_x + art_width + gap - word_box[0],
               art_y + art_height // 2 - (word_box[1] + word_box[3]) // 2),
              word, font=f_word, fill=palette.text)

    tag_y = art_y + art_height + tag_gap
    draw.text(((width - (tag_box[2] - tag_box[0])) // 2 - tag_box[0], tag_y - tag_box[1]),
              tag, font=f_tag, fill=palette.text_muted)

    rule_y = tag_y + (tag_box[3] - tag_box[1]) + rule_gap
    draw.rounded_rectangle(
        [(width - rule_width) // 2, rule_y, (width + rule_width) // 2, rule_y + rule_height],
        radius=px(2), fill=palette.accent,
    )
    return image.resize((width // SCALE, height // SCALE), Image.LANCZOS)


# --------------------------------------------------------------------------- figures


def figure_pipeline() -> Figure:
    """One turn, left to right. Three stages, because three is what fits a README's width legibly."""
    fig = Figure(
        "How one turn works",
        "Hold the chord, ask out loud, release. The stages overlap on purpose: the answer starts "
        "before the model has finished.",
    )
    fig.columns = [
        [
            Panel("Hold, and speak", "Everything that can start early, starts now.", [
                Item("Global hotkey, observe-only",
                     "suppress=False, so no keystroke is swallowed from the system."),
                Item("Screen capture starts",
                     "Every monitor, on a background thread."),
                Item("Per-app memory recalled",
                     "Plain Markdown, keyed on the foreground executable."),
                Item("Chime after a grace window",
                     "Armed early: the first chime pays a 400 ms audio cold start."),
            ]),
            Panel("Privacy Guard", "One choke point, not four call sites.", [
                Item("Password manager in front? No screenshot",
                     "The answer still comes, voice only, and the refusal is counted."),
            ]),
        ],
        [
            Panel("Two calls, one instant", "The architecture a measurement forced.", [
                Item("generate(tools=None)", mono=True, accent=True,
                     note="No tools declared, so prose is forced. Streams straight into speech."),
                Item("generate(tools=[point_at])", mono=True, accent=True,
                     note="Tools only, thinking budget zero, on its own thread."),
                Item("T = max(speech, geometry)", mono=True,
                     note="Gemini returns prose or a function call in a turn, never both. One "
                          "request had to become two."),
            ], emphasis=True),
            Panel("Geometry is a contract", "Not a convention.", [
                Item("point_at(y, x, label)", mono=True,
                     note="Typed integers, y first, normalised to 0-1000. Not a [POINT:x,y] tag "
                          "parsed back out of prose."),
            ]),
            Panel("Capture, once, correctly", "55 ms that cannot be deleted.", [
                Item("Hide, DwmFlush, grab, show",
                     "Nimbus is never in its own screenshot. Capture exclusion fails on a "
                     "layered window, and the overlay must be layered to be translucent."),
            ]),
        ],
        [
            Panel("Out to the user", "First word inside 1.5 seconds.", [
                Item("Speech flushes per sentence",
                     "At [.!?] plus whitespace, so talking starts on sentence one."),
                Item("The pointer flies, then dwells",
                     "Quadratic Bezier, a 3 s hold, then back to following the mouse."),
                Item("Teaching mode draws",
                     "A box, the surround dimmed, numbered steps, an arrow from mistake to fix."),
                Item("Escape cancels at 11 checkpoints",
                     "No pointer, no annotation, no memory write after a cancellation."),
            ]),
            Panel("What it will not do", "Recorded as a decision, not a gap.", [
                Item("It never clicks for you",
                     "The hand on the mouse stays the learner's own. That is where the learning "
                     "gets stored."),
            ]),
        ],
    ]
    fig.arrows = [
        ("Hold, and speak", "Two calls, one instant", "on release"),
        ("Two calls, one instant", "Out to the user", "stream"),
    ]
    return fig


def figure_repository() -> Figure:
    fig = Figure(
        "What is in the repository",
        "A desktop application, the backend it talks to, and the Kiro directory that directed both. "
        "All three are committed.",
    )
    fig.columns = [
        [
            Panel("Desktop app", "Python 3.13, PyQt6, Win32.", [
                Item("app.py", mono=True,
                     note="The pipeline, the overlay, the tray, the licence gate."),
                Item("ai.py, gemini_native.py", mono=True,
                     note="Provider abstraction, then the default path: structured geometry, "
                          "thinking budgets, context caching."),
                Item("overlay.py", mono=True,
                     note="Per-monitor, click-through, DPI-aware pointer and annotations."),
                Item("privacy.py", mono=True,
                     note="Refuses to screenshot a password manager or a sign-in page."),
                Item("licensing.py", mono=True,
                     note="Ed25519 verified offline. 7-day trial, 14-day grace."),
                Item("shell/, chat_hud.py", mono=True,
                     note="The window, and a chat panel excluded from screen capture at the OS "
                          "level."),
            ]),
        ],
        [
            Panel("Backend", "Next.js 15 on Vercel, Postgres.", [
                Item("23 API routes, 8 pages", accent=True,
                     note="Accounts, the trial, activation, refresh, deactivate, health."),
                Item("licence.ts", mono=True,
                     note="The security core. node:crypto only, zero dependencies, sorted keys, "
                          "no whitespace."),
                Item("Both payment rails wired, neither connected",
                     note="The code paths are covered by tests. Nothing is charged."),
            ]),
            Panel("Tests", "2,030 desktop, 27 service, 23 web.", [
                Item("The interesting ones guard intent", accent=True,
                     note="No emoji in the UI. No shell module imports crypto. No private key in "
                          "the repository. A base prompt is only ever appended to."),
            ]),
        ],
        [
            Panel(".kiro/ , the whole point", "Spec-driven development, committed.", [
                Item("9 specs", accent=True,
                     note="requirements, design, tasks and .config.kiro each. 131 requirements in "
                          "EARS form, 236 correctness properties, every reference resolving both "
                          "ways."),
                Item("16 agent hooks", accent=True,
                     note="Mostly guards. They do not check that code works, they check that a "
                          "decision has not been quietly reversed."),
                Item("8 steering files", accent=True,
                     note="Invariants, refusals, conventions. Loaded into every session so a "
                          "settled question is not re-decided."),
                Item("4 MCP servers", accent=True,
                     note="fetch enabled for live provider docs. The three that touch credentials "
                          "are off by default."),
            ], emphasis=True),
        ],
    ]
    return fig


# --------------------------------------------------------------------------- main


def write(image: Image.Image, name: str, check: bool) -> None:
    path = OUT_DIR / name
    if check:
        print(f"  {'would update' if path.is_file() else 'would create':12}  {name}")
        return
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG", optimize=True)
    print(f"  {name:24} {image.size[0]:>5} x {image.size[1]:<5} "
          f"{path.stat().st_size // 1024:>4} KB")


def audit(palette: Palette) -> list[str]:
    """Every text role, measured against every surface it can be drawn on. AA or it is a defect.

    Permanent rather than a throwaway check, for the same reason ``tests/test_theme.py`` pins the
    application's ratios: this file derives colours, and a derived colour can be wrong. Both real
    failures here were caught by measuring and would have been invisible in review. A palette change
    that makes a label unreadable now fails the regeneration instead of shipping.
    """
    problems = []
    for surface_name, surface in (("card", palette.surface), ("band", palette.band)):
        for role in ("text", "text_secondary", "text_muted", "accent_text"):
            colour = getattr(palette, role)
            ratio = theme.contrast_ratio(colour, surface)
            mark = "ok  " if ratio >= 4.5 else "FAIL"
            line = (f"  {mark}  {palette.name:5} {role:15} {colour} on the {surface_name} "
                    f"{surface}  {ratio:5.2f}:1")
            print(line)
            if ratio < 4.5:
                problems.append(line.strip())
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="report what would change and write nothing")
    args = parser.parse_args(argv)

    if not MARK.is_file():
        print(f"ERROR: {MARK} is missing. Run `python -m tools.make_icons` first.")
        return 2
    for kind, path in FONTS.items():
        if not Path(path).is_file():
            print(f"ERROR: the {kind} font is missing at {path}.")
            return 2

    print("contrast")
    problems = audit(DARK) + audit(LIGHT)
    if problems:
        print(f"\nERROR: {len(problems)} colour pairs fail WCAG AA. Nothing written.")
        return 1

    print("hero")
    for palette in (DARK, LIGHT):
        write(hero(palette), f"hero-{palette.name}.png", args.check)

    # One render each, LIGHT, opaque. No colour-scheme suffix in the name, because there is no pair to
    # tell apart and a file called `pipeline-light.png` with no dark sibling invites someone to go
    # looking for one.
    for builder, stem in ((figure_pipeline, "pipeline"), (figure_repository, "repository"),
                          (figure_tree, "tree")):
        print(stem)
        figure = builder()
        write(figure.render(LIGHT, figure.layout()), f"{stem}.png", args.check)

    if args.check:
        print("\n--check: nothing written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
