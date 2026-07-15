# МИРЭА «окружение» (mirea-list.html) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Новая публичная страница `docs/mirea-list.html` показывает живое положение в одном конкурсе МИРЭА — всех на нашем месте и выше + следующих 10 ниже, в раскладке офсайта, с подсветкой нашей строки и полными кодами.

**Architecture:** Три изолированных блока в существующем стеке генерации страниц: (1) флаг конфига `track_neighbors` по образцу `track_scores`; (2) чистая функция сбора `_gather_neighbors(config, store)` читает последний снапшот и строит окно строк; (3) рендер `build_neighbors_html(specs)` рисует статичную таблицу. Интеграция — ветка в `render_pages` + ссылка в шапке. Пайплайн не меняется: страница читает уже сохранённый снапшот.

**Tech Stack:** Python 3 (dataclasses), SQLite (`Store`), pytest, self-contained inline HTML/CSS (без внешних CSS/JS), zoneinfo (MSK).

## Global Constraints

- Публичная страница (gh-pages): всегда `<meta name="robots" content="noindex, nofollow">`, весь HTML self-contained (без внешних CSS/JS/CDN), theme-aware (light+dark через `prefers-color-scheme`).
- Экранировать любые интерполируемые значения через `format.esc`.
- Форматирование баллов через `format.g` (None → «—», `0` → «0»).
- Коды на этой странице показываются **полностью** (без `mask_code`) — сознательное отступление от других страниц (выбор пользователя; коды публичны на priem.mirea.ru).
- `config.yaml` и `state.db` — gitignored, в worktree отсутствуют и в PR не входят; тесты гермичны (строят `Snapshot`/`Store` из фикстур).
- Все тесты запускаются из корня worktree: `.venv/bin/pytest` (или `python -m pytest`).
- Наш отслеживаемый код по умолчанию — `1366129` (`tracked_codes`).
- `NEIGHBORS_AFTER = 10` (сколько строк показывать после нашей).
- Коммиты частые, по одному на задачу; сообщения на англ., с трейлером `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

## File Structure

- `vuz_monitor/config.py` — **modify**: поле `track_neighbors` в `WatchConfig` + парсинг в `load_config`.
- `vuz_monitor/dashboard.py` — **modify**: `NEIGHBORS_AFTER`, `_gather_neighbors`, `build_neighbors_html`, `_note`/`_neighbor_row` хелперы, `_NEIGHBORS_STYLE`, `_LINK_LIST`, параметр `link_neighbors` в `build_html`/`build_table_html`/`build_score_progress_html`, ветка в `render_pages`. Импорт `normalize_code` из `.models`.
- `config.example.yaml` — **modify**: документировать флаг `track_neighbors`.
- `tests/test_neighbors.py` — **create**: все тесты фичи (config-парсинг, gather, render, render_pages-интеграция).

---

## Task 1: Флаг конфига `track_neighbors`

**Files:**
- Modify: `vuz_monitor/config.py` (dataclass `WatchConfig` ~строки 23-41; `load_config` цикл watches ~строки 90-106)
- Test: `tests/test_neighbors.py`

**Interfaces:**
- Consumes: ничего (первая задача).
- Produces: `WatchConfig.track_neighbors: bool` (default `False`); `load_config` заполняет его из YAML-ключа `track_neighbors`.

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_neighbors.py` со следующим содержимым:

```python
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
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `.venv/bin/pytest tests/test_neighbors.py::test_watch_config_parses_track_neighbors -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'track_neighbors'` (или `AttributeError`).

- [ ] **Step 3: Добавить поле в `WatchConfig`**

В `vuz_monitor/config.py`, в dataclass `WatchConfig`, сразу после строки
`track_scores: bool = False` добавить:

```python
    # build the neighbors list page (docs/mirea-list.html) for this competition.
    track_neighbors: bool = False
```

- [ ] **Step 4: Парсить ключ в `load_config`**

В `vuz_monitor/config.py`, в цикле построения `WatchConfig(...)` внутри `load_config`,
сразу после строки `track_scores=bool(w.get("track_scores", False)),` добавить:

```python
                track_neighbors=bool(w.get("track_neighbors", False)),
