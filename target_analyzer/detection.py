"""Bullet-hole detection on printed paper targets (OpenCV).

Two target styles are handled by one pipeline:

* Solid black disc (25 m precision pistol style): holes are torn paper that
  shows bright against the black. Inside the disc, "bright" pixels are holes.
* Ring targets (10 m air rifle / pistol cards): white centre, thin rings, wide
  black bands. Holes expose the backing board, which is grey or wood-coloured,
  i.e. neither paper-white nor ink-black, and often saturated in colour.

Pipeline
--------
1. Estimate the paper brightness globally (95th percentile) and locally (a
   grey-level closing that fills small dark objects but follows shadows), so
   uneven lighting does not matter much.
2. Ink mask = pixels far darker than paper. If the largest ink blob is a
   solid, circular disc, remember it (solid-disc style).
3. Candidate hole pixels:
   - mid-tone pixels: darker than the local paper but not ink (grey backing,
     holes on white paper), excluding the inside of a solid disc, where the
     print itself is grey under strong light;
   - saturated pixels (brown wood backing, anywhere);
   - inside a solid disc: pixels brighter than ~65 % of paper (torn paper).
4. Morphological opening removes the thin ring lines, digit strokes and the
   anti-aliased edges of the print; what remains are hole blobs.
5. Blobs much larger than a single hole are torn regions holding several
   shots; the shot count is estimated from the area (or distributed from the
   known number of shots fired) and the blob is split into that many centres
   with k-means.
6. Every circular black shape (the band of a ring target) is a *target*; when
   a photo shows several of them (a card of 10-25 diagrams) each impact is
   assigned to the nearest target and the impacts form one *group* per target.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np


@dataclass
class Disc:
    cx: float
    cy: float
    r: float
    solid: bool = True  # True: filled black disc; False: outer ring of a ring target


@dataclass
class Impact:
    x: float          # pixel coordinates in the original image
    y: float
    count: int = 1    # shots represented by this point (always 1 after splitting)
    area: float = 0.0
    manual: bool = False
    blob_id: int = -1  # id of the torn blob the point was split from (-1: isolated)
    group: int = 0     # index into the group list (one group per target diagram)


@dataclass
class Group:
    """A set of shots measured together: one diagram of a card, or one series."""
    label: str
    cx: float | None = None  # centre and radius (px) of the printed target the
    cy: float | None = None  # group belongs to, when it was found in the photo
    r: float | None = None


@dataclass
class DetectionResult:
    disc: Disc | None
    impacts: list[Impact]
    blobs: list[np.ndarray] = field(default_factory=list)  # contours, for drawing
    single_hole_area: float = 0.0
    debug: dict = field(default_factory=dict)  # intermediate masks (work resolution)
    targets: list[Disc] = field(default_factory=list)  # every circular black shape, largest first
    groups: list[Group] = field(default_factory=list)  # impacts[i].group indexes this list


WORK_SIZE = 1920  # longest image side used for detection; parameters are tuned for it


def _odd(v: float) -> int:
    v = int(round(v))
    return max(3, v | 1)


def ink_mask(gray: np.ndarray, ink_ratio: float = 0.45) -> np.ndarray:
    """Pixels far darker than the paper (printed black)."""
    blur = cv2.medianBlur(gray, 5)
    paper = float(np.percentile(blur, 95))
    _, ink = cv2.threshold(blur, int(ink_ratio * paper), 255, cv2.THRESH_BINARY_INV)
    return ink


def _circular_ink_shapes(gray: np.ndarray, ink_ratio: float, min_r: float) -> list[Disc]:
    """Every closed ink blob that is roughly a circle of radius >= min_r, largest first."""
    ink = ink_mask(gray, ink_ratio)
    f = max(gray.shape) / 1920.0
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_odd(15 * f), _odd(15 * f)))
    closed = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, k)
    cnts, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out: list[Disc] = []
    for c in cnts:
        (x, y), r = cv2.minEnclosingCircle(c)
        if r < min_r or cv2.contourArea(c) / (np.pi * r * r) < 0.7:
            continue
        circle = np.zeros_like(gray)
        cv2.circle(circle, (int(x), int(y)), int(r * 0.95), 255, -1)
        fill = cv2.countNonZero(cv2.bitwise_and(closed, circle)) / max(1, cv2.countNonZero(circle))
        out.append(Disc(float(x), float(y), float(r), solid=fill >= 0.75))
    out.sort(key=lambda d: -d.r)
    return out


def find_disc(gray: np.ndarray, ink_ratio: float = 0.45) -> Disc | None:
    """Largest circular black shape: a solid disc, or the outer band of a ring target."""
    shapes = _circular_ink_shapes(gray, ink_ratio, 0.05 * max(gray.shape))
    return shapes[0] if shapes else None


def find_targets(gray: np.ndarray, ink_ratio: float = 0.45, min_r_frac: float = 0.03) -> list[Disc]:
    """All target diagrams in the photo (circular black shapes), largest first.

    Keeps the shapes whose radius is at least min_r_frac of the longest image side
    and at least 60 % of the median radius, so the inner thin rings exposed by a
    torn band are not taken for extra targets; a circle whose centre lies inside
    a larger kept circle is dropped for the same reason. Reading order (rows, then
    left to right) is what `assign_groups` uses for the labels, not this order.
    """
    shapes = _circular_ink_shapes(gray, ink_ratio, min_r_frac * max(gray.shape))
    if not shapes:
        return []
    median_r = float(np.median([d.r for d in shapes]))
    kept: list[Disc] = []
    for d in shapes:  # largest first, so nesting is checked against bigger ones only
        if d.r < 0.6 * median_r:
            continue
        if any(np.hypot(d.cx - k.cx, d.cy - k.cy) < k.r for k in kept):
            continue
        kept.append(d)
    return kept


def _reading_order(targets: list[Disc]) -> list[Disc]:
    """Sort targets row by row (rows are bands of about one diameter), then left to right."""
    if not targets:
        return []
    band = 1.2 * float(np.median([d.r for d in targets]))
    return sorted(targets, key=lambda d: (round(d.cy / band), d.cx))


def assign_groups(impacts: list[Impact], targets: list[Disc]) -> list[Group]:
    """Give every impact the index of the nearest target; return the group list.

    With fewer than two targets everything goes into a single group "G1". Each
    target makes one group (even when no shot hit it) so the user can see the
    empty diagrams; labels are G1, G2, ... in reading order.
    """
    if len(targets) < 2:
        g = Group("G1", targets[0].cx, targets[0].cy, targets[0].r) if targets else Group("G1")
        for imp in impacts:
            imp.group = 0
        return [g]
    ordered = _reading_order(targets)
    for imp in impacts:
        imp.group = int(min(range(len(ordered)),
                            key=lambda i: (ordered[i].cx - imp.x) ** 2 + (ordered[i].cy - imp.y) ** 2))
    return [Group(f"G{i + 1}", d.cx, d.cy, d.r) for i, d in enumerate(ordered)]


def _split_blob(contour: np.ndarray, n: int) -> list[tuple[float, float]]:
    """Split a torn region into n centres with k-means on its pixel coordinates."""
    x, y, w, h = cv2.boundingRect(contour)
    sub = np.zeros((h, w), np.uint8)
    cv2.drawContours(sub, [contour - [x, y]], -1, 255, -1)
    ys, xs = np.nonzero(sub)
    pts = np.column_stack([xs, ys]).astype(np.float32)
    n = max(1, min(n, len(pts)))
    if n == 1:
        m = cv2.moments(contour)
        if m["m00"] == 0:
            return [(float(x + w / 2), float(y + h / 2))]
        return [(m["m10"] / m["m00"], m["m01"] / m["m00"])]
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 50, 0.5)
    _, _, centers = cv2.kmeans(pts, n, None, criteria, 5, cv2.KMEANS_PP_CENTERS)
    return [(float(cx) + x, float(cy) + y) for cx, cy in centers]


def detect_impacts(
    image_bgr: np.ndarray,
    paper_ratio: float = 0.84,
    sat_thresh: int = 95,
    open_kernel: int = 11,
    min_area: float = 150.0,
    shots_fired: int | None = None,
    hole_area_hint: float | None = None,
    ink_ratio: float = 0.45,
) -> DetectionResult:
    """Detect bullet holes in a target photo.

    paper_ratio: a pixel darker than this fraction of the local paper
        brightness (and not ink) is hole backing. Lower it if shadows are
        detected as holes, raise it if light-grey holes are missed.
    sat_thresh: HSV saturation (0-255) above which a darker-than-paper pixel
        counts as coloured backing (wood, ~120-180). Paper, grey backing and
        warm-lit paper in shadow (up to ~90) stay below it.
    open_kernel: size (px) of the opening that removes ring lines and digits;
        must stay smaller than a single hole.
    shots_fired: if known, the shots not accounted for by single holes are
        distributed over the large (torn) blobs proportionally to their area.
    hole_area_hint: expected area (px^2, original image) of the bright/grey
        core of a single hole (the UI derives it from the scale and a hole
        diameter); defaults to the median of the smaller half of the blobs.
    """
    H, W = image_bgr.shape[:2]
    scale = 1.0
    if max(H, W) > WORK_SIZE:
        scale = WORK_SIZE / max(H, W)
        image_bgr = cv2.resize(image_bgr, (int(W * scale), int(H * scale)), interpolation=cv2.INTER_AREA)
    h, w = image_bgr.shape[:2]
    f = max(h, w) / 1920.0
    blur_bgr = cv2.medianBlur(image_bgr, 5)
    gray = cv2.cvtColor(blur_bgr, cv2.COLOR_BGR2GRAY)
    sat = cv2.cvtColor(blur_bgr, cv2.COLOR_BGR2HSV)[:, :, 1]

    paper_global = float(np.percentile(gray, 95))
    # printed black is grey (low chroma); the dark brown of a wooden backing in
    # shadow is just as dark but strongly coloured (orange hue, 5-25 on the
    # OpenCV 0-180 scale), so it must stay a candidate. The hue test keeps the
    # purple/green chromatic fringes of print edges in the ink mask.
    hue = cv2.cvtColor(blur_bgr, cv2.COLOR_BGR2HSV)[:, :, 0]
    chroma = blur_bgr.max(axis=2).astype(np.int16) - blur_bgr.min(axis=2).astype(np.int16)
    dark_wood = (sat > sat_thresh) & (chroma > 40) & ((hue <= 30) | (hue >= 170))
    ink = (gray < ink_ratio * paper_global) & ~dark_wood
    targets = find_targets(gray, ink_ratio)
    disc = find_disc(gray, ink_ratio)

    # local paper level: a grey-level closing fills every dark object smaller
    # than the kernel (holes, bands, print) with the surrounding paper value,
    # but leaves large-scale illumination steps (hard shadows) untouched
    kd = _odd(101 * f)
    local = cv2.morphologyEx(gray, cv2.MORPH_CLOSE,
                             cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kd, kd)))
    local = cv2.GaussianBlur(local, (5, 5), 0).astype(np.float32)
    local = np.maximum(local, 1.0)
    rel = gray.astype(np.float32) / local

    midtone = (rel < paper_ratio) & ~ink
    # coloured backing (wood) is saturated AND darker than the paper overall;
    # the second condition rejects warm-lit paper. Large torn regions are not
    # filled by the closing, so this rule must not depend on the local level.
    coloured = (sat > sat_thresh) & (gray < 0.9 * paper_global) & ~ink
    cand = midtone | coloured

    if disc is not None and disc.solid:
        inside = np.zeros_like(gray)
        cv2.circle(inside, (int(disc.cx), int(disc.cy)), int(disc.r * 0.96), 255, -1)
        inside = inside > 0
        # the black print turns grey under strong light: drop mid-tones there,
        # and take bright (torn paper) pixels instead
        cand &= ~inside
        cand |= inside & ((gray > 0.65 * paper_global) | coloured)

    mask = cand.astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    k = _odd(open_kernel)
    opened = cv2.morphologyEx(mask, cv2.MORPH_OPEN,
                              cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    cnts, _ = cv2.findContours(opened, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    max_area = 0.15 * h * w  # anything bigger is background, not a hole

    def keep(c):
        area = cv2.contourArea(c)
        if not (min_area <= area <= max_area):
            return False
        x, y, bw, bh = cv2.boundingRect(c)
        if x <= 1 or y <= 1 or x + bw >= w - 1 or y + bh >= h - 1:
            return False  # touches the image border: paper edge, table
        hull = cv2.contourArea(cv2.convexHull(c))
        return hull <= 0 or area / hull >= 0.2  # thin arcs/strips (shadow edges) are not holes

    blobs = [c for c in cnts if keep(c)]
    debug = {"ink": ink, "midtone": midtone, "coloured": coloured, "candidates": opened > 0}
    if disc is not None:
        disc = Disc(disc.cx / scale, disc.cy / scale, disc.r / scale, disc.solid)
    targets = [Disc(t.cx / scale, t.cy / scale, t.r / scale, t.solid) for t in targets]
    if not blobs:
        return DetectionResult(disc, [], [], 0.0, debug, targets, assign_groups([], targets))

    areas = np.array([cv2.contourArea(c) for c in blobs])
    if hole_area_hint:
        single = float(hole_area_hint) * scale ** 2
    else:
        # the smaller half of the blobs are (almost) all single holes; the
        # median ignores a stray tiny blob as well as the torn regions
        lower = np.sort(areas)[: max(1, len(areas) // 2)]
        single = float(np.median(lower))

    # blobs up to ~1.8x a single hole are counted as one shot
    counts = np.ones(len(blobs), dtype=int)
    multi = areas > 1.8 * single
    if multi.any():
        if shots_fired and shots_fired > int((~multi).sum()):
            remaining = shots_fired - int((~multi).sum())
            share = areas[multi] / areas[multi].sum() * remaining
            est = np.maximum(1, np.floor(share)).astype(int)
            leftover = remaining - int(est.sum())
            if leftover > 0:
                order = np.argsort(-(share - np.floor(share)))
                for i in order[:leftover]:
                    est[i] += 1
            counts[multi] = est
        else:
            # torn regions grow much faster than linearly with the number of
            # shots (paper falls out); sqrt(area ratio) matches the samples well.
            est = np.round(np.sqrt(areas[multi] / single))
            counts[multi] = np.clip(est, 2, 25).astype(int)

    impacts: list[Impact] = []
    for bid, (c, a, n) in enumerate(zip(blobs, areas, counts)):
        for (x, y) in _split_blob(c, int(n)):
            impacts.append(Impact(x / scale, y / scale, 1, float(a) / n / scale ** 2, False,
                                  bid if n > 1 else -1))
    blobs = [(c / scale).astype(np.int32) for c in blobs]
    groups = assign_groups(impacts, targets)
    return DetectionResult(disc, impacts, blobs, single / scale ** 2, debug, targets, groups)
