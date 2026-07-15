from vuz_monitor.store import Store, HISTORY_PER_WATCH
from vuz_monitor.models import Snapshot, Entrant, ProgramMeta


def _snap(watch_id, fetched_at, place=1):
    return Snapshot(
        watch_id=watch_id,
        meta=ProgramMeta(title="t", plan=None, total=1, updated_at="2026-07-15 10:00:00"),
        entrants=[Entrant(code="100", code_display="100", place=place, final_score=200.0,
                          priority=1, consent=True, contract=None, payment=None,
                          passing_main=None, passing_real=None, needs_dormitory=None, raw={})],
        fetched_at=fetched_at,
    )


def test_save_and_load_notified_snapshot(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    assert store.load_notified_snapshot("w1") is None
    store.save_notified_snapshot(_snap("w1", "2026-07-15T07:00:00+00:00", place=3))
    got = store.load_notified_snapshot("w1")
    assert got is not None and got.entrants[0].place == 3


def test_promote_copies_latest_snapshot(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    store.save(_snap("w1", "2026-07-15T06:00:00+00:00", place=5))
    store.save(_snap("w1", "2026-07-15T07:00:00+00:00", place=2))
    with store.transaction():
        store.promote_notified("w1")
    got = store.load_notified_snapshot("w1")
    assert got.fetched_at == "2026-07-15T07:00:00+00:00" and got.entrants[0].place == 2


def test_promoted_baseline_survives_snapshot_pruning(tmp_path):
    # Codex #1 regression: pruning the snapshots table must NOT drop the baseline.
    store = Store(str(tmp_path / "s.db"))
    store.save(_snap("w1", "2026-07-15T00:00:00+00:00", place=9))
    with store.transaction():
        store.promote_notified("w1")
    for h in range(1, HISTORY_PER_WATCH + 5):     # push the baseline snapshot out of the prune window
        store.save(_snap("w1", f"2026-07-15T{h:02d}:30:00+00:00", place=1))
    got = store.load_notified_snapshot("w1")
    assert got is not None and got.entrants[0].place == 9


def test_transaction_rolls_back_on_error(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    store.save(_snap("w1", "2026-07-15T07:00:00+00:00"))
    try:
        with store.transaction():
            store.promote_notified("w1")
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert store.load_notified_snapshot("w1") is None   # promote was rolled back
