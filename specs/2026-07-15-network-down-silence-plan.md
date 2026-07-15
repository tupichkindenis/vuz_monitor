# Connectivity Gate + Deliver-then-Commit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop Telegram spam when the monitoring Mac has no network, and never lose an `on_change_only` alert when delivery fails.

**Architecture:** Classify each fetch failure as connectivity (our machine) vs source (their problem) from the underlying socket errno; when *every* watch fails on connectivity across ≥2 hosts, skip the send silently. Decouple "data saved" from "alert delivered": diff each run against the last *delivered* snapshot held in a durable `notified_snapshot` table (never pruned), and promote that baseline only after a group's Telegram message(s) actually send.

**Tech Stack:** Python 3.14, httpx, sqlite3, pytest, BeautifulSoup (existing). No new dependencies.

## Global Constraints

- No new third-party dependencies. Use stdlib `errno`, `socket`, `urllib.parse`, `sqlite3`, `contextlib`.
- Existing test suite is 90 tests, all green — keep them green. Run `pytest -q` (uses `.venv`).
- Semantics: **net change since last delivery** (transient reverts coalesced; no outbox).
- Delivery is **at-least-once** (a split group may re-send its first chunk on retry) — this is accepted, not a bug.
- `watch_id = sha1(adapter+url+params)[:12]` is assumed unique per watch (validated in Task 3).
- All snapshot timestamps are UTC ISO via `models`/`now_iso()`; ordering relies on this.
- Match existing code style: Russian-localized user strings, terse comments, `from __future__ import annotations`.

---

### Task 1: Connectivity classifier `is_connectivity_error`

**Files:**
- Modify: `vuz_monitor/adapters/base.py` (add imports + function near the top-level helpers)
- Test: `tests/test_connectivity.py` (new)

**Interfaces:**
- Produces: `is_connectivity_error(exc: BaseException) -> bool` in `vuz_monitor.adapters.base`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_connectivity.py`:

```python
import errno
import socket

import httpx
import pytest

from vuz_monitor.adapters.base import is_connectivity_error


def _wrap(cause):
    """A ConnectError with `cause` in its __cause__ chain, like httpx produces."""
    err = httpx.ConnectError("connect failed")
    err.__cause__ = cause
    return err


@pytest.mark.parametrize("errno_val", [errno.EHOSTUNREACH, errno.ENETUNREACH, errno.ENETDOWN, errno.ECANCELED])
def test_net_errnos_are_connectivity(errno_val):
    assert is_connectivity_error(_wrap(OSError(errno_val, "boom"))) is True


def test_dns_gaierror_is_connectivity():
    assert is_connectivity_error(_wrap(socket.gaierror(8, "nodename nor servname provided"))) is True


def test_connection_refused_is_source():
    assert is_connectivity_error(_wrap(OSError(errno.ECONNREFUSED, "Connection refused"))) is False


def test_plain_oserror_errno_8_is_not_dns():
    # errno 8 as a bare OSError is ENOEXEC, unrelated to DNS
    assert is_connectivity_error(OSError(8, "Exec format error")) is False


def test_connect_timeout_is_ambiguous_false():
    assert is_connectivity_error(httpx.ConnectTimeout("timed out")) is False


def test_http_status_and_value_error_are_false():
    req = httpx.Request("GET", "http://x")
    resp = httpx.Response(500, request=req)
    assert is_connectivity_error(httpx.HTTPStatusError("500", request=req, response=resp)) is False
    assert is_connectivity_error(ValueError("bad table")) is False


def test_cyclic_cause_chain_terminates():
    a, b = OSError("a"), OSError("b")
    a.__cause__, b.__cause__ = b, a
    assert is_connectivity_error(a) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_connectivity.py -q`
Expected: FAIL with `ImportError: cannot import name 'is_connectivity_error'`.

- [ ] **Step 3: Write minimal implementation**

In `vuz_monitor/adapters/base.py`, add to the imports block (after `import httpx`):

```python
import errno
import socket
```

Then add near the other module-level helpers (e.g. after `now_iso`):

```python
_NET_ERRNOS = {
    errno.ENETDOWN,     # 50 — interface down
    errno.ENETUNREACH,  # 51 — network unreachable
    errno.EHOSTUNREACH, # 65 — no route to host
    errno.ECANCELED,    # 89 — connection torn down mid-request
}


def _causes(exc):
    """Yield exc and its __cause__/__context__ chain, cycle-safe."""
    seen, cur = set(), exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        yield cur
        cur = cur.__cause__ or cur.__context__


