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
3. **Analyse impacts**. Red circles are detected shots, orange ones were added by hand.
   Torn regions with several shots are split by an estimated count; type the number of
   *Shots fired* before analysing to make that count exact. Use *Add impact* /
   *Remove impact* and click on the image to correct the detection.
4. The right panel shows the group: **width**, **height** and **diameter** (extreme
   spread, centre to centre), in cm or inch.
5. Fill in weapon / distance / ammo / notes and **Save to database**.
6. Tab **Records & averages**: filter by weapon, select records and press
   *Average of selection* for the mean and sample standard deviation of every metric
   and the 90th-percentile diameter, shown in both cm and inch.
   *Load record* reopens a saved target with its impacts.

## Detection parameters

| Parameter | Meaning |
|---|---|
| Hole max brightness (% of paper) | A pixel darker than this share of the local paper brightness (grey or wood backing) counts as a hole. Lower it if shadows are picked up, raise it if light-grey holes are missed. |
| Colour saturation | Pixels more saturated than this (wood backing, coloured pasters) count as a hole. |
| Open kernel (px) | Size of the opening that erases ring lines and digits; keep it smaller than one hole. |
| Min area (px²) | Smaller blobs are ignored. |
| Hole core Ø (mm) | Expected size of the bright/grey core of a single hole, used with the scale to tell single holes from torn multi-shot regions. |
| Shots fired | Optional; makes the split of torn regions exact. |

Two target styles are supported: solid black disc (25 m precision pistol) and ring
targets with a white centre (10 m air rifle / pistol cards, grey or wood backing).

`tests\check_samples.py` runs the detector on the photos in `samples\` and writes
annotated images to `tests\out\`, which is the quickest way to tune the parameters on
new targets:

```powershell
.venv\Scripts\python tests\check_samples.py
```
