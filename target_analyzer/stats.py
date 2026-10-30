"""Group-size statistics. All physical values are in millimetres internally."""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from math import hypot, sqrt

MM_PER_UNIT = {"mm": 1.0, "cm": 10.0, "inch": 25.4}


def to_unit(mm: float, unit: str) -> float:
    return mm / MM_PER_UNIT[unit]


def from_unit(value: float, unit: str) -> float:
    return value * MM_PER_UNIT[unit]


@dataclass
class GroupStats:
    n: int
    width_mm: float        # horizontal extent, centre to centre
    height_mm: float       # vertical extent, centre to centre
    diameter_mm: float     # extreme spread: largest centre-to-centre distance
    center_x_px: float
    center_y_px: float


def group_stats(points_px: list[tuple[float, float]], px_per_mm: float) -> GroupStats | None:
    """points_px: shot centres in pixels. px_per_mm: scale (> 0)."""
    if not points_px or px_per_mm <= 0:
        return None
    xs = [p[0] for p in points_px]
    ys = [p[1] for p in points_px]
    cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
    width = (max(xs) - min(xs)) / px_per_mm
    height = (max(ys) - min(ys)) / px_per_mm
    if len(points_px) > 1:
        diameter = max(hypot(a[0] - b[0], a[1] - b[1]) for a, b in combinations(points_px, 2)) / px_per_mm
    else:
        diameter = 0.0
    return GroupStats(len(points_px), width, height, diameter, cx, cy)


def percentile(values: list[float], q: float) -> float:
    """q-th percentile (0-100) with linear interpolation between ranks (numpy default)."""
    if not values:
        return 0.0
    v = sorted(values)
    pos = (len(v) - 1) * q / 100.0
    lo = int(pos)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def mean_std(values: list[float]) -> tuple[float, float]:
    """Mean and sample standard deviation (ddof=1). Std is 0 for a single value."""
    n = len(values)
    if n == 0:
        return 0.0, 0.0
    m = sum(values) / n
    if n < 2:
        return m, 0.0
    var = sum((v - m) ** 2 for v in values) / (n - 1)
    return m, sqrt(var)


def summarize_groups(groups: list[GroupStats]) -> dict:
    """Statistics over several groups (the groups of one photo, or of many records).

    Returns {metric: (mean, sample_std)} for "width_mm", "height_mm",
    "diameter_mm" and "n_shots", plus "count" (number of groups),
    "total_shots" and "diameter_p90_mm" (90 % of the groups are at or below).
    Empty input gives zeros.
    """
    out: dict = {}
    for key, attr in (("width_mm", "width_mm"), ("height_mm", "height_mm"),
                      ("diameter_mm", "diameter_mm"), ("n_shots", "n")):
        out[key] = mean_std([float(getattr(g, attr)) for g in groups])
    out["count"] = len(groups)
    out["total_shots"] = sum(g.n for g in groups)
    out["diameter_p90_mm"] = percentile([g.diameter_mm for g in groups], 90)
    return out


def cluster_points(points: list[tuple[float, float]], max_dist: float) -> list[int]:
    """Single-linkage clustering: two points are in the same cluster when a chain
    of points at most max_dist apart joins them. Returns one cluster index per
    point; clusters are numbered 0, 1, ... in order of their leftmost-topmost
    point (row bands of max_dist, then x), so labels read like a card.
    """
    n = len(points)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, j in combinations(range(n), 2):
        if hypot(points[i][0] - points[j][0], points[i][1] - points[j][1]) <= max_dist:
            parent[find(i)] = find(j)
    roots = [find(i) for i in range(n)]
    members: dict[int, list[int]] = {}
    for i, r in enumerate(roots):
        members.setdefault(r, []).append(i)

    def key(r: int):
        xs = [points[i][0] for i in members[r]]
        ys = [points[i][1] for i in members[r]]
        cy = sum(ys) / len(ys)
        return (round(cy / max(max_dist, 1e-9)), sum(xs) / len(xs))

    order = {r: k for k, r in enumerate(sorted(members, key=key))}
    return [order[r] for r in roots]
