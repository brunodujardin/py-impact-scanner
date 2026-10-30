# py-detect-impact

Tkinter + OpenCV desktop app that detects bullet holes on target photos, measures
group size and stores results in SQLite.

- Read `docs/TECHNICAL.md` first: structure, data model, detection algorithm with its
  tuned constants, DB schema, known limitations.
- End-user behaviour is described in `docs/USER_GUIDE.md`.
- Environment: `.\setup.ps1` (creates `.venv`), `.\run.ps1` (starts the app).
  Python: `.venv\Scripts\python`.
- After touching `target_analyzer/detection.py`, run
  `.venv\Scripts\python tests\check_samples.py` and inspect `tests\out\*_detected.jpg`.
- `detection.py` and `stats.py` must stay free of Tk and SQLite imports.
- `targets.db` in the repo holds real records: test `db.py` changes on a copy. Schema
  changes go through `SCHEMA_VERSION` + an idempotent `_migrate_vN()` in `db.py`.
