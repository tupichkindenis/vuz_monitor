"""SQLite state between hourly runs.

Keeps the latest snapshot per watch (plus recent history for possible future
graphing) and a small key/value table for things like the daily-heartbeat marker.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from .models import Snapshot, snapshot_from_dict, snapshot_to_dict

HISTORY_PER_WATCH = 48         # snapshots: keep ~2 days of hourly full snapshots
HISTORY_RETENTION_DAYS = 120   # code_history: keep ~4 months of compact per-code points
MSK = ZoneInfo("Europe/Moscow")


def _to_int_bool(v) -> Optional[int]:
    return None if v is None else int(bool(v))


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
        # Compact per-code time series for dashboard sparklines. One tiny row per
        # (watch, code) per run; place/final_score nullable (NULL = «выбыл»).
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS code_history (
                   watch_id     TEXT NOT NULL,
                   code         TEXT NOT NULL,
                   ts           TEXT NOT NULL,   -- UTC ISO
                   place        INTEGER,
                   final_score  REAL,
                   passing_main INTEGER,
                   passing_real INTEGER,
                   consent      INTEGER,
                   contract     INTEGER
               )"""
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_hist ON code_history(watch_id, code, ts)"
        )
        # Compact per-run aggregate for the score-loading tracker. One row per run
        # per tracked competition. `buckets` is JSON {app_number_bucket: [total, no_score]}.
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS score_progress (
                   source   TEXT NOT NULL,   -- = watch_id of the competition
                   ts       TEXT NOT NULL,   -- UTC ISO, = snapshot fetched_at
                   total    INTEGER NOT NULL,
                   no_score INTEGER NOT NULL,
                   buckets  TEXT NOT NULL,   -- JSON {"1200000":[492,121], ...}
                   PRIMARY KEY (source, ts)
               )"""
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

    def load_all_snapshots(self, watch_id: str) -> list:
        """Every stored snapshot for a watch, oldest first (for backfilling the
        score-loading history from data already on disk)."""
        rows = self.conn.execute(
            "SELECT payload FROM snapshots WHERE watch_id=? ORDER BY fetched_at ASC",
            (watch_id,),
        ).fetchall()
        return [snapshot_from_dict(json.loads(r[0])) for r in rows]

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

    # --- code_history (dashboard sparklines) --- #
    def append_history(
        self, watch_id, code, ts, place, final_score,
        passing_main, passing_real, consent, contract,
    ) -> None:
        """Append one observation. `place`/`final_score` may be None («выбыл»)."""
        self.conn.execute(
            "INSERT INTO code_history (watch_id, code, ts, place, final_score, "
            "passing_main, passing_real, consent, contract) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                watch_id, code, ts,
                (int(place) if place is not None else None),
                (float(final_score) if final_score is not None else None),
                _to_int_bool(passing_main), _to_int_bool(passing_real),
                _to_int_bool(consent), _to_int_bool(contract),
            ),
        )
        self._prune_history(watch_id, code)
        self.conn.commit()

    def _prune_history(self, watch_id, code, days: int = HISTORY_RETENTION_DAYS) -> None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        self.conn.execute(
            "DELETE FROM code_history WHERE watch_id=? AND code=? AND ts < ?",
            (watch_id, code, cutoff),
        )

    def load_history(self, watch_id, code, days: int = HISTORY_RETENTION_DAYS) -> list:
        """Daily-downsampled points for a code, oldest first. One point per MSK day
        (the day's last observation). Each point: {day, place, final_score,
        passing_real, passing_main, consent, contract}."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = self.conn.execute(
            "SELECT ts, place, final_score, passing_main, passing_real, consent, contract "
            "FROM code_history WHERE watch_id=? AND code=? AND ts >= ? ORDER BY ts ASC",
            (watch_id, code, cutoff),
        ).fetchall()
        by_day = {}  # msk_day -> point (last row of that day wins, rows are ts-ASC)
        for ts, place, score, pmain, preal, consent, contract in rows:
            try:
                day = datetime.fromisoformat(ts).astimezone(MSK).date().isoformat()
            except (ValueError, TypeError):
                continue
            by_day[day] = {
                "day": day,
                "place": place,
                "final_score": score,
                "passing_main": None if pmain is None else bool(pmain),
                "passing_real": None if preal is None else bool(preal),
                "consent": None if consent is None else bool(consent),
                "contract": None if contract is None else bool(contract),
            }
        return [by_day[d] for d in sorted(by_day)]

    # --- score_progress (МИРЭА score-loading tracker) --- #
    def append_score_progress(
        self, source: str, ts: str, total: int, no_score: int, buckets: dict
    ) -> None:
        """Store one run's aggregate for a competition. Idempotent on (source, ts)
        so backfilling from stored snapshots can't create duplicates."""
        self.conn.execute(
            "INSERT OR REPLACE INTO score_progress (source, ts, total, no_score, buckets) "
            "VALUES (?,?,?,?,?)",
            (source, ts, int(total), int(no_score),
             json.dumps(buckets, ensure_ascii=False)),
        )
        self._prune_score_progress(source)
        self.conn.commit()

    def _prune_score_progress(self, source: str, days: int = HISTORY_RETENTION_DAYS) -> None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        self.conn.execute(
            "DELETE FROM score_progress WHERE source=? AND ts < ?", (source, cutoff)
        )

    def load_score_progress(self, source: str, days: int = HISTORY_RETENTION_DAYS) -> list:
        """Raw per-run rows for a competition, oldest first (NOT downsampled — the
        24h comparison needs hourly resolution; the trend chart downsamples itself).
        Each point: {ts, total, no_score, buckets} with INTEGER bucket keys."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = self.conn.execute(
            "SELECT ts, total, no_score, buckets FROM score_progress "
            "WHERE source=? AND ts >= ? ORDER BY ts ASC",
            (source, cutoff),
        ).fetchall()
        out = []
        for ts, total, no_score, buckets_json in rows:
            raw = json.loads(buckets_json)
            out.append({
                "ts": ts,
                "total": total,
                "no_score": no_score,
                "buckets": {int(k): v for k, v in raw.items()},
            })
        return out

    def close(self) -> None:
        self.conn.close()
