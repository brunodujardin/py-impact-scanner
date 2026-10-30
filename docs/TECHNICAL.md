# Target Impact Analyser – Technical Reference

Audience: developers and AI coding agents working on this repository. This document
describes the structure, the data flow, the detection algorithm with its tuned
constants, the database schema, and the known limitations. Read it before changing
`target_analyzer/`.

## 1. Purpose and scope

A Windows desktop application (pure Python) that:

1. loads a photo of a paper shooting target;
2. lets the user define the pixel-to-millimetre scale;
3. detects bullet holes with classical computer vision (OpenCV, no machine learning);
4. splits the holes into *groups*: one per printed target diagram when the photo shows
   a card with several diagrams, otherwise one group; the user can create, delete and
   re-assign groups;
5. computes group-size statistics (width, height, extreme spread) per group, per photo
   (mean ± std-dev over its groups) and over several photos;
6. persists sessions, groups and impacts in SQLite and aggregates them.

There is no network access, no external service, no model file.

## 2. Repository layout

```
py-detect-impact/
├── setup.ps1               creates .venv and installs requirements.txt
├── run.ps1                 starts the app: .venv\Scripts\python -m target_analyzer [db]
├── requirements.txt        opencv-python-headless, numpy, pillow, pillow-heif
├── README.md               short overview
├── docs/
│   ├── USER_GUIDE.md       end-user manual
│   └── TECHNICAL.md        this file
├── samples/                tuning photos (see §8)
├── tests/
│   ├── check_samples.py    runs the detector on samples/ and writes tests/out/*_detected.jpg
│   └── out/                generated, git-ignored
└── target_analyzer/
    ├── __init__.py
    ├── __main__.py         entry point, chooses the database path
    ├── detection.py        hole detection (OpenCV)        – no Tk, no SQLite
    ├── stats.py            group statistics, unit conversion – pure Python
    ├── db.py               SQLite persistence and aggregation
    ├── images.py           stored photo copies in <db folder>/images/ (resize, JPEG, path resolution)
    └── ui.py               Tkinter application (the only module that imports the others)
```

Dependency direction: `ui.py` → `detection.py`, `stats.py`, `db.py`, `images.py`.
The lower modules do not import each other except `db.py` → `stats.py` (for
`GroupStats`, `mean_std`, `percentile`) and `db.py` → `images.py` (to delete
orphaned copies). Keep it that way: detection, stats, db and
images must stay testable without a display.

Runtime: Python 3.10+ (uses `X | None` annotations with `from __future__ import
annotations`), Tkinter from the standard library, SQLite from the standard library.
OpenCV is the *headless* build (no GUI functions such as `cv2.imshow`).

## 3. Data model

### 3.1 In-memory (`detection.py`)

```python
@dataclass
class Disc:            # largest circular black shape found
    cx, cy, r: float   # pixels, ORIGINAL image coordinates
    solid: bool        # True = filled disc (25 m pistol target), False = outer band of a ring target

@dataclass
class Impact:          # one shot
    x, y: float        # pixels, ORIGINAL image coordinates
    count: int = 1     # always 1 after splitting (kept for future use)
    area: float = 0.0  # blob area / count, original-pixel units
    manual: bool       # True if added by the user (drawn with a dashed outline)
    blob_id: int       # index of the torn blob it was split from, -1 if isolated
    group: int = 0     # index into the group list

@dataclass
class Group:           # a set of shots measured together
    label: str         # "G1", "G2", ... or user-given
    cx, cy, r: float | None   # printed target circle (pixels) when the group comes from one

@dataclass
class DetectionResult:
    disc: Disc | None
    impacts: list[Impact]
    blobs: list[np.ndarray]   # OpenCV contours (original coordinates), for drawing
    single_hole_area: float   # reference area used to classify blobs
    debug: dict               # work-resolution boolean masks: ink, midtone, coloured, candidates
    targets: list[Disc]       # every circular black shape (target diagrams), largest first
    groups: list[Group]       # impacts[i].group indexes this list
```

The group index is an in-memory concept: deleting a group renumbers the indexes of the
following groups (`App.delete_group`). The `Group.label` is what the user sees and what
is stored.

### 3.2 Statistics (`stats.py`)

All physical quantities are stored in **millimetres**. `MM_PER_UNIT = {"mm": 1,
"cm": 10, "inch": 25.4}`; `to_unit()` / `from_unit()` convert for display and input.

