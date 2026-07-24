"""Geometry helpers: ring parsing and watertightness checking.

Pure numeric helpers with no dependency on the CityGML element model, so they
can be unit-tested in isolation.
"""

from collections import defaultdict

Point = tuple[float, float, float]


def parse_pos_list(text: str) -> list[Point]:
    """Parse a gml:posList ('x y z x y z ...') into a list of 3D points."""
    if not text:
        return []
    vals = text.split()
    pts: list[Point] = []
    for i in range(0, len(vals) - 2, 3):
        pts.append((float(vals[i]), float(vals[i + 1]), float(vals[i + 2])))
    return pts


def _edges(ring: list[Point], precision: int):
    """Yield undirected, rounded edges of one ring (closing vertex dropped)."""
    loop = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
    n = len(loop)
    for i in range(n):
        a = tuple(round(c, precision) for c in loop[i])
        b = tuple(round(c, precision) for c in loop[(i + 1) % n])
        yield (a, b) if a <= b else (b, a)


def is_closed_shell(rings, precision: int = 3) -> bool:
    """True if the shell is a closed 2-manifold.

    A shell is closed when every undirected edge is shared by exactly two
    faces. `rings` is an iterable of exterior rings, each a list of 3D points.
    Coordinates are rounded to `precision` decimals so shared vertices from
    independently listed polygons match.
    """
    counts: dict = defaultdict(int)
    faces = 0
    for ring in rings:
        if len(ring) < 4:  # need a triangle plus the closing vertex
            continue
        faces += 1
        for edge in _edges(ring, precision):
            counts[edge] += 1
    if faces == 0:
        return False
    return all(v == 2 for v in counts.values())


def non_manifold_edges(rings, precision: int = 3) -> int:
    """Number of edges not shared by exactly two faces (0 means watertight)."""
    counts: dict = defaultdict(int)
    for ring in rings:
        if len(ring) < 4:
            continue
        for edge in _edges(ring, precision):
            counts[edge] += 1
    return sum(1 for v in counts.values() if v != 2)


def extrude_prism(footprint, z_base: float, z_top: float) -> list[list[Point]]:
    """Extrude a 2D footprint into a closed prism with outward-facing normals.

    `footprint` is a closed ring of (x, y) pairs (first point repeated last).
    Returns a list of exterior rings (bottom, top, then one quad per edge). The
    footprint is reoriented to counter-clockwise (seen from above) so the top
    face normal points up, the bottom down and the sides outward. The result is
    a watertight 2-manifold by construction.
    """
    ring = footprint[:-1] if len(footprint) > 1 and footprint[0] == footprint[-1] else footprint
    n = len(ring)
    # Signed area (shoelace); negative means clockwise seen from above.
    area = sum(ring[i][0] * ring[(i + 1) % n][1] - ring[(i + 1) % n][0] * ring[i][1]
               for i in range(n))
    if area < 0:
        ring = ring[::-1]
    n = len(ring)

    top = [(x, y, z_top) for (x, y) in ring]          # CCW -> normal up
    top.append(top[0])
    bottom = [(x, y, z_base) for (x, y) in reversed(ring)]  # CW -> normal down
    bottom.append(bottom[0])
    faces = [bottom, top]
    for i in range(n):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % n]
        faces.append([
            (x0, y0, z_base), (x1, y1, z_base),
            (x1, y1, z_top), (x0, y0, z_top), (x0, y0, z_base),
        ])
    return faces


def shell_stats(rings, precision: int = 3) -> dict:
    """Closedness diagnostics for one shell.

    Returns a dict with the number of faces, the number of boundary edges
    (used once), the number of over-shared edges (used more than twice) and a
    `closed` flag that is True only for a watertight 2-manifold.
    """
    counts: dict = defaultdict(int)
    faces = 0
    for ring in rings:
        if len(ring) < 4:
            continue
        faces += 1
        for edge in _edges(ring, precision):
            counts[edge] += 1
    boundary = sum(1 for v in counts.values() if v == 1)
    over = sum(1 for v in counts.values() if v > 2)
    return {
        "faces": faces,
        "boundary_edges": boundary,
        "over_shared_edges": over,
        "closed": faces > 0 and boundary == 0 and over == 0,
    }
