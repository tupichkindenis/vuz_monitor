"""Orchestration: for each watch → fetch → diff → notify → persist."""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from . import dashboard, notify
from .adapters import get_adapter
from .adapters.base import is_connectivity_error
from .config import AppConfig, WatchConfig
from .diff import compute_changes, compute_status
from .models import ProgramMeta
from .report import CodeReport, WatchReport, group_reports, score_progress
from .store import Store

log = logging.getLogger("vuz_monitor")

HEARTBEAT_META_KEY = "last_heartbeat_date"
DASHBOARD_DIR = "docs"  # index.html (mobile cards) + table.html (desktop table)


def _process_watch(
    watch: WatchConfig, config: AppConfig, store: Store, dry_run: bool
) -> WatchReport:
    try:
        adapter = get_adapter(watch.adapter)
        snap = adapter.fetch(watch)
    except Exception as exc:  # one bad source must not abort the whole run
        log.warning("watch %s failed: %s", watch.name, exc)
        return WatchReport(
            name=watch.name,
            error=str(exc),
            net_error=is_connectivity_error(exc),
            host=urlsplit(watch.url).hostname,
            group=watch.group or watch.name,
        )

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
            # Compact per-code point for the dashboard sparklines — every hour,
            # independent of whether a Telegram message is sent. place=None when
            # the code dropped out of the list («выбыл»).
            store.append_history(
                watch.watch_id, code, snap.fetched_at,
                new_status.place, new_status.final_score,
                new_status.passing_main, new_status.passing_real,
                new_status.consent, new_status.contract,
            )

    if not dry_run:
        store.save(snap)

    return WatchReport(
        name=watch.name,
        title=snap.meta.title,
        meta=snap.meta,
        codes=code_reports,
        unchanged_source=unchanged,
        group=watch.group or watch.name,
        watch_id=watch.watch_id,
        fetched_at=snap.fetched_at,
    )


def _should_send_group(reports: list, mode: str, store: Store) -> bool:
    """Whether to message a whole group (ВУЗ + конкурс) this run.

    A group is sent when something in it actually changed — a tracked applicant's
    standing moved, a list first appeared, or a source errored. If nothing changed
    (data identical to last run) we stay silent, so no repeated hourly messages.
    """
    changed = any(r.has_changes for r in reports)
    if mode == "on_change_only":
        return changed
    if mode == "daily":
        if changed:
            return True
        return store.get_meta(HEARTBEAT_META_KEY) != date.today().isoformat()
    return True  # always


def _record_score_progress(config: AppConfig, store: Store) -> None:
    """Append one score-loading aggregate row per `track_scores` competition, from
    the snapshot just saved this run. Idempotent on (watch_id, fetched_at). On the
    first run for a watch (no history yet) seed the full trend from stored snapshots
    so the page has data immediately."""
    for watch in config.watches:
        if not watch.track_scores:
            continue
        if not store.load_score_progress(watch.watch_id):
            _backfill_watch(store, watch.watch_id)   # seed trend from disk
            continue                                  # backfill already covers this run
        snap = store.load_prev(watch.watch_id)
        if snap is None:
            continue
        agg = score_progress(snap)
        store.append_score_progress(
            watch.watch_id, agg["ts"], agg["total"], agg["no_score"], agg["buckets"]
        )


def _backfill_watch(store: Store, watch_id: str) -> int:
    n = 0
    for snap in store.load_all_snapshots(watch_id):
        agg = score_progress(snap)
        store.append_score_progress(
            watch_id, agg["ts"], agg["total"], agg["no_score"], agg["buckets"]
        )
        n += 1
    return n


def backfill_score_progress(config: AppConfig) -> int:
    """One-time seed of score_progress from every stored snapshot of each
    `track_scores` competition. Idempotent — safe to re-run."""
    store = Store(config.db_path)
    try:
        return sum(
            _backfill_watch(store, w.watch_id)
            for w in config.watches if w.track_scores
        )
    finally:
        store.close()


def _render_dashboard(config: AppConfig, store: Store, out_dir: str) -> list:
    """Generate both pages from state.db and write them into ``out_dir``.
    Returns the written file paths."""
    pages = dashboard.render_pages(config, store)  # {filename: html}
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    written = []
    for fname, html in pages.items():
        p = d / fname
        p.write_text(html, encoding="utf-8")
        written.append(str(p))
    return written


def build_dashboard(config: AppConfig, out_dir: str = DASHBOARD_DIR) -> int:
    """Standalone dashboard generation (CLI ``dashboard``) — offline, from state.db."""
    store = Store(config.db_path)
    try:
        # Seed score history from stored snapshots for any tracked competition that
        # has none yet, so a plain regen produces the score page without a full run.
        for w in config.watches:
            if w.track_scores and not store.load_score_progress(w.watch_id):
                _backfill_watch(store, w.watch_id)
        written = _render_dashboard(config, store, out_dir)
    finally:
        store.close()
    print("Dashboard written: " + ", ".join(written))
    return 0


def run(config: AppConfig, dry_run: bool = False) -> int:
    store = Store(config.db_path)
    try:
        reports = [_process_watch(w, config, store, dry_run) for w in config.watches]
        if not reports:
            print("(no watches configured)")
            return 0

        # Record the score-loading aggregate for tracked competitions before the
        # dashboard render (the page reads score_progress). An aggregation bug must
        # not fail the run.
        if not dry_run:
            try:
                _record_score_progress(config, store)
            except Exception as exc:
                log.warning("score_progress recording failed: %s", exc)

        # Regenerate the dashboard every hour from state.db — BEFORE the send
        # decision, so it stays fresh even under on_change_only (no message) and
        # regardless of Telegram success. A render bug must not fail the run.
        if not dry_run:
            try:
                _render_dashboard(config, store, DASHBOARD_DIR)
                log.info("dashboard written: %s/{index,table}.html", DASHBOARD_DIR)
            except Exception as exc:
                log.warning("dashboard generation failed: %s", exc)

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

        mode = (config.heartbeat or "always").lower()
        # Decide per group; send the FULL group (all specialties) when any changed.
        groups = [
            (name, reps)
            for name, reps in group_reports(reports)
            if _should_send_group(reps, mode, store)
        ]
        messages = notify.build_messages(groups)

        if not messages:
            log.info("heartbeat=%s: nothing to send", config.heartbeat)
            if dry_run:
                print("(nothing to send under heartbeat mode)")
            return 0

        if dry_run:
            print(("\n\n" + "═" * 40 + "\n\n").join(messages))
            return 0

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
    finally:
        store.close()
