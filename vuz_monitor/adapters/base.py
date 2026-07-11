"""Adapter base class + shared parsing helpers.

An adapter turns one data source into a normalized :class:`Snapshot`. Add a ВУЗ by
writing one adapter (or reusing ``html_table`` with a column map).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone

import httpx

from ..config import WatchConfig
from ..models import Snapshot

DEFAULT_HEADERS = {
    "User-Agent": "vuz_monitor/0.1 (personal admission-list monitor)",
    "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
}


class Adapter(ABC):
    timeout = 30.0

    @abstractmethod
    def fetch(self, watch: WatchConfig) -> Snapshot:
        """Fetch the source and return a normalized snapshot. May raise on failure."""

    def _get(self, url: str, params=None, headers=None) -> httpx.Response:
        h = dict(DEFAULT_HEADERS)
        h.update(headers or {})
        resp = httpx.get(
            url, params=params, headers=h, timeout=self.timeout, follow_redirects=True
        )
        resp.raise_for_status()
        return resp


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_int(v) -> "int | None":
    try:
        if v is None or v == "":
            return None
        return int(str(v).strip())
    except (ValueError, TypeError):
        return None


def to_num(v) -> "float | None":
    if v is None or v == "":
        return None
    try:
        return float(str(v).strip().replace(",", "."))
    except (ValueError, TypeError):
        return None


def to_score(v, scale: float = 1000.0) -> "float | None":
    """MIREA scores are integers scaled ×1000 (302000 -> 302.0)."""
    n = to_num(v)
    return None if n is None else n / scale


def truthy(v) -> bool:
    """Interpret an HTML cell / flag as a boolean consent marker."""
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    s = str(v).strip().lower()
    return s in {"1", "да", "+", "yes", "true", "оригинал", "согласие", "есть", "✓", "v"}
