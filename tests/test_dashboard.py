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


# --- _verdict_bucket (status.html светофор classifier) --------------------- #
# Green is gated on passing_real (Проходной ВП = official «прохожу сейчас»).
# passing_main (Основной ВП) is an INDEPENDENT axis, not a safety ladder:
# real MIREA data has passing_main=True while passing_real=False.
def test_verdict_flag_green_both_flags():
    assert dashboard._verdict_bucket(True, True, 10, 40) == "green"


def test_verdict_flag_amber_real_only():
    # passing_real да, passing_main нет → проходишь, но хрупко
    assert dashboard._verdict_bucket(False, True, 10, 40) == "amber"


def test_verdict_flag_red_no_real():
    assert dashboard._verdict_bucket(False, False, 900, 40) == "red"


def test_verdict_flag_red_even_when_main_true_but_real_false():
    # THE key case (test_mirea_api.py:52-53): iHP=1, iHPO=0 → officially NOT in now.
    # Must be red, never green — green requires passing_real.
    assert dashboard._verdict_bucket(True, False, 1, 40) == "red"


def test_verdict_mai_band_green_inside():
    # no flags (passing_real is None) → band by place vs kcp. band=max(3,round(60*0.1))=6
    assert dashboard._verdict_bucket(None, None, 5, 60) == "green"     # 5 <= 60-6


def test_verdict_mai_band_amber_near_border():
    assert dashboard._verdict_bucket(None, None, 58, 60) == "amber"    # 54 < 58 <= 66


def test_verdict_mai_band_red_outside():
    assert dashboard._verdict_bucket(None, None, 80, 60) == "red"      # 80 > 66


def test_verdict_none_no_flags_no_kcp():
    assert dashboard._verdict_bucket(None, None, 15, None) == "none"


def test_verdict_none_place_missing():
    assert dashboard._verdict_bucket(None, None, None, 60) == "none"


def test_verdict_none_kcp_nonpositive():
    assert dashboard._verdict_bucket(None, None, 5, 0) == "none"


# --- build_status_html (командный центр «светофор») ------------------------ #
def _status_html(reports, history=None, now=NOW):
    return dashboard.build_status_html(group_reports(reports), history or {}, now=now)


def test_status_green_needs_both_flags():
    html = _status_html([mk_report("Спец G", mk_status(passing_main=True, passing_real=True, place=5))])
    assert "Проходишь — надёжно" in html
    assert "Спец G" in html and "место 5" in html


def test_status_amber_real_only():
    html = _status_html([mk_report("Спец A", mk_status(passing_main=False, passing_real=True, place=50))])
    assert "Проходишь — хрупко" in html
    assert "Спец A" in html


def test_status_red_when_main_true_real_false_not_green():
    # THE key case: iHP=1, iHPO=0 → 🔴 with Осн.ВП marker, never green
    html = _status_html([mk_report("Спец R", mk_status(passing_main=True, passing_real=False, place=1))])
    assert "Осн.ВП: да" in html                       # honest annotation
    assert "Проходишь — надёжно" not in html          # not in green section
    assert "Мимо" in html


def test_status_mai_estimate_section():
    st = mk_status(passing_main=None, passing_real=None, place=15, plan=60)
    html = _status_html([mk_report("МАИ ПИ", st, group="МАИ — бюджет")])
    assert "По месту" in html and "оценка" in html
    assert "МАИ ПИ" in html


def test_status_mai_no_kcp_is_nodata():
    st = mk_status(passing_main=None, passing_real=None, place=15, plan=None)
    html = _status_html([mk_report("МАИ ПИ", st, group="МАИ — бюджет")])
    assert "Нет данных о проходе" in html


def test_status_paid_backup_section():
    st = mk_status(paid_ok=True, place=8, passing_main=None, passing_real=None)
    html = _status_html([mk_report("Платка", st, group="МИРЭА — платно", title="платно")])
    assert "Запасной аэродром" in html and "Платка" in html


