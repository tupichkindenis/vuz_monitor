"""Tests for the МИРЭА score-loading progress feature.

Covers the pure aggregation (report.score_progress) and the store history layer
(score_progress table: append / load / prune).
"""
from datetime import datetime, timedelta, timezone

from vuz_monitor.models import Entrant, ProgramMeta, Snapshot
from vuz_monitor.report import score_progress
from vuz_monitor.store import Store


def _entrant(code, entrance_score, is_bvi=False):
    return Entrant(
        code=str(code),
        code_display=str(code),
        entrance_score=entrance_score,
        is_bvi=is_bvi,
    )


def _snap(entrants, fetched_at="2026-07-13T09:00:00+00:00"):
    return Snapshot(
        watch_id="w1",
        meta=ProgramMeta(title="Тест", plan=5, total=len(entrants)),
        entrants=entrants,
        fetched_at=fetched_at,
    )


def test_no_score_counts_zero_and_missing_entrance():
    # 0.0 (entranceMark==0) and None both mean "балл не подгружен"; a real
    # nonzero score means loaded.
    snap = _snap([
        _entrant(1000001, 0.0),     # без баллов
        _entrant(1000002, None),    # без баллов
        _entrant(1000003, 250.0),   # с баллами
    ])
    out = score_progress(snap)
    assert out["total"] == 3
    assert out["no_score"] == 2


def test_bvi_with_zero_score_is_not_no_score():
    # БВИ (олимпиадник) legitimately has entranceMark 0 but IS admitted —
    # must not be flagged as "балл не подгружен".
    snap = _snap([
        _entrant(1000001, 0.0, is_bvi=True),   # БВИ, не считается без баллов
        _entrant(1000002, 0.0),                # без баллов
    ])
    out = score_progress(snap)
    assert out["no_score"] == 1


def test_buckets_by_application_number_range():
    snap = _snap([
        _entrant(1287292, 0.0),     # 1_200_000 bucket, без баллов
        _entrant(1266000, 300.0),   # 1_200_000 bucket, с баллами
        _entrant(1366129, 0.0),     # 1_300_000 bucket, без баллов
    ])
    out = score_progress(snap)
    assert out["buckets"][1200000] == [2, 1]   # [total, no_score]
    assert out["buckets"][1300000] == [1, 1]


def test_invalid_code_excluded_from_buckets_but_counts_in_total():
    snap = _snap([
        _entrant("", 0.0),          # пустой код — не бакетится
        _entrant("abc", None),      # нечисловой — не бакетится
        _entrant(1300000, 0.0),     # валидный
    ])
    out = score_progress(snap)
    assert out["total"] == 3                      # все в total
    assert sum(t for t, _ in out["buckets"].values()) == 1   # только валидный в бакетах
    assert 1300000 in out["buckets"]


def test_ts_is_snapshot_fetched_at():
    snap = _snap([_entrant(1300000, 0.0)], fetched_at="2026-07-13T10:11:12+00:00")
    assert score_progress(snap)["ts"] == "2026-07-13T10:11:12+00:00"


# --- store: score_progress history table --- #

def test_store_append_and_load_roundtrip():
    s = Store(":memory:")
    s.append_score_progress("w1", "2026-07-13T09:00:00+00:00", 4087, 826,
                            {1200000: [492, 121], 1300000: [450, 115]})
    rows = s.load_score_progress("w1")
    assert len(rows) == 1
    r = rows[0]
    assert r["ts"] == "2026-07-13T09:00:00+00:00"
    assert r["total"] == 4087
    assert r["no_score"] == 826
    # buckets survive the JSON roundtrip with INTEGER keys (not strings)
    assert r["buckets"][1200000] == [492, 121]
    assert r["buckets"][1300000] == [450, 115]
    s.close()


def test_store_keeps_raw_hourly_rows_not_downsampled():
    # The 24h comparison needs hourly resolution — load must NOT collapse a day.
    s = Store(":memory:")
    base = datetime.now(timezone.utc).date().isoformat()
    for h in (5, 6, 7):
        s.append_score_progress("w1", f"{base}T0{h}:00:00+00:00", 100, 100 - h, {})
    rows = s.load_score_progress("w1")
    assert len(rows) == 3                       # all three, not one-per-day
    assert [r["ts"][11:13] for r in rows] == ["05", "06", "07"]   # ascending
    s.close()


def test_store_same_source_ts_replaces_idempotent_for_backfill():
    s = Store(":memory:")
    s.append_score_progress("w1", "2026-07-13T09:00:00+00:00", 100, 50, {})
    s.append_score_progress("w1", "2026-07-13T09:00:00+00:00", 100, 40, {})  # re-backfill
    rows = s.load_score_progress("w1")
    assert len(rows) == 1
    assert rows[0]["no_score"] == 40            # replaced, not duplicated
    s.close()


