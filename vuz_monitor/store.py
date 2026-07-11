"""SQLite state between hourly runs.

Keeps the latest snapshot per watch (plus recent history for possible future
graphing) and a small key/value table for things like the daily-heartbeat marker.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Optional

from .models import Snapshot, snapshot_from_dict, snapshot_to_dict

HISTORY_PER_WATCH = 48  # keep ~2 days of hourly snapshots


class Store:
    def __init__(self, path: str = "state.db"):
        self.conn = sqlite3.connect(path)
        self._init()

    def _init(self) -> None:
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS snapshots (
                   watch_id   TEXT NOT NULL,
                   fetched_at TEXT NOT NULL,
                   payload    TEXT NOT NULL
               )"""
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_snap_watch ON snapshots(watch_id, fetched_at)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"
        )
        self.conn.commit()

    def load_prev(self, watch_id: str) -> Optional[Snapshot]:
        row = self.conn.execute(
            "SELECT payload FROM snapshots WHERE watch_id=? ORDER BY fetched_at DESC LIMIT 1",
            (watch_id,),
        ).fetchone()
        if not row:
            return None
        return snapshot_from_dict(json.loads(row[0]))

    def save(self, snap: Snapshot) -> None:
        self.conn.execute(
            "INSERT INTO snapshots (watch_id, fetched_at, payload) VALUES (?, ?, ?)",
            (
                snap.watch_id,
                snap.fetched_at,
                json.dumps(snapshot_to_dict(snap), ensure_ascii=False),
            ),
        )
        self._prune(snap.watch_id)
        self.conn.commit()

    def _prune(self, watch_id: str, keep: int = HISTORY_PER_WATCH) -> None:
        self.conn.execute(
            """DELETE FROM snapshots
                 WHERE watch_id = ?
                   AND fetched_at NOT IN (
                       SELECT fetched_at FROM snapshots
                        WHERE watch_id = ? ORDER BY fetched_at DESC LIMIT ?
                   )""",
            (watch_id, watch_id, keep),
        )

    def get_meta(self, key: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key=?", (key,)
        ).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