```

- [ ] **Step 5: Запустить тест — убедиться, что проходит**

Run: `.venv/bin/pytest tests/test_neighbors.py::test_watch_config_parses_track_neighbors -v`
Expected: PASS

- [ ] **Step 6: Коммит**

```bash
git add vuz_monitor/config.py tests/test_neighbors.py
git commit -m "feat: track_neighbors watch flag (config parsing)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Сбор данных — `_gather_neighbors`

**Files:**
- Modify: `vuz_monitor/dashboard.py` (импорты ~строка 17; новая функция рядом с `_gather_score_progress` ~строки 84-116)
- Test: `tests/test_neighbors.py`

**Interfaces:**
- Consumes: `WatchConfig.track_neighbors` (Task 1); `Store.load_prev(watch_id) -> Snapshot|None`; `Snapshot.entrants: list[Entrant]`; `config.resolve_codes(watch) -> list[str]`; `format.is_paid`; `models.normalize_code`.
- Produces: `_gather_neighbors(config, store) -> list[dict]`, где каждый dict:
  ```python
  {
    "title": str,            # snap.meta.title or watch.name
    "updated_at": str|None,  # snap.meta.updated_at (источниковое время)
    "fetched_at": str,       # snap.fetched_at (fallback времени)
    "paid": bool,            # is_paid(title) or is_paid(group/name)
    "our_codes": set[str],   # нормализованные коды (для подсветки)
    "we_absent": bool,       # нашего кода нет в списке
    "rows": list[Entrant],   # окно строк в порядке места
  }
  ```
  Плюс модульная константа `NEIGHBORS_AFTER = 10`.

- [ ] **Step 1: Написать падающие тесты**

Добавить в `tests/test_neighbors.py` (в конец файла) хелперы и тесты сбора:

```python
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
def test_gather_window_ahead_self_and_10_after():
    ents = [_ent(p, 1000000 + p) for p in range(1, 21)]     # places 1..20
    ents[4] = _ent(5, "1366129")                            # our code at place 5
    cfg, store, _ = _mk(ents)
    specs = dashboard._gather_neighbors(cfg, store)
    store.close()
    assert len(specs) == 1
    rows = specs[0]["rows"]
    assert [e.place for e in rows] == list(range(1, 16))    # 1..5 (self+ahead) + 6..15 (10 after)
    assert specs[0]["we_absent"] is False


def test_gather_we_are_first():
    ents = [_ent(p, 1000000 + p) for p in range(1, 21)]
    ents[0] = _ent(1, "1366129")
    cfg, store, _ = _mk(ents)
    specs = dashboard._gather_neighbors(cfg, store)
    store.close()
    assert [e.place for e in specs[0]["rows"]] == list(range(1, 12))   # self + 10 after = 11 rows


def test_gather_fewer_than_10_after():
    ents = [_ent(p, 1000000 + p) for p in range(1, 9)]      # places 1..8
    ents[4] = _ent(5, "1366129")                            # our code at place 5
    cfg, store, _ = _mk(ents)
    specs = dashboard._gather_neighbors(cfg, store)
    store.close()
    assert [e.place for e in specs[0]["rows"]] == [1, 2, 3, 4, 5, 6, 7, 8]  # only 3 after exist


def test_gather_absent_code_falls_back_to_top11():
    ents = [_ent(p, 1000000 + p) for p in range(1, 21)]     # our code NOT here
    cfg, store, _ = _mk(ents)
    specs = dashboard._gather_neighbors(cfg, store)
    store.close()
    assert specs[0]["we_absent"] is True
    assert [e.place for e in specs[0]["rows"]] == list(range(1, 12))   # top 11


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
```

- [ ] **Step 2: Запустить тесты — убедиться, что падают**

Run: `.venv/bin/pytest tests/test_neighbors.py -v -k gather`
Expected: FAIL — `AttributeError: module 'vuz_monitor.dashboard' has no attribute '_gather_neighbors'`.

