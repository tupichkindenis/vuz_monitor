"""Adapter registry. Map a config ``adapter:`` name to its implementation."""
from __future__ import annotations

from .base import Adapter
from .html_table import HtmlTableAdapter
from .mirea_api import MireaApiAdapter

_REGISTRY = {
    "mirea_api": MireaApiAdapter,
    "html_table": HtmlTableAdapter,
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
