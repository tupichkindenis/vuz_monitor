"""Config + secrets loading.

Config lives in ``config.yaml`` (structure, watches). The Telegram bot token comes
from the ``TELEGRAM_BOT_TOKEN`` env var (or a gitignored ``.env``), never the YAML.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

try:
    from dotenv import load_dotenv
except Exception:  # python-dotenv is optional at runtime
    load_dotenv = None


@dataclass
class WatchConfig:
    name: str
    adapter: str
    url: str
    params: dict = field(default_factory=dict)
    plan_override: Optional[int] = None
    codes: Optional[list] = None
    # which source field holds the код участника used for matching.
    # mirea_api: "superCode" (default), "snils", or "id". html_table uses columns.code.
    code_field: Optional[str] = None
    # lists that share a group go into ONE Telegram message (e.g. "МИРЭА — бюджет").
    group: Optional[str] = None
    # build the score-loading tracker (docs/mirea-scores.html) for this competition.
    track_scores: bool = False
    # build the neighbors list page (docs/mirea-list.html) for this competition.
    track_neighbors: bool = False
    # reference-only watch for the forecast page (docs/mirea-forecast.html):
    # fetched + stored for its passing thresholds, but kept OUT of Telegram and
    # of the cards/table/status/scores/neighbors pages (я сюда не подавался).
    forecast_ref: bool = False
    # html_table adapter specifics
    table_selector: Optional[str] = None
    columns: Optional[dict] = None
    encoding: Optional[str] = None

    @property
    def watch_id(self) -> str:
        """Stable id from the data source (adapter + url + params).

        The display name can change without resetting stored history.
        """
        basis = json.dumps(
            {"a": self.adapter, "u": self.url, "p": self.params},
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]


@dataclass
class TelegramConfig:
    chat_id: str
    bot_token: str


@dataclass
class AppConfig:
    telegram: TelegramConfig
    heartbeat: str
    tracked_codes: list
    watches: list
    db_path: str = "state.db"

    def resolve_codes(self, watch: WatchConfig) -> list:
        return watch.codes if watch.codes else self.tracked_codes


def load_config(path: str = "config.yaml") -> AppConfig:
    if load_dotenv:
        load_dotenv()
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"Config not found: {path}. Copy config.example.yaml to config.yaml and edit it."
        )
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    tg = raw.get("telegram") or {}
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or tg.get("bot_token") or ""
    telegram = TelegramConfig(chat_id=str(tg.get("chat_id", "")), bot_token=token)

    watches = []
    for w in raw.get("watches", []) or []:
        watches.append(
            WatchConfig(
                name=w.get("name", "unnamed"),
                adapter=w["adapter"],
                url=w["url"],
                params=w.get("params") or {},
                plan_override=w.get("plan_override"),
                codes=[str(c) for c in w["codes"]] if w.get("codes") else None,
                code_field=w.get("code_field") or raw.get("code_field"),
                group=w.get("group") or raw.get("group"),
                track_scores=bool(w.get("track_scores", False)),
                track_neighbors=bool(w.get("track_neighbors", False)),
                forecast_ref=bool(w.get("forecast_ref", False)),
                table_selector=w.get("table_selector"),
                columns=w.get("columns"),
                encoding=w.get("encoding"),
            )
        )

    seen: dict[str, str] = {}
    for w in watches:
        wid = w.watch_id
        if wid in seen:
            raise ValueError(
                f"Duplicate watch_id {wid!r}: {seen[wid]!r} and {w.name!r} "
                "resolve to the same adapter+url+params. Give them distinct url or params."
            )
        seen[wid] = w.name

    return AppConfig(
        telegram=telegram,
        heartbeat=raw.get("heartbeat", "always"),
        tracked_codes=[str(c) for c in (raw.get("tracked_codes") or [])],
        watches=watches,
        db_path=raw.get("db_path", "state.db"),
    )