def test_store_scoped_per_source():
    s = Store(":memory:")
    ts = "2026-07-13T09:00:00+00:00"
    s.append_score_progress("w1", ts, 10, 1, {})
    s.append_score_progress("w2", ts, 20, 2, {})
    assert s.load_score_progress("w1")[0]["total"] == 10
    assert s.load_score_progress("w2")[0]["total"] == 20
    assert s.load_score_progress("w9") == []
    s.close()


def test_store_days_window_and_prune():
    s = Store(":memory:")
    old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    new = datetime.now(timezone.utc).isoformat()
    s.append_score_progress("w1", old, 5, 5, {})
    s.append_score_progress("w1", new, 3, 1, {})   # append triggers 120-day prune
    rows = s.load_score_progress("w1")
    assert len(rows) == 1                            # 200-day-old row pruned
    assert rows[0]["no_score"] == 1
    s.close()


# --- config: track_scores flag --- #

def test_watch_config_parses_track_scores(tmp_path):
    from vuz_monitor.config import load_config
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "telegram: {chat_id: '1', bot_token: 'x'}\n"
        "tracked_codes: ['1366129']\n"
        "watches:\n"
        "  - {name: tracked, adapter: mirea_api, url: 'http://x', track_scores: true}\n"
        "  - {name: untracked, adapter: mirea_api, url: 'http://y'}\n",
        encoding="utf-8",
    )
    app = load_config(str(cfg))
    watches = {w.name: w for w in app.watches}
    assert watches["tracked"].track_scores is True
    assert watches["untracked"].track_scores is False   # default off


# --- pipeline wiring + backfill --- #

def _pipeline_cfg(tmp_path, track_scores, watch_name="Спец"):
    from vuz_monitor.config import AppConfig, TelegramConfig, WatchConfig
    w = WatchConfig(name=watch_name, adapter="fake", url="http://x",
                    track_scores=track_scores)
    return AppConfig(
        telegram=TelegramConfig(chat_id="c", bot_token="t"),
        heartbeat="on_change_only", tracked_codes=["1366129"],
        watches=[w], db_path=str(tmp_path / "state.db"),
    )


def _mirea_snap(watch_id, no_score_code="1366129", scored_code="1000001", ts=None):
    ent = [
        Entrant(code=no_score_code, code_display=no_score_code, entrance_score=None),
        Entrant(code=scored_code, code_display=scored_code, entrance_score=250.0),
    ]
    return Snapshot(watch_id=watch_id, meta=ProgramMeta(title="Спец", total=2),
                    entrants=ent,
                    fetched_at=ts or datetime.now(timezone.utc).isoformat())


def _run_pipeline(tmp_path, monkeypatch, cfg, snap, dry_run=False):
    from vuz_monitor import pipeline

    class FakeAdapter:
        def fetch(self, watch):
            return snap

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pipeline, "get_adapter", lambda name: FakeAdapter())
    monkeypatch.setattr(pipeline.notify, "send_message", lambda *a, **k: None)
    pipeline.run(cfg, dry_run=dry_run)


def test_pipeline_records_score_progress_for_tracked_watch(tmp_path, monkeypatch):
    cfg = _pipeline_cfg(tmp_path, track_scores=True)
    w = cfg.watches[0]
    _run_pipeline(tmp_path, monkeypatch, cfg, _mirea_snap(w.watch_id))
    s = Store(cfg.db_path)
    rows = s.load_score_progress(w.watch_id)
    s.close()
    assert len(rows) == 1
    assert rows[0]["total"] == 2
    assert rows[0]["no_score"] == 1        # 1366129 has no entrance_score


def test_pipeline_skips_untracked_watch(tmp_path, monkeypatch):
    cfg = _pipeline_cfg(tmp_path, track_scores=False)
    w = cfg.watches[0]
    _run_pipeline(tmp_path, monkeypatch, cfg, _mirea_snap(w.watch_id))
    s = Store(cfg.db_path)
    rows = s.load_score_progress(w.watch_id)
    s.close()
    assert rows == []                      # no flag → no aggregate row


def test_pipeline_dry_run_writes_no_score_progress(tmp_path, monkeypatch):
    cfg = _pipeline_cfg(tmp_path, track_scores=True)
    w = cfg.watches[0]
    _run_pipeline(tmp_path, monkeypatch, cfg, _mirea_snap(w.watch_id), dry_run=True)
    s = Store(cfg.db_path)
    rows = s.load_score_progress(w.watch_id)
    s.close()
    assert rows == []