- [ ] **Step 3: Добавить импорт `normalize_code`**

В `vuz_monitor/dashboard.py` найти строку импорта из `.format`:

```python
from .format import esc, fmt_source_time, g, is_paid, mask_code, pass_real, split_group, yesno
```

Сразу под ней (после блока импортов из `.format`) добавить:

```python
from .models import normalize_code
```

- [ ] **Step 4: Добавить константу и функцию `_gather_neighbors`**

В `vuz_monitor/dashboard.py`, сразу после функции `_gather_score_progress` (перед секцией
`# Time helpers`), добавить:

```python
NEIGHBORS_AFTER = 10  # how many rows to show below our own place


def _gather_neighbors(config, store):
    """One spec dict per `track_neighbors` competition that has a snapshot:
    {title, updated_at, fetched_at, paid, our_codes, we_absent, rows}. `rows` is the
    window «все на нашем месте и выше + следующие NEIGHBORS_AFTER», in place order.
    When our code is absent from the list, `we_absent=True` and `rows` is the top
    (NEIGHBORS_AFTER + 1)."""
    specs = []
    for w in config.watches:
        if not w.track_neighbors:
            continue
        snap = store.load_prev(w.watch_id)
        if snap is None:
            continue
        title = snap.meta.title if (snap.meta and snap.meta.title) else w.name
        our_codes = {normalize_code(c) for c in config.resolve_codes(w)}
        ranked = sorted(
            [e for e in snap.entrants if e.place is not None],
            key=lambda e: e.place,
        )
        our_places = [e.place for e in ranked if normalize_code(e.code_display) in our_codes]
        if our_places:
            cutoff = min(our_places)
            ahead_and_self = [e for e in ranked if e.place <= cutoff]
            after = [e for e in ranked if e.place > cutoff][:NEIGHBORS_AFTER]
            rows = ahead_and_self + after
            we_absent = False
        else:
            rows = ranked[: NEIGHBORS_AFTER + 1]
            we_absent = True
        specs.append({
            "title": title,
            "updated_at": snap.meta.updated_at if snap.meta else None,
            "fetched_at": snap.fetched_at,
            "paid": is_paid(title) or is_paid(w.group or w.name),
            "our_codes": our_codes,
            "we_absent": we_absent,
            "rows": rows,
        })
    return specs
```

- [ ] **Step 5: Запустить тесты — убедиться, что проходят**

Run: `.venv/bin/pytest tests/test_neighbors.py -v -k gather`
Expected: PASS (7 тестов).

- [ ] **Step 6: Коммит**

```bash
git add vuz_monitor/dashboard.py tests/test_neighbors.py
git commit -m "feat: _gather_neighbors — build the ahead+self+10 window from a snapshot

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Рендер — `build_neighbors_html`

**Files:**
- Modify: `vuz_monitor/dashboard.py` (новые хелперы + `build_neighbors_html` + `_NEIGHBORS_STYLE`; разместить после `build_score_progress_html`/`_SCORE_STYLE`, перед `_LEGEND`)
- Test: `tests/test_neighbors.py`

**Interfaces:**
- Consumes: spec-словари из `_gather_neighbors` (Task 2); `format.esc`, `format.g`, `format.yesno`, `format.fmt_source_time`; `models.normalize_code`; `dashboard._fetched_msk`.
- Produces: `build_neighbors_html(specs, now=None) -> str` (полная HTML-страница). Внутренние: `_note(e) -> str`, `_neighbor_row(e, our_codes, paid) -> str`, `_neighbor_section(spec, now) -> str`, константа строки `_NEIGHBORS_STYLE`.

- [ ] **Step 1: Написать падающие тесты**

Добавить в `tests/test_neighbors.py` (в конец файла):

```python
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
    assert "Платн" in paid_html
    assert "Согл" in budget_html


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
    assert "255" in html and "258" in html   # real values shown
    assert "—" in html                       # None → dash