def test_status_noindex_and_no_code_leak():
    html = _status_html([mk_report("Спец", mk_status(code_display="1366129"))])
    assert 'content="noindex' in html
    assert "1366129" not in html                      # код участника never leaks


def test_status_summary_counts():
    reps = [
        mk_report("G", mk_status(passing_main=True, passing_real=True), watch_id="w1"),
        mk_report("A", mk_status(passing_main=False, passing_real=True), watch_id="w2"),
        mk_report("R", mk_status(passing_main=False, passing_real=False), watch_id="w3"),
    ]
    html = _status_html(reps)
    assert "🟢 1" in html and "🟡 1" in html and "🔴 1" in html


def _pt(day, place, pm=True, pr=True):
    return {"day": day, "place": place, "final_score": 250.0,
            "passing_main": pm, "passing_real": pr, "consent": None, "contract": None}


def test_status_changes_bucket_transition_up():
    # yesterday red (real нет), today amber (real да) → «стал проходным»
    st = mk_status(passing_main=False, passing_real=True, place=40)
    hist = {("w1", "1366129"): [_pt("2026-07-10", 80, pm=False, pr=False),
                                _pt("2026-07-11", 40, pm=False, pr=True)]}
    html = dashboard.build_status_html(group_reports([mk_report("Спец", st, watch_id="w1")]),
                                       hist, now=NOW)
    assert "Что изменилось" in html
    assert "стал проходным" in html


def test_status_changes_place_move():
    st = mk_status(passing_main=True, passing_real=True, place=40)
    hist = {("w1", "1366129"): [_pt("2026-07-10", 61), _pt("2026-07-11", 40)]}
    html = dashboard.build_status_html(group_reports([mk_report("Спец", st, watch_id="w1")]),
                                       hist, now=NOW)
    assert "▲ 21" in html                                # improved by 21 places


def test_status_changes_none_message():
    st = mk_status(passing_main=True, passing_real=True, place=40)
    hist = {("w1", "1366129"): [_pt("2026-07-10", 41), _pt("2026-07-11", 40)]}  # Δ1, no transition
    html = dashboard.build_status_html(group_reports([mk_report("Спец", st, watch_id="w1")]),
                                       hist, now=NOW)
    assert "без изменений" in html


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
    assert set(pages) == {"index.html", "table.html", "status.html"}
    assert 'id="grid"' in pages["table.html"] and "место 12 из 1000" in pages["index.html"]
    store.close()


def _seed_store():
    store = Store(":memory:")
    w = WatchConfig(name="Спец", adapter="mirea_api", url="http://x", group="МИРЭА — бюджет")
    meta = ProgramMeta(title="Спец", plan=40, total=1000, updated_at="2026-07-11 06:00:00")
    ent = Entrant(code="1366129", code_display="1366129", place=12, final_score=252.0,
                  priority=1, consent=True, passing_main=True, passing_real=True)
    store.save(Snapshot(watch_id=w.watch_id, meta=meta, entrants=[ent],
                        fetched_at=datetime.now(timezone.utc).isoformat()))
    cfg = AppConfig(telegram=TelegramConfig(chat_id="", bot_token=""),
                    heartbeat="on_change_only", tracked_codes=["1366129"], watches=[w])
    return store, cfg


def test_render_pages_isolates_failing_page(monkeypatch):
    store, cfg = _seed_store()

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(dashboard, "build_status_html", boom)
    pages = dashboard.render_pages(cfg, store)
    assert "index.html" in pages and "table.html" in pages   # survivors still rendered
    assert "status.html" not in pages                        # failed page skipped, not fatal
    store.close()


def test_render_pages_includes_status():
    store, cfg = _seed_store()
    pages = dashboard.render_pages(cfg, store)
    assert "status.html" in pages
    assert "Куда я реально прохожу" in pages["status.html"]
    assert 'href="status.html"' in pages["index.html"]       # topbar link wired
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
