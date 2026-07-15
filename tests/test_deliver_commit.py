import httpx

from vuz_monitor.config import WatchConfig, AppConfig, TelegramConfig
from vuz_monitor.store import Store
from vuz_monitor import pipeline
from vuz_monitor.models import Snapshot, Entrant, ProgramMeta


def _snap(watch_id, place):
    return Snapshot(
        watch_id=watch_id,
        meta=ProgramMeta(title="t", plan=None, total=1, updated_at="2026-07-15 10:00:00"),
        entrants=[Entrant(code="100", code_display="100", place=place, final_score=200.0,
                          priority=1, consent=True, contract=None, payment=None,
                          passing_main=None, passing_real=None, needs_dormitory=None, raw={})],
        fetched_at="2026-07-15T07:00:00+00:00",
    )


def _watch(name="w"):
    return WatchConfig(name=name, adapter="fake", url="https://h/x", params={"k": name},
                       codes=["100"], group="G")


def _cfg(watches, db_path):
    return AppConfig(telegram=TelegramConfig(chat_id="1", bot_token="t"),
                     heartbeat="on_change_only", tracked_codes=["100"], watches=watches, db_path=db_path)


def _run_with_change(monkeypatch, db_path, w, send_impl):
    # Seed the delivered baseline = place 5 in the on-disk db, then run a fetch of
    # place 2 (a change). run() opens/closes its OWN Store on db_path, so we reopen
    # the file afterwards to inspect the baseline (never touch a closed connection).
    seed = Store(db_path)
    seed.save_notified_snapshot(_snap(w.watch_id, 5))
    seed.close()
    class _A:
        def fetch(self, watch): return _snap(w.watch_id, 2)
    monkeypatch.setattr(pipeline, "get_adapter", lambda name: _A())
    monkeypatch.setattr(pipeline, "_render_dashboard", lambda *a, **k: [])   # hermetic
    monkeypatch.setattr(pipeline.notify, "send_message", send_impl)
    return pipeline.run(_cfg([w], db_path), dry_run=False)


def test_baseline_advances_on_successful_send(monkeypatch, tmp_path):
    db = str(tmp_path / "s.db"); w = _watch()
    _run_with_change(monkeypatch, db, w, lambda *a, **k: None)
    assert Store(db).load_notified_snapshot(w.watch_id).entrants[0].place == 2   # promoted


def test_baseline_held_when_send_fails(monkeypatch, tmp_path):
    db = str(tmp_path / "s.db"); w = _watch()
    def _fail(*a, **k):
        raise pipeline.notify.TelegramNetworkError("unreachable")
    _run_with_change(monkeypatch, db, w, _fail)
    assert Store(db).load_notified_snapshot(w.watch_id).entrants[0].place == 5   # held → re-alerts
