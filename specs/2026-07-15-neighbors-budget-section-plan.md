# Budget Section on Neighbors Page — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a second «Бюджет» section to the neighbors page (`docs/mirea-list.html`) for the budget МИРЭА competition, filtered by «Проходной ВП» (`passing_real`) instead of «условия для платного» (`consent`).

**Architecture:** The neighbors pipeline is already multi-competition: `_gather_neighbors` iterates every `track_neighbors` watch and yields one spec dict each; `_neighbor_section` renders one section per spec. Two changes make budget work: (1) `_gather_neighbors` picks the filter predicate by competition type (`paid` → `consent`, budget → `passing_real`); (2) `_neighbor_section` parametrizes the paid-specific copy by `spec["paid"]`. Enabling `track_neighbors` on the existing budget watch then produces the second section automatically. No new page, route, or nav.

**Tech Stack:** Python 3, stdlib only; `pytest`; static HTML string builders in `vuz_monitor/dashboard.py`.

## Global Constraints

- Budget filter = `passing_real` (API `iHPO`, «Проходной ВП»); paid filter = `consent` (API `accepted`, «Соблюдены условия для платного»). Both AND `is_active`. Copied verbatim from spec §4.
- Filter is chosen by `spec["paid"]`, where `paid = is_paid(title) or is_paid(w.group or w.name)`; `is_paid` matches «договор»/«платн» (`format.py:52`).
- Rows sorted by `place` ascending; sequential numbering 1..N at render time (`enumerate(rows, 1)`). Unchanged.
- One page `docs/mirea-list.html`, two sections. Section order follows watch order in `config.yaml` (budget watch is earlier → «Бюджет» section renders first).
- Section `<h2>` gets a label prefix: paid → «Платно · {title}», budget → «Бюджет · {title}».
- Budget empty-list text: «Пока никто не проходит по Проходному ВП.». Budget absent banner: «вашего кода нет среди проходящих по Проходному ВП». Paid copy is unchanged.
- `_neighbor_row` is NOT modified: flag column stays `consent` with header «Платн»/«Согл» (budget shows «Согл», the official «Наличие согласия»).
- Full `pytest` suite stays green (was 142/142).
- `config.yaml` and `docs/` are private/gitignored — the config change and rendered HTML are not committed.

---

### Task 1: Filter by competition type in `_gather_neighbors`

**Files:**
- Modify: `vuz_monitor/dashboard.py:127-158` (`_gather_neighbors`)
- Test: `tests/test_neighbors.py`

**Interfaces:**
- Consumes: `Entrant.consent`, `Entrant.passing_real`, `Entrant.is_active`, `Entrant.place`, `Entrant.code` (from `vuz_monitor/models.py`); `is_paid` (`vuz_monitor/format.py`, already imported in dashboard.py); `config.resolve_codes(w)`, `normalize_code`, `store.load_prev`.
- Produces: `_gather_neighbors(config, store) -> list[dict]` with **unchanged** keys `{title, updated_at, fetched_at, paid, our_codes, we_absent, rows}`. Only the membership of `rows` changes for budget watches; `paid` is now computed once and reused.

Existing test helpers in `tests/test_neighbors.py` (already present — reuse, do NOT redefine): `_ent(place, code, **kw)` builds an `Entrant`; `_mk(entrants, tracked="1366129", track=True, group="МИРЭА — платно", title="1. Интеллектуальные системы", updated_at="2026-07-15 09:46:00")` builds `(cfg, store, watch)` with one saved snapshot. Passing `group="МИРЭА — бюджет"` with the default title (no «договор»/«платн») yields `paid=False`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_neighbors.py` after `test_gather_empty_when_none_eligible` (near line 113):

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_neighbors.py::test_gather_budget_filters_by_passing_real tests/test_neighbors.py::test_gather_budget_excludes_inactive -v`
Expected: FAIL — `test_gather_budget_filters_by_passing_real` and `test_gather_budget_excludes_inactive` fail (current code filters by `consent`, so budget rows `1366129`/`1000003` with `consent=False` are dropped → `rows` empty / wrong). `test_gather_paid_still_filters_by_consent_not_passing_real` already passes (paid path unchanged) — that is expected and fine.

- [ ] **Step 3: Implement the filter-by-type change**

Replace `vuz_monitor/dashboard.py:127-158` (the whole `_gather_neighbors` function) with:

```python
def _gather_neighbors(config, store):
    """One spec dict per `track_neighbors` competition that has a snapshot:
    {title, updated_at, fetched_at, paid, our_codes, we_absent, rows}. `rows` is the
    FULL filtered list of active applicants in place order, renumbered 1..N at render
    time. The filter depends on competition type: paid → `consent` (API `accepted`,
    «Соблюдены условия для платного»); budget → `passing_real` (API `iHPO`,
    «Проходной ВП»). When our code is not among them, `we_absent=True` (rows still
    hold the full eligible list)."""
    specs = []
    for w in config.watches:
        if not w.track_neighbors:
            continue
        snap = store.load_prev(w.watch_id)
        if snap is None:
            continue
        title = snap.meta.title if (snap.meta and snap.meta.title) else w.name
        our_codes = {normalize_code(c) for c in config.resolve_codes(w)}
        paid = is_paid(title) or is_paid(w.group or w.name)
        ok = (lambda e: e.consent) if paid else (lambda e: e.passing_real)
        eligible = sorted(
            [e for e in snap.entrants
             if e.place is not None and e.is_active and ok(e)],
            key=lambda e: e.place,
        )
        we_absent = not any(e.code in our_codes for e in eligible)
        specs.append({
            "title": title,
            "updated_at": snap.meta.updated_at if snap.meta else None,
            "fetched_at": snap.fetched_at,
            "paid": paid,
            "our_codes": our_codes,
            "we_absent": we_absent,
            "rows": eligible,
        })
    return specs
```

(The `ok` lambda references only its own argument `e`, not the loop variable, so there is no loop-closure bug.)

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_neighbors.py::test_gather_budget_filters_by_passing_real tests/test_neighbors.py::test_gather_budget_excludes_inactive tests/test_neighbors.py::test_gather_paid_still_filters_by_consent_not_passing_real -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Run the full neighbors suite for regressions**

Run: `.venv/bin/python -m pytest tests/test_neighbors.py -q`
Expected: PASS — all prior gather tests (`test_gather_filters_by_consent`, `test_gather_we_absent_when_our_consent_false`, etc.) still green because the default watch group is «МИРЭА — платно» (`paid=True`), preserving the `consent` filter.

- [ ] **Step 6: Commit**

```bash
git add vuz_monitor/dashboard.py tests/test_neighbors.py
git commit -m "feat: budget neighbors filter by passing_real (Проходной ВП)"
```

---

### Task 2: Parametrize `_neighbor_section` copy by competition type

**Files:**
- Modify: `vuz_monitor/dashboard.py:923-954` (`_neighbor_section`)
- Modify: `vuz_monitor/dashboard.py:957-983` (`build_neighbors_html` docstring + footer line)
- Test: `tests/test_neighbors.py`

**Interfaces:**
- Consumes: `spec` dict from Task 1 (`paid`, `title`, `rows`, `we_absent`, `our_codes`, `updated_at`, `fetched_at`); helpers `esc`, `fmt_source_time`, `_fetched_msk`, `_neighbor_row` (unchanged).
- Produces: `_neighbor_section(spec, now) -> str` with a labelled `<h2>` and type-specific empty/banner copy. `build_neighbors_html(specs, now=None, link_scores=False) -> str` unchanged signature. `render_pages` (unchanged) already assembles `mirea-list.html` from all specs.

Existing render-test helper (already present — reuse): `_spec(rows, our_codes=("1366129",), paid=True, we_absent=False, title="1. Интеллектуальные системы", updated_at="2026-07-15 09:46:00")` builds a section spec dict; pass `paid=False` for budget.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_neighbors.py` after `test_render_absent_banner` (near line 226):

```python
def test_render_section_label_paid_vs_budget():
    rows = [_ent(1, "1366129", passing_real=True)]
    paid_html = dashboard.build_neighbors_html([_spec(rows, paid=True)], now=NOW)
    budget_html = dashboard.build_neighbors_html([_spec(rows, paid=False)], now=NOW)
    assert "Платно ·" in paid_html and "Бюджет ·" not in paid_html
    assert "Бюджет ·" in budget_html and "Платно ·" not in budget_html


def test_render_budget_empty_message():
    html = dashboard.build_neighbors_html([_spec([], paid=False, we_absent=True)], now=NOW)
    assert "Пока никто не проходит по Проходному ВП" in html
    assert "для платного" not in html


def test_render_budget_absent_banner():
    rows = [_ent(1, "1000001", passing_real=True), _ent(2, "1000002", passing_real=True)]
    html = dashboard.build_neighbors_html([_spec(rows, paid=False, we_absent=True)], now=NOW)
    assert "вашего кода нет среди проходящих по Проходному ВП" in html
    assert "для платного" not in html


