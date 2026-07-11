# vuz_monitor

Hourly **Telegram** monitor for Russian university admission ranked lists
(конкурсные списки). It watches ranked lists across several ВУЗ-ы and specialties,
finds you by your код участника (superCode; configurable), reports your standing (rank,
priority, score, согласие, and MIREA's own **Проходной ВП** / **Основной ВП** passing
flags), diffs against the previous hour, and sends you a Telegram message: a status
heartbeat every hour **plus** a change summary when anything moves.

## How it works

```
config.yaml ─▶ pipeline ─▶ per watch:  adapter.fetch() → Snapshot (normalized)
                                        store.load_prev() → last hour
                                        diff.compute()   → what changed for your code
                                        notify.send()    → Telegram (status + diff)
                                        store.save()      → SQLite state
```

It runs **once and exits**; a systemd timer (or cron) supplies the hourly cadence.
State lives in a local SQLite file between runs.

**One message per group.** Lists that share a `group` (e.g. all budget specialties of a
ВУЗ) are combined into a single Telegram message, with each specialty as a section and
your standing under it. Set `group` at the top level (applies to all watches) or per
watch. Omit it and each list becomes its own message.

Each data source is a small **adapter**. Three ship today:

- `mirea_api` — a JSON REST endpoint (`priem.mirea.ru/competitions_api/entrants`).
  Reliable; the reference adapter. **Budget and paid lists share this endpoint and
  response shape** — only the competition id differs (take it from the site page URL's
  `comp_ids=`), so one watch per competition id is all you need.
- `mpei_html` — МЭИ (`pk.mpei.ru`) HTML pages. Auto-detects budget vs paid, reads
  «вакантных мест» / «данные на …» from the page text, skips the 2-level table header,
  and ranks by row order. One watch per specialty page (`entrants_listNN.html`).
- `html_table` — a generic HTML `<table>` scraper; map columns by cell index in config.
  Covers most own-site ВУЗ pages without new code.

Adding a university = one adapter (or reuse `html_table` / `mpei_html`), everything else is shared.

## Setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e .              # add: pip install -e ".[test]" to run tests

cp .env.example .env          # put your bot token here (from @BotFather)
cp config.example.yaml config.yaml
```

Get a bot token from **@BotFather** in Telegram, put it in `.env` as
`TELEGRAM_BOT_TOKEN=...`. Then message your bot once and find your chat id:

```bash
python -m vuz_monitor get-chat-id      # prints chat id(s); put it in config.yaml
python -m vuz_monitor test-notify      # confirms token + chat_id work
```

Edit `config.yaml`: your `tracked_codes` (СНИЛС / application number — formatting does
not matter) and your `watches` (ВУЗ + specialty + URL/params). The MIREA example is
pre-wired as a reference.

## Run

```bash
python -m vuz_monitor list-watches           # validate config
python -m vuz_monitor run --dry-run          # print the message, don't send or save
python -m vuz_monitor run                     # real cycle: fetch → diff → notify → save
```

## Deploy (always-on VPS)

Copy the project to `/opt/vuz_monitor`, create the venv there, then use **systemd**:

```bash
sudo cp deploy/vuz-monitor.service deploy/vuz-monitor.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start vuz-monitor.service      # one run — confirm a Telegram message
sudo systemctl enable --now vuz-monitor.timer # hourly from now on
journalctl -u vuz-monitor -f                  # watch logs
```

Or cron: see `deploy/crontab.example`.

## Config reference

| Key | Meaning |
|-----|---------|
| `telegram.chat_id` | Where messages go. `TELEGRAM_BOT_TOKEN` comes from the env, not here. |
| `heartbeat` | `always` (hourly + diffs), `on_change_only`, or `daily` (change + 1/day). |
| `code_field` | Which field identifies you (mirea_api): `superCode` (код участника, default), `snils`, or `id`. No ИНН in this API. |
| `tracked_codes` | Код(ы) участника (superCode) to find in every list. Matched by digits only. |
| `watches[].adapter` | `mirea_api` or `html_table`. |
| `watches[].url` / `params` | Source location (params become query string). |
| `watches[].plan_override` | Budget places, if the source omits it (needed for HTML). |
| `watches[].table_selector` / `columns` / `encoding` | `html_table` specifics. |

## Passing status (from MIREA, not guessed)

The tool reports MIREA's own official flags instead of a home-grown estimate:

- **Проходной ВП** (`iHPO`) — the real situation: you'd be admitted *right now*, given the
  consents submitted so far. This is the honest "am I in?" signal.
- **Основной ВП** (`iHP`) — modeled as if **everyone** submits consent: guaranteed a seat
  if you consent in time. The competitive worst case. Its count equals the place count.

Both already account for **priority** across programs — that's why an applicant who is #1
by score but has this program as priority 11 shows `Основной ВП: нет` (they'll take a
higher-priority seat elsewhere). Also reported: `согласие` (consent) and, for платное
lists, `договор оплачен`.

The `html_table` adapter can map these too — add `passing_main` / `passing_real` /
`paid_ok` columns if a ВУЗ's page publishes them; otherwise they show "нет данных".

## Notes

- One failing source never aborts the run; it's reported as an error section in the message.

## Tests

```bash
pip install -e ".[test]"
pytest
```
