"""CLI entrypoint. `python -m vuz_monitor <command>` or the `vuz-monitor` script."""
from __future__ import annotations

import argparse
import logging
import sys

from . import notify, pipeline
from .config import load_config


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="vuz_monitor", description=__doc__)
    p.add_argument("--config", default="config.yaml", help="path to config.yaml")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="one full cycle: fetch → diff → notify → save")
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="print the message to stdout; do not send or persist state",
    )
    sub.add_parser("test-notify", help="send a test Telegram message")
    sub.add_parser("get-chat-id", help="print chat id(s) from recent bot updates")
    sub.add_parser("list-watches", help="validate and list configured watches")
    return p


def _cmd_get_chat_id(cfg) -> int:
    data = notify.get_updates(cfg.telegram.bot_token)
    seen = {}
    for upd in data.get("result", []):
        msg = upd.get("message") or upd.get("channel_post") or {}
        chat = msg.get("chat") or {}
        if chat.get("id") is not None:
            label = chat.get("title") or chat.get("username") or chat.get("first_name") or ""
            seen[chat["id"]] = f"{chat.get('type', '')} {label}".strip()
    if not seen:
        print("No chats found. Send a message to your bot first, then re-run.")
        return 1
    print("Chats that have messaged your bot:")
    for cid, label in seen.items():
        print(f"  {cid}   {label}")
    return 0


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    # httpx logs the full request URL, which for Telegram contains the bot token.
    # Keep it at WARNING so the token never lands in stdout / journald / cron logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        cfg = load_config(args.config)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 2

    if args.cmd == "run":
        return pipeline.run(cfg, dry_run=args.dry_run)
    if args.cmd == "test-notify":
        notify.send_message(
            cfg.telegram.bot_token,
            cfg.telegram.chat_id,
            "🎓 vuz_monitor: тест уведомления — всё работает.",
        )
        print("Sent.")
        return 0
    if args.cmd == "get-chat-id":
        return _cmd_get_chat_id(cfg)
    if args.cmd == "list-watches":
        if not cfg.watches:
            print("No watches configured.")
            return 0
        for w in cfg.watches:
            codes = ", ".join(cfg.resolve_codes(w)) or "(none)"
            print(f"[{w.watch_id}] {w.adapter:10s} {w.name}  · codes: {codes}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
