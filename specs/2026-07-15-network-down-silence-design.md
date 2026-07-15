# Design: run-level connectivity gate ("no net → no message")

**Date:** 2026-07-15
**Status:** Approved, ready for implementation planning
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

Errno breakdown over the log:

| errno | meaning | count |
|-------|---------|-------|
| `[Errno 65] No route to host` (EHOSTUNREACH) | interface up, no route | 156 |
| `[Errno 8] nodename nor servname provided` (EAI_NONAME) | DNS itself fails | 54 |
| `[Errno 89] Operation canceled` (ECANCELED) | connection torn down mid-request | 15 |

Failures cluster into ~13 whole-run bursts (00:07, 02:05, 03:19, 04:34, 05:05,
06:19, 08:09 …). Within each burst **every watch across every university** fails
within ~1 second — MIREA (reliable JSON API) and МАИ alike. This is the signature
of the *local* machine losing connectivity for a moment, not a source outage. The
МАИ «платно» message the user pasted is one slice of such a run.

Confirming detail: on the 08:09 run all fetches failed at 08:09:20, but
`sent 2 message(s)` only landed at 08:17:45 — the network recovered ~8 min later
and the message (carrying the ⚠️ errors) went out then. On other runs the send
itself threw `network error` and nothing was delivered.

## Root cause

Not a bug in any adapter. The adapters correctly report "I couldn't reach the
host." The gap is that the pipeline treats *our-network-down* the same as
*a-source-broke*: it renders per-watch ⚠️ errors and/or crashes on the send.

## Goal (chosen)

**Stop the Telegram spam.** App-level only — no infra changes, no retry.
When a run fails because *our* network is down, emit **nothing** to Telegram and
do not crash. A genuine single-source break must still surface as ⚠️.

Approach chosen: **A — "no net → no message" (run-level connectivity gate).**
(Alternatives considered: B — collapse noisy sections into one calm line but still
send; C — preflight retry to recover blips. Both deprioritized: A is the minimal,
lowest-risk answer and its guards also kill the crash-traceback mode.)

## Design

### Principle

A run that fails because our machine had no network is not news about the
universities, so it produces no Telegram output and no crash. The gate is
deliberately strict: it fires only when **every** watch failed **and every**
failure was connectivity-level.

### 1. Classify the failure — `is_connectivity_error(exc)` (`adapters/base.py`)

Single source of truth for "our network vs their problem".

- **True** (our network) when `exc` is one of
  `httpx.ConnectError`, `httpx.ConnectTimeout`, `httpx.ReadError`,
  `httpx.WriteError`, **or** any `OSError` in the `__cause__` / `__context__`
  chain whose `errno` is in `{8, 51, 65, 89}` — EAI_NONAME / ENETUNREACH /
  EHOSTUNREACH / ECANCELED (exactly the errnos the log shows).
- **False** (their problem) for `httpx.HTTPStatusError` (500/404),
  `httpx.ReadTimeout` (source reachable but slow), `ValueError` (parse errors),
  `ECONNREFUSED` (61, source down), and everything else.

Rationale for the exclusions: a read timeout means we *connected* — the host is
up but slow, which is a source condition worth seeing. Connection-refused means
the host answered "no", also source-side.

### 2. Tag the report — `WatchReport.net_error: bool` (`report.py` + `pipeline.py`)

Add one field to `WatchReport`, default `False`. In `_process_watch`'s existing
`except Exception as exc` block, set `net_error=is_connectivity_error(exc)`
alongside the current `error=str(exc)`. No other behavior changes; a failed fetch
still returns early **without touching history or state**, so suppression cannot
corrupt data.

### 3. The gate — `run()` (`pipeline.py`)

Placed **after** the dashboard render (so freshness/timestamps are unaffected —
the re-render is a harmless no-op when no new data was fetched) and **before** the
group/send decision:

```python
# If EVERY watch failed on connectivity, our own network was down — not the
# sources. Don't emit a Telegram message full of ⚠️; just log and exit.
if reports and all(r.error and r.net_error for r in reports):
    log.warning("all %d watches unreachable (local network down); skipping send",
                len(reports))
    return 0
```

Fires only when *every* report is a connectivity failure. One real source break
mixed in (a `net_error=False` failure, or any success) lets the message through.

### 4. Guard the send — `TelegramNetworkError` (`notify.py` + `pipeline.py`)

`_api_call`'s `except httpx.HTTPError` branch raises a new
`TelegramNetworkError(RuntimeError)`. Token-missing and HTTP-status errors stay
plain `RuntimeError` — those *should* surface. The send loop catches only the
network variant:

```python
try:
    for msg in messages:
        notify.send_message(config.telegram.bot_token, config.telegram.chat_id, msg)
except notify.TelegramNetworkError:
    log.warning("Telegram unreachable; %d message(s) not sent", len(messages))
    return 0
```

This removes the crash-traceback mode (network still down at send time).
`set_meta(HEARTBEAT_META_KEY, …)` for `mode == "daily"` remains after the loop, so
a failed send does not mark the daily heartbeat as sent.

### 5. Calm text for the partial case — `_specialty_block` (`notify.py`)

When a rendered section is connectivity-failed (`report.net_error` is `True`),
render `– ⏳ временно недоступно` instead of `– ⚠️ ошибка: [Errno 65] …`. This
covers the *partial* run (e.g. MIREA ok, МАИ blipped) where the gate does not
fire but we still don't want a raw errno string in the message. Source failures
(`net_error=False`) keep the full `– ⚠️ ошибка: {error}`.

## Files touched

| File | Change |
|------|--------|
| `vuz_monitor/adapters/base.py` | add `is_connectivity_error(exc)` |
| `vuz_monitor/report.py` | add `net_error: bool = False` to `WatchReport` |
| `vuz_monitor/pipeline.py` | set `net_error` in `_process_watch`; add gate; guard send loop |
| `vuz_monitor/notify.py` | add `TelegramNetworkError`; calm text in `_specialty_block` |
| `tests/test_connectivity.py` | new test file (below) |

## Testing (TDD)

New `tests/test_connectivity.py`:

- **`is_connectivity_error`**: `httpx.ConnectError` / `ConnectTimeout` /
  `ReadError` → True; `OSError(errno=65)` (and 8, 51, 89) → True;
  `httpx.HTTPStatusError` / `httpx.ReadTimeout` / `ValueError` /
  `OSError(errno=61)` → False.
- **`_process_watch`**: adapter raising `httpx.ConnectError` → report with
  `net_error=True` and `error` set; adapter raising `ValueError` →
  `net_error=False`.
- **Gate**: `run()` with every watch raising a connectivity error →
  `notify.send_message` never called (spy/monkeypatch), returns 0; mixed run (one
  watch succeeds) → `send_message` called; all-source-fail run (`ValueError`) →
  `send_message` called and the ⚠️ text is present.
- **Send guard**: `send_message` raising `TelegramNetworkError` → `run()` returns
  0 and raises nothing.
- **`_specialty_block`**: `net_error=True` report → output contains
  "временно недоступно" and does **not** contain `[Errno`; `net_error=False`
  report → output contains "ошибка".

## Non-goals / out of scope

- No retry / preflight connectivity probe (that was Approach C).
- No infra changes (keeping the Mac awake / wait-for-network / caffeinate).
- No changes to state persistence, dashboard rendering, or adapter parsing.
