"""Tests for the МИРЭА neighbors list page (docs/mirea-list.html)."""
from datetime import datetime, timezone

from vuz_monitor import dashboard
from vuz_monitor.config import AppConfig, TelegramConfig, WatchConfig
from vuz_monitor.models import Entrant, ProgramMeta, Snapshot
from vuz_monitor.store import Store

NOW = datetime(2026, 7, 15, 7, 0, 0, tzinfo=timezone.utc)


# --- config: track_neighbors flag --- #
def test_watch_config_parses_track_neighbors(tmp_path):
    from vuz_monitor.config import load_config
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "telegram: {chat_id: '1', bot_token: 'x'}\n"
        "tracked_codes: ['1366129']\n"
        "watches:\n"
        "  - {name: tracked, adapter: mirea_api, url: 'http://x', track_neighbors: true}\n"
        "  - {name: untracked, adapter: mirea_api, url: 'http://y'}\n",
        encoding="utf-8",
    )
    app = load_config(str(cfg))
    watches = {w.name: w for w in app.watches}
    assert watches["tracked"].track_neighbors is True
    assert watches["untracked"].track_neighbors is False   # default off


# --- helpers --- #
def _ent(place, code, **kw):
    return Entrant(code=str(code), code_display=str(code), place=place, **kw)


def _mk(entrants, tracked="1366129", track=True, group="МИРЭА — платно",
        title="1. Интеллектуальные системы", updated_at="2026-07-15 09:46:00"):
    """Store(:memory:) + AppConfig with one (optionally flagged) watch and a saved
    snapshot. Returns (cfg, store, watch)."""
    store = Store(":memory:")
    w = WatchConfig(name="Спец", adapter="mirea_api", url="http://x",
                    group=group, track_neighbors=track)
    store.save(Snapshot(
        watch_id=w.watch_id,
        meta=ProgramMeta(title=title, plan=40, total=len(entrants), updated_at=updated_at),
        entrants=entrants, fetched_at=NOW.isoformat(),
    ))
    cfg = AppConfig(telegram=TelegramConfig(chat_id="", bot_token=""),
                    heartbeat="on_change_only", tracked_codes=[tracked], watches=[w])
    return cfg, store, w


# --- _gather_neighbors --- #
def test_gather_filters_by_consent():
    # places 1..6; only some gave consent (=accepted). Our code among them.
    ents = [
        _ent(1, "1000001", consent=False),
        _ent(2, "1366129", consent=True),   # us, eligible
        _ent(3, "1000003", consent=True),
        _ent(4, "1000004", consent=False),
        _ent(5, "1000005", consent=True),
        _ent(6, "1000006", consent=False),
    ]
    cfg, store, _ = _mk(ents)
    specs = dashboard._gather_neighbors(cfg, store)
    store.close()
    rows = specs[0]["rows"]
    assert [e.code for e in rows] == ["1366129", "1000003", "1000005"]  # consent only, place order
    assert specs[0]["we_absent"] is False


def test_gather_filters_by_is_active():
    ents = [
        _ent(1, "1366129", consent=True, is_active=True),
        _ent(2, "1000002", consent=True, is_active=False),   # consent but inactive → excluded
        _ent(3, "1000003", consent=True, is_active=True),
    ]
    cfg, store, _ = _mk(ents)
    specs = dashboard._gather_neighbors(cfg, store)
    store.close()
    assert [e.code for e in specs[0]["rows"]] == ["1366129", "1000003"]


def test_gather_full_list_no_window_cap():
    # 15 eligible below us → all shown (no 10-cap). We are place 1.
    ents = [_ent(1, "1366129", consent=True)]
    ents += [_ent(p, 1000000 + p, consent=True) for p in range(2, 17)]  # places 2..16
    cfg, store, _ = _mk(ents)
    specs = dashboard._gather_neighbors(cfg, store)
    store.close()
    assert len(specs[0]["rows"]) == 16   # full eligible list, not 11


def test_gather_we_absent_when_our_consent_false():
    ents = [
        _ent(1, "1000001", consent=True),
        _ent(2, "1366129", consent=False),   # us, NOT eligible
        _ent(3, "1000003", consent=True),
    ]
    cfg, store, _ = _mk(ents)
    specs = dashboard._gather_neighbors(cfg, store)
    store.close()
    assert specs[0]["we_absent"] is True
    assert [e.code for e in specs[0]["rows"]] == ["1000001", "1000003"]  # us excluded


def test_gather_empty_when_none_eligible():
    ents = [_ent(1, "1366129", consent=False), _ent(2, "1000002", consent=False)]
    cfg, store, _ = _mk(ents)
    specs = dashboard._gather_neighbors(cfg, store)
    store.close()
    assert specs[0]["rows"] == []          # spec still present (page still generated)
    assert specs[0]["we_absent"] is True


