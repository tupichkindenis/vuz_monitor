"""Adapter registry. Map a config ``adapter:`` name to its implementation."""
from __future__ import annotations

from .base import Adapter
from .html_table import HtmlTableAdapter
from .mirea_api import MireaApiAdapter
from .mpei_html import MpeiHtmlAdapter
from .stankin_html import StankinHtmlAdapter

_REGISTRY = {
    "mirea_api": MireaApiAdapter,
    "html_table": HtmlTableAdapter,
    "mpei_html": MpeiHtmlAdapter,
    "stankin_html": StankinHtmlAdapter,
}


def get_adapter(name: str) -> Adapter:
    try:
        cls = _REGISTRY[name]
    except KeyError:
        raise ValueError(
            f"Unknown adapter {name!r}. Known: {', '.join(sorted(_REGISTRY))}"
        )
    return cls()


def adapter_names() -> list:
    return sorted(_REGISTRY)