def test_backfill_from_stored_snapshots(tmp_path, monkeypatch):
    from vuz_monitor import pipeline
    cfg = _pipeline_cfg(tmp_path, track_scores=True)
    w = cfg.watches[0]
    # seed two stored snapshots at different times, both with 1 без баллов
    s = Store(cfg.db_path)
    day = datetime.now(timezone.utc).date().isoformat()
    s.save(_mirea_snap(w.watch_id, ts=f"{day}T05:00:00+00:00"))
    s.save(_mirea_snap(w.watch_id, ts=f"{day}T06:00:00+00:00"))
    s.close()

    n = pipeline.backfill_score_progress(cfg)
    assert n == 2                          # one aggregate row per stored snapshot

    s = Store(cfg.db_path)
    rows = s.load_score_progress(w.watch_id)
    s.close()
    assert [r["ts"][11:13] for r in rows] == ["05", "06"]   # both, ascending
    assert all(r["no_score"] == 1 for r in rows)


# --- dashboard: score-progress page --- #

def _iso(day_offset, hour=9):
    d = datetime.now(timezone.utc) + timedelta(days=day_offset)
    return d.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()


def _spec(title, history, tracked=None):
    return {"title": title, "history": history, "tracked": tracked}


def test_score_page_renders_title_and_before_after():
    from vuz_monitor.dashboard import build_score_progress_html
    hist = [
        {"ts": _iso(-1), "total": 100, "no_score": 50, "buckets": {1300000: [40, 20]}},
        {"ts": _iso(0), "total": 102, "no_score": 44, "buckets": {1300000: [40, 15]}},
    ]
    html = build_score_progress_html([_spec("Интеллектуальные системы", hist)])
    assert "Интеллектуальные системы" in html          # specialty title
    assert "без баллов" in html.lower()
    assert "50" in html and "44" in html               # было / стало (no_score)


def test_score_page_highlights_tracked_range():
    from vuz_monitor.dashboard import build_score_progress_html
    hist = [
        {"ts": _iso(-1), "total": 100, "no_score": 50, "buckets": {1300000: [40, 20]}},
        {"ts": _iso(0), "total": 100, "no_score": 44, "buckets": {1300000: [40, 15]}},
    ]
    tracked = {"code": "1366129", "place": 3570, "total": 100, "no_score": True, "bucket": 1300000}
    html = build_score_progress_html([_spec("Спец", hist, tracked=tracked)])
    # public page → код участника is masked (same convention as every other page)
    assert "1366129" not in html
    assert "•••6129" in html                            # «ваш номер» block, masked
    assert "you" in html                                # tracked-range highlight hook


def test_score_page_tracked_without_place_no_none():
    from vuz_monitor.dashboard import build_score_progress_html
    hist = [{"ts": _iso(0), "total": 100, "no_score": 44, "buckets": {1300000: [40, 15]}}]
    tracked = {"code": "1366129", "place": None, "total": 100, "no_score": True, "bucket": 1300000}
    html = build_score_progress_html([_spec("Спец", hist, tracked=tracked)])
    assert "место None" not in html                     # no place → don't render «место None»
    assert "•••6129" in html


def test_score_page_single_point_no_comparison():
    from vuz_monitor.dashboard import build_score_progress_html
    hist = [{"ts": _iso(0), "total": 100, "no_score": 44, "buckets": {1300000: [40, 15]}}]
    html = build_score_progress_html([_spec("Спец", hist)])   # must not raise
    assert "44" in html                                  # «Стало» still shown
    assert "<!doctype html>" in html


def test_render_pages_includes_score_page_when_tracked(tmp_path):
    cfg = _pipeline_cfg(tmp_path, track_scores=True)
    w = cfg.watches[0]
    from vuz_monitor import dashboard
    s = Store(cfg.db_path)
    s.save(_mirea_snap(w.watch_id, ts=_iso(0)))
    s.append_score_progress(w.watch_id, _iso(0), 2, 1, {1300000: [1, 1]})
    pages = dashboard.render_pages(cfg, s)
    s.close()
    assert "mirea-scores.html" in pages
    assert "Спец" in pages["mirea-scores.html"]


def test_delta_new_bucket_shows_count_without_percent():
    from vuz_monitor.dashboard import _delta_html
    assert "%" not in _delta_html(65, 0)      # baseline 0 → % is undefined, count only
    assert "+65" in _delta_html(65, 0)
    assert "%" in _delta_html(115, 101)       # normal case keeps the %


def test_render_pages_omits_score_page_when_no_tracked(tmp_path):
    cfg = _pipeline_cfg(tmp_path, track_scores=False)
    w = cfg.watches[0]
    from vuz_monitor import dashboard
    s = Store(cfg.db_path)
    s.save(_mirea_snap(w.watch_id, ts=_iso(0)))
    pages = dashboard.render_pages(cfg, s)
    s.close()
    assert "mirea-scores.html" not in pages
    assert "index.html" in pages and "table.html" in pages