def is_connectivity_error(exc: BaseException) -> bool:
    """True when the failure is OUR machine failing to reach the network
    (DNS resolution, no route, cancelled), False for source-side failures
    (HTTP status, connection refused, parse errors). Decides from the underlying
    socket error, not the httpx wrapper type — httpx.ConnectError wraps both
    EHOSTUNREACH and ECONNREFUSED, so the type alone cannot tell them apart."""
    for e in _causes(exc):
        if isinstance(e, socket.gaierror):
            return True                       # DNS lookup failed → no network/DNS
        if isinstance(e, OSError):
            if e.errno in _NET_ERRNOS:
                return True
            if e.errno == errno.ECONNREFUSED:  # 61 — host answered, refused
                return False
    return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_connectivity.py -q`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add vuz_monitor/adapters/base.py tests/test_connectivity.py
git commit -m "feat: is_connectivity_error — classify our-network vs source failures"
```

---

### Task 2: Tag reports with `net_error` and `host`

**Files:**
- Modify: `vuz_monitor/report.py` (add two `WatchReport` fields)
- Modify: `vuz_monitor/pipeline.py` (`_process_watch`: set fields on error and success reports)
- Test: `tests/test_pipeline.py` (add cases) — if absent, create it.

**Interfaces:**
- Consumes: `is_connectivity_error` (Task 1).
- Produces: `WatchReport.net_error: bool` and `WatchReport.host: Optional[str]`; success reports carry `watch_id` and `fetched_at`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_pipeline.py` (create the file with these imports if it does not exist):

```python
import errno
import httpx

from vuz_monitor.config import WatchConfig, AppConfig, TelegramConfig
from vuz_monitor.store import Store
from vuz_monitor import pipeline
from vuz_monitor.models import Snapshot, Entrant, ProgramMeta


def _snap(watch_id, code="100", place=1, updated_at="2026-07-15 10:00:00"):
    return Snapshot(
        watch_id=watch_id,
        meta=ProgramMeta(title="t", plan=None, total=1, updated_at=updated_at),
        entrants=[Entrant(code=code, code_display=code, place=place, final_score=200.0,
                          priority=1, consent=True, contract=None, payment=None,
                          passing_main=None, passing_real=None, needs_dormitory=None, raw={})],
        fetched_at="2026-07-15T07:00:00+00:00",
    )


def _watch(name="w", url="https://a.example/x", params=None):
    return WatchConfig(name=name, adapter="fake", url=url, params=params or {}, codes=["100"])


def _cfg(watches):
    return AppConfig(telegram=TelegramConfig(chat_id="1", bot_token="t"),
                     heartbeat="on_change_only", tracked_codes=["100"], watches=watches, db_path=":memory:")


class _FakeAdapter:
    def __init__(self, result):
        self._result = result  # a Snapshot, or an Exception to raise
    def fetch(self, watch):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _patch_adapter(monkeypatch, result):
    monkeypatch.setattr(pipeline, "get_adapter", lambda name: _FakeAdapter(result))


def test_process_watch_tags_connectivity_error(monkeypatch, tmp_path):
    store = Store(str(tmp_path / "s.db"))
    err = httpx.ConnectError("x"); err.__cause__ = OSError(errno.EHOSTUNREACH, "no route")
    _patch_adapter(monkeypatch, err)
    rep = pipeline._process_watch(_watch(url="https://host.one/x"), _cfg([]), store, dry_run=False)
    assert rep.error and rep.net_error is True
    assert rep.host == "host.one"


def test_process_watch_tags_source_error(monkeypatch, tmp_path):
    store = Store(str(tmp_path / "s.db"))
    _patch_adapter(monkeypatch, ValueError("bad table"))
    rep = pipeline._process_watch(_watch(), _cfg([]), store, dry_run=False)
    assert rep.error and rep.net_error is False


def test_process_watch_success_sets_watch_id_and_fetched_at(monkeypatch, tmp_path):
    store = Store(str(tmp_path / "s.db"))
    w = _watch()
    _patch_adapter(monkeypatch, _snap(w.watch_id))
    rep = pipeline._process_watch(w, _cfg([w]), store, dry_run=False)
    assert rep.error is None
    assert rep.watch_id == w.watch_id
    assert rep.fetched_at == "2026-07-15T07:00:00+00:00"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_pipeline.py -q`
Expected: FAIL — `WatchReport` has no `net_error`/`host`, or `AttributeError` on the report.

- [ ] **Step 3: Write minimal implementation**

In `vuz_monitor/report.py`, add two fields to `WatchReport` (after `group`):

```python
    net_error: bool = False          # failure was our own connectivity, not the source
    host: Optional[str] = None       # hostname of the source (for the gate's host count)