```python
@dataclass
class GroupStats:
    n: int
    width_mm, height_mm: float       # extent of the centres along x / y
    diameter_mm: float               # extreme spread: max pairwise centre distance (O(n²), n ≤ ~100)
    center_x_px, center_y_px: float  # centroid, pixels
```

`group_stats(points_px, px_per_mm)` returns `None` for an empty list or a non-positive
scale. `mean_std(values)` returns the mean and the *sample* standard deviation
(ddof = 1; 0 when fewer than two values). `percentile(values, q)` is the q-th
percentile with linear interpolation between ranks (same convention as NumPy's
default); it is used for the P90 diameter.

`summarize_groups(list[GroupStats])` is the one aggregation used at every level
(photo, selection of records): `{metric: (mean, std)}` for `width_mm, height_mm,
diameter_mm, n_shots`, plus `count` (groups), `total_shots` and `diameter_p90_mm`.
Groups are pooled, never weighted by photo.

`cluster_points(points, max_dist)` is single-linkage clustering with a union-find
(O(n²) pairs, fine for a few hundred shots): two shots belong together when a chain
of shots at most `max_dist` apart joins them. Clusters are numbered in reading order
(row bands of `max_dist`, then x). It backs the "Auto-group: distance" button.

### 3.3 Database (`db.py`)

SQLite file, default `<repo>/targets.db` (see `__main__.py`). Schema is created with
`CREATE TABLE IF NOT EXISTS` on every start, then `_migrate()` upgrades older files;
`PRAGMA user_version` holds the schema version (`SCHEMA_VERSION = 2`).

A **session** is one photo. It owns one or more **groups**; every impact belongs to a
group.

```sql
CREATE TABLE sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL,      -- ISO 8601, when saved
    shot_date       TEXT,               -- free text from the form (default: today ISO)
    image_path      TEXT,               -- "images/<stamp>_<stem>.jpg", relative to the db folder
                                        -- (absolute path in records from older versions)
    weapon          TEXT,               -- free text, used for filtering (exact match)
    distance        TEXT,
    ammo            TEXT,
    notes           TEXT,
    unit            TEXT NOT NULL,      -- display unit chosen when saved ("cm" | "inch")
    px_per_mm       REAL NOT NULL,      -- scale of the photo
    n_groups        INTEGER NOT NULL DEFAULT 1,
    n_shots         INTEGER NOT NULL,   -- total over the groups
    width_mm        REAL NOT NULL,      -- MEAN over the groups (= the group value when n_groups = 1)
    height_mm       REAL NOT NULL,
    diameter_mm     REAL NOT NULL
);
CREATE TABLE groups (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,       -- display order
    label       TEXT NOT NULL,
    cx_px       REAL, cy_px REAL, r_px REAL,   -- target circle in STORED-copy pixels, or NULL
    n_shots     INTEGER NOT NULL,
    width_mm    REAL NOT NULL,
    height_mm   REAL NOT NULL,
    diameter_mm REAL NOT NULL
);
CREATE TABLE impacts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    group_id    INTEGER REFERENCES groups(id) ON DELETE CASCADE,
    x_px        REAL NOT NULL,          -- pixels of the STORED copy (see §3.4)
    y_px        REAL NOT NULL,
    manual      INTEGER NOT NULL DEFAULT 0
);
```

`PRAGMA foreign_keys = ON` is set per connection so deleting a session deletes its
groups and impacts. Empty groups (no shot) are not stored: `App.save_record` skips them.

Migrations in `Database._migrate()`:

- drop the `mean_radius_mm` column of the first versions (`ALTER TABLE ... DROP
  COLUMN`, needs SQLite 3.35+; silently kept otherwise);
- `user_version < 2` → `_migrate_v2()`: adds `sessions.n_groups` and
  `impacts.group_id`, creates one group `G1` per session that has none, copying the
  session statistics, and attaches the session's impacts to it. It is idempotent (only
  sessions without groups are touched), so an interrupted upgrade finishes next start.

`Database.save_session(groups: list[GroupRecord], px_per_mm, unit, **meta)` writes the
session row (totals and means from `summarize_groups`), the group rows and the
impacts. `get_groups(sid)` / `get_impacts(sid)` read them back; impacts carry
`group_id` and the UI maps it to the group index by position.

`Database.aggregate(ids)` pools the **groups** of the given sessions and returns the
`summarize_groups` dict plus `sessions` (number of sessions). A 25-diagram card thus
weighs 25 times a single-group photo; the P90 is over groups.

