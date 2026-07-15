"""Tests for the МИРЭА neighbors list page (docs/mirea-list.html)."""
from datetime import datetime, timezone

from vuz_monitor import dashboard
from vuz_monitor.config import AppConfig, TelegramConfig, WatchConfig
from vuz_monitor.models import Entrant, ProgramMeta, Snapshot
from vuz_monitor.store import Store

NOW = datetime(2026, 7, 15, 7, 0, 0, tzinfo=timezone.utc)


# --- config: track_neighbors flag --- #
def test_watch_config_parses_track_neighbors(tmp_path):
    from vuz_monitor.config import load_config
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "telegram: {chat_id: '1', bot_token: 'x'}\n"
        "tracked_codes: ['1366129']\n"
        "watches:\n"
        "  - {name: tracked, adapter: mirea_api, url: 'http://x', track_neighbors: true}\n"
        "  - {name: untracked, adapter: mirea_api, url: 'http://y'}\n",
        encoding="utf-8",
    )
    app = load_config(str(cfg))
    watches = {w.name: w for w in app.watches}
    assert watches["tracked"].track_neighbors is True
    assert watches["untracked"].track_neighbors is False   # default off