```

In `vuz_monitor/pipeline.py`, add imports at the top:

```python
from urllib.parse import urlsplit

from .adapters.base import is_connectivity_error
```

In `_process_watch`, replace the `except` block:

```python
    except Exception as exc:  # one bad source must not abort the whole run
        log.warning("watch %s failed: %s", watch.name, exc)
        return WatchReport(
            name=watch.name,
            error=str(exc),
            net_error=is_connectivity_error(exc),
            host=urlsplit(watch.url).hostname,
            group=watch.group or watch.name,
        )
```

And in the success-path `return WatchReport(...)`, add two keyword args:

```python
        watch_id=watch.watch_id,
        fetched_at=snap.fetched_at,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_pipeline.py -q`
Expected: PASS (3 new tests).

- [ ] **Step 5: Commit**

```bash
git add vuz_monitor/report.py vuz_monitor/pipeline.py tests/test_pipeline.py
git commit -m "feat: tag WatchReport with net_error/host and success watch_id/fetched_at"
```

---

### Task 3: Validate `watch_id` uniqueness at config load

**Files:**
- Modify: `vuz_monitor/config.py` (`load_config`, before `return AppConfig(...)`)
- Test: `tests/test_config_unique.py` (new)

**Interfaces:**
- Produces: `load_config` raises `ValueError` on duplicate `watch_id`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_config_unique.py`:

```python
import pytest

from vuz_monitor.config import load_config


_DUP = """
telegram: {chat_id: "1", bot_token: "t"}
watches:
  - {name: "A", adapter: mirea_api, url: "https://x/y", params: {comp_ids: "1"}}
  - {name: "B", adapter: mirea_api, url: "https://x/y", params: {comp_ids: "1"}}
"""


def test_duplicate_watch_id_rejected(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(_DUP, encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate watch_id"):
        load_config(str(p))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_config_unique.py -q`
Expected: FAIL — no exception raised (two watches load fine).

- [ ] **Step 3: Write minimal implementation**

In `vuz_monitor/config.py`, insert before `return AppConfig(`:

```python
    seen: dict[str, str] = {}
    for w in watches:
        if w.watch_id in seen:
            raise ValueError(
                f"Duplicate watch_id {w.watch_id!r}: {seen[w.watch_id]!r} and {w.name!r} "
                "resolve to the same adapter+url+params. Give them distinct url or params."
            )
        seen[w.watch_id] = w.name
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_config_unique.py -q`
Expected: PASS. Also run `.venv/bin/pytest -q` to confirm the real `config.yaml`-based tests still pass (the production config has unique ids).

- [ ] **Step 5: Commit**

```bash
git add vuz_monitor/config.py tests/test_config_unique.py
git commit -m "feat: reject duplicate watch_id at config load"
```

---

### Task 4: Durable `notified_snapshot` store + `transaction()`

**Files:**
- Modify: `vuz_monitor/store.py` (schema in `_init`, new methods, `transaction` context manager)
- Test: `tests/test_notified_store.py` (new)

**Interfaces:**
- Produces on `Store`:
  - `load_notified_snapshot(watch_id: str) -> Optional[Snapshot]`
  - `save_notified_snapshot(snap: Snapshot) -> None`  (commits)
  - `promote_notified(watch_id: str) -> None`  (no commit — call inside `transaction()`)
  - `transaction()` context manager (commit on success, rollback on error)

- [ ] **Step 1: Write the failing test**

Create `tests/test_notified_store.py`:

```python
from vuz_monitor.store import Store, HISTORY_PER_WATCH
from vuz_monitor.models import Snapshot, Entrant, ProgramMeta


def _snap(watch_id, fetched_at, place=1):
    return Snapshot(
        watch_id=watch_id,
        meta=ProgramMeta(title="t", plan=None, total=1, updated_at="2026-07-15 10:00:00"),
        entrants=[Entrant(code="100", code_display="100", place=place, final_score=200.0,
                          priority=1, consent=True, contract=None, payment=None,
                          passing_main=None, passing_real=None, needs_dormitory=None, raw={})],
        fetched_at=fetched_at,
    )


def test_save_and_load_notified_snapshot(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    assert store.load_notified_snapshot("w1") is None
    store.save_notified_snapshot(_snap("w1", "2026-07-15T07:00:00+00:00", place=3))
    got = store.load_notified_snapshot("w1")
    assert got is not None and got.entrants[0].place == 3


def test_promote_copies_latest_snapshot(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    store.save(_snap("w1", "2026-07-15T06:00:00+00:00", place=5))
    store.save(_snap("w1", "2026-07-15T07:00:00+00:00", place=2))
    with store.transaction():
        store.promote_notified("w1")
    got = store.load_notified_snapshot("w1")
    assert got.fetched_at == "2026-07-15T07:00:00+00:00" and got.entrants[0].place == 2


def test_promoted_baseline_survives_snapshot_pruning(tmp_path):
    # Codex #1 regression: pruning the snapshots table must NOT drop the baseline.
    store = Store(str(tmp_path / "s.db"))
    store.save(_snap("w1", "2026-07-15T00:00:00+00:00", place=9))
    with store.transaction():
        store.promote_notified("w1")
    for h in range(1, HISTORY_PER_WATCH + 5):     # push the baseline snapshot out of the prune window
        store.save(_snap("w1", f"2026-07-15T{h:02d}:30:00+00:00", place=1))
    got = store.load_notified_snapshot("w1")
    assert got is not None and got.entrants[0].place == 9


def test_transaction_rolls_back_on_error(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    store.save(_snap("w1", "2026-07-15T07:00:00+00:00"))
    try:
        with store.transaction():
            store.promote_notified("w1")
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert store.load_notified_snapshot("w1") is None   # promote was rolled back
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_notified_store.py -q`
Expected: FAIL — `Store` has no `load_notified_snapshot`.

- [ ] **Step 3: Write minimal implementation**

In `vuz_monitor/store.py`, add to imports:

```python
from contextlib import contextmanager
```

In `_init`, add a table (before the final `self.conn.commit()`):

```python
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS notified_snapshot (
                   watch_id TEXT PRIMARY KEY,
                   payload  TEXT NOT NULL
               )"""
        )
```

Add these methods to `Store` (e.g. after `save`):

```python
    @contextmanager
    def transaction(self):
        """Commit on success, roll back on exception. Wrap a group's promotes."""
        try:
            yield
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def load_notified_snapshot(self, watch_id: str) -> Optional[Snapshot]:
        row = self.conn.execute(
            "SELECT payload FROM notified_snapshot WHERE watch_id=?", (watch_id,)
        ).fetchone()
        return snapshot_from_dict(json.loads(row[0])) if row else None

    def save_notified_snapshot(self, snap: Snapshot) -> None:
        """Set a watch's delivered baseline to `snap` (used for migration seeding)."""
        self.conn.execute(
            "INSERT INTO notified_snapshot (watch_id, payload) VALUES (?, ?) "
            "ON CONFLICT(watch_id) DO UPDATE SET payload=excluded.payload",
            (snap.watch_id, json.dumps(snapshot_to_dict(snap), ensure_ascii=False)),
        )
        self.conn.commit()

    def promote_notified(self, watch_id: str) -> None:
        """Set the delivered baseline to this watch's latest saved snapshot.
        Does NOT commit — call inside `transaction()` so a group commits atomically."""
        self.conn.execute(
            """INSERT INTO notified_snapshot (watch_id, payload)
                 SELECT watch_id, payload FROM snapshots
                  WHERE watch_id=? ORDER BY fetched_at DESC LIMIT 1
               ON CONFLICT(watch_id) DO UPDATE SET payload=excluded.payload""",
            (watch_id,),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_notified_store.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add vuz_monitor/store.py tests/test_notified_store.py
git commit -m "feat: durable notified_snapshot baseline + transaction() context manager"
```

---

### Task 5: Diff against the delivered baseline (with migration seed)

**Files:**
- Modify: `vuz_monitor/pipeline.py` (`_process_watch` baseline resolution + diff)
- Test: `tests/test_pipeline.py` (add cases; reuses helpers from Task 2)

**Interfaces:**
- Consumes: `store.load_notified_snapshot`, `store.save_notified_snapshot` (Task 4); `is_connectivity_error` (Task 1).
- Produces: `_process_watch` diffs `snap` against the delivered baseline; `first_run` iff no baseline and no prior snapshot.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_pipeline.py`:

```python
def test_new_watch_is_first_run(monkeypatch, tmp_path):
    store = Store(str(tmp_path / "s.db"))
    w = _watch()
    _patch_adapter(monkeypatch, _snap(w.watch_id, place=1))
    rep = pipeline._process_watch(w, _cfg([w]), store, dry_run=False)
    assert rep.codes[0].first_run is True


