"""SQLite persistence for analysed targets.

One *session* is one photo (with its metadata and scale). A session holds one
or more *groups* (one per target diagram, or per series); every impact belongs
to a group. Group statistics are stored per group; the session row keeps the
total number of shots and the mean of the group metrics for quick listing.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .images import delete_stored_image
from .stats import GroupStats, summarize_groups

SCHEMA_VERSION = 2

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
    n_groups        INTEGER NOT NULL DEFAULT 1,
    n_shots         INTEGER NOT NULL,
    width_mm        REAL NOT NULL,
    height_mm       REAL NOT NULL,
    diameter_mm     REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS groups (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    label       TEXT NOT NULL,
    cx_px       REAL,
    cy_px       REAL,
    r_px        REAL,
    n_shots     INTEGER NOT NULL,
    width_mm    REAL NOT NULL,
    height_mm   REAL NOT NULL,
    diameter_mm REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS impacts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    group_id    INTEGER REFERENCES groups(id) ON DELETE CASCADE,
    x_px        REAL NOT NULL,
    y_px        REAL NOT NULL,
    manual      INTEGER NOT NULL DEFAULT 0
);
"""


@dataclass
class GroupRecord:
    """One group to save: its label, optional target circle (px of the stored
    copy), statistics and impacts as (x_px, y_px, manual)."""
    label: str
    stats: GroupStats
    points_px: list[tuple[float, float, bool]]
    cx_px: float | None = None
    cy_px: float | None = None
    r_px: float | None = None
    extra: dict = field(default_factory=dict)


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self._migrate()

    # -- schema upgrades --------------------------------------------------
    def _migrate(self):
        """Bring databases created by older versions up to date."""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(sessions)")}
        if "mean_radius_mm" in cols:  # dropped: only the diameter matters
            try:
                self.conn.execute("ALTER TABLE sessions DROP COLUMN mean_radius_mm")
                self.conn.commit()
            except sqlite3.OperationalError:
                pass  # SQLite < 3.35: the unused column stays, harmless
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 2:
            self._migrate_v2()
        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.conn.commit()

    def _migrate_v2(self):
        """Version 1 had no groups: every session was a single group. Create one
        group per session carrying the session statistics and attach the impacts."""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(sessions)")}
        if "n_groups" not in cols:
            self.conn.execute("ALTER TABLE sessions ADD COLUMN n_groups INTEGER NOT NULL DEFAULT 1")
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(impacts)")}
        if "group_id" not in cols:
            self.conn.execute(
                "ALTER TABLE impacts ADD COLUMN group_id INTEGER REFERENCES groups(id) ON DELETE CASCADE")
        orphans = self.conn.execute(
            "SELECT id, n_shots, width_mm, height_mm, diameter_mm FROM sessions "
            "WHERE id NOT IN (SELECT DISTINCT session_id FROM groups)").fetchall()
        for s in orphans:
            cur = self.conn.execute(
                "INSERT INTO groups (session_id, position, label, n_shots, width_mm, height_mm, diameter_mm) "
                "VALUES (?, 0, 'G1', ?, ?, ?, ?)",
                (s["id"], s["n_shots"], s["width_mm"], s["height_mm"], s["diameter_mm"]))
            self.conn.execute("UPDATE impacts SET group_id = ? WHERE session_id = ? AND group_id IS NULL",
                              (cur.lastrowid, s["id"]))
        self.conn.commit()

    def close(self):
        self.conn.close()

    # -- sessions ---------------------------------------------------------
    def save_session(self, groups: list[GroupRecord], px_per_mm: float, unit: str, *,
                     image_path: str = "", shot_date: str = "", weapon: str = "",
                     distance: str = "", ammo: str = "", notes: str = "") -> int:
        """Store one photo with its groups and impacts. Returns the session id.

        The session row gets the total shot count and the mean of the group
        metrics (so a single-group record reads exactly like before).
        """
        if not groups:
            raise ValueError("a session needs at least one group")
        summary = summarize_groups([g.stats for g in groups])
        cur = self.conn.execute(
            """INSERT INTO sessions (created_at, shot_date, image_path, weapon, distance,
               ammo, notes, unit, px_per_mm, n_groups, n_shots, width_mm, height_mm, diameter_mm)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (datetime.now().isoformat(timespec="seconds"), shot_date, image_path, weapon,
             distance, ammo, notes, unit, px_per_mm, len(groups), summary["total_shots"],
             summary["width_mm"][0], summary["height_mm"][0], summary["diameter_mm"][0]))
        sid = cur.lastrowid
        for pos, g in enumerate(groups):
            st = g.stats
            gcur = self.conn.execute(
                """INSERT INTO groups (session_id, position, label, cx_px, cy_px, r_px,
                   n_shots, width_mm, height_mm, diameter_mm) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (sid, pos, g.label, g.cx_px, g.cy_px, g.r_px, st.n, st.width_mm, st.height_mm,
                 st.diameter_mm))
            gid = gcur.lastrowid
            self.conn.executemany(
                "INSERT INTO impacts (session_id, group_id, x_px, y_px, manual) VALUES (?,?,?,?,?)",
                [(sid, gid, x, y, int(m)) for x, y, m in g.points_px])
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

    def get_groups(self, sid: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM groups WHERE session_id = ? ORDER BY position, id", (sid,)).fetchall()

    def get_impacts(self, sid: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM impacts WHERE session_id = ? ORDER BY id", (sid,)).fetchall()

    def delete_session(self, sid: int) -> bool:
        """Delete a session with its groups and impacts. The stored photo copy is
        deleted too when no other session references it. Returns True if the
        file was removed."""
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
        """Aggregate the group metrics (mm) over every group of the given sessions.

        Returns the dict of `stats.summarize_groups` ({metric: (mean, sample_std)}
        for width_mm, height_mm, diameter_mm, n_shots; "count" = number of
        groups; "total_shots"; "diameter_p90_mm") plus "sessions": the number
        of sessions. Groups are pooled, so a card with 25 diagrams weighs 25
        times a single-group photo.
        """
        if not session_ids:
            return {}
        marks = ",".join("?" * len(session_ids))
        rows = self.conn.execute(
            f"SELECT n_shots, width_mm, height_mm, diameter_mm "
            f"FROM groups WHERE session_id IN ({marks})", session_ids).fetchall()
        out = summarize_groups([GroupStats(int(r["n_shots"]), float(r["width_mm"]), float(r["height_mm"]),
                                           float(r["diameter_mm"]), 0.0, 0.0) for r in rows])
        out["sessions"] = len(session_ids)
        return out
