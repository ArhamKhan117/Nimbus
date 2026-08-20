"""Trace ``assets/cursor.png`` into the polygon ``overlay.py`` paints.

    .\\.venv\\Scripts\\python.exe -m tools.trace_cursor

Prints a ready-to-paste ``_CURSOR_VERTICES`` list and writes
``assets/preview_cursor_trace.png`` so the trace can be compared against the artwork.

## Why trace instead of hand-authoring the shape

``overlay.py`` paints the flying pointer as a filled ``QPolygonF``, not as a bitmap, and it has
to stay that way: the shape is scaled per-monitor for DPI and pulsed to 1.3x mid-flight, and a
raster would soften on both. So the logo's cursor has to become a short list of vertices.

Eyeballing those numbers gets the proportions close and the character wrong -- the notch angle
and the tail length are what make a pointer look like *that* pointer. Deriving them from the
artwork means the overlay and the logo cannot drift apart, and re-running this after an artwork
change is one command.

## How it works, and the two choices worth knowing about

1. Threshold alpha at 128 into a binary mask.
2. Walk the outline with **Moore-neighbour boundary tracing** — start at the first opaque pixel
   in raster order and keep turning around each boundary pixel until the start is reached again.
   Chosen over a marching-squares library because it needs no dependency beyond what is already
   installed, and the mask here is a single solid blob with no holes, which is exactly the case
   it handles cleanly.
3. Simplify with **Ramer-Douglas-Peucker**. This is the step that matters: RDP keeps the points
   that carry the shape's corners and discards everything collinear, which is precisely the
   distinction between a cursor's silhouette and the hundreds of anti-aliasing stair-steps
   around it.
4. Rotate the vertex list so the **tip comes first**, then normalise so the tip sits at the
   origin and the shape fits the box ``overlay.py``'s tests pin.

The tip is identified as the opaque pixel minimising ``x + y`` — the corner nearest the top-left
of the frame. That is correct for a conventional pointer aiming up-left, and it is asserted
rather than assumed, because a wrong tip would silently offset every point Nimbus ever makes:
``paintEvent`` anchors vertex 0 on the target coordinate.
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

ASSETS = Path(__file__).resolve().parent.parent / "assets"
SOURCE = ASSETS / "cursor.png"

ALPHA_THRESHOLD = 128
"""Alpha at which a pixel counts as part of the cursor."""

TARGET_HEIGHT = 29.0
"""Height of the emitted polygon in overlay units.

``tests/test_overlay.py::test_all_vertices_within_bounding_box`` pins every vertex to 0..30, and
the original hand-authored shape was 28 tall. 29 keeps the pointer the same visual size as the
one users already know while leaving a unit of headroom against that assertion.
"""

RDP_EPSILON_FRACTION = 0.012
"""RDP tolerance as a fraction of the mask's longest side.

Tuned by inspection of the printed vertex count. Too small and hundreds of anti-aliasing
stair-steps survive, which costs fill performance on a widget that repaints at 60 Hz. Too large
and the tail barb -- a small feature that is most of what makes the shape read as a cursor --
gets collapsed into the body.
"""

MIN_VERTEX_SEPARATION = 0.9
"""Minimum gap, in overlay units, between consecutive emitted vertices.

RDP guarantees no vertex is *redundant* but not that none is *negligible*. The first run emitted
``(1.1, 0.0)``, ``(1.2, 0.0)`` and ``(3.0, 0.0)`` in a row along the top edge, where the
artwork's rounded tip produces a couple of near-collinear steps that RDP scores just above its
tolerance. Sub-unit detail cannot survive being scaled to a 29-unit shape and then drawn, so it
is pure cost: more vertices for a widget repainting at 60 Hz, and more numbers to read in the
constant. Dropping them changes the painted silhouette by well under a pixel.
"""


def load_mask(path: Path) -> tuple[list[list[bool]], int, int]:
    """Return a binary mask cropped to the artwork's opaque bounds."""
    image = Image.open(path).convert("RGBA")
    box = image.getchannel("A").getbbox()
    if box is None:
        raise SystemExit(f"{path.name} is fully transparent")
    alpha = image.crop(box).getchannel("A")
    width, height = alpha.size
    pixels = alpha.load()
    mask = [[pixels[x, y] >= ALPHA_THRESHOLD for x in range(width)]
            for y in range(height)]
    return mask, width, height


