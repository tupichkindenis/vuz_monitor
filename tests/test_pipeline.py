"""Pipeline wiring with a mock adapter (no network): run() writes code_history
AND docs/index.html every hour; --dry-run touches neither."""
import errno
from datetime import datetime, timezone

import httpx

from vuz_monitor import pipeline
from vuz_monitor.config import AppConfig, TelegramConfig, WatchConfig
from vuz_monitor.models import Entrant, ProgramMeta, Snapshot
from vuz_monitor.store import Store


class FakeAdapter:
    def __init__(self, snap):
        self._snap = snap

    def fetch(self, watch):
        return self._snap


def _legacy_snap(watch_id, place=5):
    meta = ProgramMeta(title="Спец", plan=40, total=100, updated_at="2026-07-11 09:00:00")
    ent = Entrant(code="1366129", code_display="1366129", place=place, final_score=250.0,
                  priority=1, consent=True, passing_main=True, passing_real=True)
    return Snapshot(watch_id=watch_id, meta=meta, entrants=[ent],
                    fetched_at=datetime.now(timezone.utc).isoformat())


def _legacy_cfg(tmp_path):
    w = WatchConfig(name="Спец", adapter="fake", url="http://x", group="МИРЭА — бюджет")
    return AppConfig(
        telegram=TelegramConfig(chat_id="c", bot_token="t"),
        heartbeat="on_change_only", tracked_codes=["1366129"],
        watches=[w], db_path=str(tmp_path / "state.db"),
    )


def _patch(monkeypatch, tmp_path, snap):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pipeline, "get_adapter", lambda name: FakeAdapter(snap))


def test_run_writes_history_and_dashboard(tmp_path, monkeypatch):
    cfg = _legacy_cfg(tmp_path)
    w = cfg.watches[0]
    _patch(monkeypatch, tmp_path, _legacy_snap(w.watch_id, place=5))
    sent = []
    monkeypatch.setattr(pipeline.notify, "send_message", lambda tok, chat, text: sent.append(text))

    rc = pipeline.run(cfg, dry_run=False)
    assert rc == 0

    out = tmp_path / "docs" / "index.html"
    assert out.exists()
    html = out.read_text(encoding="utf-8")
    assert "место 5" in html

    store = Store(cfg.db_path)
    pts = store.load_history(w.watch_id, "1366129")
    assert len(pts) == 1 and pts[0]["place"] == 5
    store.close()

    assert len(sent) == 1              # first run has changes → one group message


def test_dry_run_touches_nothing(tmp_path, monkeypatch):
    cfg = _legacy_cfg(tmp_path)
    w = cfg.watches[0]
    _patch(monkeypatch, tmp_path, _legacy_snap(w.watch_id))
    sent = []
    monkeypatch.setattr(pipeline.notify, "send_message", lambda tok, chat, text: sent.append(text))

    pipeline.run(cfg, dry_run=True)

    assert not (tmp_path / "docs").exists()      # no dashboard file
    store = Store(cfg.db_path)
    assert store.load_history(w.watch_id, "1366129") == []   # no history
    store.close()
    assert sent == []                            # dry-run never sends