def test_render_absent_banner():
    rows = [_ent(1, "1000001"), _ent(2, "1000002")]
    html = dashboard.build_neighbors_html([_spec(rows, we_absent=True)], now=NOW)
    assert "вашего кода нет в этом списке" in html
```

- [ ] **Step 2: Запустить тесты — убедиться, что падают**

Run: `.venv/bin/pytest tests/test_neighbors.py -v -k render`
Expected: FAIL — `AttributeError: module 'vuz_monitor.dashboard' has no attribute 'build_neighbors_html'`.

- [ ] **Step 3: Добавить хелперы и `build_neighbors_html`**

В `vuz_monitor/dashboard.py`, сразу после `build_score_progress_html` и его константы
`_SCORE_STYLE` (перед `_LEGEND`), добавить:

```python
# --------------------------------------------------------------------------- #
# Neighbors list page (docs/mirea-list.html)
# --------------------------------------------------------------------------- #
def _note(e) -> str:
    """«Примечание» text from the official passing flags."""
    if e.passing_real:
        return "планируется к зачислению"
    return "—"


def _neighbor_row(e, our_codes, paid) -> str:
    ours = normalize_code(e.code_display) in our_codes
    if ours:
        tr_cls = "you"
    elif e.passing_real:
        tr_cls = "pass-real"
    elif e.passing_main:
        tr_cls = "pass-main"
    else:
        tr_cls = ""
    num = f'{esc(e.place)}{" ◄ вы" if ours else ""}'
    flag = e.paid_ok if paid else e.consent
    cls_attr = f' class="{tr_cls}"' if tr_cls else ""
    return (
        f"<tr{cls_attr}>"
        f'<td class="num">{num}</td>'
        f'<td class="code">{esc(e.code_display)}</td>'
        f'<td class="num">{esc(e.priority) if e.priority is not None else "—"}</td>'
        f"<td>{esc(yesno(flag))}</td>"
        f'<td class="num">{esc(g(e.entrance_score))}</td>'
        f'<td class="num">{esc(g(e.achievement_score))}</td>'
        f'<td class="num">{esc(g(e.final_score))}</td>'
        f"<td>{esc(_note(e))}</td>"
        "</tr>"
    )


def _neighbor_section(spec, now) -> str:
    when = fmt_source_time(spec["updated_at"]) if spec["updated_at"] else _fetched_msk(spec["fetched_at"])
    paid = spec["paid"]
    flag_hdr = "Платн" if paid else "Согл"
    banner = ('<div class="banner">вашего кода нет в этом списке — показан топ списка</div>'
              if spec["we_absent"] else "")
    head = (
        "<thead><tr>"
        '<th class="num">№</th><th>Код</th><th class="num">Приор</th>'
        f"<th>{esc(flag_hdr)}</th>"
        '<th class="num">ВИ</th><th class="num">ИД</th><th class="num">Σбалл</th>'
        "<th>Примечание</th></tr></thead>"
    )
    body = "".join(_neighbor_row(e, spec["our_codes"], paid) for e in spec["rows"])
    return (
        f'<section class="nb-sec"><h2>{esc(spec["title"])}</h2>'
        f'<div class="caption">список по состоянию на {esc(when)}</div>'
        + banner
        + '<div class="nb-scroll"><table class="nb">'
        + head + "<tbody>" + body + "</tbody></table></div>"
        + "</section>"
    )


def build_neighbors_html(specs, now=None) -> str:
    """docs/mirea-list.html — «окружение»: для каждого track_neighbors конкурса
    таблица «все на нашем месте и выше + 10 после», раскладка офсайта, наша строка
    подсвечена, коды показаны полностью."""
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    sections = "".join(_neighbor_section(s, now) for s in specs) or \
        '<p class="empty">Нет отслеживаемых списков.</p>'
    links = _LINK_CARDS + " " + _LINK_TABLE
    return (
        "<!doctype html>\n"
        '<html lang="ru"><head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="robots" content="noindex, nofollow">\n'
        "<title>ВУЗ-мониторинг · окружение</title>\n"
        f"<style>{_NEIGHBORS_STYLE}</style>\n"
        "</head><body>\n"
        '<div class="wrap">\n'
        '<div class="topbar"><div class="summary"><b>Окружение в списке</b> · '
        + links + "</div></div>\n"
        f"{sections}\n"
        '<footer class="foot">обновляется каждый час · один конкурс · vuz_monitor</footer>\n'
        "</div>\n</body></html>\n"
    )