def test_gather_budget_filters_by_passing_real():
    # budget watch: filter is passing_real (iHPO), NOT consent
    ents = [
        _ent(1, "1000001", passing_real=False, consent=True),   # consent but not passing → excluded
        _ent(2, "1366129", passing_real=True, consent=False),   # us, passing → included
        _ent(3, "1000003", passing_real=True, consent=False),
    ]
    cfg, store, _ = _mk(ents, group="МИРЭА — бюджет")
    specs = dashboard._gather_neighbors(cfg, store)
    store.close()
    assert specs[0]["paid"] is False
    assert [e.code for e in specs[0]["rows"]] == ["1366129", "1000003"]  # passing_real only, place order
    assert specs[0]["we_absent"] is False


def test_gather_budget_excludes_inactive():
    ents = [
        _ent(1, "1366129", passing_real=True, is_active=True),
        _ent(2, "1000002", passing_real=True, is_active=False),  # passing but inactive → excluded
        _ent(3, "1000003", passing_real=True, is_active=True),
    ]
    cfg, store, _ = _mk(ents, group="МИРЭА — бюджет")
    specs = dashboard._gather_neighbors(cfg, store)
    store.close()
    assert [e.code for e in specs[0]["rows"]] == ["1366129", "1000003"]


def test_gather_paid_still_filters_by_consent_not_passing_real():
    # paid watch unchanged: consent decides, passing_real is ignored
    ents = [
        _ent(1, "1000001", consent=True, passing_real=False),   # consent → included
        _ent(2, "1366129", consent=False, passing_real=True),   # passing but no consent → EXCLUDED
        _ent(3, "1000003", consent=True, passing_real=True),
    ]
    cfg, store, _ = _mk(ents, group="МИРЭА — платно")
    specs = dashboard._gather_neighbors(cfg, store)
    store.close()
    assert specs[0]["paid"] is True
    assert [e.code for e in specs[0]["rows"]] == ["1000001", "1000003"]  # us excluded (no consent)
    assert specs[0]["we_absent"] is True


def test_gather_paid_flag_from_group():
    ents = [_ent(1, "1366129")]
    cfg_p, store_p, _ = _mk(ents, group="МИРЭА — платно")
    cfg_b, store_b, _ = _mk(ents, group="МИРЭА — бюджет")
    paid = dashboard._gather_neighbors(cfg_p, store_p)[0]["paid"]
    budget = dashboard._gather_neighbors(cfg_b, store_b)[0]["paid"]
    store_p.close(); store_b.close()
    assert paid is True and budget is False


def test_gather_skips_watch_without_snapshot():
    store = Store(":memory:")
    w = WatchConfig(name="Спец", adapter="mirea_api", url="http://x", track_neighbors=True)
    cfg = AppConfig(telegram=TelegramConfig(chat_id="", bot_token=""),
                    heartbeat="on_change_only", tracked_codes=["1366129"], watches=[w])
    specs = dashboard._gather_neighbors(cfg, store)   # nothing saved
    store.close()
    assert specs == []


def test_gather_ignores_unflagged_watch():
    ents = [_ent(1, "1366129")]
    cfg, store, _ = _mk(ents, track=False)
    specs = dashboard._gather_neighbors(cfg, store)
    store.close()
    assert specs == []


# --- build_neighbors_html --- #
def _spec(rows, our_codes=("1366129",), paid=True, we_absent=False,
          title="1. Интеллектуальные системы", updated_at="2026-07-15 09:46:00"):
    return {"title": title, "updated_at": updated_at, "fetched_at": NOW.isoformat(),
            "paid": paid, "our_codes": {normalize_code(c) for c in our_codes},
            "we_absent": we_absent, "rows": rows}


def _import_norm():
    from vuz_monitor.models import normalize_code
    return normalize_code


normalize_code = _import_norm()


def test_render_highlights_our_row_and_shows_full_code():
    rows = [_ent(1, "1366129", final_score=258.0), _ent(2, "1179201", final_score=256.0)]
    html = dashboard.build_neighbors_html([_spec(rows)], now=NOW)
    assert 'class="you"' in html          # our row highlighted
    assert "◄ вы" in html                 # marker
    assert "1366129" in html              # full code, NOT masked
    assert "•••6129" not in html
    assert 'content="noindex' in html and "<!doctype html>" in html


def test_render_paid_vs_budget_column_header():
    rows = [_ent(1, "1366129")]
    paid_html = dashboard.build_neighbors_html([_spec(rows, paid=True)], now=NOW)
    budget_html = dashboard.build_neighbors_html([_spec(rows, paid=False)], now=NOW)
    assert "Платн" in paid_html and "Согл" not in paid_html
    assert "Согл" in budget_html and "Платн" not in budget_html