### 3.4 Stored photo copies (`images.py`)

`store_image(image_bgr, db_path, source_name) -> (relative_path, factor)` writes a
JPEG copy to `<db folder>/images/<YYYYmmdd_HHMMSS>_<sanitised stem>.jpg`, downscaled
with `INTER_AREA` so the longest side is at most `MAX_SIDE = 2500` px, quality
`JPEG_QUALITY = 80` (≈ 0.5 MB for a 2500 px target photo; a PNG was ≈ 4 MB). The
file is written through `cv2.imencode` + `Path.write_bytes` so non-ASCII paths work.
Records saved by the short-lived PNG version keep their `.png` path and load fine.

`Database.delete_session(sid)` deletes the row (impacts cascade) and then, if no
other session references the same `image_path`, calls `delete_stored_image`, which
only ever removes files inside `<db folder>/images/` (absolute paths from older
records are never touched). It returns True when a file was removed.
A name clash appends `_2`, `_3`, ...

`factor` (≤ 1) is the ratio stored-copy pixels / original pixels. `App.save_record`
multiplies every impact coordinate, every group circle and `px_per_mm` by it before
calling `save_session`, so a record is self-consistent with its copy and `Load record`
(`resolve_image_path`) shows the impacts at the right place. Group statistics are
unaffected (they were computed in mm before saving). The original photo is never
referenced again.

## 4. Detection algorithm (`detection.py`)

Entry point:

```python
detect_impacts(image_bgr, paper_ratio=0.84, sat_thresh=95, open_kernel=11,
               min_area=150.0, shots_fired=None, hole_area_hint=None,
               ink_ratio=0.45) -> DetectionResult
```

All parameters except `hole_area_hint` and `ink_ratio` are exposed in the UI.

### 4.1 Working resolution

If the longest side exceeds `WORK_SIZE = 1920`, the image is downscaled
(`INTER_AREA`) and every absolute constant below refers to that scale. Output
coordinates, contours and areas are converted back to original pixels
(`/ scale`, areas `/ scale²`). `hole_area_hint` is given in original pixels and is
converted *to* work units (`* scale²`). Kernels that must follow the print size use
`f = max(h, w) / 1920` as a multiplier (`_odd(101 * f)` etc.).

### 4.2 Pre-processing

```
blur_bgr = medianBlur(image, 5)          # removes JPEG noise and paper fibres
gray     = BGR2GRAY(blur_bgr)
hue, sat = BGR2HSV(blur_bgr)[..., 0:2]   # hue 0-180, saturation 0-255
chroma   = max(B,G,R) - min(B,G,R)
paper_global = percentile(gray, 95)      # the paper is the brightest large surface
dark_wood = (sat > sat_thresh) & (chroma > 40) & (hue <= 30 | hue >= 170)
ink      = (gray < ink_ratio * paper_global) & ~dark_wood   # printed black (≈ < 100 on a 225 paper)
```

`dark_wood` exists because a wooden backing seen through a hole in deep shadow is as
dark as print (gray 30–55 on the 22 Hunter card) but strongly orange (sat 170–220,
hue 9–16), whereas print is grey. Such pixels must not be ink, otherwise both hole rules
below exclude them. The chroma floor keeps warm-lit print (chroma ≤ 40 after the median)
in the ink mask: lowering it to 20–35 turned the thin ring lines of IMG_5861 and the
band interiors of IMG_5862 into candidates. The hue test rejects the purple/green
chromatic fringes along print edges. Very dark wood with chroma below 40 (gray ≈ 30,
cells 16 and 11 of the card) is still missed and needs a manual add.

### 4.3 Disc detection (`find_disc`)

Ink mask → closing with a 15 px ellipse (fills the thin white ring lines and digits)
→ largest external contour → `minEnclosingCircle`. Rejected if radius < 5 % of the
image or if `contourArea / (π r²) < 0.7` (not circular). `solid` is true when at
least 75 % of the pixels inside 0.95 r are ink: a filled disc. A ring target gives
`solid = False` (its white centre lowers the fill to ≈ 0.5). The UI uses the circle
for the "auto scale" tool in both cases.

### 4.4 Local paper level and the two pixel rules

```
local = GaussianBlur(morphologyEx(gray, MORPH_CLOSE, ellipse(101·f)), 5)
rel   = gray / local
```

