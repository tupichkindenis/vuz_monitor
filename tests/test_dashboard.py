"""Dashboard rendering: cards, escaping, code states, sparkline edge cases,
and generate() from state.db."""
from datetime import datetime, timezone

from vuz_monitor import dashboard
from vuz_monitor.config import AppConfig, TelegramConfig, WatchConfig
from vuz_monitor.diff import CodeStatus
from vuz_monitor.models import Entrant, ProgramMeta, Snapshot
from vuz_monitor.report import CodeReport, WatchReport, group_reports
from vuz_monitor.store import Store

NOW = datetime(2026, 7, 11, 7, 0, 0, tzinfo=timezone.utc)
FETCHED = datetime(2026, 7, 11, 6, 30, 0, tzinfo=timezone.utc).isoformat()  # 30 min old → fresh


def mk_status(**kw):
    d = dict(
        code_display="1366129", present=True, place=10, priority=1,
        final_score=250.0, consent=True, passing_main=True, passing_real=True,
        paid_ok=None, contract=None, payment=None, needs_dormitory=None,
        ahead=9, total=1000, plan=40,
    )
    d.update(kw)
    return CodeStatus(**d)


def mk_report(name, status, group="МИРЭА — бюджет", watch_id="w1", fetched_at=FETCHED, title=None):
    return WatchReport(
        name=name, title=(title if title is not None else name), group=group,
        codes=[CodeReport(status=status, changes=[], first_run=False)],
        watch_id=watch_id, fetched_at=fetched_at,
    )


def _html(reports, history=None):
    return dashboard.build_html(group_reports(reports), history or {}, now=NOW)


# --- applications-per-day page (mirea-applications.html) -------------------- #
APP_HISTORY = [
    {"ts": "2026-07-13T09:00:00+00:00", "total": 4000, "no_score": 100, "buckets": {}},
    {"ts": "2026-07-13T20:00:00+00:00", "total": 4087, "no_score": 90, "buckets": {}},   # last of 13th MSK
    {"ts": "2026-07-14T20:00:00+00:00", "total": 4484, "no_score": 80, "buckets": {}},
    {"ts": "2026-07-15T20:00:00+00:00", "total": 4672, "no_score": 70, "buckets": {}},
]


def test_daily_totals_downsamples_last_per_msk_day():
    assert dashboard._daily_totals(APP_HISTORY) == [
        ("2026-07-13", 4087), ("2026-07-14", 4484), ("2026-07-15", 4672),
    ]


def test_daily_totals_empty():
    assert dashboard._daily_totals([]) == []


def test_build_applications_html_renders_series():
    html = dashboard.build_applications_html(
        [{"title": "Интеллектуальные системы", "history": APP_HISTORY, "tracked": None}], now=NOW)
    assert "Интеллектуальные системы" in html
    assert "4672" in html and "4087" in html         # daily totals present
    assert "+397" in html                            # delta 14th (4484-4087)
    assert "<svg" in html                            # chart rendered
    assert 'content="noindex' in html                # not indexed
    assert 'href="index.html"' in html               # cross-link back


def test_build_applications_html_empty_specs():
    html = dashboard.build_applications_html([], now=NOW)
    assert "<svg" not in html                         # no chart, but must not crash
    assert 'content="noindex' in html


# --- cards / summary ------------------------------------------------------- #
def test_present_card_renders_standing():
    html = _html([mk_report("Спец A", mk_status(place=12, final_score=252.0))])
    assert "место 12 из 1000" in html
    assert "балл 252" in html
    assert "проходите" in html
    assert "pass-real" in html
    assert "Проходной ВП: 1/1" in html
    assert "Основной ВП: 1" in html


def test_code_is_masked_on_page():
    html = _html([mk_report("Спец", mk_status(code_display="1366129"))])
    assert "•••6129" in html          # masked code shown
    assert "1366129" not in html      # full код участника never in the public page
    assert 'content="noindex' in html  # reduce search indexing


def test_filter_switchers_present():
    reps = [
        mk_report("A", mk_status(), group="МИРЭА — бюджет", watch_id="w1"),
        mk_report("B", mk_status(), group="МИРЭА — платно", watch_id="w2", title="платно"),
        mk_report("C", mk_status(), group="МЭИ — бюджет", watch_id="w3"),
    ]
    html = dashboard.build_html(group_reports(reps), {}, now=NOW)
    assert 'data-dim="vuz"' in html and 'data-dim="osnova"' in html      # both switcher rows
    assert ">МИРЭА<" in html and ">МЭИ<" in html                          # ВУЗ chips
    assert ">Бюджет<" in html and ">Платно<" in html                      # основа chips
    assert 'data-vuz="МИРЭА" data-osnova="бюджет"' in html                # sections carry axes
    assert 'data-vuz="МИРЭА" data-osnova="платно"' in html
    assert 'data-vuz="МЭИ" data-osnova="бюджет"' in html
    assert "localStorage" in html                                        # JS enhancement present