_NEIGHBORS_STYLE = """
:root{--bg:#f5f6f8;--card:#fff;--fg:#1a1d21;--muted:#6b7280;--border:#e5e7eb;--green:#15803d;--amber:#b45309;--accent:#2563eb;--you:#fef9c3;--row-green:rgba(34,197,94,.10);--row-amber:rgba(245,158,11,.12);}
@media (prefers-color-scheme:dark){:root{--bg:#0f1216;--card:#171b21;--fg:#e6e8eb;--muted:#9aa4b2;--border:#252b33;--green:#4ade80;--amber:#fbbf24;--accent:#60a5fa;--you:#3f3a12;--row-green:rgba(34,197,94,.13);--row-amber:rgba(245,158,11,.13);}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);line-height:1.4;font:14px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;}
.wrap{max-width:900px;margin:0 auto;padding:12px;}
.topbar{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--border);padding:8px 0;margin-bottom:12px;z-index:5;}
.summary{font-size:14px;}
.page-link{color:var(--accent);text-decoration:none;margin-left:8px;font-size:13px;}
.nb-sec{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px;margin-bottom:16px;}
.nb-sec h2{font-size:16px;margin:0 0 2px;}
.caption{font-size:12px;color:var(--muted);margin-bottom:10px;}
.banner{font-size:13px;background:var(--you);border-radius:8px;padding:8px 10px;margin-bottom:10px;}
.nb-scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;}
table.nb{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;font-size:13px;}
.nb th{text-align:left;color:var(--muted);font-weight:600;font-size:11px;border-bottom:1px solid var(--border);padding:6px 8px;white-space:nowrap;}
.nb td{padding:6px 8px;border-bottom:1px solid var(--border);white-space:nowrap;}
.nb .num{text-align:right;}
.nb th.num{text-align:right;}
.nb td.code{font-variant-numeric:tabular-nums;}
.nb tbody tr.pass-real{background:var(--row-green);}
.nb tbody tr.pass-main{background:var(--row-amber);}
.nb tbody tr.you{background:var(--you);font-weight:600;}
.foot{font-size:11px;color:var(--muted);text-align:center;margin-top:16px;}
.empty{color:var(--muted);}
"""
```

- [ ] **Step 4: Запустить тесты — убедиться, что проходят**

Run: `.venv/bin/pytest tests/test_neighbors.py -v -k render`
Expected: PASS (5 тестов).

- [ ] **Step 5: Коммит**

```bash
git add vuz_monitor/dashboard.py tests/test_neighbors.py
git commit -m "feat: build_neighbors_html — official-layout neighbors table

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Интеграция в `render_pages` + ссылка в шапке + документация

**Files:**
- Modify: `vuz_monitor/dashboard.py` (`_LINK_LIST` рядом с `_LINK_SCORES` ~строки 345-347; `render_pages` ~строки 68-81; сигнатуры+summary-бары `build_html` ~389/424, `build_table_html` ~542/581, `build_score_progress_html` ~773/783)
- Modify: `config.example.yaml` (документация флага рядом с `track_scores` ~строки 47-50)
- Test: `tests/test_neighbors.py`

**Interfaces:**
- Consumes: `_gather_neighbors` (Task 2), `build_neighbors_html` (Task 3).
- Produces: `render_pages` включает `"mirea-list.html"` при наличии данных; `build_html`/`build_table_html`/`build_score_progress_html` принимают `link_neighbors: bool = False` и подмешивают `_LINK_LIST`.

- [ ] **Step 1: Написать падающие тесты**

