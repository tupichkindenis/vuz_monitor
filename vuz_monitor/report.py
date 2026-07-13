"""Per-run report objects passed from the pipeline to the notifier."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .diff import CodeStatus
from .models import ProgramMeta, Snapshot

BUCKET_WIDTH = 100_000  # application-number range width (superCode), as in the reference


def score_progress(snap: Snapshot) -> dict:
    """Aggregate one competition's score-loading state.

    «Без баллов» = a row whose вступительные (``entrance_score``) haven't loaded
    (``None`` or ``0.0``) AND that isn't БВИ (an olympiad admit legitimately has
    entranceMark 0). Buckets group by application-number (``superCode`` == ``code``)
    range so you can watch the loading wave move toward a given number.

    Returns ``{ts, total, no_score, buckets: {bucket: [total, no_score]}}``.
    ``total`` counts every entrant; a non-numeric code is left out of ``buckets``
    only (so bucket totals may sum to less than ``total``).
    """
    total = 0
    no_score = 0
    buckets: dict[int, list] = {}
    for e in snap.entrants:
        total += 1
        missing = (not e.entrance_score) and (not e.is_bvi)
        if missing:
            no_score += 1
        try:
            bucket = (int(e.code) // BUCKET_WIDTH) * BUCKET_WIDTH
        except (TypeError, ValueError):
            continue  # non-numeric code: count in total, skip the range breakdown
        cell = buckets.setdefault(bucket, [0, 0])
        cell[0] += 1
        if missing:
            cell[1] += 1
    return {
        "ts": snap.fetched_at,
        "total": total,
        "no_score": no_score,
        "buckets": buckets,
    }


@dataclass
class CodeReport:
    status: CodeStatus
    changes: list = field(default_factory=list)
    first_run: bool = False


@dataclass
class WatchReport:
    name: str
    title: Optional[str] = None
    meta: Optional[ProgramMeta] = None
    codes: list = field(default_factory=list)
    error: Optional[str] = None
    unchanged_source: bool = False  # source's updated_at matched the previous run
    group: Optional[str] = None     # lists sharing a group go into one message
    watch_id: Optional[str] = None  # for dashboard history lookup
    fetched_at: Optional[str] = None  # last snapshot time (dashboard freshness)

    @property
    def has_changes(self) -> bool:
        return bool(self.error) or any(
            cr.changes or cr.first_run for cr in self.codes
        )


def group_reports(reports: list) -> list:
    """Bucket reports by group (ВУЗ + конкурс), preserving first-seen order.

    Shared by the notifier pipeline and the dashboard so both render the same
    ВУЗ+основа sections.
    """
    order = []
    buckets = {}
    for r in reports:
        key = r.group or r.name
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(r)
    return [(k, buckets[k]) for k in order]
