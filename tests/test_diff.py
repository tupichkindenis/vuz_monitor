from vuz_monitor.diff import compute_changes, compute_status
from vuz_monitor.models import Entrant, ProgramMeta, Snapshot, normalize_code

TRACKED = "1287292"  # код участника (superCode)


def _snap(rows, plan=5, updated="t"):
    """rows: list of (code, place, score, consent, passing_main, passing_real)."""
    entrants = [
        Entrant(
            code=normalize_code(c),
            code_display=c,
            place=p,
            final_score=s,
            consent=cons,
            passing_main=pmain,
            passing_real=preal,
        )
        for (c, p, s, cons, pmain, preal) in rows
    ]
    meta = ProgramMeta(title="Test", plan=plan, total=len(entrants), updated_at=updated)
    return Snapshot(watch_id="w", meta=meta, entrants=entrants, fetched_at="now")


def test_status_uses_official_flags():
    snap = _snap(
        [
            ("1000001", 1, 300.0, True, True, True),
            ("1000002", 2, 295.0, True, True, False),
            (TRACKED, 3, 292.0, False, True, False),
        ]
    )
    st = compute_status(snap, TRACKED)
    assert st.present is True
    assert st.place == 3
    assert st.ahead == 2
    assert st.passing_main is True     # Основной ВП
    assert st.passing_real is False    # Проходной ВП


def test_status_not_found():
    snap = _snap([("1000001", 1, 300.0, True, True, True)])
    st = compute_status(snap, TRACKED)
    assert st.present is False
    assert st.place is None
    assert st.passing_real is None


def test_first_run_has_no_changes():
    new = compute_status(_snap([(TRACKED, 3, 292.0, False, True, False)]), TRACKED)
    assert compute_changes(None, new) == []


def test_passing_flip_produces_changes():
    prev = compute_status(
        _snap([(TRACKED, 3, 292.0, False, True, False)]), TRACKED
    )
    new = compute_status(
        _snap([(TRACKED, 2, 292.0, True, True, True)]), TRACKED
    )
    fields = {c.field: (c.old, c.new) for c in compute_changes(prev, new)}
    assert fields["place"] == (3, 2)
    assert fields["consent"] == (False, True)
    assert fields["passing_real"] == (False, True)   # Проходной ВП flipped to да
    assert "passing_main" not in fields              # unchanged
    assert "final_score" not in fields               # unchanged


def test_priority_change_tracked():
    prev = compute_status(_snap([(TRACKED, 3, 292.0, False, True, False)]), TRACKED)
    new = compute_status(_snap([(TRACKED, 3, 292.0, False, True, False)]), TRACKED)
    # same standing -> no changes
    assert compute_changes(prev, new) == []
