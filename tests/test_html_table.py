from pathlib import Path

from vuz_monitor.adapters.html_table import HtmlTableAdapter
from vuz_monitor.config import WatchConfig

FIX = Path(__file__).parent / "fixtures"


def _watch():
    return WatchConfig(
        name="HTML test",
        adapter="html_table",
        url="https://example-vuz.ru/list.html",
        table_selector="table.competition",
        columns={"place": 0, "code": 1, "priority": 2, "final_score": 3, "consent": 4},
        plan_override=2,
    )


def test_parse_html_fixture():
    html_text = (FIX / "html_sample.html").read_text(encoding="utf-8")
    snap = HtmlTableAdapter().parse(html_text, _watch())

    assert len(snap.entrants) == 4  # header row (<th>) skipped
    assert snap.meta.plan == 2

    e = snap.by_code("166-172-036 59")
    assert e.place == 3
    assert e.final_score == 292.0
    assert e.priority == 2
    assert e.consent is False
    assert snap.by_code("111-111-111 11").consent is True  # "Да" -> True


def test_place_inferred_from_order_when_column_absent():
    html_text = (FIX / "html_sample.html").read_text(encoding="utf-8")
    w = _watch()
    w.columns = {"code": 1, "final_score": 3, "consent": 4}  # no place column
    snap = HtmlTableAdapter().parse(html_text, w)
    # rank falls back to row order
    assert [e.place for e in snap.entrants] == [1, 2, 3, 4]
