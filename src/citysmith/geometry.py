"""Geometry helpers: ring parsing and watertightness checking.

Pure numeric helpers with no dependency on the CityGML element model, so they
can be unit-tested in isolation. Deliberately dependency-free (no numpy,
shapely, etc.), matching the project's pure-Python-plus-lxml architecture.
"""

from collections import defaultdict

Point = tuple[float, float, float]


def _sub(a: Point, b: Point) -> Point:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a: Point, b: Point) -> Point:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a: Point, s: float) -> Point:
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a: Point, b: Point) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Point, b: Point) -> Point:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _normalize(a: Point) -> Point:
    n = _dot(a, a) ** 0.5
    return _scale(a, 1.0 / n) if n > 1e-9 else (0.0, 0.0, 0.0)


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


def _ring_normal_centroid(ring):
    """One ring's own (Newell's method) unit normal and centroid, or
    (None, centroid) if the ring is degenerate (fewer than 3 points, or the
    points don't define a plane, e.g. collinear)."""
    loop = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
    n = len(loop)
    if n < 3:
        return None, None
    centroid = _scale(tuple(sum(p[i] for p in loop) for i in range(3)), 1.0 / n)
    normal = (0.0, 0.0, 0.0)
    for i in range(n):
        a, b = loop[i], loop[(i + 1) % n]
        normal = _add(normal, _cross(_sub(a, centroid), _sub(b, centroid)))
    normal = _normalize(normal)
    return (normal if normal != (0.0, 0.0, 0.0) else None), centroid


def cluster_coplanar_rings(rings, angle_tol: float = 0.05, dist_tol: float = 0.5):
    """Group ring indices into clusters that lie on one shared plane: same
    normal direction (dot product within `angle_tol` of 1.0) and the same
    perpendicular offset (their planes no more than `dist_tol` metres apart).

    This exists because a single thematic surface (e.g. one WallSurface
    `boundedBy`) can legitimately bundle either several small panels of the
    *same* physical face (the "missing panel = window gap" pattern this
    project's LOD2 merge targets) or several genuinely different faces
    altogether (e.g. a building's whole perimeter under one WallSurface, a
    common CityGRID convention, exactly how this project's own test fixture
    is built). Only the first case should ever be merged into one surface;
    clustering by plane first is what tells them apart, rather than
    guessing from polygon count alone.

    Connected-components (union-find), not greedy first-fit. Two rings join
    the same cluster when their normals are parallel *and* the perpendicular
    distance between their planes is within `dist_tol`; membership is then
    transitive. This replaced an earlier greedy approach that compared every
    candidate ring against the single *first* ring that happened to start a
    cluster, using a distance tolerance of 5% of the surface's overall 3D
    bounding-box extent. That was wrong on real data in two compounding
    ways. First, the scale: a wall's 3D extent is dominated by building
    height (~18 m here), so 5% of it is ~0.9 m, which silently swallowed
    genuine ~1.2-1.5 m facade setbacks (a recessed panel) into the same
    cluster as the flat wall around them. Second, the order dependence:
    because coplanarity was tested only against the cluster's fixed first
    ring rather than a shared criterion, whether a borderline panel joined
    depended on iteration order. The net effect was that a flat wall's
    panels got bundled together with a recessed panel from a *different*,
    parallel plane; `union_coplanar_polygons` then correctly refused to
    merge that mixed cluster (its points aren't coplanar), and the whole
    region fell back to the raw panel mosaic, including the panels that
    *were* coplanar and should have merged. Comparing planes pairwise at an
    absolute metric tolerance (perpendicular offset, independent of building
    height) separates the setback into its own cluster and lets the flat
    wall's panels merge. `dist_tol` is deliberately absolute (metres):
    real-world CityGML is in a projected CRS, panel-to-panel modelling noise
    within one flat face is at most a few decimetres, and a real
    architectural recess/step is at least ~0.5 m, so 0.5 m cleanly separates
    the two while staying well clear of both.

    Degenerate rings (no well-defined normal) each get their own singleton
    cluster, never merged with anything. Returns a list of index-lists into
    `rings`, ordered by their smallest member index for determinism.
    """
    infos = [_ring_normal_centroid(r) for r in rings]
    n = len(rings)
    parent = list(range(n))

    def find(x):
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:  # path compression
            parent[x], x = root, parent[x]
        return root

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for i in range(n):
        ni, ci = infos[i]
        if ni is None:
            continue
        for j in range(i + 1, n):
            nj, cj = infos[j]
            if nj is None:
                continue
            if _dot(ni, nj) <= (1 - angle_tol):
                continue
            # Perpendicular gap between the two planes, checked against both
            # normals so one ring's noisy normal can't force a false join.
            offset = max(abs(_dot(_sub(cj, ci), ni)), abs(_dot(_sub(ci, cj), nj)))
            if offset <= dist_tol:
                union(i, j)

    groups: dict = {}
    for i in range(n):
        key = i if infos[i][0] is None else find(i)
        groups.setdefault(key, []).append(i)
    return [groups[k] for k in sorted(groups, key=lambda k: min(groups[k]))]


