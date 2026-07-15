import pytest

from vuz_monitor.config import load_config


_DUP = """
telegram: {chat_id: "1", bot_token: "t"}
watches:
  - {name: "A", adapter: mirea_api, url: "https://x/y", params: {comp_ids: "1"}}
  - {name: "B", adapter: mirea_api, url: "https://x/y", params: {comp_ids: "1"}}
"""


def test_duplicate_watch_id_rejected(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(_DUP, encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate watch_id"):
        load_config(str(p))
