# pwListManager

One-way sync from [Trakt.tv](https://trakt.tv) to [Letterboxd](https://letterboxd.com), with iOS push notifications via [Pushover](https://pushover.net).

pwListManager watches your Trakt watchlist and watched history, and mirrors changes to Letterboxd — adding movies to your Letterboxd watchlist, marking them watched, and transferring ratings. It also migrates movies in your Trakt watchlist to custom lists to bypass the 100-item limit on free accounts. When a movie is marked watched, you get a Pushover notification with a deep link to rate it in the Letterboxd iOS app.

**This is one-way sync only: Trakt → Letterboxd.** It does not sync changes from Letterboxd back to Trakt. This fits a workflow where Trakt is the source of truth (e.g. tracked via a media server like Plex) and Letterboxd is the destination.

Why? This solves a problem in my personal media workflow. Trakt is widely supported by media apps for scrobbling and watchlist adding, but the new Trakt experience sucks, and I like Letterboxd for movies.

Designed to run on a Raspberry Pi 4 (ARM64) inside Docker. Set it up once and it syncs automatically.

## Features

- **Watchlist migration** — Moves movies from the default Trakt watchlist to custom lists, bypassing the 100-item limit on free Trakt accounts. Automatically overflows to additional lists when the primary fills up.
- **One-way sync to Letterboxd** — Adds watchlisted movies to your Letterboxd watchlist and marks watched movies as watched on Letterboxd. Does not sync back.
- **Rating transfer** — Copies Trakt ratings (1–10) to Letterboxd (0.5–5 stars)
- **iOS notifications** — Pushover alerts with Letterboxd deep links so you can rate movies immediately after watching
- **Pre-check before writes** — Checks if a movie is already on your Letterboxd watchlist/watched list before making API calls, preventing duplicate entries and overwriting manual ratings
- **Auto-sync scheduler** — Runs incremental sync on a configurable interval (default 15 minutes), only processing new items
- **Job queue** — Background queue with progress tracking, pause/resume, and ETA
- **Web UI** — Setup wizard, dashboard, task management, and log viewer — no YAML editing required
- **Cloudflare bypass** — Uses `curl_cffi` with Chrome TLS fingerprint to authenticate with Letterboxd
- **Error alerts** — Priority Pushover notifications when API calls fail or credentials expire

## Quick Start

### Docker (recommended)

```bash
git clone https://github.com/piersjones/pwListManager.git
cd pwListManager
docker compose up -d
```

Open `http://<your-pi-ip>:5050` and follow the setup wizard — enter your credentials, authenticate with Trakt, and you're running.

### Manual

```bash
git clone https://github.com/piersjones/pwListManager.git
cd pwListManager
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m src.web_server
```

Open `http://localhost:5050` and configure everything through the web UI.

## Setup

All configuration is done through the web UI at `http://<host>:5050/setup`.

### Trakt

1. Create a Trakt API app at [trakt.tv/oauth/applications](https://trakt.tv/oauth/applications)
2. Set the **Redirect URI** to `urn:ietf:wg:oauth:2.0:oob`
3. Enter your Client ID and Client Secret in the setup page
4. Click **Authenticate with Trakt** — the app uses Device Authorization flow, so you'll visit `trakt.tv/activate` and enter a code
5. Select which custom lists to sync from (or create new ones)

### Letterboxd

Letterboxd has no public write API. pwListManager logs in programmatically using `curl_cffi` (Chrome TLS fingerprint) to bypass Cloudflare protection. Enter your Letterboxd username and password in the setup page.

### Pushover

1. Create a Pushover app at [pushover.net/apps/build](https://pushover.net/apps/build)
2. Enter your User Key and API Token in the setup page
3. Use the **Test Notification** button to verify

## How It Works

### Sync Flow (Trakt → Letterboxd, one-way)

```
Trakt Watchlist
      │
      ▼
Custom Trakt List (bypasses 100-item free account limit)
      │
      ├──► Letterboxd Watchlist
      │
Trakt Watched History
      │
      ├──► Letterboxd Watched
      │         │
      │         ▼
      │    Pushover Notification
      │    (with Letterboxd deep link to rate)
      │
Trakt Ratings
      │
      └──► Letterboxd Ratings (1-10 → 0.5-5 stars)
```

### Auto-Sync

The scheduler runs on a configurable interval (default 15 minutes). On first run, it prompts you to either:
- **Run a full sync** — Processes all existing items
- **Skip** — Takes a snapshot of current state and only processes new items going forward

Subsequent runs are incremental — only movies added since the last sync are processed.

### Job Queue

All sync operations go through a background job queue with:
- Progress tracking and ETA
- Pause/resume/clear controls
- Most-recent-first processing for watched sync
- Automatic Letterboxd re-authentication on session expiry
- Rate limiting with exponential backoff (3–5s delays + jitter)

## Web UI

| Page | Description |
|------|-------------|
| **Dashboard** | Stats, recent sync history with progress-flow cards, auto-refresh every 5s |
| **Setup** | Configure Trakt, Letterboxd, and Pushover credentials; test connections; manage custom lists; auto-sync controls with countdown timer |
| **Tasks** | Trigger sync actions, view Trakt watchlist, manage job queue |
| **Logs** | Live log viewer |

## Project Structure

```
pwListManager/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── src/
│   ├── main.py              # CLI daemon (legacy)
│   ├── config.py            # YAML config parser
│   ├── database.py          # SQLite state tracking
│   ├── trakt_client.py      # Trakt OAuth & API
│   ├── letterboxd_client.py # Letterboxd web scraper
│   ├── notifier.py          # Pushover notifications
│   ├── rate_limiter.py      # Throttling & backoff
│   ├── job_queue.py         # Background job queue
│   ├── logger.py            # Logging configuration
│   ├── web_server.py        # Flask entry point
│   └── web/
│       ├── app.py           # Flask app & API routes
│       └── templates/        # Jinja2 HTML templates
└── tests/
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health` | GET | Health check (Docker) |
| `/api/status` | GET | Dashboard stats |
| `/api/recent` | GET | Recent sync actions |
| `/api/queue/status` | GET | Queue state & progress |
| `/api/queue/enqueue/watched` | POST | Enqueue watched sync |
| `/api/queue/enqueue/watchlist` | POST | Enqueue watchlist sync |
| `/api/queue/enqueue/migrate` | POST | Enqueue Trakt migration |
| `/api/queue/pause` | POST | Pause the queue |
| `/api/queue/resume` | POST | Resume the queue |
| `/api/queue/clear` | POST | Clear the queue |
| `/api/scheduler/status` | GET | Auto-sync status & countdown |
| `/api/scheduler/toggle` | POST | Enable/disable auto-sync |
| `/api/scheduler/trigger` | POST | Manually trigger auto-sync |
| `/api/scheduler/interval` | POST | Change sync interval |
| `/api/trakt/lists` | GET | List user's Trakt custom lists |
| `/api/trakt/create-list` | POST | Create a new Trakt list |
| `/api/test/trakt` | GET | Test Trakt connection |
| `/api/test/letterboxd` | GET | Test Letterboxd login |
| `/api/test/pushover` | GET | Test Pushover notification |
| `/api/settings/export` | GET | Export config as JSON |
| `/api/settings/import` | POST | Import config from JSON |
| `/api/save-config` | POST | Save config from web form |
| `/api/erase-history` | POST | Clear sync history |
| `/api/factory-reset` | POST | Full reset (config + data) |

## Docker

The Docker image is based on `python:3.11-slim` with `curl_cffi` system dependencies. Data persists via volumes:

| Volume | Purpose |
|--------|---------|
| `./data/` | SQLite database |
| `./logs/` | Application logs |

Configuration is managed through the web UI and stored in `config.yaml` inside the container. No manual config file editing needed.

The container includes a health check against `/api/health` and restarts automatically via `unless-stopped` policy.

## Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run tests
pytest tests/

# Run the web server locally
python -m src.web_server --debug

# Run with custom host/port
python -m src.web_server --host 0.0.0.0 --port 8080
```

## Troubleshooting

### Cloudflare blocking Letterboxd requests

pwListManager uses `curl_cffi` with Chrome TLS fingerprint to bypass Cloudflare. If this stops working (Cloudflare updates their detection), the fallback is a headless Playwright browser — not yet implemented.

### Trakt 420 Account Limit

Trakt free accounts are limited to 100 items per list and 5 custom lists. pwListManager handles this by:
- Automatically overflowing to additional lists when the primary fills up
- Skipping items that exceed the limit with detailed logging
- Creating new lists as needed (up to the 5-list limit)

### First-Time Sync Banner Reappearing

If you see the "First-Time Auto-Sync" banner after a server restart, it means the initial snapshots weren't saved (usually due to a Trakt API failure during setup). Click **Run Full Sync** or **Skip** to dismiss it — both options save snapshots so the banner won't return.

## License

This project is for personal use. Letterboxd's terms of service do not permit automated access to their platform. Use at your own risk.