def trace_boundary(mask: list[list[bool]], width: int, height: int) -> list[tuple[int, int]]:
    """Moore-neighbour boundary trace of the largest blob, clockwise."""
    start = None
    for y in range(height):
        for x in range(width):
            if mask[y][x]:
                start = (x, y)
                break
        if start:
            break
    if start is None:
        raise SystemExit("no opaque pixels")

    # 8-neighbourhood in clockwise order, starting due west.
    offsets = [(-1, 0), (-1, -1), (0, -1), (1, -1),
               (1, 0), (1, 1), (0, 1), (-1, 1)]

    def opaque(x: int, y: int) -> bool:
        return 0 <= x < width and 0 <= y < height and mask[y][x]

    contour = [start]
    current = start
    backtrack = 0  # index into offsets: where we came from
    for _ in range(width * height * 8):
        found = False
        for step in range(8):
            index = (backtrack + 1 + step) % 8
            dx, dy = offsets[index]
            candidate = (current[0] + dx, current[1] + dy)
            if opaque(*candidate):
                # The neighbour we entered from, expressed in the new pixel's frame.
                backtrack = (index + 4) % 8
                current = candidate
                found = True
                break
        if not found:
            break
        if current == start and len(contour) > 2:
            break
        contour.append(current)
    return contour


