"""SQLite persistence for analysed targets."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from .images import delete_stored_image
from .stats import GroupStats, mean_std, percentile

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL,
    shot_date       TEXT,
    image_path      TEXT,
    weapon          TEXT,
    distance        TEXT,
    ammo            TEXT,
    notes           TEXT,
    unit            TEXT NOT NULL,
    px_per_mm       REAL NOT NULL,
    n_shots         INTEGER NOT NULL,
    width_mm        REAL NOT NULL,
    height_mm       REAL NOT NULL,
    diameter_mm     REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS impacts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    x_px        REAL NOT NULL,
    y_px        REAL NOT NULL,
    manual      INTEGER NOT NULL DEFAULT 0
);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self):
        """Bring databases created by older versions up to date."""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(sessions)")}
        if "mean_radius_mm" in cols:  # dropped: only the diameter matters
            try:
                self.conn.execute("ALTER TABLE sessions DROP COLUMN mean_radius_mm")
                self.conn.commit()
            except sqlite3.OperationalError:
                pass  # SQLite < 3.35: the unused column stays, harmless

    def close(self):
        self.conn.close()

    # -- sessions ---------------------------------------------------------
    def save_session(self, stats: GroupStats, px_per_mm: float, unit: str,
                     points_px: list[tuple[float, float, bool]], *,
                     image_path: str = "", shot_date: str = "", weapon: str = "",
                     distance: str = "", ammo: str = "", notes: str = "") -> int:
        cur = self.conn.execute(
            """INSERT INTO sessions (created_at, shot_date, image_path, weapon, distance,
               ammo, notes, unit, px_per_mm, n_shots, width_mm, height_mm, diameter_mm)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (datetime.now().isoformat(timespec="seconds"), shot_date, image_path, weapon,
             distance, ammo, notes, unit, px_per_mm, stats.n, stats.width_mm,
             stats.height_mm, stats.diameter_mm))
        sid = cur.lastrowid
        self.conn.executemany(
            "INSERT INTO impacts (session_id, x_px, y_px, manual) VALUES (?,?,?,?)",
            [(sid, x, y, int(m)) for x, y, m in points_px])
        self.conn.commit()
        return sid

    def list_sessions(self, weapon: str | None = None) -> list[sqlite3.Row]:
        q = "SELECT * FROM sessions"
        args: tuple = ()
        if weapon:
            q += " WHERE weapon = ?"
            args = (weapon,)
        q += " ORDER BY created_at DESC"
        return self.conn.execute(q, args).fetchall()

    def get_session(self, sid: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()

    def get_impacts(self, sid: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM impacts WHERE session_id = ? ORDER BY id", (sid,)).fetchall()

    def delete_session(self, sid: int) -> bool:
        """Delete a session and its impacts. The stored photo copy is deleted too
        when no other session references it. Returns True if the file was removed."""
        row = self.conn.execute("SELECT image_path FROM sessions WHERE id = ?", (sid,)).fetchone()
        if row is None:
            return False
        image_path = row["image_path"] or ""
        self.conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
        self.conn.commit()
        if not image_path:
            return False
        still_used = self.conn.execute(
            "SELECT 1 FROM sessions WHERE image_path = ? LIMIT 1", (image_path,)).fetchone()
        if still_used:
            return False
        return delete_stored_image(image_path, self.path)

    def weapons(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT weapon FROM sessions WHERE weapon != '' ORDER BY weapon").fetchall()
        return [r[0] for r in rows]

    # -- aggregates -------------------------------------------------------
    def aggregate(self, session_ids: list[int]) -> dict:
        """Aggregate the group metrics (mm) over the given sessions.

        Returns {metric: (mean, sample_std)} for width_mm, height_mm, diameter_mm
        and n_shots, plus "count" (number of sessions) and "diameter_p90_mm": the
        90th percentile of the session diameters, i.e. 90 % of the sessions had a
        group diameter at or below this value.
        """
        if not session_ids:
            return {}
        marks = ",".join("?" * len(session_ids))
        rows = self.conn.execute(
            f"SELECT width_mm, height_mm, diameter_mm, n_shots "
            f"FROM sessions WHERE id IN ({marks})", session_ids).fetchall()
        out = {}
        for key in ("width_mm", "height_mm", "diameter_mm", "n_shots"):
            out[key] = mean_std([float(r[key]) for r in rows])
        out["count"] = len(rows)
        out["diameter_p90_mm"] = percentile([float(r["diameter_mm"]) for r in rows], 90)
        return out