def test_render_note_mapping():
    rows = [
        _ent(1, "1366129", passing_real=True),                    # → планируется к зачислению
        _ent(2, "222", passing_real=False, passing_main=True),    # → amber row, note «—»
        _ent(3, "333", passing_real=None, passing_main=None),     # → note «—»
    ]
    html = dashboard.build_neighbors_html([_spec(rows)], now=NOW)
    assert html.count("планируется к зачислению") == 1   # only the passing_real row
    assert "pass-main" in html                           # amber row present


def test_render_scores_and_missing():
    rows = [_ent(1, "1366129", entrance_score=255.0, achievement_score=3.0, final_score=258.0),
            _ent(2, "222", entrance_score=None, achievement_score=None, final_score=None)]
    html = dashboard.build_neighbors_html([_spec(rows)], now=NOW)
    assert '<td class="num">255</td>' in html   # entrance shown via g
    assert '<td class="num">258</td>' in html   # final shown via g
    assert "—" in html                          # None scores → dash
    assert "None" not in html                   # g used, never str(None)


def test_render_sequential_numbering():
    # official places are gappy (5, 40, 800) but № must be 1,2,3
    rows = [_ent(5, "1366129"), _ent(40, "222"), _ent(800, "333")]
    html = dashboard.build_neighbors_html([_spec(rows)], now=NOW)
    assert '<td class="num">1 ◄ вы</td>' in html   # our row → seq 1, not place 5
    assert '<td class="num">2</td>' in html         # seq 2, not place 40
    assert '<td class="num">3</td>' in html         # seq 3, not place 800
    assert '<td class="num">5</td>' not in html      # official place never shown as №
    assert '<td class="num">800</td>' not in html


def test_render_flag_uses_consent_not_paid_ok():
    # consent=True, paid_ok=False → flag column must show «да» (consent), not «нет»
    rows = [_ent(1, "1366129", consent=True, paid_ok=False)]
    html = dashboard.build_neighbors_html([_spec(rows, paid=True)], now=NOW)
    # flag td is the 4th cell; assert the yes-value is present for a consent row
    assert "<td>да</td>" in html


def test_render_empty_eligible_message():
    html = dashboard.build_neighbors_html([_spec([], we_absent=True)], now=NOW)
    assert "Пока никто не выполнил условия для платного" in html
    assert "вашего кода нет" not in html   # empty message replaces the banner


def test_render_absent_banner():
    rows = [_ent(1, "1000001", consent=True), _ent(2, "1000002", consent=True)]
    html = dashboard.build_neighbors_html([_spec(rows, we_absent=True)], now=NOW)
    assert "вашего кода нет среди выполнивших условия для платного" in html


# --- render_pages integration --- #
def test_render_pages_includes_list_page_when_flagged():
    ents = [_ent(1, "1366129"), _ent(2, "1179201")]
    cfg, store, _ = _mk(ents, track=True)
    pages = dashboard.render_pages(cfg, store)
    store.close()
    assert "mirea-list.html" in pages
    assert 'href="mirea-list.html"' in pages["index.html"]
    assert 'href="mirea-list.html"' in pages["table.html"]


def test_render_pages_omits_list_page_when_not_flagged():
    ents = [_ent(1, "1366129")]
    cfg, store, _ = _mk(ents, track=False)
    pages = dashboard.render_pages(cfg, store)
    store.close()
    assert "mirea-list.html" not in pages
    assert 'href="mirea-list.html"' not in pages["index.html"]


def test_scores_and_list_pages_cross_link_bidirectionally():
    ents = [_ent(1, "1366129"), _ent(2, "1179201")]
    store = Store(":memory:")
    w = WatchConfig(name="Спец", adapter="mirea_api", url="http://x",
                    group="МИРЭА — платно", track_neighbors=True, track_scores=True)
    ts = NOW.isoformat()
    store.save(Snapshot(watch_id=w.watch_id,
        meta=ProgramMeta(title="Спец (платно)", plan=15, total=2, updated_at="2026-07-15 09:46:00"),
        entrants=ents, fetched_at=ts))
    store.append_score_progress(w.watch_id, ts, 2, 0, {1300000: [2, 0]})
    cfg = AppConfig(telegram=TelegramConfig(chat_id="", bot_token=""),
                    heartbeat="on_change_only", tracked_codes=["1366129"], watches=[w])
    pages = dashboard.render_pages(cfg, store)
    store.close()
    assert "mirea-list.html" in pages and "mirea-scores.html" in pages
    assert 'href="mirea-scores.html"' in pages["mirea-list.html"]   # list → scores
    assert 'href="mirea-list.html"' in pages["mirea-scores.html"]   # scores → list
