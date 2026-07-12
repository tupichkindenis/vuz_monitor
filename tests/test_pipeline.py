"""Pipeline wiring with a mock adapter (no network): run() writes code_history
AND docs/index.html every hour; --dry-run touches neither."""
from datetime import datetime, timezone

from vuz_monitor import pipeline
from vuz_monitor.config import AppConfig, TelegramConfig, WatchConfig
from vuz_monitor.models import Entrant, ProgramMeta, Snapshot
from vuz_monitor.store import Store


class FakeAdapter:
    def __init__(self, snap):
        self._snap = snap

    def fetch(self, watch):
        return self._snap


def _snap(watch_id, place=5):
    meta = ProgramMeta(title="Спец", plan=40, total=100, updated_at="2026-07-11 09:00:00")
    ent = Entrant(code="1366129", code_display="1366129", place=place, final_score=250.0,
                  priority=1, consent=True, passing_main=True, passing_real=True)
    return Snapshot(watch_id=watch_id, meta=meta, entrants=[ent],
                    fetched_at=datetime.now(timezone.utc).isoformat())


def _cfg(tmp_path):
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
    cfg = _cfg(tmp_path)
    w = cfg.watches[0]
    _patch(monkeypatch, tmp_path, _snap(w.watch_id, place=5))
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
    cfg = _cfg(tmp_path)
    w = cfg.watches[0]
    _patch(monkeypatch, tmp_path, _snap(w.watch_id))
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
    cfg = _cfg(tmp_path)
    w = cfg.watches[0]
    _patch(monkeypatch, tmp_path, _snap(w.watch_id))
    monkeypatch.setattr(pipeline.notify, "send_message", lambda *a, **k: None)
    monkeypatch.setattr(
        pipeline.dashboard, "generate",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    rc = pipeline.run(cfg, dry_run=False)
    assert rc == 0                                # run still succeeds
    assert not (tmp_path / "docs" / "index.html").exists()

    store = Store(cfg.db_path)
    assert len(store.load_history(w.watch_id, "1366129")) == 1   # history still written
    store.close()


def test_build_dashboard_cli(tmp_path, monkeypatch):
    """Standalone `dashboard` command regenerates from state.db, offline."""
    cfg = _cfg(tmp_path)
    w = cfg.watches[0]
    # seed one snapshot + history via a run, then regenerate to a fresh path
    _patch(monkeypatch, tmp_path, _snap(w.watch_id, place=7))
    monkeypatch.setattr(pipeline.notify, "send_message", lambda *a, **k: None)
    pipeline.run(cfg, dry_run=False)

    out = tmp_path / "sub" / "d.html"
    rc = pipeline.build_dashboard(cfg, out=str(out))
    assert rc == 0
    assert out.exists()
    assert "место 7" in out.read_text(encoding="utf-8")