# --- desktop summary table -------------------------------------------------- #
def test_table_row_per_specialty_sortable_masked():
    reps = [
        mk_report("Спец A", mk_status(place=12, priority=1, final_score=252.0), watch_id="w1"),
        mk_report("Спец B", mk_status(place=5, priority=2, final_score=260.0), watch_id="w2"),
    ]
    html = dashboard.build_table_html(group_reports(reps), {}, now=NOW)
    assert html.count('<tr class=') == 2          # one row per specialty (thead <tr> has no class)
    assert '<table id="grid"' in html and "data-num" in html      # sortable numeric headers
    assert 'data-sort="12"' in html and 'data-sort="5"' in html   # place values sortable
    assert 'getElementById(\'grid\')' in html                     # sort script present
    assert 'content="noindex' in html
    assert "•••6129" in html and "1366129" not in html            # masked, no leak
    assert 'href="index.html"' in html                            # cross-link back to cards


def test_table_no_vp_flags_shows_dash():
    html = dashboard.build_table_html(
        group_reports([mk_report("МАИ", mk_status(passing_real=None, passing_main=None),
                                 group="МАИ — бюджет")]), {}, now=NOW)
    assert '<tr class="neutral"' in html
    assert '<td class="preal"><span class="muted">—</span></td>' in html   # Прох.ВП «—»


def test_table_absent_row():
    st = mk_status(present=False, place=None, final_score=None, priority=None,
                   passing_real=None, passing_main=None)
    html = dashboard.build_table_html(group_reports([mk_report("Спец", st)]), {}, now=NOW)
    assert '<tr class="absent"' in html and "выбыл" in html


def test_table_filter_chips_and_row_axes():
    reps = [
        mk_report("A", mk_status(), group="МИРЭА — бюджет", watch_id="w1"),
        mk_report("B", mk_status(), group="МЭИ — платно", watch_id="w2", title="платно"),
    ]
    html = dashboard.build_table_html(group_reports(reps), {}, now=NOW)
    assert 'data-dim="vuz"' in html and 'data-dim="osnova"' in html      # filter chip rows
    assert ">МИРЭА<" in html and ">МЭИ<" in html
    assert 'data-vuz="МИРЭА" data-osnova="бюджет"' in html               # rows carry axes
    assert 'data-vuz="МЭИ" data-osnova="платно"' in html
    assert "data-nosort" in html                                        # Тренд not sortable


def test_table_sparkline_from_history():
    reps = [mk_report("Спец", mk_status(), watch_id="w1")]
    hist = {("w1", "1366129"): [{"place": 100}, {"place": 60}, {"place": 40}]}
    html = dashboard.build_table_html(group_reports(reps), hist, now=NOW)
    assert 'class="spark"' in html and "spark-place" in html and "<polyline" in html


def test_cards_page_links_to_table():
    html = _html([mk_report("Спец", mk_status())])
    assert 'href="table.html"' in html            # cards → table cross-link


def test_render_pages_returns_both():
    store = Store(":memory:")
    w = WatchConfig(name="Спец", adapter="mirea_api", url="http://x", group="МИРЭА — бюджет")
    meta = ProgramMeta(title="Спец", plan=40, total=1000, updated_at="2026-07-11 06:00:00")
    ent = Entrant(code="1366129", code_display="1366129", place=12, final_score=252.0,
                  priority=1, consent=True, passing_main=True, passing_real=True)
    store.save(Snapshot(watch_id=w.watch_id, meta=meta, entrants=[ent],
                        fetched_at=datetime.now(timezone.utc).isoformat()))
    cfg = AppConfig(telegram=TelegramConfig(chat_id="", bot_token=""),
                    heartbeat="on_change_only", tracked_codes=["1366129"], watches=[w])
    pages = dashboard.render_pages(cfg, store)
    assert set(pages) == {"index.html", "table.html"}
    assert 'id="grid"' in pages["table.html"] and "место 12 из 1000" in pages["index.html"]
    store.close()


def test_vp_legend_present():
    html = _html([mk_report("Спец", mk_status())])
    assert "Что такое ВП" in html          # collapsible legend
    assert "высший приоритет" in html
    assert "Проходной ВП" in html and "Основной ВП" in html


def test_switchers_omitted_when_single_choice():
    # one ВУЗ, one основа → no point offering a filter
    html = _html([mk_report("Только один", mk_status(), group="МИРЭА — бюджет")])
    assert 'data-dim="vuz"' not in html
    assert 'data-dim="osnova"' not in html