Добавить в `tests/test_neighbors.py` (в конец файла):

```python
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
```

- [ ] **Step 2: Запустить тесты — убедиться, что падают**

Run: `.venv/bin/pytest tests/test_neighbors.py -v -k render_pages`
Expected: FAIL — `mirea-list.html` не в `pages` (KeyError/assert).

- [ ] **Step 3: Добавить ссылку `_LINK_LIST`**

В `vuz_monitor/dashboard.py` найти:

```python
_LINK_SCORES = '<a class="page-link" href="mirea-scores.html">📊 баллы</a>'
```

Сразу под ней добавить:

```python
_LINK_LIST = '<a class="page-link" href="mirea-list.html">👥 окружение</a>'
```

- [ ] **Step 4: Ветка в `render_pages`**

В `vuz_monitor/dashboard.py` заменить тело `render_pages` (функция целиком, от `def render_pages` до `return pages`) на:

```python
def render_pages(config, store, now=None) -> dict:
    """All pages from a single state.db pass: {filename: html}. The score-loading
    page (mirea-scores.html) and the neighbors page (mirea-list.html) are included
    only when their feature has data for at least one competition."""
    groups, history = _gather(config, store)
    specs = _gather_score_progress(config, store)
    neighbors = _gather_neighbors(config, store)
    has_scores = bool(specs)
    has_neighbors = bool(neighbors)
    pages = {
        "index.html": build_html(groups, history, now=now,
                                 link_scores=has_scores, link_neighbors=has_neighbors),
        "table.html": build_table_html(groups, history, now=now,
                                       link_scores=has_scores, link_neighbors=has_neighbors),
    }
    if has_scores:
        pages["mirea-scores.html"] = build_score_progress_html(specs, now=now,
                                                               link_neighbors=has_neighbors)
    if has_neighbors:
        pages["mirea-list.html"] = build_neighbors_html(neighbors, now=now)
    return pages
```

- [ ] **Step 5: Прокинуть `link_neighbors` в `build_html`**

В `vuz_monitor/dashboard.py` в сигнатуре `build_html` заменить:

```python
def build_html(groups, history, now=None, link_scores=False) -> str:
```

на:

```python
def build_html(groups, history, now=None, link_scores=False, link_neighbors=False) -> str:
```

Затем в теле `build_html` найти строку, формирующую topbar:

```python
        + _summary_bar(groups, now, _LINK_TABLE + (" " + _LINK_SCORES if link_scores else ""))
```

заменить на:

```python
        + _summary_bar(groups, now, _LINK_TABLE
                       + (" " + _LINK_SCORES if link_scores else "")
                       + (" " + _LINK_LIST if link_neighbors else ""))
```

- [ ] **Step 6: Прокинуть `link_neighbors` в `build_table_html`**

В сигнатуре `build_table_html` заменить:

```python
def build_table_html(groups, history, now=None, link_scores=False) -> str:
```

на:

```python
def build_table_html(groups, history, now=None, link_scores=False, link_neighbors=False) -> str:
```

Затем найти в теле:

```python
        '<div class="topbar">' + _summary_bar(groups, now, _LINK_CARDS + (" " + _LINK_SCORES if link_scores else "")) + filters + "</div>\n"
```

заменить на:

```python
        '<div class="topbar">' + _summary_bar(groups, now, _LINK_CARDS
            + (" " + _LINK_SCORES if link_scores else "")
            + (" " + _LINK_LIST if link_neighbors else "")) + filters + "</div>\n"
```

- [ ] **Step 7: Прокинуть `link_neighbors` в `build_score_progress_html`**

В сигнатуре `build_score_progress_html` заменить:

```python
def build_score_progress_html(specialties, now=None) -> str:
```

на:

```python
def build_score_progress_html(specialties, now=None, link_neighbors=False) -> str:
```

Затем найти в теле:

```python
    links = _LINK_CARDS + " " + _LINK_TABLE
```

заменить на:

```python
    links = _LINK_CARDS + " " + _LINK_TABLE + (" " + _LINK_LIST if link_neighbors else "")
```

