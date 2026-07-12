"""Per-run report objects passed from the pipeline to the notifier."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .diff import CodeStatus
from .models import ProgramMeta


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