def test_migration_seeds_baseline_and_is_not_first_run(monkeypatch, tmp_path):
    store = Store(str(tmp_path / "s.db"))
    w = _watch()
    store.save(_snap(w.watch_id, place=5))          # existing history, no notified_snapshot
    _patch_adapter(monkeypatch, _snap(w.watch_id, place=5))   # unchanged this run
    rep = pipeline._process_watch(w, _cfg([w]), store, dry_run=False)
    assert rep.codes[0].first_run is False
    assert rep.has_changes is False                  # seeded baseline == current → no change
    assert store.load_notified_snapshot(w.watch_id) is not None   # baseline was seeded


def test_diff_is_against_delivered_baseline_not_last_snapshot(monkeypatch, tmp_path):
    store = Store(str(tmp_path / "s.db"))
    w = _watch()
    store.save_notified_snapshot(_snap(w.watch_id, place=5))   # last DELIVERED = place 5
    store.save(_snap(w.watch_id, place=5))                     # last SAVED also 5
    _patch_adapter(monkeypatch, _snap(w.watch_id, place=2))    # now moved to 2
    rep = pipeline._process_watch(w, _cfg([w]), store, dry_run=False)
    assert rep.has_changes is True                             # 5 -> 2 vs delivered baseline
```

Note: `_snap` in Task 2 ignores an extra kwarg; extend it to accept `place`:
replace the Task-2 `_snap` signature `def _snap(watch_id, code="100", place=1, updated_at=...)` — it already accepts `place`. Good.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_pipeline.py -q`
Expected: FAIL — diff still uses `load_prev`; `first_run` still keyed on `prev is None`; migration test finds no seeded baseline.

- [ ] **Step 3: Write minimal implementation**

In `vuz_monitor/pipeline.py` `_process_watch`, replace the block that computes `prev`, `unchanged`, and the per-code loop head:

```python
    prev = store.load_prev(watch.watch_id)
    unchanged = bool(
        prev is not None
        and snap.meta.updated_at
        and prev.meta.updated_at == snap.meta.updated_at
    )

    # Change-detection baseline = last DELIVERED snapshot, not last saved. On the
    # first run after deploy an existing watch has snapshots but no baseline yet;
    # seed it from the last snapshot so we don't re-announce «первый запуск».
    baseline = store.load_notified_snapshot(watch.watch_id)
    if baseline is None and prev is not None:
        store.save_notified_snapshot(prev)
        baseline = prev

    code_reports = []
    for code in config.resolve_codes(watch):
        new_status = compute_status(snap, code, watch.plan_override)
        base_status = compute_status(baseline, code, watch.plan_override)
        changes = compute_changes(base_status, new_status)
        code_reports.append(
            CodeReport(status=new_status, changes=changes, first_run=baseline is None)
        )
        if not dry_run and new_status is not None:
```

