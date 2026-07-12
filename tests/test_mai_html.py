"""МАИ adapter: table selection (largest with «Уникальный код»), budget «Согласие»
vs paid «Договор», updated_at parsing, and cascade-by-label resolution.

Fixtures are TRIMMED but REAL saved DOM from public.mai.ru (headers, quota tables,
«Дата последнего обновления»)."""
from pathlib import Path

import pytest

from vuz_monitor.adapters import base
from vuz_monitor.adapters.mai_html import MaiHtmlAdapter, _norm, _parse_updated
from vuz_monitor.config import WatchConfig

FIX = Path(__file__).parent / "fixtures"


def _parse(fixture, name, group):
    html = (FIX / fixture).read_text(encoding="utf-8")
    w = WatchConfig(name=name, adapter="mai_html", url="https://priem.mai.ru/list/", group=group)
    return MaiHtmlAdapter().parse(html, w)


# --- parse: budget --------------------------------------------------------- #
def test_budget_picks_largest_table_and_maps():
    snap = _parse("mai_budget.html", "ИВТ", "МАИ — бюджет")
    # 5 tables in the file (4 small quotas + общий конкурс); the largest wins
    assert snap.meta.total == len(snap.entrants) == 9
    assert snap.meta.updated_at == "2026-07-12 15:58:18"  # normalized ДД.ММ.ГГГГ → ISO

    me = snap.by_code("1366129")
    assert me.place == 1137           # № column, not row order
    assert me.final_score == 258.0    # «Сумма баллов» (not «по предметам»/«за ИД»)
    assert me.priority == 5
    assert me.consent is False        # empty «Согласие» → нет
    assert me.contract is None and me.payment is None      # budget has no «Договор»
    assert me.passing_main is None and me.passing_real is None  # МАИ publishes no ВП flags


# --- parse: paid («Договор» instead of «Согласие») ------------------------- #
def test_paid_uses_dogovor_as_condition():
    snap = _parse("mai_paid.html", "ИВТ", "МАИ — платно")
    me = snap.by_code("1366129")
    assert me.place == 176
    assert me.final_score == 258.0
    assert me.contract is False       # empty «Договор» → нет
    assert me.consent is False        # paid consent == contract (no «Оплата» column)
    assert me.payment is None
    assert me.passing_main is None and me.passing_real is None
    assert snap.meta.updated_at == "2026-07-12 15:58:18"


# --- updated_at parsing ---------------------------------------------------- #
def test_parse_updated():
    assert _parse_updated("… Дата последнего обновления: 12.07.2026 15:58:18 …") == "2026-07-12 15:58:18"
    assert _parse_updated("no stamp here") is None


# --- cascade resolve (_pick) ---------------------------------------------- #
def test_pick_exact_one_match_on_real_fragment():
    spec_html = (FIX / "mai_cascade_spec.html").read_text(encoding="utf-8")
    tok = MaiHtmlAdapter._pick(spec_html, "Информатика и вычислительная техника", step="spec")
    assert tok and tok not in ("", "0")


def test_pick_errors_on_missing_label():
    spec_html = (FIX / "mai_cascade_spec.html").read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        MaiHtmlAdapter._pick(spec_html, "Такого направления нет", step="spec")


def test_pick_normalizes_nbsp_and_whitespace():
    frag = '<select id="lvl"><option value="t">Базовое\xa0высшее  образование</option>' \
           '<option value="0">---</option></select>'
    assert MaiHtmlAdapter._pick(frag, "Базовое высшее образование", select_id="lvl", step="level") == "t"


def test_pick_scoped_to_select_id():
    # a #place with two options; exact-match «МАИ» must not match «Взлет МАИ»
    frag = '<select id="place"><option value="0">---</option>' \
           '<option value="a">МАИ</option><option value="b">Филиал "Взлет МАИ"</option></select>'
    assert MaiHtmlAdapter._pick(frag, "МАИ", select_id="place", step="place") == "a"


# --- defensive URL build --------------------------------------------------- #
def test_data_url_from_token_and_passthrough():
    assert MaiHtmlAdapter._data_url("p2026_1_l1") == "https://public.mai.ru/priem/list/data/p2026_1_l1.html"
    assert MaiHtmlAdapter._data_url("https://x/y.html") == "https://x/y.html"
    assert MaiHtmlAdapter._data_url("/abs/path.html") == "https://public.mai.ru/abs/path.html"


# --- M0 shared helper: colspan-aware header alignment ---------------------- #
def test_parse_labeled_table_expands_colspan():
    from bs4 import BeautifulSoup
    html = ("<table><tr><th>№</th><th colspan='2'>Уникальный код</th><th>Приоритет</th></tr>"
            "<tr><td>1</td><td>1366129</td><td>x</td><td>4</td></tr></table>")
    tbl = BeautifulSoup(html, "lxml").find("table")
    cols, rows = base.parse_labeled_table(tbl, [("code", "уникальный код"), ("priority", "приоритет")])
    # colspan=2 shifts «Приоритет» to index 3, and the code column to index 1
    assert cols["code"] == 1 and cols["priority"] == 3
    assert rows[0][cols["priority"]] == "4"


def test_norm():
    assert _norm("Базовое\xa0высшее  образование") == "базовое высшее образование"
    assert _norm("Прикладная  математика") == "прикладная математика"
