# -*- coding: utf-8 -*-
"""Pure-Python space-planning algorithm for the Generate Layout tool.

No Revit API / IronPython-specific calls here - this module is deliberately
kept independent of the host so its logic can be sanity-tested standalone
(plain `python`/`uv run`) before being wired into Revit drawing code.

Not a code-compliance engine. Corridor width and egress-distance checks are
rule-of-thumb design aids only - always verify against the applicable code
and consult a code reviewer / AHJ before relying on any generated layout.
"""

import random


def expand_program(program):
    """program: list of {name, target_area, min_width, qty, category,
    adjacency}. category is optional and falls back to name. adjacency is
    optional and normalizes to "window"/"entry"/"core"/"" (blank/
    unrecognized text silently falls back to "" - never raises). Returns a
    flat list of {name, category, target_area, min_width, adjacency}
    expanded by qty, largest-area first (stable base ordering; callers may
    re-sort for variation)."""
    items = []
    for row in program:
        qty = int(row.get("qty", 1) or 1)
        category = row.get("category") or row["name"]
        adjacency = (row.get("adjacency") or "").strip().lower()
        if adjacency not in ("window", "entry", "core"):
            adjacency = ""
        for i in range(qty):
            label = row["name"] if qty == 1 else "{} {}".format(row["name"], i + 1)
            items.append({
                "name": label,
                "category": category,
                "target_area": float(row["target_area"]),
                "min_width": float(row.get("min_width") or 0.0),
                "adjacency": adjacency,
            })
    items.sort(key=lambda it: it["target_area"], reverse=True)
    return items


PALETTE = [
    (156, 204, 232),  # light blue
    (252, 197, 143),  # light orange
    (168, 216, 168),  # light green
    (232, 168, 200),  # light pink
    (216, 196, 168),  # tan
    (200, 180, 232),  # light purple
    (232, 220, 140),  # light yellow
    (180, 220, 220),  # light teal
]


def assign_colors(categories):
    """categories: iterable of category name strings. Returns
    {category: (r, g, b)}, cycling the palette if there are more
    categories than colors."""
    unique = sorted(set(categories))
    return {cat: PALETTE[i % len(PALETTE)] for i, cat in enumerate(unique)}


def subtract_rect(region, obstacle):
    """region, obstacle: (x, y, w, h). Returns the list of up to 4
    axis-aligned rectangles that make up region minus obstacle (or
    [region] unchanged if they don't overlap)."""
    rx, ry, rw, rh = region
    ox, oy, ow, oh = obstacle

    ix0, iy0 = max(rx, ox), max(ry, oy)
    ix1, iy1 = min(rx + rw, ox + ow), min(ry + rh, oy + oh)
    if ix0 >= ix1 - 1e-9 or iy0 >= iy1 - 1e-9:
        return [region]

    pieces = []
    if ix0 - rx > 1e-9:
        pieces.append((rx, ry, ix0 - rx, rh))
    if (rx + rw) - ix1 > 1e-9:
        pieces.append((ix1, ry, (rx + rw) - ix1, rh))
    if iy0 - ry > 1e-9:
        pieces.append((ix0, ry, ix1 - ix0, iy0 - ry))
    if (ry + rh) - iy1 > 1e-9:
        pieces.append((ix0, iy1, ix1 - ix0, (ry + rh) - iy1))
    return pieces


def _bboxes_touch_or_overlap(a, b, tolerance):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (
        ax - tolerance > bx + bw or bx - tolerance > ax + aw
        or ay - tolerance > by + bh or by - tolerance > ay + ah
    )


