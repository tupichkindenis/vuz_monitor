"""code_history: append, daily (MSK) downsample, retention window, prune."""
from datetime import datetime, timedelta, timezone

from vuz_monitor.store import Store


def _store():
    return Store(":memory:")


def _today():
    return datetime.now(timezone.utc).date().isoformat()


def _next_day():
    return (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()


def test_daily_downsample_last_obs_wins():
    s = _store()
    base = _today()
    # two observations on the same MSK day → one point, the later one wins
    s.append_history("w1", "1366129", f"{base}T05:00:00+00:00", 50, 250.0, True, False, True, None)
    s.append_history("w1", "1366129", f"{base}T06:00:00+00:00", 47, 251.0, True, True, True, None)
    pts = s.load_history("w1", "1366129")
    assert len(pts) == 1
    assert pts[0]["day"] == base
    assert pts[0]["place"] == 47           # last obs of the day
    assert pts[0]["final_score"] == 251.0
    assert pts[0]["passing_main"] is True
    assert pts[0]["passing_real"] is True
    assert pts[0]["consent"] is True
    s.close()


def test_msk_boundary_not_naive_utc():
    s = _store()
    base = _today()
    # 20:00Z → MSK 23:00 (same day); 21:30Z → MSK 00:30 (next day). A naive UTC
    # date() would wrongly bucket both on `base`.
    s.append_history("w1", "c", f"{base}T20:00:00+00:00", 10, 300.0, None, None, False, None)
    s.append_history("w1", "c", f"{base}T21:30:00+00:00", 8, 301.0, None, None, False, None)
    pts = s.load_history("w1", "c")
    assert [p["day"] for p in pts] == [base, _next_day()]
    s.close()


def test_absent_writes_null_place():
    s = _store()
    s.append_history("w1", "c", f"{_today()}T09:00:00+00:00", None, None, None, None, False, None)
    pts = s.load_history("w1", "c")
    assert len(pts) == 1
    assert pts[0]["place"] is None
    assert pts[0]["final_score"] is None
    assert pts[0]["consent"] is False
    s.close()


def test_days_window_excludes_old():
    s = _store()
    old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    new = datetime.now(timezone.utc).isoformat()
    s.append_history("w1", "c", old, 5, 1.0, None, None, False, None)
    s.append_history("w1", "c", new, 3, 1.0, None, None, False, None)
    pts = s.load_history("w1", "c", days=3)
    assert len(pts) == 1          # the 10-day-old point is outside a 3-day window
    assert pts[0]["place"] == 3
    s.close()


def test_prune_removes_rows_past_retention():
    s = _store()
    ancient = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    s.conn.execute(
        "INSERT INTO code_history (watch_id, code, ts, place) VALUES (?,?,?,?)",
        ("w1", "c", ancient, 9),
    )
    s.conn.commit()
    # any append triggers the 120-day prune → the 200-day-old row is gone
    s.append_history("w1", "c", datetime.now(timezone.utc).isoformat(), 3, 1.0, None, None, False, None)
    rows = s.conn.execute(
        "SELECT COUNT(*) FROM code_history WHERE watch_id='w1'"
    ).fetchone()[0]
    assert rows == 1
    s.close()


def test_history_scoped_per_watch_and_code():
    s = _store()
    ts = datetime.now(timezone.utc).isoformat()
    s.append_history("w1", "a", ts, 1, 1.0, None, None, False, None)
    s.append_history("w1", "b", ts, 2, 1.0, None, None, False, None)
    s.append_history("w2", "a", ts, 3, 1.0, None, None, False, None)
    assert len(s.load_history("w1", "a")) == 1
    assert s.load_history("w1", "a")[0]["place"] == 1
    assert s.load_history("w2", "a")[0]["place"] == 3
    assert s.load_history("w9", "z") == []
    s.close()