def test_render_pages_budget_and_paid_sections_both_present():
    store = Store(":memory:")
    ts = NOW.isoformat()
    wp = WatchConfig(name="ИСУ платно", adapter="mirea_api", url="http://p",
                     group="МИРЭА — платно", track_neighbors=True)
    wb = WatchConfig(name="ИСУ бюджет", adapter="mirea_api", url="http://b",
                     group="МИРЭА — бюджет", track_neighbors=True)
    store.save(Snapshot(watch_id=wp.watch_id,
        meta=ProgramMeta(title="ИСУ / договор", plan=122, total=2, updated_at="2026-07-15 09:46:00"),
        entrants=[_ent(1, "1366129", consent=True), _ent(2, "1179201", consent=True)], fetched_at=ts))
    store.save(Snapshot(watch_id=wb.watch_id,
        meta=ProgramMeta(title="ИСУ / общий", plan=11, total=2, updated_at="2026-07-15 09:46:00"),
        entrants=[_ent(1, "1366129", passing_real=True), _ent(2, "1289372", passing_real=True)], fetched_at=ts))
    cfg = AppConfig(telegram=TelegramConfig(chat_id="", bot_token=""),
                    heartbeat="on_change_only", tracked_codes=["1366129"], watches=[wb, wp])
    pages = dashboard.render_pages(cfg, store)
    store.close()
    html = pages["mirea-list.html"]
    assert "Бюджет ·" in html and "Платно ·" in html            # both sections present
    assert html.index("Бюджет ·") < html.index("Платно ·")      # budget first (config order: wb before wp)
    assert "1289372" in html                                    # budget-only code shown
    assert "1 ◄ вы" in html                                     # our row numbered sequentially
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_neighbors.py::test_render_section_label_paid_vs_budget tests/test_neighbors.py::test_render_budget_empty_message tests/test_neighbors.py::test_render_budget_absent_banner tests/test_neighbors.py::test_render_pages_budget_and_paid_sections_both_present -v`
Expected: FAIL — labels «Платно ·»/«Бюджет ·» are not yet emitted; budget empty/banner still say «для платного».

- [ ] **Step 3: Implement the parametrized section**

Replace `vuz_monitor/dashboard.py:923-954` (the whole `_neighbor_section` function) with:

```python
def _neighbor_section(spec, now) -> str:
    when = fmt_source_time(spec["updated_at"]) if spec["updated_at"] else _fetched_msk(spec["fetched_at"])
    paid = spec["paid"]
    flag_hdr = "Платн" if paid else "Согл"
    label = "Платно" if paid else "Бюджет"
    empty_txt = ("Пока никто не выполнил условия для платного."
                 if paid else "Пока никто не проходит по Проходному ВП.")
    absent_txt = ("вашего кода нет среди выполнивших условия для платного"
                  if paid else "вашего кода нет среди проходящих по Проходному ВП")
    h2 = f'<h2>{esc(label)} · {esc(spec["title"])}</h2>'
    if not spec["rows"]:
        return (
            f'<section class="nb-sec">{h2}'
            f'<div class="caption">список по состоянию на {esc(when)}</div>'
            f'<p class="empty">{esc(empty_txt)}</p>'
            "</section>"
        )
    banner = (f'<div class="banner">{esc(absent_txt)}</div>'
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
        f'<section class="nb-sec">{h2}'
        f'<div class="caption">список по состоянию на {esc(when)}</div>'
        + banner
        + '<div class="nb-scroll"><table class="nb">'
        + head + "<tbody>" + body + "</tbody></table></div>"
        + "</section>"
    )
```

- [ ] **Step 4: Update `build_neighbors_html` docstring and footer**

In `vuz_monitor/dashboard.py`, inside `build_neighbors_html` (line numbers drift after Task 1 — match by content). Replace this exact docstring:

```python
    """docs/mirea-list.html — «окружение»: для каждого track_neighbors конкурса
    таблица только тех, кто выполнил условия («соблюдены условия для платного» =
    consent) и активен, со сквозной нумерацией 1..N — раскладка офсайта, наша
    строка подсвечена, коды показаны полностью."""
```

with:

```python
    """docs/mirea-list.html — «окружение»: одна секция на каждый track_neighbors
    конкурс. Фильтр зависит от типа: платный — «Соблюдены условия для платного»
    (consent = API accepted); бюджетный — «Проходной ВП» (passing_real = API iHPO).
    Только активные, со сквозной нумерацией 1..N — раскладка офсайта, наша строка
    подсвечена, коды показаны полностью."""
```

And replace this exact footer line:

```python
        '<footer class="foot">обновляется каждый час · один конкурс · vuz_monitor</footer>\n'
```

with:

```python
        '<footer class="foot">обновляется каждый час · конкурсы МИРЭА · vuz_monitor</footer>\n'