def test_no_vp_flags_render_neutral_not_fail():
    # source without ВП flags (МАИ): passing_real & passing_main both None
    st = mk_status(passing_real=None, passing_main=None)
    html = _html([mk_report("МАИ спец", st, group="МАИ — бюджет")])
    assert '<span class="pill grey">—</span>' in html   # neutral pill, not «не проходите»/«нет данных»
    assert "Проходной ВП: 0/0" in html                  # no-flag specialty excluded from denominator
    assert "проходите:" not in html                     # group header omits the misleading counter


def test_amber_when_only_main_passes():
    html = _html([mk_report("Спец", mk_status(passing_real=False, passing_main=True))])
    assert "pass-main" in html
    assert "не проходите" in html


def test_paid_uses_contract_wording():
    st = mk_status(consent=True, contract=True, payment=False)
    html = _html([mk_report("Платн", st, group="МЭИ — платно", title="платно")])
    assert "Соблюдены условия для платного: да" in html
    assert "договор: да" in html
    assert "оплата: нет" in html


# --- three code states ----------------------------------------------------- #
def test_absent_shows_vybyl():
    st = mk_status(present=False, place=None, final_score=None, consent=False,
                   passing_main=None, passing_real=None, priority=None)
    html = _html([mk_report("Спец", st)])
    assert "выбыл" in html


def test_no_status_shows_no_data():
    rep = WatchReport(name="X", group="G",
                      codes=[CodeReport(status=None, changes=[], first_run=False)],
                      watch_id="w1", fetched_at=FETCHED)
    html = _html([rep])
    assert "нет данных" in html
    assert "источник ещё не опрашивался" in html


# --- escaping -------------------------------------------------------------- #
def test_html_escaping():
    html = _html([mk_report('A & B <x> "q"', mk_status(), group="ВУЗ & <b>")])
    assert "A &amp; B &lt;x&gt;" in html
    assert "ВУЗ &amp; &lt;b&gt;" in html
    assert "<x>" not in html          # the raw injected tag must not survive


# --- sparklines ------------------------------------------------------------ #
def test_spark_row_zero_points():
    assert "копим историю" in dashboard._spark_row([])


def test_sparkline_one_point_is_a_dot():
    svg = dashboard._sparkline([50], higher_is_better=False, cls="spark-place")
    assert "<circle" in svg
    assert "polyline" not in svg


def test_sparkline_flat_series_no_div_by_zero():
    svg = dashboard._sparkline([50, 50, 50], higher_is_better=False, cls="spark-place")
    assert "polyline" in svg          # a straight mid-line, no crash


def test_sparkline_all_null_returns_empty():
    assert dashboard._sparkline([None, None], higher_is_better=False, cls="x") == ""


def test_sparkline_gap_is_segmented():
    # [50] | gap | [40, 30]  → one polyline (the 2-pt run) + a single-point circle + end marker
    svg = dashboard._sparkline([50, None, 40, 30], higher_is_better=False, cls="x")
    assert svg.count("<polyline") == 1
    assert svg.count("<circle") >= 2


def test_spark_row_all_null_place_still_shows_score():
    pts = [{"place": None, "final_score": 250.0}, {"place": None, "final_score": 251.0}]
    row = dashboard._spark_row(pts)
    assert "spark-dash" in row         # место empty → muted dash
    assert "spark-score" in row        # балл present → its own sparkline


# --- generate() from state.db ---------------------------------------------- #
def test_generate_reads_state_db():
    store = Store(":memory:")
    w = WatchConfig(name="Спец", adapter="mirea_api", url="http://x", group="МИРЭА — бюджет")
    meta = ProgramMeta(title="Спец", plan=40, total=1000, updated_at="2026-07-11 06:00:00")
    ent = Entrant(code="1366129", code_display="1366129", place=12, final_score=252.0,
                  priority=1, consent=True, passing_main=True, passing_real=True)
    snap = Snapshot(watch_id=w.watch_id, meta=meta, entrants=[ent],
                    fetched_at=datetime.now(timezone.utc).isoformat())
    store.save(snap)
    store.append_history(w.watch_id, "1366129", snap.fetched_at, 12, 252.0, True, True, True, None)
    cfg = AppConfig(telegram=TelegramConfig(chat_id="", bot_token=""),
                    heartbeat="on_change_only", tracked_codes=["1366129"], watches=[w])

    html = dashboard.generate(cfg, store)
    assert "место 12 из 1000" in html
    assert "балл 252" in html
    assert "Проходной ВП: 1/1" in html
    assert "11.07 06:00" in html       # source updated_at, MSK dd.mm HH:MM
    store.close()


def test_generate_missing_snapshot_is_no_data():
    store = Store(":memory:")
    w = WatchConfig(name="Спец", adapter="mirea_api", url="http://x", group="МИРЭА — бюджет")
    cfg = AppConfig(telegram=TelegramConfig(chat_id="", bot_token=""),
                    heartbeat="on_change_only", tracked_codes=["1366129"], watches=[w])
    html = dashboard.generate(cfg, store)   # nothing saved yet
    assert "нет данных" in html
    store.close()