(The `store.append_history(...)` call and the rest of the loop body stay unchanged;
`store.save(snap)` after the loop stays unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_pipeline.py -q`
Expected: PASS (all Task 2 + Task 5 cases).

- [ ] **Step 5: Commit**

```bash
git add vuz_monitor/pipeline.py tests/test_pipeline.py
git commit -m "feat: diff against delivered baseline with lazy migration seed"
```

---

### Task 6: `TelegramNetworkError` + narrowed raise + calm section text

**Files:**
- Modify: `vuz_monitor/notify.py` (new exception, `_api_call`, `_specialty_block`)
- Test: `tests/test_notify_neterror.py` (new)

**Interfaces:**
- Consumes: `is_connectivity_error` (Task 1); `WatchReport.net_error` (Task 2).
- Produces: `notify.TelegramNetworkError(RuntimeError)`; `_api_call` raises it only for connectivity; `_specialty_block` renders `⏳ временно недоступно` for `net_error` sections.

- [ ] **Step 1: Write the failing test**

Create `tests/test_notify_neterror.py`:

```python
import errno
import httpx
import pytest

from vuz_monitor import notify
from vuz_monitor.report import WatchReport


def test_api_call_raises_telegram_network_error_on_connectivity(monkeypatch):
    def _boom(*a, **k):
        err = httpx.ConnectError("x"); err.__cause__ = OSError(errno.EHOSTUNREACH, "no route")
        raise err
    monkeypatch.setattr(notify.httpx, "post", _boom)
    with pytest.raises(notify.TelegramNetworkError):
        notify._api_call("tok", "sendMessage", json={"chat_id": "1", "text": "hi"})


def test_api_call_non_connectivity_stays_plain_runtimeerror(monkeypatch):
    def _boom(*a, **k):
        raise httpx.ReadTimeout("slow")
    monkeypatch.setattr(notify.httpx, "post", _boom)
    with pytest.raises(RuntimeError) as ei:
        notify._api_call("tok", "sendMessage", json={"chat_id": "1", "text": "hi"})
    assert not isinstance(ei.value, notify.TelegramNetworkError)


def test_specialty_block_net_error_is_calm():
    rep = WatchReport(name="Программная инженерия", error="[Errno 65] No route to host", net_error=True)
    out = notify._specialty_block(rep, show_code=False)
    assert "временно недоступно" in out
    assert "[Errno" not in out


def test_specialty_block_source_error_keeps_oshibka():
    rep = WatchReport(name="X", error="table not found", net_error=False)
    out = notify._specialty_block(rep, show_code=False)
    assert "ошибка" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_notify_neterror.py -q`
Expected: FAIL — `notify.TelegramNetworkError` does not exist; `_specialty_block` still prints the errno.

- [ ] **Step 3: Write minimal implementation**

In `vuz_monitor/notify.py`, add near the top (after imports):

```python
from .adapters.base import is_connectivity_error


class TelegramNetworkError(RuntimeError):
    """Telegram unreachable due to OUR connectivity (not a Telegram-side error)."""
```

Replace the `except httpx.HTTPError:` branch of `_api_call`:

```python
    except httpx.HTTPError as exc:
        # Never surface the URL — it contains the bot token.
        if is_connectivity_error(exc):
            raise TelegramNetworkError(f"Telegram {method}: network unreachable") from None
        raise RuntimeError(f"Telegram {method}: network error") from None
```

In `_specialty_block`, replace the error line:

```python
    if report.error:
        if report.net_error:
            return f"{head}\n– ⏳ временно недоступно"
        return f"{head}\n– ⚠️ ошибка: {_esc(report.error)}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_notify_neterror.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add vuz_monitor/notify.py tests/test_notify_neterror.py
git commit -m "feat: TelegramNetworkError for connectivity + calm ⏳ section text"
```

---

### Task 7: The connectivity gate in `run()`

**Files:**
- Modify: `vuz_monitor/pipeline.py` (`run`, after dashboard render, before send decision)
- Test: `tests/test_gate.py` (new)

**Interfaces:**
- Consumes: `WatchReport.net_error`/`host` (Task 2).
- Produces: `run()` returns 0 without sending when every report is a connectivity error across ≥2 hosts.

- [ ] **Step 1: Write the failing test**

Create `tests/test_gate.py`:

```python
import errno
import httpx

from vuz_monitor.config import WatchConfig, AppConfig, TelegramConfig
from vuz_monitor.store import Store
from vuz_monitor import pipeline


def _watch(name, url):
    return WatchConfig(name=name, adapter="fake", url=url, params={"k": name}, codes=["100"])


def _cfg(watches):
    return AppConfig(telegram=TelegramConfig(chat_id="1", bot_token="t"),
                     heartbeat="always", tracked_codes=["100"], watches=watches, db_path=":memory:")


def _conn_err():
    e = httpx.ConnectError("x"); e.__cause__ = OSError(errno.EHOSTUNREACH, "no route"); return e


def _patch_all_fail(monkeypatch, exc):
    class _A:
        def fetch(self, w): raise exc
    monkeypatch.setattr(pipeline, "get_adapter", lambda name: _A())
    monkeypatch.setattr(pipeline, "_render_dashboard", lambda *a, **k: [])  # hermetic: no docs/ writes


def test_gate_fires_when_all_connectivity_across_two_hosts(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(pipeline.notify, "send_message", lambda *a, **k: sent.append(a))
    _patch_all_fail(monkeypatch, _conn_err())
    cfg = _cfg([_watch("a", "https://host.one/x"), _watch("b", "https://host.two/x")])
    rc = pipeline.run(cfg, dry_run=False)
    assert rc == 0 and sent == []            # nothing sent


def test_gate_does_not_fire_single_host(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(pipeline.notify, "send_message", lambda *a, **k: sent.append(a))
    _patch_all_fail(monkeypatch, _conn_err())
    cfg = _cfg([_watch("a", "https://only.host/x"), _watch("b", "https://only.host/y")])
    pipeline.run(cfg, dry_run=False)
    assert sent != []                        # one host → gate off → error message attempted


def test_gate_off_in_dry_run(monkeypatch, capsys, tmp_path):
    _patch_all_fail(monkeypatch, _conn_err())
    cfg = _cfg([_watch("a", "https://host.one/x"), _watch("b", "https://host.two/x")])
    pipeline.run(cfg, dry_run=True)
    assert "недоступно" in capsys.readouterr().out   # dry-run still renders the ⏳ sections
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_gate.py -q`
Expected: FAIL — `test_gate_fires...` sees a send (no gate yet).

- [ ] **Step 3: Write minimal implementation**

In `vuz_monitor/pipeline.py` `run()`, immediately after the dashboard-render block and before `mode = (config.heartbeat or "always").lower()`:

```python
        # Every watch failed on OUR connectivity, across ≥2 independent hosts →
        # the local machine was offline, not the sources. Say nothing; don't crash.
        if not dry_run:
            hosts = {r.host for r in reports if r.host}
            if reports and all(r.error and r.net_error for r in reports) and len(hosts) >= 2:
                log.warning(
                    "local network down: %d watches unreachable across %d hosts; skipping send",
                    len(reports), len(hosts),
                )
                return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_gate.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add vuz_monitor/pipeline.py tests/test_gate.py
git commit -m "feat: gate the send when all watches fail connectivity across ≥2 hosts"
```

---

### Task 8: Per-group send + baseline promote + at-least-once guard

**Files:**
- Modify: `vuz_monitor/pipeline.py` (`run`, the send loop)
- Test: `tests/test_deliver_commit.py` (new)

**Interfaces:**
- Consumes: `notify.TelegramNetworkError` (Task 6); `store.promote_notified`, `store.transaction` (Task 4); `WatchReport.watch_id`/`fetched_at` (Task 2).
- Produces: baselines advance only for delivered groups; a failed send holds the baseline so the change re-alerts.

- [ ] **Step 1: Write the failing test**

Create `tests/test_deliver_commit.py`:

```python
import httpx

from vuz_monitor.config import WatchConfig, AppConfig, TelegramConfig
from vuz_monitor.store import Store
from vuz_monitor import pipeline
from vuz_monitor.models import Snapshot, Entrant, ProgramMeta


def _snap(watch_id, place):
    return Snapshot(
        watch_id=watch_id,
        meta=ProgramMeta(title="t", plan=None, total=1, updated_at="2026-07-15 10:00:00"),
        entrants=[Entrant(code="100", code_display="100", place=place, final_score=200.0,
                          priority=1, consent=True, contract=None, payment=None,
                          passing_main=None, passing_real=None, needs_dormitory=None, raw={})],
        fetched_at="2026-07-15T07:00:00+00:00",
    )


def _watch(name="w"):
    return WatchConfig(name=name, adapter="fake", url="https://h/x", params={"k": name},
                       codes=["100"], group="G")


def _cfg(watches, db_path):
    return AppConfig(telegram=TelegramConfig(chat_id="1", bot_token="t"),
                     heartbeat="on_change_only", tracked_codes=["100"], watches=watches, db_path=db_path)


def _run_with_change(monkeypatch, db_path, w, send_impl):
    # Seed the delivered baseline = place 5 in the on-disk db, then run a fetch of
    # place 2 (a change). run() opens/closes its OWN Store on db_path, so we reopen
    # the file afterwards to inspect the baseline (never touch a closed connection).
    seed = Store(db_path)
    seed.save_notified_snapshot(_snap(w.watch_id, 5))
    seed.close()
    class _A:
        def fetch(self, watch): return _snap(w.watch_id, 2)
    monkeypatch.setattr(pipeline, "get_adapter", lambda name: _A())
    monkeypatch.setattr(pipeline, "_render_dashboard", lambda *a, **k: [])   # hermetic
    monkeypatch.setattr(pipeline.notify, "send_message", send_impl)
    return pipeline.run(_cfg([w], db_path), dry_run=False)


def test_baseline_advances_on_successful_send(monkeypatch, tmp_path):
    db = str(tmp_path / "s.db"); w = _watch()
    _run_with_change(monkeypatch, db, w, lambda *a, **k: None)
    assert Store(db).load_notified_snapshot(w.watch_id).entrants[0].place == 2   # promoted


def test_baseline_held_when_send_fails(monkeypatch, tmp_path):
    db = str(tmp_path / "s.db"); w = _watch()
    def _fail(*a, **k):
        raise pipeline.notify.TelegramNetworkError("unreachable")
    _run_with_change(monkeypatch, db, w, _fail)
    assert Store(db).load_notified_snapshot(w.watch_id).entrants[0].place == 5   # held → re-alerts
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_deliver_commit.py -q`
Expected: FAIL — `test_baseline_held_when_send_fails` errors: the flat send loop lets `TelegramNetworkError` propagate out of `run()` (crash), and there is no promote yet.

- [ ] **Step 3: Write minimal implementation**

In `vuz_monitor/pipeline.py` `run()`, replace the flat send block:

```python
        for msg in messages:
            notify.send_message(config.telegram.bot_token, config.telegram.chat_id, msg)
        if mode == "daily":
            store.set_meta(HEARTBEAT_META_KEY, date.today().isoformat())
        log.info("sent %d message(s)", len(messages))
        return 0
```

with a per-group loop that promotes each group's baseline only on delivery:

```python
        delivered_all = True
        for name, greports in groups:
            try:
                for msg in notify.build_messages([(name, greports)]):
                    notify.send_message(config.telegram.bot_token, config.telegram.chat_id, msg)
            except notify.TelegramNetworkError:
                log.warning("Telegram unreachable; stopped after earlier group(s); will re-alert next run")
                delivered_all = False
                break
            # Group delivered → advance its watches' delivered baseline, atomically.
            with store.transaction():
                for r in greports:
                    if r.watch_id and r.fetched_at:   # successfully-fetched watches only
                        store.promote_notified(r.watch_id)
        if mode == "daily" and delivered_all:
            store.set_meta(HEARTBEAT_META_KEY, date.today().isoformat())
        log.info("send complete (delivered_all=%s)", delivered_all)
        return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_deliver_commit.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add vuz_monitor/pipeline.py tests/test_deliver_commit.py
git commit -m "feat: per-group send with deliver-then-commit baseline promote"
```

---

### Task 9: Full-suite regression + dry-run smoke

**Files:**
- Test: run everything; no product code expected (fix any fallout here).

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: PASS — all prior 90 tests plus the new ones. If anything red, fix the cause (do not delete assertions).

- [ ] **Step 2: Dry-run against the real config (no Telegram traffic)**

Run: `.venv/bin/python -m vuz_monitor --dry-run` (or the project's dry-run entrypoint — check `vuz_monitor/__main__.py`)
Expected: renders messages/sections to stdout, sends nothing, exits 0. Connectivity-failed sections (if any) show `⏳ временно недоступно`, not `[Errno …]`.

- [ ] **Step 3: Commit any fixes**

```bash
git add -A
git commit -m "test: full-suite regression green for connectivity gate + deliver-then-commit"
```

---

## Self-Review

**Spec coverage (each spec section → task):**
- §1 classification → Task 1. §2 report tagging → Task 2. §3 gate → Task 7.
- §4 deliver-then-commit: durable table/methods → Task 4; baseline diff + migration seed → Task 5; per-group send + promote → Task 8.
- §5 send guard `TelegramNetworkError` → Task 6. §6 calm text → Task 6.
- §7 config uniqueness → Task 3. Documented caveats (UTC, gaierror ⏳, host heuristic, at-least-once) → asserted/accepted in Tasks 6/7/8 tests + comments.
- Testing matrix → distributed across Tasks 1–8; pruning/migration/reverted/at-least-once regressions in Tasks 4/5/8. Full regression → Task 9.

**Placeholder scan:** none — every code and test step carries real content.

**Type consistency:** `is_connectivity_error(exc)->bool`, `load_notified_snapshot->Optional[Snapshot]`, `save_notified_snapshot(snap)`, `promote_notified(watch_id)`, `transaction()`, `TelegramNetworkError`, `WatchReport.net_error/host/watch_id/fetched_at` are used with identical names/signatures across tasks.

**Open implementation notes for the executor:**
- The run()-level tests (Tasks 7, 8) stub `pipeline._render_dashboard` to keep them hermetic (no `docs/` writes). Task 8 uses an on-disk db and reopens it after `run()` — do NOT inspect a `Store` that `run()` closed in its `finally`.
- `_should_send_group`/`WatchReport.has_changes` already treat `error` as sendable (`bool(self.error) or …`), so connectivity-failed sections reach the send path under `on_change_only` — that is what the gate (Task 7) intercepts, and what the dry-run test relies on.
- Confirm patch targets against `pipeline.py` at execution time (`get_adapter`, `notify`, `_render_dashboard`, `Store` are all module-level names in `pipeline`).
