# Cheap Flight Tracker

Watches **live Google Flights prices** from your city to the places you care about and sends you
a **Telegram message and/or email** when it finds a deal. If it can no longer fetch prices, it
tells you that too.

- Origins: an airport (`IST`) or a city (`Istanbul`).
- Destinations: airports (`AMS`), cities (`London`), or whole countries (`DE`).
- Finds the cheapest day in a date window (a fixed range or the next N days), one-way or round
  trip with a stay length, optionally only certain weekdays or departure hours.
- Alerts when the price is at or below your limit, and/or a set percentage below the lowest price
  seen in the last 30 days.
- Runs on a cron schedule inside Docker, or once on demand.

## Quick start

The image is published to GitHub Container Registry for `linux/amd64` and `linux/arm64`, so there
is nothing to build.

1. Create a Telegram bot and/or an email app password (see below).
2. Get the compose file and the examples, then edit `config.yaml` and `.env`:
   ```bash
   mkdir cheap-flight-tracker && cd cheap-flight-tracker
   base=https://raw.githubusercontent.com/emreoztoprak/cheap-flight-tracker/main
   curl -fsSL -o docker-compose.yml $base/docker-compose.yml
   curl -fsSL -o config.yaml $base/config.example.yaml
   curl -fsSL -o .env $base/.env.example
   docker compose pull
   ```
3. Check notifications, then do a dry run (prints alerts instead of sending them):
   ```bash
   docker compose run --rm cheap-flights --test-notify
   docker compose run --rm cheap-flights --dry-run
   ```
4. Start it:
   ```bash
   docker compose up -d
   docker compose logs -f
   ```

Without compose:

```bash
docker run -d --name cheap-flights --restart unless-stopped \
  -v "$PWD/config.yaml:/config/config.yaml:ro" -v cfr-data:/data \
  --env-file .env ghcr.io/emreoztoprak/cheap-flight-tracker:latest
```

To update later: `docker compose pull && docker compose up -d`. Your price history in the
`cfr-data` volume is kept.

### Image tags

| Tag | What it is |
|---|---|
| `latest` | The newest build of `main` |
| `1.2.3`, `1.2`, `1` | A release, from a `v1.2.3` git tag |
| `sha-abc1234` | One specific commit of `main` |

Use a named volume for `/data` (as above). A bind-mounted host directory must be writable by
UID 10001.

## Telegram setup

1. In Telegram, talk to **@BotFather**, send `/newbot`, and copy the token into
   `TELEGRAM_BOT_TOKEN`.
2. Send any message to your new bot.
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and copy
   `result[0].message.chat.id` into `TELEGRAM_CHAT_ID`.

## Email setup (Gmail)

Turn on 2-Step Verification, create an **App password** at
<https://myaccount.google.com/apppasswords>, and put it in `SMTP_PASSWORD`. Use
`smtp.gmail.com`, port `587`. Other providers work the same way; port `465` uses implicit TLS.

## Command line

| Flag | Meaning |
|---|---|
| *(none)* | Run a check now, then on `schedule`, until stopped |
| `--once` | Run one check and exit (exit code 1 if the run failed) |
| `--dry-run` | Run one check, print alerts to stdout, keep no state |
| `--test-notify` | Send a test message to every channel and exit |
| `--config PATH` | Config file (default `$CFR_CONFIG` or `/config/config.yaml`) |
| `--data-dir PATH` | State directory (default `$CFR_DATA_DIR` or `/data`) |
| `--log-level LEVEL` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |

Exit code `2` means the configuration is invalid; the log says exactly which field.

## Configuration reference

See `config.example.yaml` for a complete example. Values like `"${NAME}"` are read from
environment variables; a missing variable is a startup error. `CFR_SCHEDULE` and `CFR_LOG_LEVEL`
override the file.

### Top level

