# Neighbors «соблюдены условия для платного» filter — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Страница «окружение» (`docs/mirea-list.html`) показывает только абитуриентов с «соблюдены условия для платного» (`consent`=accepted) и активных, сквозной нумерацией 1..N — как отфильтрованный вид офсайта МИРЭА.

**Architecture:** Правка изолирована в `vuz_monitor/dashboard.py`. Данные уже в `state.db` (новый фетч не нужен). Меняем: (1) `_gather_neighbors` — фильтр по `consent`+`is_active`, полный список без окна; (2) рендер `_neighbor_section`/`_neighbor_row` — сквозной №, флаг из `consent` (было `paid_ok`), баннер/пустой список. TDD в `tests/test_neighbors.py`.

**Tech Stack:** Python 3, pytest. Без новых зависимостей.

## Global Constraints

- Все изменения только в `vuz_monitor/dashboard.py` и `tests/test_neighbors.py`. Пайплайн, деплой, адаптеры, модели — не трогать.
- Поле фильтра — `Entrant.consent` (маппится из API `accepted`), НЕ `Entrant.paid_ok` (API `pc`).
- Доп. условие фильтра — `Entrant.is_active`.
- Нумерация «№» — сквозная `1..N` по отфильтрованному списку; официальное `place` не показывать.
- Ветка `feat/neighbors-paid-filter` (создана). Спека: `specs/2026-07-15-neighbors-paid-filter-design.md`.
- Регрессия: полный `pytest` зелёный после каждой задачи.

---

### Task 1: `_gather_neighbors` — фильтр по consent+is_active, полный список

**Files:**
- Modify: `vuz_monitor/dashboard.py:127` (удалить `NEIGHBORS_AFTER`), `:130-168` (`_gather_neighbors`)
- Test: `tests/test_neighbors.py` (заменить оконные gather-тесты на фильтр-тесты)

**Interfaces:**
- Consumes: `config.watches`, `store.load_prev(watch_id)`, `normalize_code`, `is_paid`, `config.resolve_codes(w)` — как сейчас.
- Produces: `_gather_neighbors(config, store) -> list[dict]`; каждый dict = `{title, updated_at, fetched_at, paid, our_codes: set[str], we_absent: bool, rows: list[Entrant]}`. `rows` = все `Entrant` с `place is not None and consent and is_active`, сортированы по `place`. `we_absent = наш код не в rows`.

- [ ] **Step 1: Заменить оконные gather-тесты новыми фильтр-тестами**

Удалить из `tests/test_neighbors.py` устаревшие тесты окна:
`test_gather_window_ahead_self_and_10_after`, `test_gather_we_are_first`,
`test_gather_fewer_than_10_after`, `test_gather_absent_code_falls_back_to_top11`,
`test_gather_multi_code_window_anchors_on_min_place`.

Оставить без изменений: `test_watch_config_parses_track_neighbors`,
`test_gather_paid_flag_from_group`, `test_gather_skips_watch_without_snapshot`,
`test_gather_ignores_unflagged_watch`, а также хелперы `_ent`, `_mk`.

Добавить (в секцию `# --- _gather_neighbors --- #`):

```python
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
```

- [ ] **Step 2: Запустить новые тесты — убедиться, что падают**

Run: `python -m pytest tests/test_neighbors.py -k "gather_filters or gather_full_list or gather_we_absent or gather_empty" -v`
Expected: FAIL (текущая оконная логика возвращает окно по place, не фильтрует по consent).

- [ ] **Step 3: Удалить константу `NEIGHBORS_AFTER`**

В `vuz_monitor/dashboard.py` удалить строку 127:

```python
NEIGHBORS_AFTER = 10  # how many rows to show below our own place
```

- [ ] **Step 4: Переписать `_gather_neighbors`**

Заменить тело функции (`dashboard.py:130-168`) целиком на:

```python
def _gather_neighbors(config, store):
    """One spec dict per `track_neighbors` competition that has a snapshot:
    {title, updated_at, fetched_at, paid, our_codes, we_absent, rows}. `rows` is the
    FULL list of applicants who meet the paid conditions (`consent` = API `accepted`)
    and are active (`is_active`), in place order — the official «Соблюдены условия для
    платного» filtered view, renumbered 1..N at render time. When our code is not among
    them, `we_absent=True` (rows still hold the full eligible list)."""
    specs = []
    for w in config.watches:
        if not w.track_neighbors:
            continue
        snap = store.load_prev(w.watch_id)
        if snap is None:
            continue
        title = snap.meta.title if (snap.meta and snap.meta.title) else w.name
        our_codes = {normalize_code(c) for c in config.resolve_codes(w)}
        eligible = sorted(
            [e for e in snap.entrants
             if e.place is not None and e.consent and e.is_active],
            key=lambda e: e.place,
        )
        we_absent = not any(e.code in our_codes for e in eligible)
        specs.append({
            "title": title,
            "updated_at": snap.meta.updated_at if snap.meta else None,
            "fetched_at": snap.fetched_at,
            "paid": is_paid(title) or is_paid(w.group or w.name),
            "our_codes": our_codes,
            "we_absent": we_absent,
            "rows": eligible,
        })
    return specs
```