A **grey-level closing** (dilate then erode) replaces every dark object smaller than
the kernel (holes, ring bands up to ~100 px, print) by the surrounding paper value,
but reproduces large-scale illumination steps exactly. This is what makes hard phone
shadows harmless; an earlier version used a plain dilation and produced a strip of
false positives along every shadow edge.

```
midtone  = (rel < paper_ratio) & ~ink                       # grey backing, holes on white paper
coloured = (sat > sat_thresh) & (gray < 0.9·paper_global) & ~ink   # wood backing
cand     = midtone | coloured
```

`coloured` deliberately uses the *global* paper level: a torn region wider than the
closing kernel is not filled by the closing, so `rel ≈ 1` inside it and the midtone
rule misses its interior; saturation does not have that problem. The
`gray < 0.9·paper_global` term rejects warm-lit bright paper; the `sat > 95` default
rejects shadowed warm paper (measured 75–90) while wood measures 120–180.

### 4.5 Solid-disc override

If a solid disc was found, inside 0.96 r the rules are replaced:

```
cand[inside] = (gray > 0.65·paper_global) | coloured
```

Reason: strongly lit black print reads as mid-grey (up to ≈ 125 on a 230 paper) and
would otherwise be flagged; inside the disc the holes are *bright* torn paper instead.

### 4.6 Morphology and blob filtering

```
mask   = close(cand, 5)          # joins the ragged edges of a hole
opened = open(mask, open_kernel) # erases ring lines, digit strokes, anti-aliased print edges
contours = findContours(opened, RETR_EXTERNAL)
```

A contour is kept when all of the following hold:

| Test | Rationale |
|---|---|
| `min_area ≤ area ≤ 0.15·h·w` | drops specks and background surfaces |
| bounding box does not touch the image border | paper edge, table, plastic sleeve |
| `area / convexHullArea ≥ 0.2` | thin arcs along shadow boundaries are not holes |

### 4.7 Single-hole reference and shot count

```
single = hole_area_hint·scale²  if given, else median(smallest half of the areas)
multi  = areas > 1.8·single
```

For multi blobs the count is:

- if `shots_fired` is known and exceeds the number of single blobs: the remaining shots
  are shared proportionally to area (floor, at least 1 each, remainder to the largest
  fractional parts);
- otherwise `clip(round(sqrt(area / single)), 2, 25)`. The square root was fitted on
  the samples: torn regions lose whole flaps of paper, so area grows much faster than
  linearly with the number of shots.

Each multi blob is then split into `count` centres by `cv2.kmeans` on its filled
pixel coordinates (`_split_blob`). Single blobs use the contour centroid.

The UI passes `hole_area_hint = π·(Ø/2 · px_per_mm)²` with Ø from the "Hole core Ø
(mm)" field (default 4.0) whenever the scale is set. Without a scale the fallback
median is fragile when many blobs are noise, which is why the status line asks the
user to set the scale first.

### 4.8 Targets and groups

`find_targets(gray, ink_ratio, min_r_frac=0.03)` reuses the disc search for *every*
closed ink contour: circularity ≥ 0.7, radius ≥ 3 % of the longest side. It then drops
shapes smaller than 60 % of the median radius and shapes whose centre lies inside a
larger kept circle, so the inner rings exposed when a band is torn open are not extra
targets. `find_disc` is the same search restricted to radius ≥ 5 % and returns the
largest shape. `detect_impacts` runs `find_targets` at work resolution and converts the
circles back to original pixels.

`assign_groups(impacts, targets)` gives every impact the index of the nearest target
centre and returns one `Group` per target (empty ones included, so the user sees the
diagrams without shots), labelled `G1…Gn` in reading order (`_reading_order`: rows are
bands of 1.2 × median radius, then x). With fewer than two targets everything goes into
a single `G1` that carries the disc circle when there is one. Shots far from every
diagram (on the blank paper between cells) still go to the nearest one; the UI's
*Move impact to selected group* tool corrects that.

On the 22 Hunter card (`samples/fe341a73-….jpg`, 1440×1920, 25 numbered diagrams plus
small ones in the margins) 27 of the 30 circles are found; the three missed ones have a
band broken by the grid line or pencil marks. The circles found are correct and the
assignment makes no mistake on the 89 default-parameter impacts.

### 4.9 Debug output

`DetectionResult.debug` holds the boolean masks `ink`, `midtone`, `coloured`,
`candidates` at work resolution. `tests/check_samples.py` shows how to visualise a
run; to inspect the masks, stack them with `np.hstack` after converting to `uint8`.

