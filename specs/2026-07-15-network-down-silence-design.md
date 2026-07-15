# Design: connectivity gate + deliver-then-commit ("no net → no message, no lost alert")

**Date:** 2026-07-15
**Status:** Approved (revised after Codex review), ready for implementation planning
**Author:** Denis + Claude (brainstorming session)

## Problem

The hourly monitor (launchd on the user's Mac) sometimes fires when the machine
has **no working network** — most often overnight, when the Mac is asleep / WiFi
has not reassociated after wake. When that happens the run produces alarming noise:

- Telegram messages full of `– ⚠️ ошибка: [Errno 65] No route to host` — one per
  specialty, identical, across every ВУЗ in the run.
- Or, when the network is still down at send time, an uncaught
  `RuntimeError: Telegram sendMessage: network error` traceback in `launchd.log`
  and **no** message at all.

### Evidence (launchd.log, 2026-07-15)

| errno | meaning | count |
|-------|---------|-------|
| `[Errno 65] No route to host` (EHOSTUNREACH) | interface up, no route | 156 |
| `[Errno 8] nodename nor servname provided` (EAI_NONAME, `socket.gaierror`) | DNS itself fails | 54 |
| `[Errno 89] Operation canceled` (ECANCELED) | connection torn down mid-request | 15 |

Failures cluster into ~13 whole-run bursts overnight (00:07, 02:05, 03:19, 04:34,
05:05, 06:19, 08:09 …). Within each burst **every watch across every university**
fails within ~1 second — MIREA (JSON API), МАИ, МЭИ, Станкин alike. This is the
signature of the *local* machine losing connectivity for a moment, not a source
outage. On the 08:09 run all fetches failed at 08:09:20, but `sent 2 message(s)`
only landed at 08:17:45 — the network recovered ~8 min later and the message
(carrying the ⚠️ errors) went out then.

### Root cause

Not a bug in any adapter. The adapters correctly report "I couldn't reach the
host." The gap is that the pipeline treats *our-network-down* the same as
*a-source-broke*: it renders per-watch ⚠️ errors and/or crashes on the send.

### Second, deeper problem found in review (Codex)

`store.save(snap)` in `_process_watch` (`pipeline.py:59`) advances the
change-detection baseline **before** the Telegram send (`pipeline.py:208`). The
configured mode is `on_change_only` (`config.yaml:3`). So if a real change is
saved but the send then fails, the next run diffs against the already-saved
state, sees "no change," and **the alert is lost forever**. This already happens
today (the send crashes *after* the save); simply catching the send error would
turn a visible crash into silent data loss. The fix must decouple "data saved"
from "alert delivered."

## Goal

1. **Stop the Telegram spam** — when a run fails because *our* network is down,
   emit nothing to Telegram and do not crash. A genuine single-source break must
   still surface as ⚠️.
2. **Never lose a change** — a delivery failure must not drop an `on_change_only`
   alert; the change re-alerts on the next run that can deliver.

App-level only. No infra changes (no caffeinate / wait-for-network). No retry loop.

## Design

### 1. Classify the failure — `is_connectivity_error(exc)` (`adapters/base.py`)

Single source of truth for "our network vs their problem." Decides from the
**underlying socket error**, walking the `__cause__` / `__context__` chain with a
visited-set (cycle-safe) — NOT from the httpx wrapper type, because
`httpx.ConnectError` wraps DNS-fail, EHOSTUNREACH, ENETUNREACH **and**
ECONNREFUSED alike, so a type check cannot tell our-network from source-down.

```python
import errno, socket

_NET_ERRNOS = {
    errno.ENETDOWN,     # 50
    errno.ENETUNREACH,  # 51
    errno.EHOSTUNREACH, # 65
    errno.ECANCELED,    # 89 — connection torn down mid-request (seen in bursts)
}

def _causes(exc):
    seen, cur = set(), exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        yield cur
        cur = cur.__cause__ or cur.__context__

def is_connectivity_error(exc) -> bool:
    for e in _causes(exc):
        if isinstance(e, socket.gaierror):
            return True             # DNS resolution failed → no network/DNS
        if isinstance(e, OSError):  # gaierror handled above
            if e.errno in _NET_ERRNOS:
                return True
            if e.errno == errno.ECONNREFUSED:  # 61
                return False        # reachable host refused → source down
    return False
```

Decisions and rationale:
- `socket.gaierror` is matched **by type**, before the errno check. It is an
  `OSError` subclass whose `.errno` is a `getaddrinfo` code (`EAI_NONAME` == 8),
  NOT the POSIX `OSError` errno 8 (`ENOEXEC`). The log's `[Errno 8]` is a
  `gaierror`; matching "OSError errno 8" would conflate it with an unrelated error.
- `ECONNREFUSED` (61) → **False**: the host answered, it just refused. Source-side.
- `ConnectTimeout` wraps a `TimeoutError`/`socket.timeout` with **no errno**, so it
  falls through to `False`. A connect-timeout is genuinely ambiguous (local drop
  vs remote firewall); we do not silence on uncertainty. The real failures are
  immediate errnos, not timeouts, so this costs nothing.
- `ReadTimeout` (source reachable but slow), `HTTPStatusError` (500/404),
  `ValueError` (parse) → **False** (their problem).

### 2. Tag the report — `WatchReport.net_error` + `WatchReport.host` (`report.py`, `pipeline.py`)

Add two fields to `WatchReport`, both defaulted:
- `net_error: bool = False`
- `host: Optional[str] = None`

`_process_watch`'s `except` block sets `net_error=is_connectivity_error(exc)` and
`host=urlsplit(watch.url).hostname` alongside the existing `error=str(exc)`. The
**success** path also sets `watch_id=watch.watch_id` and `fetched_at=snap.fetched_at`
on its `WatchReport` (currently unset), so run() can advance the delivery marker
per watch. A failed fetch still returns early **without** touching history/state.

### 3. The gate — `run()` (`pipeline.py`)

Placed **after** the dashboard render (dashboard stays fresh — see §5) and
**before** the send decision, and it must NOT fire in `dry_run`:

```python
net_down = (
    not dry_run
    and reports
    and all(r.error and r.net_error for r in reports)
    and len({r.host for r in reports if r.host}) >= 2
)
if net_down:
    log.warning("local network down: %d watches unreachable across %d hosts; skipping send",
                len(reports), len({r.host for r in reports if r.host}))
    return 0
```

Fires only when *every* report is a connectivity failure **and** those failures
span **≥2 distinct hosts**. Rationale:
- One real source break (a `net_error=False` failure) or any success → message
  goes through.
- The ≥2-hosts requirement means a single-host outage (e.g. all 10 МАИ watches,
  `public.mai.ru` down) is reported as errors, not silently swallowed. Only many
  independent hosts failing at once implies the local machine is offline.
- **Documented limitation:** with fewer than 2 distinct hosts configured, the gate
  cannot engage — single-host local-vs-source failure is undecidable without an
  active probe, which is out of scope.

### 4. Deliver-then-commit — never lose a change (`pipeline.py`, `store.py`)

**Marker.** A per-watch meta key `notified:{watch_id}` holds the `fetched_at` of
the last snapshot whose group message was delivered.

**Baseline for change-detection.** `_process_watch` computes the diff against the
last **delivered** snapshot, not the last saved one:
- New `store.load_notified_snapshot(watch_id)`: reads `notified:{watch_id}`; if
  set, loads the snapshot row with that `fetched_at`; returns `None` if the marker
  is unset (fresh watch) or its snapshot was pruned after a long outage.
- `None` baseline → `first_run` semantics → the group is sent. So the first alert,
  or an alert after a long delivery gap, is never lost (at worst re-sent once).
- `store.save(snap)` still runs every hour (append-only, pruned to last N), so
  `load_prev` — used by the dashboard and by the `unchanged`/`updated_at` flag —
  keeps returning the freshest snapshot. Only the change-detection baseline moves
  to the delivered marker.

**Send per group, commit the marker on success.** Replace the flat
`for msg in messages: send_message(...)` loop with a per-group loop so the marker
advances exactly for delivered groups:

```python
delivered_all = True
for name, reports in groups:
    msgs = notify.build_messages([(name, reports)])   # 1 group → 1+ messages (TG_LIMIT split)
    try:
        for msg in msgs:
            notify.send_message(config.telegram.bot_token, config.telegram.chat_id, msg)
    except notify.TelegramNetworkError:
        log.warning("Telegram unreachable; stopping after delivering earlier group(s)")
        delivered_all = False
        break                       # this + remaining groups keep old markers → re-alert next run
    for r in reports:               # group delivered → advance its watches' markers
        if r.watch_id and r.fetched_at:
            store.set_meta(f"notified:{r.watch_id}", r.fetched_at)

if mode == "daily" and delivered_all:
    store.set_meta(HEARTBEAT_META_KEY, date.today().isoformat())
```

A group whose send fails, and every group after it, keeps its previous marker; its
accumulated change re-alerts on the next deliverable run. This also fixes the
multi-message atomicity bug: partial delivery across groups is now correct instead
of mislabeled "N not sent."

### 5. Guard the send — `TelegramNetworkError` (`notify.py`)

`_api_call`'s `except httpx.HTTPError` branch raises `TelegramNetworkError(RuntimeError)`
**only** when `is_connectivity_error(exc)` is true; other Telegram failures
(read-timeout, protocol error, remote disconnect) stay a plain `RuntimeError` and
still surface. Token-missing and HTTP-status errors also stay plain `RuntimeError`.
With the §4 marker, even a plain-`RuntimeError` crash no longer loses data (the
marker was not advanced), so this guard is now about clean logs, not durability.

### 6. Calm text for the partial case — `_specialty_block` (`notify.py`)

When a rendered section is connectivity-failed (`report.net_error`), render
`– ⏳ временно недоступно` instead of `– ⚠️ ошибка: [Errno 65] …`. Covers a
*partial* run (some hosts ok, one blipped) where the gate does not fire but we
still don't want a raw errno string. Source failures (`net_error=False`) keep the
full `– ⚠️ ошибка: {error}`.

### Dashboard freshness (addressing Codex #8)

Because `store.save(snap)` still runs every hour and the dashboard reads the
latest snapshot via `load_prev`, a suppressed or undelivered run does **not**
stale the dashboard beyond the current hour — only the *change-detection baseline*
is held back. No new staleness is introduced.

## Files touched

| File | Change |
|------|--------|
| `vuz_monitor/adapters/base.py` | add `is_connectivity_error(exc)` + `_causes` + `_NET_ERRNOS` |
| `vuz_monitor/report.py` | add `net_error: bool` and `host: Optional[str]` to `WatchReport` |
| `vuz_monitor/store.py` | add `load_notified_snapshot(watch_id)` (+ query snapshot by `fetched_at`) |
| `vuz_monitor/pipeline.py` | set `net_error`/`host` on error reports and `watch_id`/`fetched_at` on success reports; diff against notified baseline; add gate; per-group send + marker commit |
| `vuz_monitor/notify.py` | add `TelegramNetworkError`; raise it only for connectivity in `_api_call`; calm text in `_specialty_block` |
| `tests/test_connectivity.py` | new test file (below) |

## Testing (TDD)

New `tests/test_connectivity.py`, plus additions to pipeline/notify tests:

**Classification (`is_connectivity_error`)**
- `httpx.ConnectError` wrapping `OSError(EHOSTUNREACH)` / `OSError(ENETUNREACH)` → True.
- `httpx.ConnectError` wrapping `socket.gaierror(EAI_NONAME)` → True (DNS).
- `httpx.ConnectError` wrapping `OSError(ECONNREFUSED)` → **False** (the contradiction Codex caught).
- `OSError(errno=8)` (ENOEXEC, not a gaierror) → False (must not be treated as DNS).
- `httpx.ConnectTimeout` (no errno) → False.
- `httpx.ReadTimeout`, `httpx.HTTPStatusError`, `ValueError` → False.
- Cyclic `__cause__`/`__context__` chain terminates (visited-set) and is classified.

**Report tagging (`_process_watch`)**
- adapter raises connectivity error → report `net_error=True`, `host` set from `watch.url`.
- adapter raises `ValueError` → `net_error=False`.
- success → report carries `watch_id` and `fetched_at`.

**Gate (`run`)**
- every watch connectivity-fails across ≥2 hosts → `send_message` never called, returns 0.
- every watch connectivity-fails but **all one host** → gate does NOT fire (message attempted).
- mixed (one watch succeeds) → sends.
- all-source-fail (`ValueError`) → sends, ⚠️ text present.
- `dry_run=True` with all-connectivity → dry-run output still printed (gate exempt).

**Deliver-then-commit (`run` + `store`)**
- change detected, send succeeds → `notified:{watch_id}` advances to current `fetched_at`.
- change detected, `send_message` raises `TelegramNetworkError` → marker NOT advanced;
  next run (state saved, source unchanged) still reports the change (`has_changes` True).
- two groups, group 1 delivers then group 2 throws → group 1 marker advanced, group 2
  marker unchanged; `daily` heartbeat meta NOT set (`delivered_all` False).
- no marker yet (fresh watch) → treated as first_run → sends.

**Send guard / text**
- `send_message` raising `TelegramNetworkError` → `run()` returns 0, no exception.
- non-connectivity Telegram `httpx.HTTPError` → plain `RuntimeError` (still surfaces).
- `_specialty_block` with `net_error=True` → output contains "временно недоступно",
  contains no `[Errno`; with `net_error=False` → contains "ошибка".

## Non-goals / out of scope

- No retry / preflight connectivity probe.
- No infra changes (keeping the Mac awake / wait-for-network / caffeinate).
- No active reachability probe to disambiguate single-host outages (the ≥2-hosts
  gate is the chosen heuristic instead).
- No changes to adapter parsing or the dashboard's rendering logic.
