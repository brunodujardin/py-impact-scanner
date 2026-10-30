# Target impact analyser

Desktop app (Python, Tkinter, OpenCV, SQLite) that detects bullet holes on a photo
of a paper target, measures the group size and stores the results in a SQLite
database. Photos can be JPEG, PNG, HEIC/HEIF (iPhone), BMP, TIFF or WebP; EXIF
rotation is applied automatically. Large photos are analysed at 1920 px.

Documentation: [docs/USER_GUIDE.md](docs/USER_GUIDE.md) for users,
[docs/TECHNICAL.md](docs/TECHNICAL.md) for developers and AI agents.

## Setup and run (Windows, PowerShell)

```powershell
.\setup.ps1   # once: creates .venv and installs requirements.txt
.\run.ps1     # starts the app (runs setup.ps1 itself if .venv is missing)
```

If scripts are blocked: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.
The database is `targets.db` next to this file, with a JPEG copy of every saved photo
(max 2500 px) in `images\` beside it; pass another path to `run.ps1` to change it.

## Workflow

1. **Open image** (JPEG, PNG, HEIC, ...).
2. **Scale**: *Measure 2 points*: click two points on the photo whose real distance
   you know, then enter the length and pick cm or inch. Or *Auto: from outer black
   circle* and enter the diameter of the black (200 mm on a 25 m precision target).
3. **Analyse impacts**. Shots are drawn as numbered circles in the colour of their
   group (dashed when added by hand). A photo with several target diagrams (a card of
   25 small targets) gets one group per diagram automatically; a single target gives
   one group. Torn regions with several shots are split by an estimated count; type
   the number of *Shots fired* before analysing to make that count exact. Use *Add
   impact* / *Remove impact* / *Move impact to selected group* and click on the image
   to correct the detection.
4. **Groups** (right panel): *New*, *Rename*, *Delete*, *Auto-group: target circles*,
   *Auto-group: distance*. The selected group shows its **width**, **height** and
   **diameter** (extreme spread, centre to centre), in cm or inch; *This image* shows
   the mean ± standard deviation and the P90 diameter over the groups of the photo.
5. Fill in weapon / distance / ammo / notes and **Save to database** (one record per
   photo, with all its groups and shots).
6. Tab **Records & averages**: filter by weapon, select records and press
   *Average of selection* for the mean and sample standard deviation of every metric
   over all the groups of the selected records, and the 90th-percentile diameter,
   shown in both cm and inch. *Load record* reopens a saved target with its groups
   and impacts.

## Detection parameters

| Parameter | Meaning |
|---|---|
| Hole max brightness (% of paper) | A pixel darker than this share of the local paper brightness (grey or wood backing) counts as a hole. Lower it if shadows are picked up, raise it if light-grey holes are missed. |
| Colour saturation | Pixels more saturated than this (wood backing, coloured pasters) count as a hole. |
| Open kernel (px) | Size of the opening that erases ring lines and digits; keep it smaller than one hole. |
| Min area (px²) | Smaller blobs are ignored. |
| Hole core Ø (mm) | Expected size of the bright/grey core of a single hole, used with the scale to tell single holes from torn multi-shot regions. |
| Shots fired | Optional; makes the split of torn regions exact. |

Target styles supported: solid black disc (25 m precision pistol), ring targets with
a white centre (10 m air rifle / pistol cards, grey or wood backing) and cards of
25 small ring diagrams (.22 Hunter), each diagram becoming a group.

`tests\check_samples.py` runs the detector on the photos in `samples\` and writes
annotated images to `tests\out\`, which is the quickest way to tune the parameters on
new targets:

```powershell
.venv\Scripts\python tests\check_samples.py
```