## 5. User interface (`ui.py`)

`App(tk.Tk)` with a `ttk.Notebook`: tab *Analyse* and tab *Records & averages*.

State held on the `App` instance:

| Attribute | Meaning |
|---|---|
| `image_path`, `image_bgr`, `pil_image` | the loaded photo (BGR for OpenCV, PIL RGB for display) |
| `view_scale`, `view_off` | fit-to-canvas transform; `_to_view()` / `_to_image()` convert coordinates both ways |
| `mode` | `None`, `"scale"`, `"add"`, `"remove"` or `"assign"`; `on_click()` dispatches on it |
| `scale_points`, `px_per_mm` | the 2-point tool and the resulting scale |
| `impacts: list[Impact]`, `disc` | detection result and user edits; `impact.group` is the group index |
| `groups: list[Group]`, `cur_group` | the groups and the index selected in the group table |
| `group_stats: dict[int, GroupStats]`, `summary` | per-group statistics (only groups with shots, only with a scale) and the `summarize_groups` dict for the photo |
| `var_*` | Tk variables bound to the parameter entries |

Group handling: `_members()` (group index → impact indexes), `_ensure_group()` (creates
`G1` when the user adds a shot with no group), `new_group` / `rename_group` /
`delete_group` (deleting removes the group's impacts after confirmation and renumbers
the indexes above it), `auto_group_circles` (`find_targets` + `assign_groups` on the
current impacts), `auto_group_distance` (`cluster_points`; threshold asked in the
display unit, default 5 cm, or in pixels without a scale), the *assign* click mode
(moves the nearest impact into `cur_group`). Labels are kept unique
(`_unique_label`). The *add* mode adds to `cur_group`.

Drawing (`redraw`): each group has a colour from `GROUP_COLOURS` (cycled); its target
circle (dashed, solid and thick when selected) with the label, its impacts numbered
within the group (manual ones dashed), its bounding box and centroid cross. The green
disc from the auto-scale tool is drawn only while no group carries a circle.

Other key methods: `open_image` / `_load_image` (Pillow loader `load_image()` with EXIF
transpose and optional `pillow_heif` opener; returns BGR), `analyze` (builds the
hint, calls `detect_impacts`, takes `impacts` and `groups` from the result),
`update_stats` (recomputes every `GroupStats`, the photo summary, fills the group table
and the labels), `save_record` (one `GroupRecord` per non-empty group, coordinates and
circles scaled by the stored-copy factor), `_refresh_records`, `compute_average`
(pooled group statistics: mean ± std and P90 in both cm and inch, independent of the
display unit), `load_record` (restores groups and impacts, mapping `group_id` to the
group position), `delete_records` (rows and orphaned stored copies, see
`Database.delete_session`).

The canvas image is rebuilt on every `<Configure>` event (resize); there is no zoom.
Click tolerance for *Remove* and *Move* is `CLICK_TOLERANCE_PX` = 25 screen pixels.
The scale dialog is a `simpledialog.Dialog` subclass (`ScaleDialog`) returning
`(value, unit)`.

`__main__.py`: `python -m target_analyzer [db_path]`; default database is
`<repo>/targets.db`.

## 6. Testing

There is no unit-test suite yet. Two checks exist:

1. `tests/check_samples.py` – runs `detect_impacts` with defaults on every image in
   `samples/`, prints blob/impact/target/group counts, the group sizes (scale assumed
   from the detected circle = 200 mm, so the cm values are only meaningful for the
   solid-disc targets) and the shots per group, and writes annotated images to
   `tests/out/` (target circles with `label:count` in green). Run it after any change
   to `detection.py` and look at the pictures; counts alone can hide regressions.
2. A scripted Tk smoke test was used during development (load the card → scale →
   analyse → add / move / remove a shot → delete an empty and a full group → new and
   rename → save → records table → average → load record and compare → distance
   clustering on `3.jpg` → load a migrated v1 record). It is not committed; `App`
   methods can be driven directly as long as a display is available
   (`app.after(..., run); app.mainloop()`), with `simpledialog.askfloat/askstring`,
   `messagebox.askyesno/showinfo` and `ScaleDialog` patched via `unittest.mock`. Test
   the database layer on a *copy* of `targets.db` (the file in the repository holds
   real records).

When printing the aggregate text on a Windows console set `PYTHONIOENCODING=utf-8`
(it contains `±` and `σ`).