def test_dashboard_survives_render_error(tmp_path, monkeypatch):
    """A dashboard render bug must not fail the hourly run."""
    cfg = _legacy_cfg(tmp_path)
    w = cfg.watches[0]
    _patch(monkeypatch, tmp_path, _legacy_snap(w.watch_id))
    monkeypatch.setattr(pipeline.notify, "send_message", lambda *a, **k: None)
    monkeypatch.setattr(
        pipeline.dashboard, "render_pages",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    rc = pipeline.run(cfg, dry_run=False)
    assert rc == 0                                # run still succeeds
    assert not (tmp_path / "docs" / "index.html").exists()

    store = Store(cfg.db_path)
    assert len(store.load_history(w.watch_id, "1366129")) == 1   # history still written
    store.close()


def test_build_dashboard_cli(tmp_path, monkeypatch):
    """Standalone `dashboard` command regenerates BOTH pages from state.db, offline."""
    cfg = _legacy_cfg(tmp_path)
    w = cfg.watches[0]
    # seed one snapshot + history via a run, then regenerate to a fresh dir
    _patch(monkeypatch, tmp_path, _legacy_snap(w.watch_id, place=7))
    monkeypatch.setattr(pipeline.notify, "send_message", lambda *a, **k: None)
    pipeline.run(cfg, dry_run=False)

    out_dir = tmp_path / "sub"
    rc = pipeline.build_dashboard(cfg, out_dir=str(out_dir))
    assert rc == 0
    assert (out_dir / "index.html").exists() and (out_dir / "table.html").exists()
    assert "место 7" in (out_dir / "index.html").read_text(encoding="utf-8")
    assert 'id="grid"' in (out_dir / "table.html").read_text(encoding="utf-8")


def _snap(watch_id, code="100", place=1, updated_at="2026-07-15 10:00:00"):
    return Snapshot(
        watch_id=watch_id,
        meta=ProgramMeta(title="t", plan=None, total=1, updated_at=updated_at),
        entrants=[Entrant(code=code, code_display=code, place=place, final_score=200.0,
                          priority=1, consent=True, contract=None, payment=None,
                          passing_main=None, passing_real=None, needs_dormitory=None, raw={})],
        fetched_at="2026-07-15T07:00:00+00:00",
    )


def _watch(name="w", url="https://a.example/x", params=None):
    return WatchConfig(name=name, adapter="fake", url=url, params=params or {}, codes=["100"])


def _cfg(watches):
    return AppConfig(telegram=TelegramConfig(chat_id="1", bot_token="t"),
                     heartbeat="on_change_only", tracked_codes=["100"], watches=watches, db_path=":memory:")


class _FakeAdapter:
    def __init__(self, result):
        self._result = result  # a Snapshot, or an Exception to raise
    def fetch(self, watch):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _patch_adapter(monkeypatch, result):
    monkeypatch.setattr(pipeline, "get_adapter", lambda name: _FakeAdapter(result))


def test_process_watch_tags_connectivity_error(monkeypatch, tmp_path):
    store = Store(str(tmp_path / "s.db"))
    err = httpx.ConnectError("x"); err.__cause__ = OSError(errno.EHOSTUNREACH, "no route")
    _patch_adapter(monkeypatch, err)
    rep = pipeline._process_watch(_watch(url="https://host.one/x"), _cfg([]), store, dry_run=False)
    assert rep.error and rep.net_error is True
    assert rep.host == "host.one"


def test_process_watch_tags_source_error(monkeypatch, tmp_path):
    store = Store(str(tmp_path / "s.db"))
    _patch_adapter(monkeypatch, ValueError("bad table"))
    rep = pipeline._process_watch(_watch(), _cfg([]), store, dry_run=False)
    assert rep.error and rep.net_error is False


def test_process_watch_success_sets_watch_id_and_fetched_at(monkeypatch, tmp_path):
    store = Store(str(tmp_path / "s.db"))
    w = _watch()
    _patch_adapter(monkeypatch, _snap(w.watch_id))
    rep = pipeline._process_watch(w, _cfg([w]), store, dry_run=False)
    assert rep.error is None
    assert rep.watch_id == w.watch_id
    assert rep.fetched_at == "2026-07-15T07:00:00+00:00"


def test_new_watch_is_first_run(monkeypatch, tmp_path):
    store = Store(str(tmp_path / "s.db"))
    w = _watch()
    _patch_adapter(monkeypatch, _snap(w.watch_id, place=1))
    rep = pipeline._process_watch(w, _cfg([w]), store, dry_run=False)
    assert rep.codes[0].first_run is True


def test_migration_seeds_baseline_and_is_not_first_run(monkeypatch, tmp_path):
    store = Store(str(tmp_path / "s.db"))
    w = _watch()
    store.save(_snap(w.watch_id, place=5))          # existing history, no notified_snapshot
    _patch_adapter(monkeypatch, _snap(w.watch_id, place=5))   # unchanged this run
    rep = pipeline._process_watch(w, _cfg([w]), store, dry_run=False)
    assert rep.codes[0].first_run is False
    assert rep.has_changes is False                  # seeded baseline == current → no change
    assert store.load_notified_snapshot(w.watch_id) is not None   # baseline was seeded


def test_diff_is_against_delivered_baseline_not_last_snapshot(monkeypatch, tmp_path):
    store = Store(str(tmp_path / "s.db"))
    w = _watch()
    store.save_notified_snapshot(_snap(w.watch_id, place=5))   # last DELIVERED = place 5
    store.save(_snap(w.watch_id, place=5))                     # last SAVED also 5
    _patch_adapter(monkeypatch, _snap(w.watch_id, place=2))    # now moved to 2
    rep = pipeline._process_watch(w, _cfg([w]), store, dry_run=False)
    assert rep.has_changes is True                             # 5 -> 2 vs delivered baseline