```

(No test asserts the footer text, so this is safe.)

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_neighbors.py::test_render_section_label_paid_vs_budget tests/test_neighbors.py::test_render_budget_empty_message tests/test_neighbors.py::test_render_budget_absent_banner tests/test_neighbors.py::test_render_pages_budget_and_paid_sections_both_present -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Run the full suite for regressions**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS — all tests green. In particular `test_render_paid_vs_budget_column_header` still holds: the paid label «Платно» contains substring «Платн» (present as expected) and no «Согл»; the budget label «Бюджет» contains neither «Платн» nor breaks the «Согл» header assertion. `test_render_empty_eligible_message` and `test_render_absent_banner` still pass (default `_spec` is `paid=True`, so the paid copy is unchanged).

- [ ] **Step 7: Commit**

```bash
git add vuz_monitor/dashboard.py tests/test_neighbors.py
git commit -m "feat: budget neighbors section label + Проходной ВП copy"
```

---

### Task 3: Enable the budget watch and verify end-to-end

**Files:**
- Modify: `config.yaml` (private/gitignored — NOT committed): budget watch «1. Интеллектуальные системы управления и обработки информации» (group «МИРЭА — бюджет», `competitions[]: "1862638623056207158"`, near lines 11-15).
- Verify: `docs/mirea-list.html` (gitignored build artifact).

**Interfaces:**
- Consumes: Task 1 + Task 2 code; live `state.db`. No new code, no unit test (config is private). This task's deliverable is a correctly rendered live page.

- [ ] **Step 1: Add `track_neighbors: true` to the budget watch**

In `config.yaml`, find the first watch block:

```yaml
  - name: "1. Интеллектуальные системы управления и обработки информации"
    group: "МИРЭА — бюджет"
    ...
    url: "https://priem.mirea.ru/competitions_api/entrants"
    params: { "competitions[]": "1862638623056207158", edu_level: 2 }
```

Add the line `track_neighbors: true` to that block (matching the indentation of the sibling keys), exactly as it already exists on the «МИРЭА — платно» «1. Интеллектуальные системы…» watch. Do not touch any other watch.

- [ ] **Step 2: Regenerate the dashboard from the real database**

Run: `.venv/bin/python -m vuz_monitor dashboard`
Expected: prints `Dashboard written: docs/index.html, docs/table.html, docs/mirea-scores.html, docs/mirea-list.html`

- [ ] **Step 3: Verify the budget section content**

Run:
```bash
.venv/bin/python - <<'PY'
import re
html = open("docs/mirea-list.html", encoding="utf-8").read()
assert "Бюджет ·" in html, "budget section missing"
assert "Платно ·" in html, "paid section missing"
# budget section = from «Бюджет ·» up to the next «Платно ·» (config order: budget first)
b = html.index("Бюджет ·"); p = html.index("Платно ·")
assert b < p, "budget should render before paid (config order)"
budget = html[b:p]
codes = re.findall(r'class="code">(\d+)<', budget)
print("budget codes:", codes)
assert codes[:6] == ["1289372","1640958","1503592","1687874","1726973","1366129"], codes[:6]
assert "1 ◄ вы" not in budget and "6 ◄ вы" in budget, "our code should be №6 in budget"
print("OK: budget section has 11-row Проходной ВП list, code 1366129 = №6")
PY
```
Expected: prints the budget codes and `OK: budget section has 11-row Проходной ВП list, code 1366129 = №6`. (Exact codes/positions may shift as the live list updates hourly; the structure — «Бюджет» section present, our code shown with «◄ вы» at its current sequential position — is what must hold.)

- [ ] **Step 4: Visual check in the browser**

Render a copy to a browse-allowed path and open it:
```bash
cp docs/mirea-list.html /private/tmp/verify-mirea-list.html
```
Open `/private/tmp/verify-mirea-list.html` with the `/browse` skill. Confirm: two sections («Бюджет · …» then «Платно · …»), the budget section lists the Проходной-ВП applicants with sequential № and our row highlighted «◄ вы», paid section unchanged.

- [ ] **Step 5: No commit**

`config.yaml` and `docs/` are gitignored — nothing to commit for this task. Report the verification output.

---

## Notes for the executor

- Use `.venv/bin/python` for all commands (the repo's virtualenv; bare `python3` is 3.14 without deps).
- Do not modify `_neighbor_row`, styles, the topbar/links, or the page-existence logic (`has_neighbors`).
- The default test-helper watch group is «МИРЭА — платно» (`paid=True`); pass `group="МИРЭА — бюджет"` for budget cases.
