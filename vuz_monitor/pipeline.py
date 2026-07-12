"""Orchestration: for each watch → fetch → diff → notify → persist."""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from . import dashboard, notify
from .adapters import get_adapter
from .config import AppConfig, WatchConfig
from .diff import compute_changes, compute_status
from .models import ProgramMeta
from .report import CodeReport, WatchReport, group_reports
from .store import Store

log = logging.getLogger("vuz_monitor")

HEARTBEAT_META_KEY = "last_heartbeat_date"
DASHBOARD_OUT = "docs/index.html"


def _process_watch(
    watch: WatchConfig, config: AppConfig, store: Store, dry_run: bool
) -> WatchReport:
    try:
        adapter = get_adapter(watch.adapter)
        snap = adapter.fetch(watch)
    except Exception as exc:  # one bad source must not abort the whole run
        log.warning("watch %s failed: %s", watch.name, exc)
        return WatchReport(name=watch.name, error=str(exc), group=watch.group or watch.name)

    prev = store.load_prev(watch.watch_id)
    unchanged = bool(
        prev is not None
        and snap.meta.updated_at
        and prev.meta.updated_at == snap.meta.updated_at
    )

    code_reports = []
    for code in config.resolve_codes(watch):
        new_status = compute_status(snap, code, watch.plan_override)
        prev_status = compute_status(prev, code, watch.plan_override)
        changes = compute_changes(prev_status, new_status)
        code_reports.append(
            CodeReport(status=new_status, changes=changes, first_run=prev is None)
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


def _render_dashboard(config: AppConfig, store: Store, out: str) -> None:
    """Generate the dashboard HTML from state.db and write it to ``out``."""
    html = dashboard.generate(config, store)
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(html, encoding="utf-8")


def build_dashboard(config: AppConfig, out: str = DASHBOARD_OUT) -> int:
    """Standalone dashboard generation (CLI ``dashboard``) — offline, from state.db."""
    store = Store(config.db_path)
    try:
        _render_dashboard(config, store, out)
    finally:
        store.close()
    print(f"Dashboard written: {out}")
    return 0


def run(config: AppConfig, dry_run: bool = False) -> int:
    store = Store(config.db_path)
    try:
        reports = [_process_watch(w, config, store, dry_run) for w in config.watches]
        if not reports:
            print("(no watches configured)")
            return 0

        # Regenerate the dashboard every hour from state.db — BEFORE the send
        # decision, so it stays fresh even under on_change_only (no message) and
        # regardless of Telegram success. A render bug must not fail the run.
        if not dry_run:
            try:
                _render_dashboard(config, store, DASHBOARD_OUT)
                log.info("dashboard written: %s", DASHBOARD_OUT)
            except Exception as exc:
                log.warning("dashboard generation failed: %s", exc)

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

        for msg in messages:
            notify.send_message(config.telegram.bot_token, config.telegram.chat_id, msg)
        if mode == "daily":
            store.set_meta(HEARTBEAT_META_KEY, date.today().isoformat())
        log.info("sent %d message(s)", len(messages))
        return 0
    finally:
        store.close()