## 7. Expected behaviour on the target styles

| Style | Example | Hole appearance | Rules that fire |
|---|---|---|---|
| Solid black disc, white ring lines and digits inside (25 m precision pistol, ISSF, black Ø 200 mm) | `samples/1-3.jpg` | bright torn paper on black; holes outside the disc are grey on white | inside: bright > 0.65 paper; outside: midtone |
| Ring target, white centre, thin black rings, wide black bands, several diagrams per card (10 m air rifle/pistol) | `samples/IMG_58xx.jpeg` | grey backing (gray ≈ 0.65 paper) or wood backing (orange, sat 120–180); also holes on the blank paper between diagrams | midtone and coloured |
| Card of 25 small ring diagrams (.22 "Hunter" card, 5 × 5 numbered cells plus margin diagrams), one group per cell | `samples/fe341a73-….jpg` | wood backing, from bright orange to nearly black in the phone shadow; black backing in a few cells | coloured, midtone, `dark_wood` exclusion; `find_targets` for the groups |

Measured levels on the samples (after the 5 px median): paper 209–235, ink 15–60
(up to 125 in a lit corner of the disc), grey backing ≈ 150, wood 108–150 gray with
hue ≈ 16/180, shadowed warm paper sat 75–90.

## 8. Sample set and tuning history

| File | Content | Notes |
|---|---|---|
| `samples/1.jpg` | .45, 15 m, solid disc | large torn region (≈ 6 shots), one hole on the paper below the disc |
| `samples/2.jpg` | .45, 10 m, solid disc | two big torn regions, few isolated holes |
| `samples/3.jpg` | SP01, 10 m, solid disc | ≈ 15 clean holes, two small clusters |
| `samples/IMG_5857–5863.jpeg` | 10 m air targets, iPhone 3024×4032 | grey or wood backing, hard phone shadows, crumpled plastic sleeve, coloured pasters on IMG_5860 |
| `samples/fe341a73-….jpg` | .22 Hunter card, 25 cells, 1440×1920 | the multi-group sample: ≈ 90 shots in 23 cells, 2–8 per cell; half the card in a hard shadow; two cells with a black backing (undetectable by colour) |

Failure modes fixed during tuning, in order: (1) ring lines and digits detected →
opening; (2) lit black print detected as midtone → disc override; (3) 4032 px photos
→ work resolution 1920; (4) strips along hard shadow edges → closing-based local
level; (5) chromatic fringes and warm-lit paper as "coloured" → `gray < 0.9 paper`
and `sat > 95`; (6) a stray tiny blob shrinking the reference area so every hole was
split → median of the smaller half plus UI hint; (7) thin shadow arcs → solidity.

(8) dark wood in shadow classified as ink and therefore invisible → `dark_wood`
exclusion (§4.2); on IMG_5860–5863 this also joined the previously fragmented torn
regions into whole blobs (blob count 21–27 → 5–11 with the same or fewer impacts).

Known leftovers: a thin arc at a shadow boundary on IMG_5861; brightly lit wood
(> 0.9 paper) partly missed; nearly black wood (chroma < 40) and black backings missed;
without a scale, holes may still be over-split; coloured pasters are detected as
impacts by design (they usually cover real holes); three of the 30 circles of the
Hunter card are not found (band broken by a grid line or pencil marks).

## 9. Extension points

- **Per-target-type presets**: store parameter sets (paper ratio, saturation, kernel,
  hole Ø, disc diameter) and expose a combobox in `_build_analyze`.
- **Perspective correction**: detect the outer circle as an ellipse (`cv2.fitEllipse`
  on the disc contour) and warp to a circle before detection; the scale tool would
  then be exact in every direction.
- **Scoring**: ring radii are known for ISSF targets; with each `Group`'s circle and
  the scale, each impact's ring value is `floor` of its distance to the group centre.
- **Schema changes**: bump `SCHEMA_VERSION`, add a `_migrate_vN()` step in
  `Database._migrate()` guarded by `PRAGMA user_version`; existing users have
  `targets.db` files. Keep every step idempotent.
- **Group labels from the print**: the cell numbers of a card could be read (template
  matching of the digits) to label the groups 1–25 instead of G1–G27 in reading order.
- **Tests**: `detection.py` and `stats.py` are display-free; pytest cases can assert
  impact counts within a tolerance on `samples/` and exact values on synthetic images.