- [ ] **Step 5: Запустить тесты — убедиться, что проходят**

Run: `python -m pytest tests/test_neighbors.py -v`
Expected: PASS (новые фильтр-тесты зелёные; оставленные gather-тесты зелёные).

- [ ] **Step 6: Полный прогон — регрессий нет**

Run: `python -m pytest -q`
Expected: PASS, все тесты. (Render-тесты `build_neighbors_html` не затронуты — они передают строки явно.)

- [ ] **Step 7: Commit**

```bash
git add vuz_monitor/dashboard.py tests/test_neighbors.py
git commit -m "feat: neighbors — filter by consent (соблюдены условия для платного) + is_active, full list"
```

---

### Task 2: Рендер — сквозной №, флаг из consent, баннер и пустой список

**Files:**
- Modify: `vuz_monitor/dashboard.py:906-930` (`_neighbor_row`), `:933-954` (`_neighbor_section`)
- Test: `tests/test_neighbors.py` (обновить баннер-тест, добавить seq/flag/empty тесты)

**Interfaces:**
- Consumes: `spec["rows"]` (list[Entrant]), `spec["our_codes"]`, `spec["we_absent"]`, `spec["paid"]` из Task 1; хелперы `esc`, `g`, `yesno`, `_note`.
- Produces: `_neighbor_row(e, seq, our_codes, paid) -> str` (новая сигнатура: добавлен `seq: int`, № показывает `seq`, флаг = `e.consent`). `_neighbor_section(spec, now) -> str` (нумерует через `enumerate(rows, 1)`; пустой `rows` → сообщение; баннер для `we_absent`).

- [ ] **Step 1: Обновить баннер-тест и добавить новые render-тесты**

В `tests/test_neighbors.py` заменить `test_render_absent_banner` на новый текст и
добавить тесты. Итоговые тесты в секции `# --- build_neighbors_html --- #`:

```python
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
```

- [ ] **Step 2: Запустить новые тесты — убедиться, что падают**

Run: `python -m pytest tests/test_neighbors.py -k "sequential_numbering or flag_uses_consent or empty_eligible or absent_banner" -v`
Expected: FAIL (сейчас № = place, флаг = paid_ok, баннер старого текста, пустого сообщения нет).

- [ ] **Step 3: Переписать `_neighbor_row` (сквозной seq + флаг из consent)**

Заменить `_neighbor_row` (`dashboard.py:906-930`) на:

```python
def _neighbor_row(e, seq, our_codes, paid) -> str:
    ours = e.code in our_codes
    if ours:
        tr_cls = "you"
    elif e.passing_real:
        tr_cls = "pass-real"
    elif e.passing_main:
        tr_cls = "pass-main"
    else:
        tr_cls = ""
    num = f'{esc(seq)}{" ◄ вы" if ours else ""}'
    flag = e.consent
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
```

- [ ] **Step 4: Переписать `_neighbor_section` (enumerate + баннер + пустой список)**

Заменить `_neighbor_section` (`dashboard.py:933-954`) на:

```python
def _neighbor_section(spec, now) -> str:
    when = fmt_source_time(spec["updated_at"]) if spec["updated_at"] else _fetched_msk(spec["fetched_at"])
    paid = spec["paid"]
    flag_hdr = "Платн" if paid else "Согл"
    if not spec["rows"]:
        return (
            f'<section class="nb-sec"><h2>{esc(spec["title"])}</h2>'
            f'<div class="caption">список по состоянию на {esc(when)}</div>'
            '<p class="empty">Пока никто не выполнил условия для платного.</p>'
            "</section>"
        )
    banner = ('<div class="banner">вашего кода нет среди выполнивших условия для платного</div>'
              if spec["we_absent"] else "")
    head = (
        "<thead><tr>"
        '<th class="num">№</th><th>Код</th><th class="num">Приор</th>'
        f"<th>{esc(flag_hdr)}</th>"
        '<th class="num">ВИ</th><th class="num">ИД</th><th class="num">Σбалл</th>'
        "<th>Примечание</th></tr></thead>"
    )
    body = "".join(
        _neighbor_row(e, i, spec["our_codes"], paid)
        for i, e in enumerate(spec["rows"], 1)
    )
    return (
        f'<section class="nb-sec"><h2>{esc(spec["title"])}</h2>'
        f'<div class="caption">список по состоянию на {esc(when)}</div>'
        + banner
        + '<div class="nb-scroll"><table class="nb">'
        + head + "<tbody>" + body + "</tbody></table></div>"
        + "</section>"
    )
```