def _point_in_polygon_2d(pt, loop) -> bool:
    """Even-odd ray cast. `loop` is a list of (x, y), not closed. Points
    exactly on the boundary are undefined, which is fine here: outer and hole
    loops from edge cancellation are vertex-disjoint, so a vertex of one loop
    never lands on the edge of another."""
    x, y = pt
    inside = False
    n = len(loop)
    j = n - 1
    for i in range(n):
        xi, yi = loop[i]
        xj, yj = loop[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def union_coplanar_polygons(rings, snap: int = 3):
    """Merge a cluster of coplanar polygon rings into their true outer
    boundary by cancelling every edge shared by two adjacent panels.

    `rings` is a flat list of rings (lists of (x, y, z) points, first point
    optionally repeated last); pass every ring of every polygon in the
    cluster, exterior *and* interior. Which polygon a ring came from does not
    matter: an edge walked by two panels appears twice and cancels, an edge
    on the true outline appears once and survives. The surviving edges are
    traced into closed loops; loops nested inside an odd number of others are
    holes (window/door gaps left as missing panels, or real cut interior
    rings) and are dropped, so the result is a clean filled simplified
    surface. That is exactly what an LOD2 surface should be for this source.

    Unlike a convex hull this follows concavities faithfully and adds no
    diagonals, so an L-shaped or stepped wall keeps its real outline.

    Returns a list of closed 3D outer rings (usually one), or None when the
    merge is unsafe and the caller should fall back to the untouched
    per-polygon path:
      * fewer than 3 points, or no well-defined plane (degenerate);
      * the rings aren't actually coplanar (several genuinely different faces
        bundled under one thematic surface, a legitimate CityGRID
        convention: fitting one plane through them would be silently wrong);
      * the panels meet at a T-junction (a vertex mid-edge), where edge
        cancellation would leave a broken outline.
    """
    loops = [r[:-1] if len(r) > 1 and r[0] == r[-1] else r for r in rings]
    loops = [lp for lp in loops if len(lp) >= 3]
    all_points = [p for lp in loops for p in lp]
    if len(all_points) < 3:
        return None

    centroid = _scale(
        tuple(sum(p[i] for p in all_points) for i in range(3)), 1.0 / len(all_points)
    )
    normal = (0.0, 0.0, 0.0)
    for lp in loops:
        n = len(lp)
        for i in range(n):
            normal = _add(normal, _cross(_sub(lp[i], centroid), _sub(lp[(i + 1) % n], centroid)))
    normal = _normalize(normal)
    if normal == (0.0, 0.0, 0.0):
        return None

    extent = max(
        (max(p[i] for p in all_points) - min(p[i] for p in all_points) for i in range(3)),
        default=0.0,
    )
    if extent > 1e-9:
        if max(abs(_dot(_sub(p, centroid), normal)) for p in all_points) > 0.05 * extent:
            return None

    arbitrary = (1.0, 0.0, 0.0) if abs(normal[0]) < 0.9 else (0.0, 1.0, 0.0)
    u = _normalize(_cross(normal, arbitrary))
    v = _cross(normal, u)

    def to2d(p):
        return (round(_dot(_sub(p, centroid), u), snap), round(_dot(_sub(p, centroid), v), snap))

    # Undirected edge multiset across every ring; shared panel edges cancel,
    # leaving exactly the outline (outer boundary plus any hole boundaries).
    edge_count: dict = defaultdict(int)
    for lp in loops:
        pts = [to2d(p) for p in lp]
        n = len(pts)
        for i in range(n):
            a, b = pts[i], pts[(i + 1) % n]
            if a == b:
                continue
            edge_count[(a, b) if a <= b else (b, a)] += 1
    boundary = [e for e, c in edge_count.items() if c % 2 == 1]
    if len(boundary) < 3:
        return None

    verts = {p for e in boundary for p in e}
    # T-junction guard: a vertex strictly interior to a surviving edge means
    # panels don't meet edge-to-edge, so cancellation is unreliable. Bail.
    for (a, b) in boundary:
        ax, ay = a
        bx, by = b
        for p in verts:
            if p == a or p == b:
                continue
            px, py = p
            if abs((bx - ax) * (py - ay) - (by - ay) * (px - ax)) > 1e-6:
                continue
            dot = (px - ax) * (bx - ax) + (py - ay) * (by - ay)
            length2 = (bx - ax) ** 2 + (by - ay) ** 2
            if 1e-9 < dot < length2 - 1e-9:
                return None

    adj: dict = defaultdict(list)
    for (a, b) in boundary:
        adj[a].append(b)
        adj[b].append(a)

    # Trace surviving edges into closed loops.
    used = set()
    traced = []
    for (sa, sb) in boundary:
        key0 = (sa, sb)
        if key0 in used:
            continue
        loop = [sa]
        prev, cur = sa, sb
        used.add(key0)
        used.add((sb, sa))
        ok = True
        while cur != sa:
            loop.append(cur)
            nxt = None
            for cand in adj[cur]:
                if cand == prev:
                    continue
                if (cur, cand) not in used:
                    nxt = cand
                    break
            if nxt is None:
                ok = False
                break
            used.add((cur, nxt))
            used.add((nxt, cur))
            prev, cur = cur, nxt
        if ok and len(loop) >= 3:
            traced.append(loop)

    if not traced:
        return None

    # Even-odd nesting: a loop inside an odd number of others is a hole; drop
    # it. Loops are vertex-disjoint, so loop[0] is a safe containment probe.
    outer = []
    for i, loop in enumerate(traced):
        rep = loop[0]
        depth = sum(1 for j, other in enumerate(traced)
                    if j != i and _point_in_polygon_2d(rep, other))
        if depth % 2 == 0:
            outer.append(loop)
    if not outer:
        return None

    result = []
    for loop in outer:
        ring3d = [_add(centroid, _add(_scale(u, x), _scale(v, y))) for x, y in loop]
        ring3d.append(ring3d[0])
        result.append(ring3d)
    return result


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