def _union_bbox(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x0, y0 = min(ax, bx), min(ay, by)
    x1, y1 = max(ax + aw, bx + bw), max(ay + ah, by + bh)
    return (x0, y0, x1 - x0, y1 - y0)


MAX_MERGE_LOOSENESS = 3.0  # a merged bbox may be at most this many times the sum
                           # of its members' own areas - see merge_obstacles


def merge_obstacles(obstacles, tolerance=1.0, max_looseness=MAX_MERGE_LOOSENESS):
    """obstacles: list of (x,y,w,h). Groups obstacles into connected
    components by PAIRWISE proximity between each obstacle's own original
    bbox (touching/overlapping within `tolerance` feet) - not by repeatedly
    growing a running union rect. Users often select many individual
    elements (e.g. stair treads/stringers) that together form one logical
    "core" - without grouping, subtracting each one separately fragments
    the free area into far more pieces than the obstacle actually
    represents.

    Real-world testing found a serious failure mode in an earlier version
    of this function that grew a running "cur" bbox and re-tested THAT
    (already-expanded) rect against the next candidate: a chain of only
    loosely-connected, spatially scattered obstacles (e.g. several existing
    walls picked as "must stay" across a large floor, each pair merely
    within `tolerance` of some other pair) could all get swept into ONE
    bbox, even though the first and last in the chain were nowhere near
    each other - on a real project this produced a single false "obstacle"
    covering 61% of the boundary from just two picked elements.

    Grouping by pairwise proximity between ORIGINAL bboxes (via union-find)
    fixes the growing-bbox contamination, but a tight real cluster (many
    stair-tread pieces packed close together) and a loose scattered chain
    can still both end up as ONE connected component if any pairwise link
    exists anywhere in the chain - the difference is how TIGHTLY packed the
    resulting group actually is. So each component's union bbox is only
    used if it's tight: union bbox area no more than `max_looseness` times
    the SUM of its members' own areas (a real stair core: many small
    pieces packed within a modest footprint, high fill ratio; a scattered
    wall chain: mostly-empty floor space between members, low fill ratio -
    the observed real-world case measured ~19x). A component that fails
    this check is returned as its individual, UNMERGED original rects
    instead of one misleading giant bbox - understating obstacle footprint
    (several correct, separate obstacles) is far safer for a design aid
    than wildly overstating it."""
    n = len(obstacles)
    if n == 0:
        return []

    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    for i in range(n):
        for j in range(i + 1, n):
            if _bboxes_touch_or_overlap(obstacles[i], obstacles[j], tolerance):
                union(i, j)

    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(obstacles[i])

    merged = []
    for members in groups.values():
        if len(members) == 1:
            merged.append(members[0])
            continue
        union_bbox = members[0]
        for m in members[1:]:
            union_bbox = _union_bbox(union_bbox, m)
        union_area = union_bbox[2] * union_bbox[3]
        sum_area = sum(m[2] * m[3] for m in members)
        if sum_area > 1e-9 and union_area <= sum_area * max_looseness:
            merged.append(union_bbox)
        else:
            merged.extend(members)  # too loose to merge safely - keep separate
    return merged


def subtract_obstacles(boundary, obstacles):
    """boundary: (x,y,w,h). obstacles: list of (x,y,w,h). Iteratively
    subtracts each obstacle from the current set of free regions. Always
    produces an exact, non-overlapping cover of boundary-minus-all-obstacles
    (order-independent for correctness), though it can fragment into more
    pieces than a "maximal rectangle" decomposition would - acceptable for
    a heuristic design aid."""
    regions = [boundary]
    for obs in obstacles:
        regions = [piece for r in regions for piece in subtract_rect(r, obs)]
    return regions


def slice_rect(rect, items):
    """rect: (x, y, w, h) in feet. items: list of {name, target_area, min_width}.

    Cuts along the rect's longer axis. Each item gets its "natural" size
    (target_area / other_dim, floored at min_width). If the natural sizes
    undershoot the available axis length, the remainder is reported as an
    explicit leftover_rect rather than force-inflating the last room. If
    they overshoot, items are scaled down proportionally (dropping the
    smallest-target violators first) until everyone fits or is unplaced.

    Returns (placed, unplaced, leftover_rect). placed: list of
    {name, x, y, w, h, area}. unplaced: list of items that couldn't fit
    even at min_width. leftover_rect: {x,y,w,h} or None.
    """
    x, y, w, h = rect
    horizontal = w >= h
    axis_dim = w if horizontal else h
    other_dim = h if horizontal else w

    if other_dim <= 1e-6 or not items:
        return [], list(items), None

    remaining = [(it, max(it["target_area"] / other_dim, it.get("min_width") or 0.0)) for it in items]
    unplaced = []

    while remaining:
        total = sum(size for _, size in remaining)
        if total <= axis_dim + 1e-6:
            break
        scale = axis_dim / total if total > 1e-9 else 0.0
        violations = [
            (it, size) for it, size in remaining
            if size * scale < (it.get("min_width") or 0.0) - 1e-6
        ]
        if not violations:
            remaining = [(it, size * scale) for it, size in remaining]
            break
        drop_it = min(violations, key=lambda pair: pair[0]["target_area"])[0]
        remaining = [(it, size) for it, size in remaining if it is not drop_it]
        unplaced.append(drop_it)

    placed = []
    cursor = 0.0
    for it, size in remaining:
        if horizontal:
            placed.append({
                "name": it["name"], "category": it["category"],
                "x": x + cursor, "y": y, "w": size, "h": h, "area": size * h,
            })
        else:
            placed.append({
                "name": it["name"], "category": it["category"],
                "x": x, "y": y + cursor, "w": w, "h": size, "area": w * size,
            })
        cursor += size

    leftover_rect = None
    leftover_len = axis_dim - cursor
    if leftover_len > 1e-6:
        if horizontal:
            leftover_rect = {"x": x + cursor, "y": y, "w": leftover_len, "h": h}
        else:
            leftover_rect = {"x": x, "y": y + cursor, "w": w, "h": leftover_len}

    return placed, unplaced, leftover_rect


def _resolve_enclosed_size(min_v, max_v, rng):
    """Resolves one width or depth value for expand_enclosed_program.
    Defensively swaps if max_v < min_v (a reversed/typo'd range). rng=None
    -> deterministic midpoint (equals min_v exactly when max_v == min_v -
    exact division by 2.0, no rounding - i.e. today's unranged-row
    behavior, bit-identical). rng given -> rng.uniform(min_v, max_v)
    (also exact min_v when unranged, since uniform(a, a) == a)."""
    if max_v < min_v:
        min_v, max_v = max_v, min_v
    if rng is None:
        return (min_v + max_v) / 2.0
    return rng.uniform(min_v, max_v)


def parse_size_range(text):
    """Parses a Width/Depth DataGrid cell. "10" -> exact size
    (10.0, 10.0). "10-14" (whitespace around the dash tolerated, e.g.
    "10 - 14") -> a range, returned low-to-high regardless of typed order
    (so "14-10" also yields (10.0, 14.0) - defensive against a reversed
    typo). Returns None for anything else (blank, non-numeric, zero/
    negative, more than one dash) - never raises, matching this
    codebase's tolerant DataGrid-cell-parsing convention; callers should
    skip the row on None, same as any other malformed cell."""
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None
    parts = [p.strip() for p in s.split("-")]
    try:
        if len(parts) == 1:
            v = float(parts[0])
            if v <= 0:
                return None
            return (v, v)
        if len(parts) == 2 and parts[0] and parts[1]:
            a, b = float(parts[0]), float(parts[1])
            if a <= 0 or b <= 0:
                return None
            return (min(a, b), max(a, b))
    except (ValueError, TypeError):
        return None
    return None


def format_size_range(min_val, max_val):
    """Inverse of parse_size_range, for reconstructing a saved row's
    DataGrid cell text on load. "10" when min_val == max_val (today's
    exact-size case), else "10-14". Uses "{:g}" so a whole number
    round-trips to what a user would actually type (no trailing ".0")."""
    def fmt(v):
        return "{:g}".format(v)
    if abs(max_val - min_val) < 1e-9:
        return fmt(min_val)
    return "{}-{}".format(fmt(min_val), fmt(max_val))


def expand_enclosed_program(program, size_rng=None):
    """program: list of {name, category, width, depth, qty, adjacency,
    max_width, max_depth}. Enclosed rooms (private offices, conference
    rooms - anything needing real walls/doors) get their size specified,
    not derived from an area. width/depth are the minimum (or the exact
    size, if max_width/max_depth are absent or equal to them).
    adjacency is optional and normalizes to "window"/"entry"/"core"/""
    (blank/unrecognized text silently falls back to "" - never raises).

    size_rng: optional random.Random instance. When a row carries
    max_width/max_depth greater than its width/depth (a size range), each
    expanded instance independently samples its own width/depth from that
    range via size_rng.uniform() - so N instances of the same row get N
    different sizes. Pass a freshly re-seeded random.Random(same_seed) on
    every call that must reproduce the same resolved sizes (see
    build_layout's size_rng doc). size_rng=None (default) resolves every
    ranged row to its midpoint instead - deterministic, and identical to
    today's exact-value behavior when a row isn't ranged at all.

    Returns a flat list of {name, category, width, depth, area, adjacency,
    min_width, max_width, min_depth, max_depth} expanded by qty, sorted by
    a STABLE per-row nominal_area descending - ((min_width+max_width)/2 *
    (min_depth+max_depth)/2), the same midpoint convention
    _resolve_enclosed_size uses for size_rng=None - NOT the individually
    RESOLVED per-instance area/depth. Sorting on a value shared by every
    instance of one row (rather than each instance's own randomized draw)
    keeps all instances of one row contiguous in the output list (Python's
    sort is stable, and one row's instances are generated consecutively
    before sorting) - this is what lets pack_shelves's same-shelf
    same-category size-inheritance (see its own docstring) actually
    trigger: instances of one row landing scattered apart in the pack
    sequence, which individually-randomized sort order could cause, would
    rarely end up adjacent enough to share a shelf. pack_shelves itself
    does not require any particular sort order for correctness - it tracks
    shelf_depth as a running max, not a pre-sorted first-item assumption.

    min_width/max_width/min_depth/max_depth are the row's own resolved
    range bounds (identical for every instance of one row), additively
    exposed so pack_shelves can validate whether inheriting a neighboring
    instance's size is still within THIS instance's own valid range - not
    to be confused with the differently-scoped `min_width` key on
    open-zone items from expand_program (a slice_rect floor, not a range
    bound - separate dicts, separate code paths, no collision)."""
    items = []
    for row in program:
        qty = int(row.get("qty", 1) or 1)
        category = row.get("category") or row["name"]
        min_w = float(row["width"])
        max_w = float(row.get("max_width") or row["width"])
        min_d = float(row["depth"])
        max_d = float(row.get("max_depth") or row["depth"])
        nominal_area = ((min_w + max_w) / 2.0) * ((min_d + max_d) / 2.0)
        adjacency = (row.get("adjacency") or "").strip().lower()
        if adjacency not in ("window", "entry", "core"):
            adjacency = ""
        for i in range(qty):
            label = row["name"] if qty == 1 else "{} {}".format(row["name"], i + 1)
            width = _resolve_enclosed_size(min_w, max_w, size_rng)
            depth = _resolve_enclosed_size(min_d, max_d, size_rng)
            items.append({
                "name": label, "category": category,
                "width": width, "depth": depth, "area": width * depth,
                "adjacency": adjacency, "nominal_area": nominal_area,
                "min_width": min_w, "max_width": max_w,
                "min_depth": min_d, "max_depth": max_d,
            })
    items.sort(key=lambda it: it["nominal_area"], reverse=True)
    return items


def pack_shelves(region, items):
    """region: (x,y,w,h). items: list of {name, category, width, depth,
    area}, pre-sorted by expand_enclosed_program's stable nominal-area-
    descending order (not required for correctness - shelf_depth is
    tracked as a running max below, not a pre-sorted first-item assumption
    - but keeping instances of one program row contiguous is what makes
    the same-shelf same-category size inheritance below trigger reliably).

    Classic "Next-Fit Decreasing Height"-style shelf/strip packing: places
    items left-to-right at the current shelf's y; when an item doesn't fit
    the remaining width, starts a new shelf below. Dimensions are exactly
    what the user specified (or inherited - see below), not derived from
    area, so there is no aspect-ratio problem here at all.

    Same-shelf same-category size inheritance: when an item is placed
    immediately after another item of the SAME category on the SAME shelf
    (i.e. they'll end up touching, sharing a vertical edge), it inherits
    the predecessor's already-resolved width/depth instead of its own
    independently-drawn one - provided that inherited size still falls
    within THIS item's own min_width/max_width/min_depth/max_depth range
    (falls back to its own size if not, e.g. two different rows sharing a
    category with different ranges). This is what makes a touching row of
    same-type enclosed rooms read as visually consistent instead of each
    instance carrying its own few-square-foot difference, while rooms that
    aren't touching (different shelf, region, or separated by a different
    category) keep varying independently. Items lacking these range keys
    (e.g. any future non-ranged caller) simply never match the inheritance
    guard's bounds check and keep their own size - a safe no-op fallback.

    A wrap to a new shelf always REVERTS an item to its own independent
    size (it's no longer touching anything) and resets the inheritance
    chain - found and fixed via a Plan-agent pressure test: the region-fit
    check (iw > w or idp > h) must run AFTER this revert, not before, or a
    tentatively-inherited size that happens to pass the fit check can get
    silently replaced post-wrap by the item's own (unchecked) size,
    overflowing the region's width with no error or leftover recorded.

    Returns (placed, unplaced, leftover_rects). unplaced: items that don't
    fit the region at all (too wide/deep for it, or ran out of room going
    shelf by shelf) - reported, never forced. leftover_rects: the trailing
    gap at the end of each shelf, the vertical gap above any item shallower
    than its shelf's governing depth (a shelf's depth is set by its deepest
    item, which isn't necessarily every item on it - e.g. under a shuffled
    item order), plus whatever's left below the last shelf. Together these
    make placed+leftover an exact tiling of `region` - no area silently
    vanishes from either room space or leftover reporting.
    """
    x0, y0, w, h = region
    placed, unplaced, leftover_rects = [], [], []

    cursor_x, cursor_y = x0, y0
    shelf_y = y0
    shelf_depth = 0.0
    shelf_items = []  # (x, w, depth) placed on the current shelf so far
    prev_category, prev_width, prev_depth = None, None, None

    def close_shelf():
        gap = (x0 + w) - cursor_x
        if shelf_depth > 1e-6 and gap > 1e-6:
            leftover_rects.append({"x": cursor_x, "y": shelf_y, "w": gap, "h": shelf_depth})
        for item_x, item_w, item_depth in shelf_items:
            if shelf_depth - item_depth > 1e-6:
                leftover_rects.append({
                    "x": item_x, "y": shelf_y + item_depth, "w": item_w, "h": shelf_depth - item_depth,
                })

    for it in items:
        iw, idp = it["width"], it["depth"]

        is_first_on_shelf = cursor_x <= x0 + 1e-9
        if not is_first_on_shelf and it["category"] == prev_category:
            min_w, max_w = it.get("min_width", iw), it.get("max_width", iw)
            min_d, max_d = it.get("min_depth", idp), it.get("max_depth", idp)
            if (min_w - 1e-6 <= prev_width <= max_w + 1e-6
                    and min_d - 1e-6 <= prev_depth <= max_d + 1e-6):
                iw, idp = prev_width, prev_depth  # tentative inheritance

        if cursor_x + iw > x0 + w + 1e-6:
            close_shelf()
            cursor_y += shelf_depth
            shelf_y = cursor_y
            cursor_x = x0
            shelf_depth = 0.0
            shelf_items = []
            iw, idp = it["width"], it["depth"]  # revert - no longer adjacent to anything
            prev_category = None

        # Runs AFTER the wrap/revert block above so it always validates the
        # FINAL iw/idp that will actually be placed, never the tentative
        # (possibly-since-reverted) one - see the docstring note above.
        if iw > w + 1e-6 or idp > h + 1e-6:
            unplaced.append(it)
            continue

        if cursor_y + idp > y0 + h + 1e-6:
            unplaced.append(it)
            continue

        placed.append({
            "name": it["name"], "category": it["category"],
            "x": cursor_x, "y": cursor_y, "w": iw, "h": idp, "area": iw * idp,
        })
        shelf_items.append((cursor_x, iw, idp))
        cursor_x += iw
        shelf_depth = max(shelf_depth, idp)
        prev_category, prev_width, prev_depth = it["category"], iw, idp

    close_shelf()
    used_y = cursor_y + shelf_depth
    if used_y < y0 + h - 1e-6:
        leftover_rects.append({"x": x0, "y": used_y, "w": w, "h": (y0 + h) - used_y})

    return placed, unplaced, leftover_rects


MIN_USABLE_DIM = 3.0  # ft - free regions smaller than this in either dimension are reported as leftover, not used

# Real built floor plans (reviewed from client-supplied test-fit PDFs) never use one
# continuous corridor end-to-end - they use a branching network of short, locally-sized
# hallway segments (a main spine plus perpendicular branch stubs reaching into room
# clusters). These constants drive that branching automatically from program size.
BRANCH_BASE_AREA = 3000.0   # sf - at/below this, the main spine alone is sufficient; no branches
BRANCH_AREA_STEP = 3000.0   # sf - one more branch per this much program area beyond BRANCH_BASE_AREA
                             # (raised from an earlier 1200 - real-world testing on a large program hit
                             # the old MAX_BRANCHES=8 ceiling, producing 16 individually-labeled hallway
                             # pieces - both-sided branches double the visible count - which read as an
                             # incoherent maze even though nothing overlapped geometrically)
MAX_BRANCHES = 4            # hard cap - lowered from 8 for the same reason; keeps the drafting view legible
MIN_BRANCH_GAP = 15.0       # ft - minimum clear spacing between adjacent branch stubs, so pack_shelves
                             # still gets a usable bay between consecutive branches


def compute_branch_count(total_program_area, spine_length, branch_width):
    """Fully automatic - not a user-facing parameter. Grows roughly one branch
    per BRANCH_AREA_STEP sf of program beyond BRANCH_BASE_AREA, capped by
    MAX_BRANCHES and by how many branches can physically fit along the spine
    at even spacing with MIN_BRANCH_GAP of clearance between them (matching
    the spacing generate_branch_rects actually uses: spine_length/(N+1))."""
    if total_program_area <= BRANCH_BASE_AREA:
        return 0
    by_area = int((total_program_area - BRANCH_BASE_AREA) // BRANCH_AREA_STEP) + 1
    max_by_spacing = int(spine_length / (branch_width + MIN_BRANCH_GAP)) - 1
    return max(0, min(by_area, max_by_spacing, MAX_BRANCHES))


def generate_branch_rects(boundary_w, boundary_h, corridor, corridor_axis, branch_count, branch_width):
    """corridor: the main spine dict {x,y,w,h} as already computed by
    build_layout. Returns a flat list of (x,y,w,h) branch-piece rects, up to
    TWO per branch (one per side of the spine - every branch is double-loaded).
    A side is omitted only if the spine already runs flush to that boundary
    edge (e.g. an extreme corridor_bias). Each piece runs the full distance
    from the spine's edge to the boundary's far edge on that side - the same
    "full-span rect, clipped the same way as corridor_rect" contract, so
    subtract_obstacles needs no new edge-case handling. Not obstacle-clipped
    here - the caller subtracts obstacles the same way it already does for
    corridor_rect. Pieces start exactly at the spine's edge so they abut it
    (zero-area intersection), never overlap it."""
    if branch_count <= 0:
        return []

    rects = []
    if corridor_axis == "vertical":
        spine_length = boundary_h
        for i in range(branch_count):
            t = (i + 1) / float(branch_count + 1)
            y0 = spine_length * t - branch_width / 2.0
            y0 = min(max(y0, 0.0), boundary_h - branch_width)
            left_w = corridor["x"]
            if left_w > 1e-6:
                rects.append((0.0, y0, left_w, branch_width))
            right_x = corridor["x"] + corridor["w"]
            right_w = boundary_w - right_x
            if right_w > 1e-6:
                rects.append((right_x, y0, right_w, branch_width))
    else:
        spine_length = boundary_w
        for i in range(branch_count):
            t = (i + 1) / float(branch_count + 1)
            x0 = spine_length * t - branch_width / 2.0
            x0 = min(max(x0, 0.0), boundary_w - branch_width)
            bottom_h = corridor["y"]
            if bottom_h > 1e-6:
                rects.append((x0, 0.0, branch_width, bottom_h))
            top_y = corridor["y"] + corridor["h"]
            top_h = boundary_h - top_y
            if top_h > 1e-6:
                rects.append((x0, top_y, branch_width, top_h))
    return rects


ACCESS_CLEARANCE = 4.0  # ft - guaranteed clear space kept around every stair/elevator core


def generate_access_buffer_rects(vertical_circulation, boundary_w, boundary_h,
                                  existing_segments=None, clearance=ACCESS_CLEARANCE):
    """vertical_circulation: raw (unmerged, boundary-clipped) stair/elevator
    core rects, auto-detected from picked obstacles' Revit category. Returns
    a ring of guaranteed-clear circulation space around each core - expand
    its bbox by `clearance` on all sides (clipped to the boundary), then
    subtract the original core back out. This is a deliberate simplification
    of "route a hallway to the corridor spine" - a directional connector
    would need to reason about which side is nearest/avoid re-crossing other
    obstacles; a symmetric buffer ring delivers the same practical outcome
    (clear access near every core) far more simply, at the cost of not
    literally connecting to the spine.

    existing_segments: circulation rects already placed (corridor_rect,
    branch_rects) - unlike branches, which are generated to start exactly
    at the spine's edge and so never overlap it by construction, a buffer
    ring is generated purely from the core's own position and has no such
    guarantee: a core near the corridor can produce a ring that geometrically
    overlaps it. Subtracting existing_segments out (via subtract_obstacles,
    same as obstacle-notching) prevents that. Rings from different cores are
    also subtracted from each other as they're generated, in case two cores
    are close enough for their clearance zones to overlap."""
    exclude = list(existing_segments or [])
    rects = []
    for vx, vy, vw, vh in vertical_circulation:
        ex0, ey0 = max(vx - clearance, 0.0), max(vy - clearance, 0.0)
        ex1, ey1 = min(vx + vw + clearance, boundary_w), min(vy + vh + clearance, boundary_h)
        if ex1 - ex0 > 1e-6 and ey1 - ey0 > 1e-6:
            expanded = (ex0, ey0, ex1 - ex0, ey1 - ey0)
            pieces = subtract_obstacles(expanded, [(vx, vy, vw, vh)] + exclude)
            rects.extend(pieces)
            exclude.extend(pieces)
    return rects


def _dedupe_sorted(values, tol=1e-6):
    """values: iterable of floats. Returns a sorted list with near-duplicate
    values (within tol) collapsed to one - used to build a clean grid-line
    coordinate list from raw polygon vertex coordinates, which can carry
    the same "same wall" coordinate repeated across multiple loop points."""
    out = []
    for v in sorted(values):
        if not out or v - out[-1] > tol:
            out.append(v)
    return out


# Live-verified (against a real, genuinely rectilinear Room boundary) that
# Revit's GetBoundarySegments emits ordinary joint noise between consecutive
# segment endpoints on the order of 1e-4 ft - script.py's get_boundary_polygon
# already tolerates that when deciding a boundary IS rectilinear (see its own
# tol=1e-3 note), but the raw per-vertex coordinates it hands to
# compute_boundary_exclusion_rects still carry that noise untouched (nothing
# snaps them to a shared grid). The default _dedupe_sorted tolerance (1e-6)
# is far too tight to collapse it, so two "same wall" grid lines can survive
# as separate, near-coincident coordinates - producing an exclusion
# rectangle with a near-zero width or height. That's harmless in pure
# Python, but it crashes script.py's draw_layout the moment it tries to
# build a DB.Line for that rect's outline: Revit's Application.
# ShortCurveTolerance rejects any curve shorter than ~1/32" (~0.0026 ft).
# BOUNDARY_GRID_TOL is comfortably above both the observed noise floor and
# ShortCurveTolerance, while staying far smaller than any real room
# dimension - so every surviving grid cell, and therefore every returned
# exclusion rect, is guaranteed drawable.
BOUNDARY_GRID_TOL = 0.01  # ft (~1/8")


def _point_in_polygon(px, py, loops):
    """Even-odd (crossing-number) ray-casting point-in-polygon test across
    the combined edge set of EVERY loop in `loops` simultaneously - outer
    boundary and any holes together, undifferentiated. This deliberately
    skips classifying loops as outer-vs-hole by winding direction: even-odd
    handles holes and multiple disjoint outer loops correctly with zero
    classification code and no dependency on which winding convention the
    caller's source data happens to use.

    loops: list of loops, each a list of (x, y) vertices (closed implicitly
    - the last vertex connects back to the first). Assumes purely
    orthogonal (axis-aligned) edges - callers must have already verified
    this (see script.py's get_boundary_polygon orthogonality check) - so
    only vertical edges are tested (a horizontal edge can never cross a
    horizontal ray) and no floating-point division is needed. A half-open
    y-range test (y_lo <= py < y_hi) avoids double-counting a ray that
    passes exactly through a shared vertex - the standard fix for this
    ray-casting edge case."""
    inside = False
    for loop in loops:
        n = len(loop)
        for i in range(n):
            x0, y0 = loop[i]
            x1, y1 = loop[(i + 1) % n]
            if abs(x0 - x1) > 1e-9:
                continue  # horizontal edge - can't cross a horizontal ray
            y_lo, y_hi = (y0, y1) if y0 < y1 else (y1, y0)
            if y_lo <= py < y_hi and px < x0:
                inside = not inside
    return inside


def _merge_exclusion_row(rects, tol=1e-6):
    """rects: list of (x, y, w, h) EXCLUDED grid cells believed to lie in one
    row (same y, same h). Merges only consecutive cells whose edges touch
    exactly (within tol) - pure rectangle run-length merging, never
    approximates. Deliberately NOT merge_obstacles: that function's bbox
    union is fine for scattered user-picked obstacles (a slightly-too-large
    merged rect is harmless there) but wrong here - two excluded cells that
    don't themselves form a rectangle (e.g. an L-shaped notch spanning
    misaligned cells) could union into a bbox whose extra corners fall
    INSIDE the real polygon, silently excluding genuine floor area - the
    exact bug this feature exists to fix, just relocated. Row-only merging
    (no cross-row/column pass) is a deliberate, cheap simplification -
    consistent with this module's existing "acceptable for a heuristic
    design aid" tradeoffs (see subtract_obstacles's own fragmentation note)."""
    if not rects:
        return []
    ordered = sorted(rects, key=lambda r: r[0])
    merged = [ordered[0]]
    for r in ordered[1:]:
        px, py, pw, ph = merged[-1]
        x, y, w, h = r
        if abs(x - (px + pw)) < tol:
            merged[-1] = (px, py, (x + w) - px, ph)
        else:
            merged.append(r)
    return merged


def compute_boundary_exclusion_rects(boundary_w, boundary_h, polygon_loops):
    """polygon_loops: list of loops (each a list of local (x,y) vertices),
    already confirmed orthogonal by the caller - or falsy/empty, in which
    case this returns [] immediately (a perfectly rectangular boundary's
    polygon IS its own bbox, so there's nothing to exclude - this is also
    the safe behavior when the caller couldn't extract/verify a polygon).

    Builds a grid from every distinct X/Y coordinate across all loop
    vertices (plus the boundary's own 0/boundary_w/boundary_h edges,
    deduped within tolerance), forming candidate cells between consecutive
    grid lines. Any cell whose center falls OUTSIDE the real polygon (via
    _point_in_polygon) becomes an exclusion rectangle - these are the
    bounding-box areas that aren't actually part of the real (possibly
    L-shaped/notched) Room or Floor boundary. Adjacent excluded cells
    within the same grid row are merged via _merge_exclusion_row (an exact
    merge - NOT merge_obstacles, see its docstring for why that would be
    wrong here).

    Because the grid's own lines ARE the polygon's vertex coordinates,
    every polygon edge lies exactly on a grid line - so a cell's exact
    center can never land on a polygon edge, no numerical-tolerance
    fudging needed for that case.

    Returns a list of (x, y, w, h) exclusion rectangles, already clipped
    to [0,boundary_w] x [0,boundary_h] by construction (grid lines never
    fall outside that range)."""
    if not polygon_loops:
        return []

    xs = [0.0, boundary_w]
    ys = [0.0, boundary_h]
    for loop in polygon_loops:
        for x, y in loop:
            xs.append(x)
            ys.append(y)
    # BOUNDARY_GRID_TOL (not _dedupe_sorted's default 1e-6) - see its own
    # docstring: real Revit boundary vertices carry enough joint noise that
    # a tighter tolerance can leave a near-zero-width cell, which crashes
    # Revit when script.py later tries to draw it (below ShortCurveTolerance).
    xs = _dedupe_sorted(xs, tol=BOUNDARY_GRID_TOL)
    ys = _dedupe_sorted(ys, tol=BOUNDARY_GRID_TOL)

    exclusions = []
    for j in range(len(ys) - 1):
        y0, y1 = ys[j], ys[j + 1]
        h = y1 - y0
        if h <= BOUNDARY_GRID_TOL:
            continue
        row_excluded = []
        for i in range(len(xs) - 1):
            x0, x1 = xs[i], xs[i + 1]
            w = x1 - x0
            if w <= BOUNDARY_GRID_TOL:
                continue
            cx, cy = (x0 + x1) / 2.0, y0 + h / 2.0
            if not _point_in_polygon(cx, cy, polygon_loops):
                row_excluded.append((x0, y0, w, h))
        exclusions.extend(_merge_exclusion_row(row_excluded))
    return exclusions


def _select_regions_for_budget(candidates, total_area, margin=1.3):
    """candidates: list of (x,y,w,h) sorted largest-first. Greedily selects
    the largest regions until their cumulative area covers total_area*margin
    (or all candidates are exhausted). Returns (selected, remaining) -
    remaining candidates are left fully available for whatever's next."""
    if total_area <= 0:
        return [], list(candidates)
    selected, remaining = [], []
    cum_area = 0.0
    target = total_area * margin
    for r in candidates:
        if not selected or cum_area < target:
            selected.append(r)
            cum_area += r[2] * r[3]
        else:
            remaining.append(r)
    return selected, remaining


def _distribute_by_area(regions, items):
    """regions: list of (x,y,w,h). items: list of {..., target_area}.
    Splits items across regions proportionally to each region's area
    (largest regions get first crack at the largest items)."""
    region_items = [[] for _ in regions]
    total_region_area = sum(r[2] * r[3] for r in regions)
    total_item_area = sum(it["target_area"] for it in items)
    if not regions or total_item_area <= 0:
        return region_items
    cum_area_fracs = []
    running_area = 0.0
    for r in regions:
        running_area += r[2] * r[3]
        cum_area_fracs.append(running_area / total_region_area if total_region_area > 1e-9 else 1.0)
    running_item = 0.0
    for it in items:
        target_frac = (running_item + it["target_area"] / 2.0) / total_item_area
        idx = len(cum_area_fracs) - 1
        for i, cf in enumerate(cum_area_fracs):
            if target_frac <= cf:
                idx = i
                break
        region_items[idx].append(it)
        running_item += it["target_area"]
    return region_items


# --- Window/entry/core adjacency preference (soft, never hard-fails placement) ---
#
# Certain program categories read better near windows/curtain walls (private
# offices, conference rooms); others read better in the core, away from them
# (storage, server/IT); reception reads better near the entry door. This is
# always a soft preference - it changes which REGIONS a preference group's
# items get offered first, never whether an item can be placed at all.
#
# pack_shelves/slice_rect/_select_regions_for_budget/_distribute_by_area are
# all reused completely unchanged - the region SELECTION call still happens
# exactly once per program type, with the same total-area budget. What's new
# is that the *already-selected* region pool then gets partitioned so each
# region is visited by at most one preference group's packing pass (see
# build_layout) - naively re-running pack_shelves/slice_rect against the same
# region for multiple groups would silently overlap placements, since neither
# function has any notion that a region is already partially occupied.

def _nearest_edge_for_point(cx, cy, boundary_w, boundary_h):
    """Which boundary edge ("left"/"right"/"bottom"/"top") local point
    (cx, cy) is nearest to. Ties break toward the first edge in the fixed
    ["left", "right", "bottom", "top"] order - deterministic."""
    distances = {
        "left": cx,
        "right": boundary_w - cx,
        "bottom": cy,
        "top": boundary_h - cy,
    }
    order = ["left", "right", "bottom", "top"]
    best = order[0]
    for edge in order[1:]:
        if distances[edge] < distances[best]:
            best = edge
    return best


def _region_window_distance(region, window_edges, boundary_w, boundary_h):
    """Minimum perpendicular distance from region (x,y,w,h) to the nearest
    edge in window_edges (a set/iterable of "left"/"right"/"bottom"/"top").
    Returns None if window_edges is empty - callers must treat None as "no
    window preference data available, fall back to input order unchanged"."""
    if not window_edges:
        return None
    x, y, w, h = region
    edge_distances = {
        "left": x,
        "right": boundary_w - (x + w),
        "bottom": y,
        "top": boundary_h - (y + h),
    }
    return min(edge_distances[e] for e in window_edges if e in edge_distances)


def _region_entry_distance(region, door_points):
    """Minimum straight-line distance from region's center to the nearest
    (x, y) door point. Returns None if door_points is empty."""
    if not door_points:
        return None
    x, y, w, h = region
    cx, cy = x + w / 2.0, y + h / 2.0
    return min(((dx - cx) ** 2 + (dy - cy) ** 2) ** 0.5 for dx, dy in door_points)


def _partition_by_adjacency(items):
    """Stable partition of an already-ordered item list into (window_items,
    entry_items, core_items, none_items) by item["adjacency"] - each
    resulting list preserves the input list's relative order (so e.g.
    expand_enclosed_program's stable nominal-area-descending order survives
    the split, keeping same-row instances contiguous within each group -
    see pack_shelves's same-shelf same-category size inheritance)."""
    window_items, entry_items, core_items, none_items = [], [], [], []
    buckets = {"window": window_items, "entry": entry_items, "core": core_items}
    for it in items:
        buckets.get(it.get("adjacency", ""), none_items).append(it)
    return window_items, entry_items, core_items, none_items


def _order_regions_for_group(regions, group, window_edges, door_points, boundary_w, boundary_h):
    """Re-sorts `regions` (list of (x,y,w,h)) for one preference group's
    placement pass: "window" -> ascending distance to the nearest marked
    window edge (closest first); "core" -> descending distance (farthest
    first); "entry" -> ascending distance to the nearest door point
    (closest first). Falls back to the input order unchanged - never
    errors - whenever the relevant preference source (window_edges/
    door_points) wasn't provided, or for group "none"."""
    if group == "window" and window_edges:
        return sorted(regions, key=lambda r: _region_window_distance(r, window_edges, boundary_w, boundary_h))
    if group == "core" and window_edges:
        return sorted(regions, key=lambda r: -_region_window_distance(r, window_edges, boundary_w, boundary_h))
    if group == "entry" and door_points:
        return sorted(regions, key=lambda r: _region_entry_distance(r, door_points))
    return list(regions)


# Fixed priority order for consuming the shared region pool below: entry
# first (typically a single, identity-defining item - reception - that
# should claim its one best-matching region before anything else competes
# for it), then window (usually more items than entry), then core (which
# benefits from whatever's left over almost by construction - regions
# farther from marked edges/doors - so running it last is an emergent win
# rather than something needing its own logic), then none (unmarked items,
# using the pool's original largest-first order).
_ADJACENCY_GROUP_ORDER = ("entry", "window", "core", "none")


def _pack_enclosed_grouped(regions, items, window_edges, door_points, boundary_w, boundary_h):
    """Replaces build_layout's single enclosed-packing pass with one pass
    per adjacency group, each group drawing from a shared, shrinking
    region pool - every region a group's loop actually calls pack_shelves
    on is removed from the pool before the next group runs, so no two
    groups can ever pack into the same region (pack_shelves has no notion
    of partial occupancy - re-running it against an already-used region
    would silently overlap placements). A region a group only partially
    fills still leaves real usable space behind (pack_shelves's own
    region_leftovers - shelf gaps, the tail below the last shelf) - that
    space is reclaimed into the pool for the NEXT group rather than lost,
    or every group after the first would starve on a program where item
    counts don't neatly match region sizes (found via a live end-to-end
    test: a mixed window/core/none program placed only 7 of 14 items
    before this fix, vs. all 14 with adjacency unused on the same site).
    Only leftover pieces at least MIN_USABLE_DIM in both dimensions are
    reclaimed; smaller ones go straight to all_leftover, same as today.
    When every item has adjacency=="" (nobody used the feature), this
    reduces to exactly today's single pass in the original region order -
    the critical regression property this function must preserve (any
    leftover the "none" group itself can't reclaim - since it's last -
    ends up in the returned pool, flowing to build_layout's open-zone
    reclaiming exactly as it always has).
    Returns (all_placed, all_unplaced, all_leftover, remaining_regions) -
    same shape build_layout's enclosed-packing block already produced."""
    window_items, entry_items, core_items, none_items = _partition_by_adjacency(items)
    by_group = {"entry": entry_items, "window": window_items, "core": core_items, "none": none_items}

    pool = list(regions)
    all_placed, all_leftover, all_unplaced = [], [], []

    for group in _ADJACENCY_GROUP_ORDER:
        group_items = by_group[group]
        if not group_items or not pool:
            all_unplaced.extend(group_items)
            continue
        ordered = _order_regions_for_group(pool, group, window_edges, door_points, boundary_w, boundary_h)
        remaining_items = group_items
        touched = []
        reclaimed = []
        for region in ordered:
            if not remaining_items:
                break  # untouched regions stay in the pool for the next group
            placed, unplaced, region_leftovers = pack_shelves(region, remaining_items)
            touched.append(region)
            for p in placed:
                p["adjacency"] = group
            all_placed.extend(placed)
            for lo in region_leftovers:
                if lo["w"] >= MIN_USABLE_DIM and lo["h"] >= MIN_USABLE_DIM:
                    reclaimed.append((lo["x"], lo["y"], lo["w"], lo["h"]))
                else:
                    all_leftover.append(lo)
            remaining_items = unplaced
        all_unplaced.extend(remaining_items)
        pool = [r for r in pool if r not in touched] + reclaimed

    return all_placed, all_unplaced, all_leftover, pool


def _pack_open_grouped(regions, items, window_edges, door_points, boundary_w, boundary_h):
    """Open-zone analog of _pack_enclosed_grouped. Unlike pack_shelves,
    slice_rect has no "retry next region" concept - _distribute_by_area
    pre-splits a batch of items across a batch of regions by area, then
    slice_rect independently processes each region. So each group here
    gets the full currently-remaining region pool (ordered by that
    group's preference) offered to _distribute_by_area at once; only the
    regions _distribute_by_area actually assigned at least one item to are
    "touched" (run through slice_rect, removed from the pool) - regions
    assigned nothing stay available for the next group. A touched region
    slice_rect only partially fills leaves a real region_leftover behind -
    reclaimed into the pool for the next group rather than lost, same
    reasoning and same live-tested fix as _pack_enclosed_grouped. When
    every item has adjacency=="" (nobody used the feature), this reduces
    to exactly today's single _distribute_by_area + slice_rect pass in
    the original region order - the critical regression property this
    function must preserve.
    Returns (all_placed, all_unplaced, all_leftover, remaining_regions)."""
    window_items, entry_items, core_items, none_items = _partition_by_adjacency(items)
    by_group = {"entry": entry_items, "window": window_items, "core": core_items, "none": none_items}

    pool = list(regions)
    all_placed, all_unplaced, all_leftover = [], [], []

    for group in _ADJACENCY_GROUP_ORDER:
        group_items = by_group[group]
        if not group_items:
            continue
        if not pool:
            all_unplaced.extend(group_items)
            continue
        ordered = _order_regions_for_group(pool, group, window_edges, door_points, boundary_w, boundary_h)
        region_items = _distribute_by_area(ordered, group_items)
        touched = []
        reclaimed = []
        for region, item_list in zip(ordered, region_items):
            if not item_list:
                continue
            touched.append(region)
            placed, unplaced, region_leftover = slice_rect(region, item_list)
            for p in placed:
                p["adjacency"] = group
            all_placed.extend(placed)
            all_unplaced.extend(unplaced)
            if region_leftover:
                if region_leftover["w"] >= MIN_USABLE_DIM and region_leftover["h"] >= MIN_USABLE_DIM:
                    reclaimed.append((region_leftover["x"], region_leftover["y"],
                                       region_leftover["w"], region_leftover["h"]))
                else:
                    all_leftover.append(region_leftover)
        pool = [r for r in pool if r not in touched] + reclaimed

    return all_placed, all_unplaced, all_leftover, pool


def build_layout(boundary_w, boundary_h, enclosed_program, open_program, corridor_width=5.0,
                  corridor_axis="horizontal", corridor_bias=0.5, item_order="largest_first",
                  obstacles=None, rng=None, region_margin=1.3, branch_width=None,
                  window_edges=None, door_points=None, vertical_circulation=None, size_rng=None,
                  boundary_exclusion_rects=None):
    """boundary is (0,0)-(boundary_w, boundary_h) in feet.

    enclosed_program: list of {name, category, width, depth, qty} - rooms
    that need real walls/doors (private offices, conference rooms, etc.).
    Placed at their EXACT specified size via shelf-packing (`pack_shelves`)
    - no shape is derived from area, so there's no aspect-ratio problem.
    open_program: list of {name, category, target_area, min_width, qty} -
    open zones (reception, break areas, open office) that don't need a
    specific shape - they simply fill whatever space is left after
    enclosed rooms and the corridor, via the existing `slice_rect`.

    corridor_axis: "horizontal" (corridor runs left-right, bands stacked
    top/bottom) or "vertical" (corridor runs top-bottom, bands side by side).
    corridor_bias: 0..1 fraction of the cross-axis span where the corridor
    is centered (0.5 = centered; bias toward an existing door if found).
    item_order: "largest_first" (default, more stable/predictable results),
    "smallest_first", or "shuffled" (a seeded random permutation - requires
    `rng`, used by `generate_scored_candidates` for structural diversity
    beyond what the two fixed orders alone produce).
    obstacles: optional list of (x, y, w, h) fixed obstacle footprints
    (e.g. stair/elevator cores, walls that must stay) in the same local
    coordinate system as the boundary. Clipped to the boundary and
    subtracted from the workable area before rooms are placed - the
    subdivision routes around them rather than just flagging them.
    rng: optional random.Random instance, required only for
    item_order="shuffled". Passing the same seeded rng reproduces the same
    candidate deterministically - useful for debugging one specific result.
    region_margin: how much headroom (as a multiple of the program's total
    area) to give region selection before spilling into more regions -
    see `_select_regions_for_budget`. Default matches v1.1-v1.3 behavior.
    branch_width: width in feet of auto-generated branch hallways off the
    main corridor (see compute_branch_count/generate_branch_rects) -
    defaults to corridor_width. Branch count is fully automatic (driven by
    total program area), not user-facing - real built floor plans never
    use one continuous corridor end-to-end, so above a size threshold this
    adds perpendicular branch stubs reaching into the boundary on both
    sides of the spine, matching that real-world pattern.
    window_edges: optional set/iterable of "left"/"right"/"bottom"/"top" -
    boundary edges marked as having windows/curtain walls. Items whose
    program row set adjacency="window" or "core" get a soft placement
    preference toward/away from these edges (never a hard requirement -
    an item still gets placed even if it can't get its preferred region).
    door_points: optional list of (x, y) existing door locations - items
    with adjacency="entry" (e.g. reception) get a soft preference toward
    whichever selected region is nearest one of these. Also already used
    by check_egress_heuristics for travel-distance flags.
    vertical_circulation: optional list of (x, y, w, h) stair/elevator
    core footprints - a SUBSET of `obstacles` (already excluded from room
    placement as obstacles; this list just additionally marks which of
    them get a guaranteed-clear access buffer ring, see
    generate_access_buffer_rects). Not merged like `obstacles` is - each
    entry is clipped to the boundary individually.
    size_rng: optional random.Random instance, used ONLY to resolve
    enclosed-room size ranges (see expand_enclosed_program's max_width/
    max_depth) - fully decoupled from `rng` above, which stays scoped to
    item_order="shuffled". Pass a freshly re-seeded random.Random(same_seed)
    on every call within one Generate click that must produce the same
    resolved room sizes; `rng` may still vary per call for structural
    diversity (corridor axis/bias/order) without affecting sizes.
    boundary_exclusion_rects: optional list of (x, y, w, h) rects marking
    bounding-box area that ISN'T actually part of the real (possibly
    irregular/L-shaped) Room or Floor boundary - see
    compute_boundary_exclusion_rects. Handled exactly like
    vertical_circulation: clipped to the boundary individually, NOT run
    through merge_obstacles (which would risk unioning exclusion rects into
    a bbox that eats real floor area - the exact bug this parameter exists
    to fix, just relocated). Folded into the same obstacle-notching and
    free-region subtraction obstacles already go through, so rooms are
    never placed outside the real boundary shape.
    """
    enclosed_items = expand_enclosed_program(enclosed_program, size_rng=size_rng)
    open_items = expand_program(open_program)
    if item_order == "smallest_first":
        enclosed_items = list(reversed(enclosed_items))
        open_items = list(reversed(open_items))
    elif item_order == "shuffled":
        if rng is None:
            raise ValueError("item_order='shuffled' requires an rng (random.Random instance)")
        rng.shuffle(enclosed_items)
        rng.shuffle(open_items)

    boundary = (0, 0, boundary_w, boundary_h)

    clipped_obstacles = []
    for (ox, oy, ow, oh) in merge_obstacles(obstacles or []):
        cx0, cy0 = max(ox, 0), max(oy, 0)
        cx1, cy1 = min(ox + ow, boundary_w), min(oy + oh, boundary_h)
        if cx1 - cx0 > 1e-6 and cy1 - cy0 > 1e-6:
            clipped_obstacles.append((cx0, cy0, cx1 - cx0, cy1 - cy0))

    clipped_vertical_circulation = []
    for (vx, vy, vw, vh) in (vertical_circulation or []):
        cx0, cy0 = max(vx, 0), max(vy, 0)
        cx1, cy1 = min(vx + vw, boundary_w), min(vy + vh, boundary_h)
        if cx1 - cx0 > 1e-6 and cy1 - cy0 > 1e-6:
            clipped_vertical_circulation.append((cx0, cy0, cx1 - cx0, cy1 - cy0))

    # Bounding-box area that isn't actually part of a real (possibly
    # irregular/L-shaped) Room or Floor boundary - handled like
    # vertical_circulation, never merge_obstacles (see build_layout's docstring).
    clipped_boundary_exclusions = []
    for (ex, ey, ew, eh) in (boundary_exclusion_rects or []):
        cx0, cy0 = max(ex, 0), max(ey, 0)
        cx1, cy1 = min(ex + ew, boundary_w), min(ey + eh, boundary_h)
        if cx1 - cx0 > 1e-6 and cy1 - cy0 > 1e-6:
            clipped_boundary_exclusions.append((cx0, cy0, cx1 - cx0, cy1 - cy0))

    # Clamped well inside 0..1 (not just off the edges) so a door sitting right at
    # the boundary can't shrink one band down to a near-unusable sliver, which
    # would force every program item into the other band and cause needless overfill.
    corridor_bias = min(max(corridor_bias, 0.3), 0.7)

    if corridor_axis == "vertical":
        cross_span = boundary_w
        corridor_x = cross_span * corridor_bias - corridor_width / 2.0
        corridor_x = min(max(corridor_x, 0.0), cross_span - corridor_width)
        corridor = {"x": corridor_x, "y": 0, "w": corridor_width, "h": boundary_h}
    else:
        cross_span = boundary_h
        corridor_y = cross_span * corridor_bias - corridor_width / 2.0
        corridor_y = min(max(corridor_y, 0.0), cross_span - corridor_width)
        corridor = {"x": 0, "y": corridor_y, "w": boundary_w, "h": corridor_width}
    corridor_rect = (corridor["x"], corridor["y"], corridor["w"], corridor["h"])

    # Real built floor plans branch off the main spine into shorter, locally-sized
    # hallway segments once the program is big enough that one straight corridor
    # would leave rooms too deep/far from circulation - see compute_branch_count.
    branch_width = branch_width if branch_width is not None else corridor_width
    total_program_area = (sum(it["area"] for it in enclosed_items)
                           + sum(it["target_area"] for it in open_items))
    spine_length = boundary_h if corridor_axis == "vertical" else boundary_w
    branch_count = compute_branch_count(total_program_area, spine_length, branch_width)
    branch_rects = generate_branch_rects(
        boundary_w, boundary_h, corridor, corridor_axis, branch_count, branch_width)

    # Guaranteed-clear access space around every stair/elevator core - see
    # generate_access_buffer_rects for why this is a buffer ring rather than
    # a routed connector to the spine. Passes the corridor/branches already
    # placed so a core sitting near circulation doesn't produce an
    # overlapping ring.
    access_rects = generate_access_buffer_rects(
        clipped_vertical_circulation, boundary_w, boundary_h,
        existing_segments=[corridor_rect] + branch_rects)

    circulation_segments = [corridor_rect] + branch_rects + access_rects

    # The corridor/branch drawn footprints must also be notched around any true
    # obstacles they overlap, or the drawing would show circulation cutting
    # straight through a stair/elevator core. Room placement is already correct
    # without this (all of circulation_segments is subtracted from free_regions
    # below too) - this only affects what gets drawn for circulation itself.
    corridor_pieces = []
    for seg in circulation_segments:
        corridor_pieces.extend(subtract_obstacles(seg, clipped_obstacles + clipped_boundary_exclusions))

    free_regions = subtract_obstacles(
        boundary, clipped_obstacles + circulation_segments + clipped_boundary_exclusions)

    # Obstacles scattered at different positions along an axis each cut a
    # full-span strip, which can cascade into many narrow fragments even
    # with only a handful of obstacles. Rather than force a room into every
    # sliver, concentrate rooms into the largest/best-shaped regions and
    # treat the rest as leftover/circulation space, which is what odd
    # slivers around scattered obstacles realistically become anyway.
    candidates = [r for r in free_regions if r[2] >= MIN_USABLE_DIM and r[3] >= MIN_USABLE_DIM]
    leftover = [{"x": r[0], "y": r[1], "w": r[2], "h": r[3]} for r in free_regions if r not in candidates]
    candidates.sort(key=lambda r: r[2] * r[3], reverse=True)

    # --- Open zones first: no shape constraint, so slice_rect (unchanged
    # from v1/v1.1) can adapt to whatever region it's given - grouped by
    # window/entry/core adjacency preference (see _pack_open_grouped; a
    # no-op regrouping into a single pass when nobody uses the feature).
    # Reordered from enclosed-first (v1.1-v1.9) per explicit user request:
    # open zones anchor the largest usable chunks first, enclosed rooms
    # fill in around them afterward, ordered by sqft (see
    # expand_enclosed_program's stable nominal-area-descending sort).
    #
    # Known, accepted trade-off (independently assessed as high-severity by
    # a Plan-agent pressure test before this shipped): enclosed rooms have
    # hard exact-dimension requirements pack_shelves can't adapt to a
    # leftover shape the way slice_rect can, while _select_regions_for_budget
    # itself is purely area-greedy with zero awareness of region shape.
    # Handing that shape-blind selector to open zones first can leave
    # enclosed rooms with more fragmented, worse-fit leftover space than
    # they'd get going first - this is the direct trade the user asked for,
    # not a strict improvement; verify actual unplaced-enclosed impact
    # against a known baseline before assuming it helped. ---
    total_open_area = sum(it["target_area"] for it in open_items)
    open_regions, remaining_candidates = _select_regions_for_budget(
        candidates, total_open_area, margin=region_margin)

    open_placed, open_unplaced, open_leftovers, untouched_open_regions = _pack_open_grouped(
        open_regions, open_items, window_edges, door_points, boundary_w, boundary_h)
    for p in open_placed:
        p["kind"] = "open"
    all_placed = list(open_placed)
    leftover.extend(open_leftovers)
    remaining_candidates.extend(untouched_open_regions)  # never touched - fully available for enclosed rooms
    all_unplaced = list(open_unplaced)

    # --- Enclosed rooms: fill whatever's left - unused regions plus leftover
    # space from open packing that's still big enough to be worth offering,
    # packed exact-dimension via pack_shelves (trying the next region for
    # whatever didn't fit in the last - simple greedy multi-bin packing). ---
    reclaimable = [lo for lo in leftover if lo["w"] >= MIN_USABLE_DIM and lo["h"] >= MIN_USABLE_DIM]
    leftover = [lo for lo in leftover if lo not in reclaimable]
    enclosed_candidates = list(remaining_candidates) + [(lo["x"], lo["y"], lo["w"], lo["h"]) for lo in reclaimable]
    enclosed_candidates.sort(key=lambda r: r[2] * r[3], reverse=True)

    total_enclosed_area = sum(it["area"] for it in enclosed_items)
    enclosed_regions, unused_enclosed_candidates = _select_regions_for_budget(
        enclosed_candidates, total_enclosed_area, margin=region_margin)
    leftover.extend({"x": r[0], "y": r[1], "w": r[2], "h": r[3]} for r in unused_enclosed_candidates)

    # _pack_enclosed_grouped already reports every item unplaced when
    # enclosed_regions is empty (each group's items hit its own "if not
    # pool" branch), so no separate "if not enclosed_regions" fallback is
    # needed here.
    enclosed_placed, enclosed_unplaced, enclosed_leftovers, untouched_enclosed_regions = _pack_enclosed_grouped(
        enclosed_regions, enclosed_items, window_edges, door_points, boundary_w, boundary_h)
    for p in enclosed_placed:
        p["kind"] = "enclosed"
    all_placed.extend(enclosed_placed)
    all_unplaced.extend(enclosed_unplaced)
    leftover.extend(enclosed_leftovers)
    # Enclosed rooms are the last placement stage, so any region
    # _pack_enclosed_grouped never touched (or reclaimed leftover it never
    # got around to using) has nowhere further to go - it's genuine
    # unallocated space.
    leftover.extend({"x": r[0], "y": r[1], "w": r[2], "h": r[3]} for r in untouched_enclosed_regions)

    return {
        "rooms": all_placed,
        "unplaced": all_unplaced,
        "leftover": leftover,
        "corridor": corridor,
        "corridor_pieces": [{"x": p[0], "y": p[1], "w": p[2], "h": p[3]} for p in corridor_pieces],
        "obstacles": [{"x": o[0], "y": o[1], "w": o[2], "h": o[3]} for o in clipped_obstacles],
        "branches": [{"x": b[0], "y": b[1], "w": b[2], "h": b[3]} for b in branch_rects],
        "branch_count": branch_count,
        "boundary_exclusions": [{"x": e[0], "y": e[1], "w": e[2], "h": e[3]} for e in clipped_boundary_exclusions],
    }


def check_egress_heuristics(layout, corridor_min_width=44.0 / 12.0, max_travel_distance=200.0, door_points=None):
    """Rule-of-thumb heuristic flags only - NOT a code compliance check.
    corridor_min_width in feet (default 44in). max_travel_distance in feet.
    door_points: list of (x, y) existing door locations to measure travel
    distance to (nearest one used per room); if None/empty, distance is
    not evaluated and flagged as "unknown"."""
    flags = []

    corridor = layout["corridor"]
    corridor_actual_width = min(corridor["w"], corridor["h"])
    if corridor_actual_width < corridor_min_width - 1e-6:
        flags.append(
            "Corridor width {:.2f}' is below the {:.2f}' rule-of-thumb minimum - verify against your code's "
            "required egress width for the actual occupant load.".format(corridor_actual_width, corridor_min_width)
        )

    if door_points:
        for room in layout["rooms"]:
            cx = room["x"] + room["w"] / 2.0
            cy = room["y"] + room["h"] / 2.0
            nearest = min(
                ((dx - cx) ** 2 + (dy - cy) ** 2) ** 0.5
                for dx, dy in door_points
            )
            if nearest > max_travel_distance:
                flags.append(
                    "{}: straight-line distance to nearest known door is {:.0f}' (over the {:.0f}' "
                    "rule-of-thumb threshold) - verify actual travel path distance against code.".format(
                        room["name"], nearest, max_travel_distance)
                )
    else:
        flags.append("No existing doors found near the boundary - exit travel distance could not be estimated.")

    return flags


def generate_variation_params(count, rng):
    """Produces `count` randomized (corridor_axis, corridor_bias, item_order,
    region_margin) tuples for generate_scored_candidates - the randomized
    counterpart to script.py's fixed 5-variation list. corridor_bias is kept
    within build_layout's own usable clamp range (0.3-0.7) so no candidate
    is wasted on a bias that would just get clamped anyway. region_margin
    jitters how many regions get selected for enclosed vs. open (default
    elsewhere in this module is 1.3), giving real structural diversity in
    which regions end up used, not just corridor placement.
    rng: a random.Random instance - required, since candidates are only
    reproducible given a seeded rng (there is no un-seeded fallback here)."""
    orders = ("largest_first", "smallest_first", "shuffled")
    params = []
    for _ in range(count):
        axis = rng.choice(("horizontal", "vertical"))
        bias = rng.uniform(0.3, 0.7)
        order = rng.choice(orders)
        margin = rng.uniform(1.15, 1.6)
        params.append((axis, bias, order, margin))
    return params


def score_layout(layout, boundary_w, boundary_h, egress_flags, window_edges=None, door_points=None):
    """Higher is better. Terms, heaviest first:
    - large fixed penalty per unplaced item (a real failure, not a quality nit)
    - utilization: (room area + corridor area) / boundary area
    - open-zone aspect-ratio penalty (enclosed rooms are exact user-specified
      dimensions now, so there's nothing to penalize there - see "kind")
    - egress heuristic flags (from check_egress_heuristics)
    - leftover fragment *count* (many small fragments score worse than one
      or two larger ones, independent of their total area)
    - window/entry/core adjacency preference (small - a tie-breaker among
      otherwise-similar candidates, never large enough to justify placing
      something badly elsewhere to satisfy it). window_edges/door_points
      default to None, contributing exactly 0 - existing callers/tests
      that don't pass them are unaffected."""
    boundary_area = boundary_w * boundary_h
    if boundary_area <= 1e-6:
        return 0.0

    score = 0.0
    score -= 1000.0 * len(layout["unplaced"])

    room_area = sum(r["area"] for r in layout["rooms"])
    # Sums the actual obstacle-notched corridor_pieces (spine + any branches),
    # not the raw corridor/branch rects - those can overlap an obstacle, which
    # would over-credit utilization for area that isn't actually circulation.
    # Safe to sum directly: pieces from different segments abut rather than
    # overlap (branches meet the spine at zero-area intersections), and each
    # piece is already individually obstacle-notched, so there's no double count.
    circulation_area = sum(p["w"] * p["h"] for p in layout["corridor_pieces"])
    score += 100.0 * (room_area + circulation_area) / boundary_area

    for r in layout["rooms"]:
        if r.get("kind") != "open":
            continue
        ratio = max(r["w"], r["h"]) / max(min(r["w"], r["h"]), 1e-6)
        if ratio > 2.0:
            score -= 5.0 * (ratio - 2.0)

    score -= 10.0 * len(egress_flags)
    score -= 2.0 * len(layout["leftover"])

    if window_edges or door_points:
        max_dist = (boundary_w ** 2 + boundary_h ** 2) ** 0.5
        if max_dist > 1e-6:
            for r in layout["rooms"]:
                adjacency = r.get("adjacency")
                region = (r["x"], r["y"], r["w"], r["h"])
                if adjacency == "window" and window_edges:
                    d = _region_window_distance(region, window_edges, boundary_w, boundary_h)
                    if d is not None:
                        score += 3.0 * (1.0 - min(d / max_dist, 1.0))
                elif adjacency == "core" and window_edges:
                    d = _region_window_distance(region, window_edges, boundary_w, boundary_h)
                    if d is not None:
                        score += 3.0 * min(d / max_dist, 1.0)
                elif adjacency == "entry" and door_points:
                    d = _region_entry_distance(region, door_points)
                    if d is not None:
                        score += 3.0 * (1.0 - min(d / max_dist, 1.0))

    return score


def generate_scored_candidates(boundary_w, boundary_h, enclosed_program, open_program, corridor_width,
                                obstacles=None, candidate_count=20, keep_count=3, seed=None, door_points=None,
                                window_edges=None, vertical_circulation=None, size_seed=None,
                                boundary_exclusion_rects=None):
    """Generates `candidate_count` randomized build_layout candidates (via
    generate_variation_params), scores each with score_layout, and returns
    the top `keep_count` sorted best-first as a list of:
    {layout, params, flags, score}
    layout/flags have the same shape script.py's drawing/results code
    already consumes for the fixed-variation path, so a scored candidate
    draws through the same create_drafting_view/draw_layout calls.
    seed: optional int for reproducible candidate generation - the same
    seed always produces the same set of candidates.
    door_points: optional list of (x, y) existing door locations - passed
    through to check_egress_heuristics for real travel-distance scoring
    (omit to skip that check) AND to build_layout/score_layout for
    "entry"-adjacency preference placement/scoring (e.g. reception).
    window_edges: optional set/iterable of "left"/"right"/"bottom"/"top" -
    passed through to build_layout/score_layout for "window"/"core"-
    adjacency preference placement/scoring.
    vertical_circulation: optional list of (x, y, w, h) stair/elevator core
    footprints (a subset of `obstacles`) - passed through to build_layout
    for guaranteed-clear access buffer generation (see
    generate_access_buffer_rects).
    boundary_exclusion_rects: optional list of (x, y, w, h) bounding-box
    area that isn't actually part of the real (possibly irregular) Room/
    Floor boundary - passed through to build_layout unchanged (see
    compute_boundary_exclusion_rects / build_layout's own docstring).
    size_seed: optional value passed to random.Random() to resolve
    enclosed-room size ranges (build_layout's size_rng) - every candidate
    gets a FRESH random.Random(size_seed), so resolved room sizes are
    identical across every kept candidate even though each candidate's
    own structural rng (corridor axis/bias/order) still varies - room
    size is deliberately not a candidate-diversity axis. If omitted,
    derived once from the outer `rng` stream (`rng.random()`) rather than
    left None - random.Random(None) would reseed from OS entropy on every
    construction, silently giving each candidate a different, unrelated
    size seed for any caller using ranged rows without threading this
    explicitly (script.py's real UI path always passes it explicitly)."""
    rng = random.Random(seed)
    variation_params = generate_variation_params(candidate_count, rng)
    if size_seed is None:
        size_seed = rng.random()

    scored = []
    for axis, bias, order, margin in variation_params:
        build_rng = random.Random(rng.random()) if order == "shuffled" else None
        size_rng = random.Random(size_seed)
        layout = build_layout(
            boundary_w=boundary_w, boundary_h=boundary_h,
            enclosed_program=enclosed_program, open_program=open_program,
            corridor_width=corridor_width, corridor_axis=axis, corridor_bias=bias,
            item_order=order, obstacles=obstacles, rng=build_rng, region_margin=margin,
            window_edges=window_edges, door_points=door_points, vertical_circulation=vertical_circulation,
            size_rng=size_rng, boundary_exclusion_rects=boundary_exclusion_rects,
        )
        flags = check_egress_heuristics(layout, door_points=door_points)
        score = score_layout(layout, boundary_w, boundary_h, flags, window_edges=window_edges, door_points=door_points)
        scored.append({
            "layout": layout,
            "params": {"corridor_axis": axis, "corridor_bias": bias, "item_order": order, "region_margin": margin},
            "flags": flags,
            "score": score,
        })

    scored.sort(key=lambda c: c["score"], reverse=True)

    # Diversity-aware selection: picking pure top-N by score tends to collapse
    # onto near-identical variants of a single local optimum (e.g. every top
    # scorer sharing the same corridor axis/bias neighborhood) rather than
    # showing genuinely different concepts - especially on sites where one
    # corridor topology is structurally favored (narrow boundary, obstacles
    # clustered to one side). Bucket by coarse corridor shape and take the
    # best-scoring candidate per bucket first; only once buckets run out
    # (or keep_count exceeds the number of distinct buckets seen) fall back
    # to next-best-overall, so the kept set is visibly diverse whenever the
    # site actually allows it, without ever keeping a genuinely worse
    # candidate over a better one within the same bucket.
    selected_indices = []
    seen_buckets = set()
    for idx, c in enumerate(scored):
        bucket = _candidate_bucket(c["params"])
        if bucket not in seen_buckets:
            selected_indices.append(idx)
            seen_buckets.add(bucket)
        if len(selected_indices) >= keep_count:
            break
    if len(selected_indices) < keep_count:
        selected_set = set(selected_indices)
        for idx in range(len(scored)):
            if idx not in selected_set:
                selected_indices.append(idx)
                selected_set.add(idx)
            if len(selected_indices) >= keep_count:
                break

    selected = [scored[i] for i in selected_indices]
    selected.sort(key=lambda c: c["score"], reverse=True)
    return selected


def _candidate_bucket(params):
    """Coarse structural bucket used by generate_scored_candidates for
    diversity-aware selection: corridor axis x bias tercile (6 buckets
    total - low/mid/high bias on each of the 2 axes). Deliberately coarse
    (not also keyed on item_order/region_margin) so it's fine-grained
    enough to separate genuinely different-looking layouts but coarse
    enough that a typical 20-candidate batch populates most/all buckets,
    covering this tool's max of 5 kept options."""
    bias = params["corridor_bias"]
    if bias < 0.43:
        bias_bucket = "low"
    elif bias > 0.57:
        bias_bucket = "high"
    else:
        bias_bucket = "mid"
    return (params["corridor_axis"], bias_bucket)
