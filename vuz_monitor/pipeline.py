"""Orchestration: for each watch → fetch → diff → notify → persist."""
from __future__ import annotations

import logging
from datetime import date

from . import notify
from .adapters import get_adapter
from .config import AppConfig, WatchConfig
from .diff import compute_changes, compute_status
from .models import ProgramMeta
from .report import CodeReport, WatchReport
from .store import Store

log = logging.getLogger("vuz_monitor")

HEARTBEAT_META_KEY = "last_heartbeat_date"


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


def _reports_to_send(reports: list, config: AppConfig, store: Store) -> list:
    """Which lists to message this run (heartbeat decision is per-list now)."""
    mode = (config.heartbeat or "always").lower()
    if mode == "on_change_only":
        return [r for r in reports if r.has_changes]
    if mode == "daily":
        due = store.get_meta(HEARTBEAT_META_KEY) != date.today().isoformat()
        return list(reports) if due else [r for r in reports if r.has_changes]
    return list(reports)  # always


def _group_reports(reports: list) -> list:
    """Bucket reports by group (ВУЗ + конкурс), preserving first-seen order."""
    order = []
    buckets = {}
    for r in reports:
        key = r.group or r.name
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(r)
    return [(k, buckets[k]) for k in order]


def run(config: AppConfig, dry_run: bool = False) -> int:
    store = Store(config.db_path)
    try:
        reports = [_process_watch(w, config, store, dry_run) for w in config.watches]
        to_send = _reports_to_send(reports, config, store)
        groups = _group_reports(to_send)            # one message per (ВУЗ + конкурс)
        messages = notify.build_messages(groups)

        if not reports:
            print("(no watches configured)")
            return 0
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
        if (config.heartbeat or "").lower() == "daily":
            store.set_meta(HEARTBEAT_META_KEY, date.today().isoformat())
        log.info("sent %d message(s)", len(messages))
        return 0
    finally:
        store.close()