| Key | Default | Meaning |
|---|---|---|
| `schedule` | `0 */6 * * *` | Cron expression for checks (a check also runs at startup) |
| `timezone` | `UTC` | Time zone for the schedule, "today", and health alert times |
| `defaults` | – | Route settings every route inherits; a key set on a route replaces the default |
| `search` | – | See below |
| `routes` | required | List of routes |
| `notify` | required | `telegram` and/or `email` |
| `health` | – | See below |
| `logging` | – | `level` (`INFO`), `format` (`text` or `json`), `file`, `max_size_mb`, `backups` — see [Data](#data) |

### Route settings

| Key | Default | Meaning |
|---|---|---|
| `name` | required | Unique name (letters, digits, `_ . -`) |
| `from` | required | Airport code or city |
| `to` | required | One or a list of airport codes, cities, or 2-letter country codes |
| `trip` | `round-trip` | `round-trip` or `one-way` |
| `nights` | `3-7` | Round trips only: stay length, `5` or `3-7` |
| `window` | `{next_days: 90}` | `{next_days: N}` or `{from: YYYY-MM-DD, to: YYYY-MM-DD}` |
| `weekdays` | all | Allowed departure days, e.g. `[fri, sat]` |
| `depart_time` | any | Outbound departure hours, e.g. `"06:00-14:00"` (whole hours) |
| `stops` | `any` | `direct`, `max_1` or `any` |
| `currency` | `EUR` | 3-letter currency code |
| `passengers` | `{adults: 1}` | `adults`, `children` |
| `top_n` | `3` | Options listed per alert |
| `max_airports_per_country` | `10` | Country destinations use their largest airports, up to this many |
| `alert.max_price` | – | Alert when the cheapest price is at or below this |
| `alert.drop_percent` | – | Alert when the cheapest price is this % below the 30-day low (needs 3 earlier runs) |

At least one of `alert.max_price` / `alert.drop_percent` is required. The same deal is not sent
again unless the price drops further or 7 days pass.

**How places are searched.** A city in the built-in list (London, Paris, Berlin, Rome, Milan,
New York, Barcelona, Amsterdam, Madrid, Istanbul, Moscow, Stockholm, Frankfurt) is one search
covering all its airports. Other cities use each of their airports. A country uses up to
`max_airports_per_country` of its airports, larger ones first (ranked by airport class and longest
runway, a rough proxy); list airport codes instead when you want exact control. Quote `"NO"` (Norway) — YAML otherwise reads it as `false`.

**How many searches?** Each departure date × each stay length × each destination airport is one
Google request (~1 s with the default delay). A 90-day round trip with `nights: 3-7` to one city
is 450 searches. Above `search.max_searches_per_run` the tool searches every 2nd, 3rd… date
instead and logs a warning.

### `search`

| Key | Default | Meaning |
|---|---|---|
| `delay_seconds` | `1.0` | Pause between requests (±30%) |
| `max_searches_per_run` | `2000` | Budget per run |
| `retries` | `3` | Retries per request for network errors and rate limiting |
| `timeout_seconds` | `20` | Per request |

### `notify`

`telegram: {bot_token, chat_id}` and/or
`email: {smtp_host, smtp_port (587), username, password, from, to}`. Every message goes to every
configured channel; if one fails, the other still gets it.

### `health`

| Key | Default | Meaning |
|---|---|---|
| `alert_after_failed_runs` | `2` | Send "can't fetch flight data" after this many failed runs in a row |
| `reminder_hours` | `24` | Repeat the alert this often while it stays broken |

A run fails when more than 90% of its searches fail. You get one alert when it starts, a reminder
every `reminder_hours`, and a "works again" message when it recovers.

## Health alerts and troubleshooting

| Cause in the alert | What it means | What to do |
|---|---|---|
| `consent_wall` | Google shows its cookie-consent page | The built-in consent cookie needs an update |
| `blocked` | Rate-limited or CAPTCHA | Raise `delay_seconds`, lower `max_searches_per_run`, or wait |
| `parse_error` | Google changed its page | The parser needs an update; the page is saved in `/data/debug/` |
| `network_error` | No connection to Google | Check the host's network/DNS |
| `internal_error` | A bug | See `docker logs` for the traceback |

Run `--log-level DEBUG` to see every search and its result. The level applies to this tool's own
messages; libraries it uses only ever log warnings.

**Stopping:** Ctrl+C (or `docker stop`) finishes the current search, still reports what was found
so far, then exits. Press Ctrl+C a second time to exit immediately.

## Data

`/data/state.db` (SQLite) keeps price history (90 days), sent alerts and health state.
`/data/heartbeat` is updated while running; the Docker health check marks the container
unhealthy if it is older than 10 minutes.

Logs go to stdout (`docker compose logs`) and also to `/data/logs/cheap-flights.log`, so they
survive `docker compose down`. The file rotates at `logging.max_size_mb` (10 MB), keeping
`logging.backups` (5) old files as `cheap-flights.log.1` … `.5` — at most 60 MB. A relative
`logging.file` is placed under the data directory; `file: ""` turns the file off. `--dry-run` and
`--test-notify` never write to it. Secrets are masked in the file just like on stdout.

```bash
docker compose exec cheap-flights tail -f /data/logs/cheap-flights.log
docker compose cp cheap-flights:/data/logs ./logs
```

## Development

The host needs only Docker; `scripts/dev.sh` runs commands in a Python 3.12 + uv container.

```bash
./scripts/dev.sh uv sync
./scripts/dev.sh uv run pytest            # unit tests (no network)
./scripts/dev.sh uv run pytest -m live    # one real Google search
./scripts/dev.sh uv run ruff format . && ./scripts/dev.sh uv run ruff check .
./scripts/dev.sh uv run python scripts/capture_fixtures.py   # refresh parser fixtures
./scripts/dev.sh uv run python scripts/build_airports.py     # refresh airport data
```

To build the image yourself: `docker build -t ghcr.io/emreoztoprak/cheap-flight-tracker:latest .`
(`docker compose up` then uses it).

CI (`.github/workflows/ci.yml`) runs formatting, lint and tests on every push and pull request.
Pushes to `main` publish `latest`, `main` and `sha-…` images; pushing a `v1.2.3` tag publishes
`1.2.3`, `1.2` and `1`. Pull requests only check that the image builds.

Airport data: [OurAirports](https://ourairports.com/data/) (public domain).