- [ ] **Step 8: Запустить тесты — убедиться, что проходят**

Run: `.venv/bin/pytest tests/test_neighbors.py -v -k render_pages`
Expected: PASS (2 теста).

- [ ] **Step 9: Документировать флаг в `config.example.yaml`**

В `config.example.yaml` найти блок-комментарий про `track_scores` (около строк 47-50,
заканчивается «…Можно на нескольких.»). Сразу после него, с тем же отступом, добавить:

```yaml
    # track_neighbors: true      # build docs/mirea-list.html for THIS competition:
                                 # «окружение» — все на нашем месте и выше + 10 после,
                                 # раскладка офсайта, наша строка подсвечена, коды
                                 # показаны полностью. Область — один конкурс.
```

- [ ] **Step 10: Полный прогон тестов (регрессия)**

Run: `.venv/bin/pytest -q`
Expected: PASS — все тесты, включая существующие `test_dashboard.py` / `test_score_progress.py` (сигнатуры `build_*` расширены дефолтными параметрами, поведение без флага не изменилось).

- [ ] **Step 11: Коммит**

```bash
git add vuz_monitor/dashboard.py config.example.yaml tests/test_neighbors.py
git commit -m "feat: wire mirea-list.html into render_pages + topbar link + docs

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Финальная проверка (после всех задач)

- [ ] **Полный прогон + дымовой рендер**

Run: `.venv/bin/pytest -q`
Expected: все зелёные.

Опционально (дымовой рендер настоящей страницы из фикстур — не из приватного `state.db`):
собрать `Snapshot` вручную, вызвать `build_neighbors_html`, записать во временный файл и
открыть в браузере глазами. Проверить: наша строка жёлтая с «◄ вы», коды целиком, тёмная
тема (эмуляция `prefers-color-scheme: dark`).

- [ ] **Ручной шаг после мержа (вне PR):** в приватном `config.yaml` на watch
  `params: { "competitions[]": "1862638623058304310", edu_level: 2 }` (МИРЭА — платно,
  «1. Интеллектуальные системы…») добавить `track_neighbors: true`, затем регенерировать:
  `.venv/bin/python -m vuz_monitor dashboard` — появится `docs/mirea-list.html`.

---

## Self-Review (проведено при написании плана)

**Spec coverage:**
- §4.1 конфиг `track_neighbors` → Task 1. ✅
- §4.2 `_gather_neighbors` (окно, our_codes, we_absent, paid) → Task 2. ✅
- §4.3 `build_neighbors_html` (колонки, полные коды, Примечание-маппинг, подсветка «вы», Платн/Согл, горизонтальный скролл) → Task 3. ✅
- §4.4 `render_pages` + `_LINK_LIST` + `link_neighbors` в трёх рендерах + обратные ссылки → Task 4. ✅
- §5 крайние случаи: выбыл/топ-11 (Task 2 + banner Task 3), мы №1 (Task 2), <10 после (Task 2), None→«—» (Task 3), нет снапшота (Task 2), несколько кодов→min place (Task 2). ✅
- §6 все тесты представлены как код в Task 1-4. ✅
- §7 YAGNI: без истории/JS/маскировки/лишних колонок — соблюдено. ✅
- §8 файлы: config.py, dashboard.py, config.example.yaml, tests — все затронуты. ✅

**Placeholder scan:** плейсхолдеров нет; каждый шаг содержит конкретный код/команду/ожидаемый вывод. ✅

**Type consistency:** `_gather_neighbors → list[dict]` с ключами `{title, updated_at, fetched_at, paid, our_codes, we_absent, rows}` — согласованы между Task 2 (produces), Task 3 `_spec` (тестовый двойник) и Task 4. `build_neighbors_html(specs, now)`, `build_*_html(..., link_neighbors=False)`, `_LINK_LIST` — имена совпадают во всех задачах. `NEIGHBORS_AFTER=10`. Подсветка через `normalize_code(e.code_display) in our_codes`. ✅