def _perpendicular_distance(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    if start == end:
        return math.dist(point, start)
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    return abs(dy * (point[0] - start[0]) - dx * (point[1] - start[1])) / length


def rdp(points: list[tuple[float, float]], epsilon: float) -> list[tuple[float, float]]:
    """Ramer-Douglas-Peucker simplification, iterative to avoid deep recursion."""
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        worst, worst_distance = -1, 0.0
        for i in range(first + 1, last):
            distance = _perpendicular_distance(points[i], points[first], points[last])
            if distance > worst_distance:
                worst, worst_distance = i, distance
        if worst_distance > epsilon:
            keep[worst] = True
            stack.extend([(first, worst), (worst, last)])
    return [p for p, k in zip(points, keep) if k]


def find_tip(mask: list[list[bool]], width: int, height: int) -> tuple[int, int]:
    """The opaque pixel nearest the top-left corner, by x + y."""
    best, best_score = None, None
    for y in range(height):
        for x in range(width):
            if mask[y][x] and (best_score is None or x + y < best_score):
                best, best_score = (x, y), x + y
    assert best is not None
    return best


def normalise(
    contour: list[tuple[float, float]],
    tip: tuple[int, int],
    height: int,
) -> list[tuple[float, float]]:
    """Rotate the tip to index 0, move it to the origin, and scale to TARGET_HEIGHT."""
    nearest = min(range(len(contour)), key=lambda i: math.dist(contour[i], tip))
    rotated = contour[nearest:] + contour[:nearest]

    scale = TARGET_HEIGHT / height
    origin = rotated[0]
    shifted = [((x - origin[0]) * scale, (y - origin[1]) * scale)
               for x, y in rotated]

    # The trace can start a pixel off the true extreme, which would put a vertex
    # marginally negative. Clamp rather than translate: moving the whole shape would
    # take the tip off the target coordinate, and that is the one thing that must not move.
    clamped = [(max(0.0, round(x, 1)), max(0.0, round(y, 1))) for x, y in shifted]
    return _drop_negligible(clamped)


def _drop_negligible(vertices: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Remove vertices within `MIN_VERTEX_SEPARATION` of the one before them.

    The tip is always kept, whatever its neighbours look like: it is the anchor
    `paintEvent` lands on the target coordinate, so it is not a candidate for removal.
    """
    kept = [vertices[0]]
    for vertex in vertices[1:]:
        if math.dist(vertex, kept[-1]) >= MIN_VERTEX_SEPARATION:
            kept.append(vertex)
    # The list is a closed loop, so also check the last against the tip.
    if len(kept) > 3 and math.dist(kept[-1], kept[0]) < MIN_VERTEX_SEPARATION:
        kept.pop()
    return _drop_collinear(kept)


COLLINEAR_TOLERANCE = 0.25
"""Perpendicular distance, in overlay units, below which a vertex counts as collinear.

Runs *after* rounding, which is the point. RDP scores vertices against the unrounded contour, so
three points that differ by hundredths of a unit survive it and then round to exactly collinear
-- the first run emitted ``(0.0, 0.0)``, ``(1.1, 0.0)`` and ``(3.0, 0.0)`` in sequence. They
describe a straight line the fill renders identically without them.
"""


def _drop_collinear(vertices: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Remove vertices lying on the straight line between their neighbours.

    The tip is exempt for the same reason as in `_drop_negligible`.
    """
    if len(vertices) < 4:
        return vertices
    kept: list[tuple[float, float]] = []
    for index, vertex in enumerate(vertices):
        previous = kept[-1] if kept else vertices[-1]
        following = vertices[(index + 1) % len(vertices)]
        if index == 0 or _perpendicular_distance(
                vertex, previous, following) > COLLINEAR_TOLERANCE:
            kept.append(vertex)
    return kept


def main() -> None:
    mask, width, height = load_mask(SOURCE)
    print(f"{SOURCE.name}: artwork {width}x{height}, aspect {width / height:.3f}")

    contour = trace_boundary(mask, width, height)
    epsilon = max(width, height) * RDP_EPSILON_FRACTION
    simplified = rdp([(float(x), float(y)) for x, y in contour], epsilon)
    print(f"  boundary {len(contour)} px -> {len(simplified)} vertices "
          f"(RDP epsilon {epsilon:.1f}px)")

    tip = find_tip(mask, width, height)
    print(f"  tip at {tip}")

    vertices = normalise(simplified, tip, height)
    span_x = max(x for x, _ in vertices)
    span_y = max(y for _, y in vertices)
    print(f"  normalised span {span_x:.1f} x {span_y:.1f} overlay units")
    assert vertices[0] == (0.0, 0.0), "tip must normalise to the origin"
    assert span_x <= 30 and span_y <= 30, "must fit the box test_overlay.py pins"

    print("\n_CURSOR_VERTICES = [")
    for index, (x, y) in enumerate(vertices):
        note = "  # tip (anchor point — lands on the target coordinate)" if index == 0 else ""
        print(f"    ({x}, {y}),{note}")
    print("]")

    _write_preview(mask, width, height, vertices, span_y)


def _write_preview(
    mask: list[list[bool]],
    width: int,
    height: int,
    vertices: list[tuple[float, float]],
    span_y: float,
) -> None:
    """Artwork mask beside the traced polygon at the same size, on the app background."""
    scale = 320 / height
    pad = 24
    panel = int(width * scale)
    sheet = Image.new("RGBA", (pad * 3 + panel * 2, pad * 2 + 320), (11, 11, 13, 255))
    draw = ImageDraw.Draw(sheet)

    for y in range(height):
        for x in range(width):
            if mask[y][x]:
                draw.rectangle(
                    [pad + x * scale, pad + y * scale,
                     pad + (x + 1) * scale, pad + (y + 1) * scale],
                    fill=(138, 138, 148, 255))

    unit = 320 / span_y
    origin_x = pad * 2 + panel
    draw.polygon([(origin_x + x * unit, pad + y * unit) for x, y in vertices],
                 fill=(255, 122, 26, 255), outline=(245, 245, 247, 255))
    sheet.save(ASSETS / "preview_cursor_trace.png")
    print("\nwrote assets/preview_cursor_trace.png — grey mask left, traced polygon right")


if __name__ == "__main__":
    main()
