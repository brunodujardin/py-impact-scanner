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
