from pathlib import Path

from vuz_monitor.adapters.stankin_html import StankinHtmlAdapter
from vuz_monitor.config import WatchConfig

FIX = Path(__file__).parent / "fixtures"


def _parse(fixture):
    html = (FIX / fixture).read_text(encoding="utf-8")
    w = WatchConfig(name="Станкин test", adapter="stankin_html", url="https://x/grid")
    return StankinHtmlAdapter().parse(html, w)


def test_budget_columns_and_place():
    snap = _parse("stankin_budget.html")
    assert len(snap.entrants) == 2  # header row skipped

    me = snap.by_code("1366129")
    assert me.place == 300          # from the № column, NOT row order
    assert me.final_score == 260.0  # plain integer, not scaled
    assert me.priority == 4
    assert me.consent is False              # empty «Согласие» -> нет
    assert me.passing_main is False and me.passing_real is False
    assert me.contract is None and me.payment is None  # budget has no contract

    top = snap.by_code("1500000")
    assert top.place == 1
    assert top.consent is True                          # «Согласие» = да
    assert top.passing_main is True and top.passing_real is True  # ✓ checkmarks


def test_paid_contract_is_the_condition():
    snap = _parse("stankin_paid.html")

    me = snap.by_code("1366129")
    assert me.contract is False    # «Наличие договора» пусто
    assert me.payment is None      # Станкин paid has no «Оплата» column
    assert me.consent is False     # условия для платного = наличие договора (нет)
    assert me.passing_main is True # Основной высший = ✓

    both = snap.by_code("1500000")
    assert both.contract is True   # «Наличие договора» = да
    assert both.consent is True    # условия соблюдены
