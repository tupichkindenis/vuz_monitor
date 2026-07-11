import json
from pathlib import Path

from vuz_monitor.adapters.mirea_api import MireaApiAdapter
from vuz_monitor.config import WatchConfig

FIX = Path(__file__).parent / "fixtures"


def _watch(code_field=None):
    return WatchConfig(
        name="MIREA test",
        adapter="mirea_api",
        url="https://priem.example/api",
        params={"competitions[]": "1", "edu_level": 2},
        code_field=code_field,
    )


def _data():
    return json.loads((FIX / "mirea_sample.json").read_text(encoding="utf-8"))


def test_parse_mirea_fixture_meta():
    snap = MireaApiAdapter().parse(_data(), _watch())
    assert len(snap.entrants) == 6
    assert snap.meta.plan == 5
    assert snap.meta.total == 6
    assert snap.meta.min_score == 81
    assert snap.meta.updated_at == "2026-07-10 15:17:40"
    assert snap.meta.is_final is False


def test_default_match_is_by_super_code():
    snap = MireaApiAdapter().parse(_data(), _watch())  # default code_field
    e = snap.by_code("1287292")                        # код участника
    assert e is not None
    assert e.place == 3
    assert e.priority == 11
    assert e.final_score == 292.0          # 292000 / 1000
    assert e.entrance_score == 282.0
    assert e.achievement_score == 10.0
    assert e.consent is False              # accepted == 0
    # snils is NOT the match key by default
    assert snap.by_code("166-172-036 59") is None


def test_official_passing_flags_mapped():
    snap = MireaApiAdapter().parse(_data(), _watch())
    tracked = snap.by_code("1287292")
    assert tracked.passing_main is True    # iHP == 1  (Основной ВП)
    assert tracked.passing_real is False   # iHPO == 0 (Проходной ВП)
    assert tracked.paid_ok is False        # pc == 0
    assert tracked.needs_dormitory is True # needDormitory == 1
    # place 1: passes on both flags; place 2 has paid conditions met
    assert snap.by_code("1000001").passing_real is True
    assert snap.by_code("1000002").paid_ok is True


def test_match_by_snils_when_configured():
    snap = MireaApiAdapter().parse(_data(), _watch(code_field="snils"))
    e = snap.by_code("166-172-036 59")
    assert e is not None
    assert e.place == 3
    # СНИЛС formatting does not matter (matched by digits only)
    assert snap.by_code("16617203659") is e