- [ ] **Step 5: Запустить тесты neighbors — убедиться, что проходят**

Run: `python -m pytest tests/test_neighbors.py -v`
Expected: PASS (новые render-тесты зелёные; `test_render_highlights_our_row_and_shows_full_code`, `test_render_note_mapping`, `test_render_scores_and_missing`, `test_render_paid_vs_budget_column_header` остаются зелёными — их строки place 1..3 совпадают с seq 1..3).

- [ ] **Step 6: Полный прогон — регрессий нет**

Run: `python -m pytest -q`
Expected: PASS, все тесты.

- [ ] **Step 7: Commit**

```bash
git add vuz_monitor/dashboard.py tests/test_neighbors.py
git commit -m "feat: neighbors — sequential № numbering + consent flag + empty/absent messaging"
```

---

### Task 3: End-to-end проверка на реальных данных

**Files:**
- Modify: none (проверка вывода на `state.db`)

**Interfaces:**
- Consumes: `dashboard.render_pages(config, store)` на реальном `state.db` и приватном `config.yaml`.

- [ ] **Step 1: Сгенерировать страницы из реального state.db**

Run: `python -m vuz_monitor dashboard`
Expected: команда завершается без ошибок; `docs/mirea-list.html` перегенерирован.

- [ ] **Step 2: Проверить состав и нумерацию платной секции**

Run:
```bash
python3 - <<'PY'
import re
html = open("docs/mirea-list.html", encoding="utf-8").read()
# our code present and marked as №1
assert "1366129" in html, "наш код отсутствует"
assert "1 ◄ вы" in html, "наш код не №1 / не отмечен"
# neighbors from screenshot appear in order (renumbered)
for i, code in enumerate(["1366129", "1179201", "1569330"], 1):
    assert code in html, f"{code} отсутствует"
# official place 338 must NOT appear as a № cell
assert '<td class="num">338</td>' not in html, "показано официальное место вместо seq"
print("OK: платная секция отфильтрована, сквозная нумерация, 1366129 = №1")
PY
```
Expected: печатает `OK: ...` без AssertionError.

- [ ] **Step 3: Визуальная сверка со скриншотом офсайта**

Run: `~/.claude/skills/gstack/browse/dist/browse goto "file://$(pwd)/docs/mirea-list.html" && ~/.claude/skills/gstack/browse/dist/browse screenshot /tmp/mirea-list-check.png`
Прочитать скриншот, сверить: только строки «да» по флагу, порядок 1366129 → 1179201 → 1569330 …, сквозная нумерация 1,2,3.

- [ ] **Step 4: Финальный коммит (если Step 1 изменил docs/)**

```bash
git add docs/mirea-list.html
git commit -m "chore: regenerate mirea-list.html with paid-conditions filter"
```

---

## Self-Review

**Spec coverage:**
- §4 «поле фильтра consent» → Task 1 Step 4 (`e.consent`), Task 2 Step 3 (flag). ✓
- §4 «is_active» → Task 1 Step 4 + test `test_gather_filters_by_is_active`. ✓
- §4 «весь список (вариант A)» → Task 1 (нет среза) + `test_gather_full_list_no_window_cap`. ✓
- §4 «сквозная нумерация 1..N, place не показываем» → Task 2 Step 3/4 + `test_render_sequential_numbering`. ✓
- §5.2 «флаг paid_ok→consent» → Task 2 Step 3 + `test_render_flag_uses_consent_not_paid_ok`. ✓
- §6 «пусто» → Task 2 Step 4 + `test_render_empty_eligible_message`. ✓
- §6 «мы не в eligible → баннер» → Task 1 (`we_absent`) + Task 2 (баннер) + `test_gather_we_absent_when_our_consent_false`, `test_render_absent_banner`. ✓
- §8 регрессия 139 тестов → каждый Task Step 6 (`pytest -q`). ✓
- §9 end-to-end → Task 3. ✓

**Placeholder scan:** плейсхолдеров нет; весь код приведён целиком.

**Type consistency:** `_neighbor_row(e, seq, our_codes, paid)` — новая сигнатура определена в Task 2 Interfaces и вызвана через `enumerate(rows, 1)` в `_neighbor_section` (Task 2 Step 4). `_gather_neighbors` возвращает `rows: list[Entrant]` (Task 1), потребляется как `spec["rows"]` (Task 2). Согласовано.
