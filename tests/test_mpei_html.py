from pathlib import Path

from vuz_monitor.adapters.mpei_html import MpeiHtmlAdapter
from vuz_monitor.config import WatchConfig

FIX = Path(__file__).parent / "fixtures"


def _watch(url="https://pk.mpei.ru/info/entrants_list14.html"):
    return WatchConfig(name="МЭИ ИВТ", adapter="mpei_html", url=url)


def _parse(fixture):
    html = (FIX / fixture).read_text(encoding="utf-8")
    return MpeiHtmlAdapter().parse(html, _watch())


def test_budget_meta_and_header_skip():
    snap = _parse("mpei_budget.html")
    assert snap.meta.plan == 201                      # «вакантных мест: 201»
    assert snap.meta.updated_at == "2026-07-11 11:35:00"  # normalized to ISO
    assert len(snap.entrants) == 3                    # two header rows skipped


def test_budget_fields_and_place_order():
    snap = _parse("mpei_budget.html")
    top = snap.by_code("1514555")
    assert top.place == 1 and top.passing_main is True and top.consent is False

    me = snap.by_code("1366129")
    assert me.place == 2
    assert me.final_score == 259.0                    # plain integer, NOT ×1000
    assert me.priority == 3
    assert me.consent is False                        # Согласие: нет
    assert me.passing_main is False and me.passing_real is False
    assert me.needs_dormitory is False                # «б/о»
    assert me.contract is None and me.payment is None # budget has no contract/payment

    other = snap.by_code("1200000")
    assert other.consent is True and other.needs_dormitory is True  # Согласие да, «с/о гар.»


def test_paid_variant_autodetect_and_conditions():
    snap = _parse("mpei_paid.html")
    assert snap.meta.plan == 59

    me = snap.by_code("1366129")
    assert me.contract is True and me.payment is False   # Договор да, Оплата нет
    assert me.consent is False                            # условия = договор И оплата

    both = snap.by_code("1500000")
    assert both.contract is True and both.payment is True
    assert both.consent is True                           # оба «да» -> условия соблюдены
