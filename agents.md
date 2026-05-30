# agents.md - pwListManager Project Manual

This file serves as the master tracking, specification, and instruction document for the language model (agent) implementing the **pwListManager** synchronization tool. 

> [!IMPORTANT]
> **Instructions for the Agent:**
> 1. **Maintain this File**: You must update the **Backlog** and **Changelog** sections in this file at the end of every turn to reflect what was done, what is in progress, and what remains.
> 2. **Review Corrections**: Always check the "Corrections and Things Learned" section before starting any code changes.
> 3. **Follow Best Practices**: Strictly adhere to the architecture and best practices outlined in this document.

---

## 1. Project Objectives & Specifications

The objective of `pwListManager` is to create a lightweight, robust, Dockerized Python utility running on a Raspberry Pi 4 (ARM64) that automates one-way media watchlist sync from Trakt.tv to Letterboxd, with iOS push notifications via Pushover.

**This is one-way sync only: Trakt → Letterboxd.** It does not sync changes from Letterboxd back to Trakt.

### Core Requirements
1. **Trakt Watchlist Limit Bypass (100 Items)**:
   - Trakt free accounts limit default watchlists to 100 items.
   - To bypass this: scan the default Trakt watchlist periodically.
   - For each film found, add it to a custom Trakt list (configurable via web UI, defaulting to `movie-watchlist`), and then remove it from the default Trakt watchlist.
   - When the primary custom list fills up (100 items), overflow automatically to additional lists.
   - This migration must occur once during setup for existing films and automatically for all future additions.
2. **Watchlist Addition Sync**:
   - When a movie is added to the Trakt watchlist (and thus moved to the custom Trakt list by this tool), add it to the user's Letterboxd watchlist.
3. **Watched/History Sync**:
   - When a movie is marked as watched or complete on Trakt, mark the same movie as watched on Letterboxd.
4. **iOS Push Notifications with Deeplinks**:
   - When a movie is marked as watched on Letterboxd by this tool, send a Pushover notification to the user's iPhone.
   - The notification must contain a deep link using Letterboxd's `x-callback-url` protocol:
     `letterboxd://x-callback-url/log?name={encoded_movie_title}`
     This enables the user to quickly rate and review the movie inside the iOS Letterboxd app.
5. **Robust Error Alerts**:
   - Adequate debug logging (written to stdout and a rolling log file `logs/app.log`).
   - If an API call fails or credentials/cookies expire, send a priority=1 Pushover notification to alert the user immediately.

---

## 2. Technical Stack & Libraries

- **Language**: Python 3.11+
- **Environment**: Docker & Docker Compose running on Linux ARM64 (Raspberry Pi 4).
- **State Database**: SQLite (`data/sync_state.db`) to track syncing states (preventing duplicates and double notifications).
- **Trakt API Integration**:
  - Official Trakt API (OAuth via Device Authorization flow for authorization).
  - Use `requests` directly or a lightweight library (e.g. `trakt.py`), but prioritize official API calls to maximize future compatibility.
- **Letterboxd Integration**:
   - Since Letterboxd lacks a public write API, use **programmatic login via `curl_cffi`** (Chrome TLS impersonation). Standard `requests` and `cloudscraper` both get 403 from Cloudflare — only `curl_cffi` with `impersonate="chrome"` works.
   - Perform web requests mirroring the AJAX calls that Letterboxd's web client triggers:
     - Add to watchlist: `POST /film/{slug}/add-to-watchlist/`
     - Remove from watchlist: `POST /film/{slug}/remove-from-watchlist/`
     - Mark as watched: `POST /s/{uid}/watch/` with `watched=true` (uid format: `film:51896`)
     - Unmark watched: `POST /s/{uid}/watch/` with `watched=false`
     - The film UID is extracted from `production-data` JSON embedded in the page HTML.
   - Implement a modular `LetterboxdClient` interface so it can be easily updated or replaced with a headless Playwright browser if Cloudflare security changes.
- **Notification Engine**:
  - Pushover REST API (POSTing to `https://api.pushover.net/1/messages.json`).

---

## 3. Best Practices & Design Patterns

1. **State Isolation**: Keep all credentials, database files, and logs out of version control. Configuration is managed primarily through the web UI (Setup page) and stored in `config.yaml`. `.gitignore` excludes sensitive files.
2. **Graceful Failures**: Wrap Letterboxd and Trakt API calls in try-except blocks. In case of authentication failures (e.g., expired cookies/tokens), log a critical error and trigger a Pushover alert.
3. **Pebble-in-a-Shoe Prevention**:
   - Check if a movie is already synced using the SQLite DB before attempting a Letterboxd write.
   - Throttle requests to Letterboxd (e.g., 2-5 seconds pause between operations) to avoid IP throttling or account flags.
4. **ARM64 Docker Prep**: Use `python:3.11-slim` or similar lightweight images. Avoid heavy dependencies unless necessary. Ensure Playwright (if adopted) is installed using ARM64-compatible layers.

---

## 4. Git Project & Structure

- **Repository Directory Layout**:
  ```text
  pwListManager/
  ├── Dockerfile
  ├── docker-compose.yml
  ├── requirements.txt
  ├── config.yaml.example
  ├── agents.md             <-- This file
  ├── src/
  │   ├── __init__.py
  │   ├── main.py           <-- CLI daemon (legacy, web UI is primary)
  │   ├── web_server.py     <-- Web UI entry point
  │   ├── config.py         <-- Parses yaml configuration
  │   ├── database.py       <-- SQLite initialization & operations
  │   ├── trakt_client.py   <-- OAuth & list manipulation
  │   ├── trakt_migrate.py  <-- CLI migration script (legacy)
  │   ├── letterboxd_client.py <-- Scraping & session emulation
  │   ├── notifier.py       <-- Pushover delivery & deep linking
  │   ├── rate_limiter.py   <-- Throttling, backoff & 429 handling
  │   ├── job_queue.py       <-- Background job queue with progress/ETA
  │   └── logger.py         <-- Standard logging configuration
  ├── src/web/
  │   ├── __init__.py
  │   ├── app.py            <-- Flask web server & API routes
  │   ├── templates/        <-- Jinja2 HTML templates
  │   │   ├── base.html
  │   │   ├── index.html
  │   │   ├── setup.html
  │   │   ├── trakt_auth.html
  │   │   ├── onboarding.html
  │   │   ├── tasks.html
  │   │   └── logs.html
  │   └── static/
  │       └── style.css
  └── tests/
      ├── __init__.py
      ├── test_trakt.py
      ├── test_letterboxd.py
      ├── test_notifier.py
      ├── test_database.py
      └── test_main.py
  ```

---

## 5. Corrections and Things Learned

*(This section is to be updated dynamically during development when the user provides corrections or when the model makes system-level discoveries.)*

- **Initial Setup**: Corrections and learnings are tracked below as they are discovered during development.
- **SQLite in-memory testing**: The original `Database` class used `get_connection()` to create a new connection per call, which caused `:memory:` databases to lose state between operations. Refactored to use a persistent connection (`_get_conn()`) so in-memory tests work correctly.
- **Letterboxd authentication risk**: Letterboxd has no public write API. The initial approach used cookie injection, which was blocked by Cloudflare (403). The current approach uses:
  1. **Programmatic login** via `curl_cffi` (Chrome TLS impersonation) POST to `letterboxd.com/user/login.do` with username/password — bypasses Cloudflare and creates fresh session cookies each run.
  2. **Cloudflare bypass**: Standard `requests` and `cloudscraper` both get 403 from Cloudflare. Only `curl_cffi` with `impersonate="chrome"` works.
  3. **API endpoints**: Letterboxd's modern React-based UI no longer includes `data-action` attributes in HTML. The correct endpoints are:
     - Add to watchlist: `POST /film/{slug}/add-to-watchlist/`
     - Remove from watchlist: `POST /film/{slug}/remove-from-watchlist/`
     - Mark as watched: `POST /s/{uid}/watch/` with `watched=true` (uid format: `film:51896`)
     - Unmark watched: `POST /s/{uid}/watch/` with `watched=false`
     - The film UID is extracted from `production-data` JSON embedded in the page HTML.
  4. If Cloudflare blocks `curl_cffi` in the future, the fallback is Playwright headless browser.
- **Rate limiting and anti-detection**: Implemented in `src/rate_limiter.py`:
  - All Letterboxd requests throttled with 3-5s delays + random jitter to mimic human pacing
  - Automatic session re-authentication on 401/403 responses
  - 429 (rate limit) handling: reads `Retry-After` header and sleeps accordingly
  - Exponential backoff on transient failures (5s → 10s → 20s → 40s → 80s, max 5 retries)
  - Trakt API: 1s delays between requests, 429/Retry-After handling
  - `must.be.logged.in` error codes trigger automatic re-login
- **Trakt 420 Account Limit**: Trakt free accounts are limited to 100 items per list (watchlist, collection, custom lists). The 420 status code means "Account Limit Exceeded". When adding items to a custom list that would exceed 100, Trakt returns 420 with a response body like "The string did not match the expected pattern" or similar validation errors. The `add_to_custom_list` and `remove_from_watchlist` methods now handle 420 by falling back to single-item adds and skipping items that hit the limit, with detailed logging of the Trakt response body. Additionally, Trakt free accounts are limited to **5 custom lists** — creating more lists returns 420. The `create_custom_list_with_name` method now catches 420 and raises a clear error message explaining the limit.
- **JobQueue._run() indentation bug**: The `_run()` method was outside the `JobQueue` class (indent 0 instead of 4), causing `AttributeError: 'JobQueue' object has no attribute '_run'` and 500 errors on all queue operations. Fixed by moving `_run` back inside the class.
- **Migration data loss bug**: The old batch migration removed ALL movies from the default watchlist, even those that failed to add to the custom list (420 limit). This caused 87 movies to be lost — not on either list. Fixed by refactoring migration into individual queue items where each movie is only removed from the watchlist if it was successfully added to the custom list.
- **Overflow custom list support**: Trakt free accounts are limited to 100 items per list. The app now supports multiple custom lists via `trakt_custom_list_names` config (comma-separated). When the first list fills up, `add_single_to_custom_list` automatically tries the next list, creating new ones as needed (e.g. `movie-watchlist-2`, `movie-watchlist-3`).
- **Custom list config consolidation**: Replaced separate `custom_list_name` and `custom_list_names` config fields with a single `custom_list_names` comma-separated field. The first name is the primary list, subsequent names are overflow lists. Users specify these explicitly — no auto-generation. The `custom_list_name` property still works as a fallback, returning the first item from `custom_list_names`.
- **Watchlist sync includes custom lists**: The `_expand_and_enqueue_watchlist` method now fetches movies from all configured custom lists in addition to the default Trakt watchlist. This is essential because movies that have been migrated to custom lists are no longer on the default watchlist, so they would otherwise be missed by the sync.
- **Queue pause/resume semantics**: `get_status()["running"]` should return `self._running` (thread alive), NOT `self._running and not self._paused`. The old formula meant paused queues appeared "not running", hiding the resume button. Now `running` means "thread alive" and `paused` means "paused", so the UI can show three distinct states: actively processing, paused, and idle. `resume()` must call `start()` if the worker thread has exited but items remain — otherwise items added while paused would never be processed. Auto-sync must skip when paused (not just when running) to avoid adding duplicate expansion jobs. Enqueue endpoints should allow adding items while paused (returning "queued, resume to process") and only block when actively processing.
- **Letterboxd pre-check before writes**: Before adding a film to the Letterboxd watchlist or marking it as watched, the app now checks the film's current status on Letterboxd by fetching the film page and parsing HTML for watchlist/watched indicators. This prevents: (1) overwriting watched dates that the user set manually, (2) overwriting ratings, (3) unnecessary API calls that waste rate limit budget. The `get_film_status(slug)` method extracts UID, watchlist status, and watched status from the film page HTML. If the check fails (e.g., HTML parsing can't determine status), the app falls back to making the write request. The `add_to_watchlist`, `mark_watched`, and `rate_film` methods now accept optional pre-resolved `slug` and `uid` params to avoid duplicate requests when the status check already fetched the film page.
- **Onboarding sync page**: After Trakt auth completes, the user is redirected to `/onboarding` which prompts them to run their first syncs (Migrate Trakt Watchlist, Sync Watchlist to Letterboxd, Sync Watched to Letterboxd). All sync actions from this page pass `suppress_notifications=true` so the user isn't flooded with Pushover alerts during the initial bulk sync. The `suppress_notifications` flag propagates from expansion jobs to individual queue items. When set, `mark_watched` jobs skip the Pushover notification. Auto-sync and manual syncs from the Tasks page do NOT suppress notifications — only onboarding syncs do.
- **Queue type labels**: Renamed queue item type badges from cryptic abbreviations (LB+WL, Migrate, etc.) to descriptive labels: Letterboxd WL, Letterboxd Watched, Trakt List, Trakt Sync, Watched Sync, Watchlist Sync.
- **ConfigError crash after factory reset**: `Config` properties raise `ConfigError` when values are missing. After factory reset, `get_config()` returned a Config object with empty data, and any route accessing `config.trakt_client_id` etc. would crash with a 500 error. Fixed by: (1) `get_config()` returns Config object regardless of completeness (no validation), (2) `_safe_config_dict()` extracts values with try/except `ConfigError` for template rendering, (3) `check_setup_complete()` validates only Trakt credentials (not Letterboxd/Pushover), (4) all routes that access config properties have try/except `ConfigError` guards.
- **Setup flow independence**: Trakt authentication should work independently of Letterboxd/Pushover configuration. The `check_setup_complete()` function now only requires Trakt credentials + auth to consider setup complete. Test connection buttons and Trakt auth link are always visible on the setup page. Trakt test validates client_id without requiring OAuth (interprets 401 as "valid client_id, not yet authenticated"). API test routes are in `allowed_routes` so they work during setup. **Updated**: `check_setup_complete()` now requires **all three** services (Trakt creds + auth + Letterboxd creds + Pushover creds) before considering setup complete. This keeps nav hidden until fully configured. `redirect_if_not_setup()` added a "services" missing case redirecting to `/setup`.
- **Authenticate button must save form first**: The "Authenticate with Trakt" button was a plain `<a href="/trakt-auth">` link — filling in credentials and clicking it navigated to `/trakt-auth` without saving the form, which then errored with "Please enter your Trakt Client ID first". Fix: convert to `<button>` with `saveAndAuth()` JS that POSTs form data to `/api/save-config` via fetch, then redirects to `/trakt-auth` on success. Button order: Authenticate before Test Connection.
- **Trakt auth poll JSON parsing crash**: The `/trakt-auth/poll` endpoint called `r.json()` unconditionally. If Trakt returned a non-JSON response (e.g., HTML error page, empty body), `r.json()` raised `json.JSONDecodeError("Expecting value: line 1 column 1 (char 0)")` which was caught by the generic `except Exception` and displayed raw to the user. Fix: wrap `r.json()` in try/except `ValueError` and return a user-friendly message including the HTTP status code and noting possible network/invalid credentials issues.
- **List selector disabled until authed**: Moved auth buttons (Authenticate + Test Connection) above the custom list selector on the Trakt section of setup page. Added `.disabled-section` CSS class (opacity: 0.5, pointer-events: none, user-select: none) applied to the list selector when not authenticated. Added hint message "Authenticate with Trakt above to manage custom lists". JS guards prevent `addListSlot`, `removeLastListSlot`, `openCreateListModal`, and `fetchTraktLists` from operating when not authed.
- **Trakt auth poll empty body handling**: Trakt's `/oauth/device/token` endpoint returns HTTP 400 with an **empty body** (no JSON) when the user hasn't authorized yet. The original code called `r.json()` unconditionally which raised `json.JSONDecodeError` on the empty body, and the old fix treated `ValueError` as a terminal error (`status: error`). This caused polling to stop immediately on the first attempt — the user couldn't complete auth even after visiting trakt.tv/activate. Fix: when `r.json()` fails, set `data = {}` (empty dict) instead of returning an error. Then for HTTP 400 with empty body, return `{"status": "pending"}` so the JS keeps polling. The JS was also updated to: (1) keep polling on unexpected statuses instead of stopping, (2) add timeout after 120 polls (~10min), (3) count consecutive network errors and only stop after 15 failures.
- **Post-auth redirect to setup**: After Trakt auth completes, the user is now redirected to `/setup` (not `/onboarding`) with a flash message guiding them to configure Letterboxd and Pushover. `check_setup_complete()` now requires all three services (Trakt creds + auth + Letterboxd creds + Pushover creds) to consider setup complete. This keeps the nav hidden until all services are configured. `redirect_if_not_setup()` handles the new "services" missing case by redirecting to `/setup`.
- **Factory reset blocked by redirect_if_not_setup**: `/api/factory-reset` and `/api/erase-history` were not in `allowed_routes`. When setup was incomplete (missing Letterboxd/Pushover), `redirect_if_not_setup` would intercept these requests and redirect to `/setup` before the handler could run. The fetch would follow the redirect, get HTML instead of JSON, and fail. Fix: added both endpoints to `allowed_routes`.
- **Test buttons must save form first**: All test/action buttons on setup page (Test Connection for Trakt, Letterboxd, Pushover; Authenticate with Trakt) read config from disk. If user fills in fields and clicks a button without clicking "Save Configuration" first, the saved config is stale/empty. Fix: created shared `saveForm()` helper that POSTs to `/api/save-config`. All 4 buttons call `await saveForm()` at the start of their handler before making their API call.

---

## 6. Backlog

### Stage 1: Repo Setup & Config
- [x] Initialize repository structure and `.gitignore`.
- [x] Create `requirements.txt` with initial dependencies.
- [x] Write `src/logger.py` for standard stdout/file logging.
- [x] Create `config.yaml.example` and `src/config.py` parser.

### Stage 2: Trakt API & Watchlist Migration
- [x] Set up Trakt client OAuth application logic with Device Login flow.
- [x] Implement function to retrieve the default watchlist.
- [x] Implement migration logic: Add items to custom list -> remove items from default watchlist.
- [x] Write CLI test commands to verify Trakt watchlist clearing works.

### Stage 3: Letterboxd Authenticated Scraper
- [x] Implement `LetterboxdClient` using `requests.Session` populated by custom cookies.
- [x] Extract CSRF token `com.xk72.webparts.csrf`.
- [x] Implement watchlist toggle method.
- [x] Implement movie logging/watched method.
- [x] Verify functionality against a dummy/test Letterboxd account.

### Stage 4: Pushover & iOS Deeplinks
- [x] Implement `Notifier` client targeting Pushover API.
- [x] Create rate/review notification builder containing `letterboxd://x-callback-url/log?name={encoded_title}`.
- [x] Create system error notification alerts.

### Stage 5: State Machine & Sync Loop
- [x] Implement SQLite DB helper (`database.py`) with `sync_state` schemas.
- [x] Write main sync orchestrator integrating Trakt, Letterboxd, and Notifier.
- [x] Implement continuous scheduler daemon loop in `src/main.py`.

### Stage 6: Web UI & Rate Limiting
- [x] Implement rate limiter module (`rate_limiter.py`) with throttling, jitter, exponential backoff, and 429 handling.
- [x] Add automatic re-authentication on 401/403/must.be.logged.in errors in Letterboxd client.
- [x] Add Trakt 429/Retry-After handling.
- [x] Build Flask web UI with dashboard, setup, Trakt auth, logs, and manual actions.
- [x] Add `remove_from_watchlist` and `unmark_watched` methods to Letterboxd client.
- [x] Redesign web UI: Dashboard with descriptive stat cards, History with progress-flow cards, Tasks page with Trakt timeline and sync actions, first-time setup flow redirect.
- [x] Enhanced database schema with `letterboxd_slug` and `trakt_slug` columns for rich movie linking.
- [x] Fixed Trakt auth badge: proper token validation including expiry/refresh check.
- [x] Implemented job queue system (`job_queue.py`) with preview, progress, pause/resume/clear, and ETA.
- [x] Queue preview: sync actions with >10 items show a review modal before starting.
- [x] Queue processes items most-recent-first for watched sync.
- [x] Added `get_ratings()` to TraktClient for rating transfer from Trakt (1-10 scale).
- [x] Added `rate_film()` to LetterboxdClient (converts Trakt 1-10 to LB 0.5-5 stars).
- [x] Trakt movie links now use `ids.slug` from API data instead of `tmdb-{id}` format.
- [x] Suppressed garbled SSL request noise in werkzeug server log.
- [x] Fixed web server to initialize file logger so logs page works.
- [x] Fixed queue hang: removed synchronous Trakt API calls from Tasks page (now loaded via AJAX).
- [x] Made queue robust: start() returns False if queue is empty, _save_queue uses atomic write, processed list capped at 500.
- [x] Trakt watchlist table in Tasks now loaded on-demand via button click instead of on page load.
- [x] Fixed queue hang: removed synchronous Trakt API calls from Flask routes. All sync operations now return immediately and process in background thread. Expansion jobs (sync_watched_all, sync_watchlist_all) fetch from Trakt inside the queue worker, not the HTTP handler.
- [x] Flask server now uses `threaded=True` for concurrent request handling during queue processing.
- [x] Fixed queue deadlock: `threading.Lock()` → `threading.RLock()` — `_save_queue()` was called inside `with self._lock:` blocks, causing a non-reentrant deadlock that froze the entire server.
- [x] Queue now reuses a single Letterboxd client session across all items (was creating a new login per item).
- [x] Queue worker re-logins to Letterboxd automatically on session expiry (401/403/must.be.logged.in).

### Stage 7: Docker Integration
- [x] Create `Dockerfile` and `docker-compose.yml`.
- [x] Add `/api/health` endpoint for Docker healthcheck.
- [x] Create `.dockerignore` and update `.gitignore`.
- [ ] Verify builds run cleanly on ARM64 architectures.

### Stage 8: SIMKL Integration (Backlog)
- [ ] Research SIMKL API and authentication flow.
- [ ] Add SIMKL client module with watchlist and watched sync.
- [ ] Add SIMKL configuration to web UI setup.
- [ ] Add SIMKL sync to orchestrator loop.

---

## 7. Changelog

*(Track completed tasks here with dates)*

- **2026-05-29**: Project planned and initialized. Created `agents.md` skeleton and implementation plans.
- **2026-05-29**: Setup repository structure, configuration parser, requirements, and rolling logger (Stage 1 complete).
- **2026-05-29**: Implemented Trakt client API with Device Authorization, custom list creation, items migration, and a CLI test script (Stage 2 complete).
- **2026-05-29**: Implemented Letterboxd client web scraper mimicking AJAX requests, including resolving TMDb IDs to Letterboxd slugs, watchlisting films, and marking them as watched (Stage 3 complete).
- **2026-05-29**: Implemented Pushover notification client, containing rate/review iOS app deep-linking using x-callback-url and priority=1 error alerts (Stage 4 complete).
- **2026-05-29**: Implemented SQLite database helper with persistent connection pattern, main sync orchestrator (`main.py`) integrating Trakt watchlist migration, Letterboxd watchlist/watched sync, and Pushover notifications, plus scheduled daemon loop (Stage 5 complete).
- **2026-05-29**: Refactored Letterboxd client from cookie injection to programmatic login via `curl_cffi` (Chrome TLS impersonation). Discovered Cloudflare blocks `requests` and `cloudscraper` — only `curl_cffi` works. Identified correct API endpoints: `add-to-watchlist/`, `remove-from-watchlist/`, and `/s/{uid}/watch/` for watched status. All live tests passing: Trakt auth, watchlist fetch (336 movies), watched fetch (770 movies), Pushover notifications, Letterboxd login, watchlist add/remove, mark watched/unwatched.
- **2026-05-29**: Implemented rate limiter (`rate_limiter.py`) with throttling (3-5s + jitter), exponential backoff on failures, 429 Retry-After handling, automatic Letterboxd re-authentication on 401/403. Built Flask web UI with dashboard, setup/config, Trakt device auth flow, logs viewer, and manual action endpoints. Added `remove_from_watchlist` and `unmark_watched` methods to Letterboxd client (Stage 6 complete).
- **2026-05-29**: Redesigned web UI: Dashboard stat cards now have descriptive labels. Added History section with progress-flow cards (Trakt Watchlist → Letterboxd Watchlist → Watched → Notified). Added Tasks page with sync actions, quick actions, and Trakt watchlist timeline with per-movie action buttons. Added first-time setup flow redirect (config → Trakt auth → Dashboard). Fixed Trakt auth badge to properly validate token expiry and refresh capability. Added `letterboxd_slug` to database for Letterboxd movie links. Added `resolve-slug` API endpoint.
- **2026-05-29**: Implemented job queue system (`job_queue.py`) with preview, progress, pause/resume/clear, and ETA. Queue processes items most-recent-first for watched sync. Sync actions with >10 items show a review modal before starting. Added `get_ratings()` to TraktClient for rating transfer. Added `rate_film()` to LetterboxdClient (converts Trakt 1-10 to LB 0.5-5 stars). Trakt movie links now use `ids.slug` from API data. Suppressed garbled SSL request noise in werkzeug. Fixed web server to initialize file logger so logs page works.
- **2026-05-29**: Redesigned web UI: Dashboard stat cards now have descriptive labels. Added History section with progress-flow cards (Trakt Watchlist → Letterboxd Watchlist → Watched → Notified). Added Tasks page with sync actions, quick actions, and Trakt watchlist timeline with per-movie action buttons. Added first-time setup flow redirect (config → Trakt auth → Dashboard). Fixed Trakt auth badge to properly validate token expiry and refresh capability. Added `letterboxd_slug` to database for Letterboxd movie links. Added `resolve-slug` API endpoint.
- **2026-05-30**: Fixed Trakt 420 (Account Limit Exceeded) error during watchlist migration. Root cause: Trakt free accounts are limited to 100 items per list. The `add_to_custom_list` method had a bug where the 420 handler's `else` branch called `raise_for_status()` on non-429/non-420 status codes, but the logic flow after a 429 retry could cause the 420 check to be bypassed. Fixed by restructuring the status code handling with explicit `if/elif/else` branches. Added 420 fallback to single-item adds with skip tracking for items that exceed the limit. Added response body logging (`r.text[:300]`) to all Trakt API error responses for better debugging. Updated `remove_from_watchlist` with the same 420 handling. Updated `get_custom_list_id` and `create_custom_list` with debug logging for slug resolution. Updated migration job in `job_queue.py` to gracefully handle partial failures (continue with removal even if some adds fail).
- **2026-05-30**: Fixed critical bug: `JobQueue._run()` method was outside the class (indent 0 instead of 4), causing `AttributeError` on `self._run` and 500 errors on all queue operations. Moved `_run` back inside the `JobQueue` class. Added try/except error handling to all Flask queue API endpoints (`/api/queue/enqueue/migrate`, `/api/queue/enqueue/watched`, `/api/queue/enqueue/watchlist`) to surface errors properly instead of returning bare 500s. Added detailed `[migrate]` attribution logging to the migration job handler. Added `[Trakt]` attribution logging to `add_to_custom_list` and `get_watchlist_movies` for request/response tracing.
- **2026-05-30**: Refactored watchlist migration from monolithic batch job to individual queue items for full progress visibility. Each movie is now a separate `migrate_movie` queue item showing progress (e.g. "42/336 done"). Added `add_single_to_custom_list` and `remove_single_from_watchlist` methods to TraktClient for per-movie operations. **Critical fix**: movies that fail to add to the custom list (420 limit) are no longer removed from the default watchlist — they stay on the watchlist safely. Previously, all 336 movies were removed from the watchlist even if 87 failed to add to the custom list, causing data loss.
- **2026-05-30**: Added overflow custom list support. When the first custom list hits Trakt's 100-item limit, the app automatically creates and uses a second list (e.g. `movie-watchlist-2`), then `movie-watchlist-3`, etc. Added `trakt_custom_list_names` config field (comma-separated) and `get_list_with_room()` method that finds a list with available slots. `add_single_to_custom_list` now automatically falls back to overflow lists on 420 errors. Added `create_custom_list_with_name()` for creating lists with specific names.
- **2026-05-30**: Fixed history cards to show actual progress state — step 2 ("On Letterboxd") now uses `letterboxd_slug` instead of `watchlist_synced`, so it correctly shows as pending when a movie was only migrated to Trakt's custom list but not yet added to Letterboxd. Added action buttons to history cards ("→ LB Watchlist", "Mark Watched") for movies that need further sync. Added queue items list to Tasks page showing pending, current, and recently completed items with type badges. Added Danger Zone to Setup page with "Erase History" and "Factory Reset" (double confirmation) options. Added `erase_all()` to Database and `/api/erase-history`, `/api/factory-reset` API endpoints.
- **2026-05-30**: Redesigned Setup page with collapsible sections (Authentication & Connection Tests, App Settings, Danger Zone). Combined Trakt credentials, auth, list config, and connection tests into one section. Added connection test buttons for all three services (Trakt, Letterboxd, Pushover) with inline success/error feedback. Added "Check Lists" button to show Trakt custom list status (name, slug, item count, full/active/not created). Added "Create Missing Lists" button to create any configured lists that don't exist yet on Trakt. Replaced separate `custom_list_name` and `custom_list_names` config fields with a single `custom_list_names` comma-separated field (first is primary, rest are overflow). Removed old `/test-pushover` and `/test-letterboxd` routes in favor of `/api/test/*` endpoints.
- **2026-05-30**: Redesigned list selector UX: replaced comma-separated text input with dynamic dropdown selectors that populate from the Trakt API. Each slot (Primary, Overflow 1, etc.) shows existing Trakt lists with item counts, plus a "Create new list..." option that opens a modal to create a list on the spot. Added `/api/trakt/lists` endpoint (returns all user's custom lists) and `/api/trakt/create-list` endpoint (creates a single list by name). Updated watchlist sync (`_expand_and_enqueue_watchlist`) to also fetch movies from all configured custom lists, not just the default watchlist — this ensures movies already migrated to custom lists are still synced to Letterboxd. Updated Tasks page description to reflect this.
- **2026-05-30**: Improved Pushover watched notification deep linking. When a Letterboxd slug is available (resolved during sync), the notification URL now uses `https://letterboxd.com/film/{slug}/` (Universal Link) which opens directly in the Letterboxd app on iOS to the exact film page. Falls back to `letterboxd://x-callback-url/log?name={title}` when no slug is available. Updated `send_movie_watched_notification` to accept an optional `letterboxd_slug` parameter.
- **2026-05-30**: Added automatic sync scheduler as a background thread in the web server. Runs on the configured `sync_interval_minutes` (default 15 min). Enqueues watchlist + watched sync jobs if the queue is idle. Added `/api/scheduler/status`, `/api/scheduler/toggle`, and `/api/scheduler/interval` endpoints. Setup page shows auto-sync toggle with enable/disable and last sync time.
- **2026-05-30**: Added password/secret reveal toggle (eye icon 👁) on all sensitive fields (Trakt Client Secret, Letterboxd Password, Pushover API Token). Clicking toggles between `type="password"` and `type="text"`.
- **2026-05-30**: Added export/import settings feature. Export downloads config as JSON file. Import reads JSON and overwrites `config.yaml`. Warning displayed that exported settings include all credentials and should not be shared. Added `/api/settings/export` and `/api/settings/import` endpoints.
- **2026-05-30**: Fixed "Fetching watched list from Trakt..." stuck in queue status. Expansion jobs (`sync_watched_all`, `sync_watchlist_all`, `migrate_trakt_watchlist`) now clear `_current_job` after expansion completes, so the status no longer shows stale "Fetching..." messages.
- **2026-05-30**: Onboarding flow already existed via `redirect_if_not_setup` before_request hook — redirects to `/setup` if no config, and to `/trakt-auth` if config exists but Trakt not authenticated. Setup page title changes to "Welcome to pwListManager" when no config exists.
- **2026-05-30**: Auto-sync now processes only NEW items (incremental mode). Added `list_snapshots` table to SQLite database that stores the set of trakt_ids seen after each sync cycle. When auto-sync runs, it diffs the current list against the snapshot to find only newly-added items. Manual syncs (triggered from the UI) still process all items. The `enqueue_migrate_trakt_watchlist()`, `enqueue_watchlist_sync_all()`, and `enqueue_watched_sync_all()` methods now accept an `incremental=False` parameter. Auto-sync passes `incremental=True`, manual syncs use the default `incremental=False`.
- **2026-05-30**: Fixed queue pause/resume UX. Previously, `get_status()["running"]` returned `self._running and not self._paused`, which meant the resume button was never visible (paused state showed as "not running"). Changed `running` to just `self._running` (thread alive) so the UI can distinguish between actively processing, paused, and idle. Updated Tasks page to show Resume button when paused (whether thread is alive or exited). `resume()` now calls `start()` if the worker thread has exited but items remain in the queue. Auto-sync scheduler now skips when queue is paused (not just when running). Enqueue endpoints now allow adding items while paused (returning "Job queued — resume to process") and only block when the queue is actively processing.
- **2026-05-30**: Added Letterboxd pre-check before writes. Before adding a film to the Letterboxd watchlist or marking it as watched, the app now checks the film's current status on Letterboxd by fetching the film page and parsing HTML for watchlist/watched indicators. This prevents overwriting watched dates, overwriting ratings, and unnecessary API calls. Added `get_film_status(slug)` method to `LetterboxdClient` that extracts UID, watchlist status, and watched status from the film page HTML. If the check fails (e.g., HTML parsing can't determine status), the app falls back to making the write request. Updated `add_to_watchlist`, `mark_watched`, and `rate_film` to accept optional pre-resolved `slug` and `uid` params to avoid duplicate requests when the status check already fetched the film page.
- **2026-05-30**: Added onboarding sync page at `/onboarding`. After Trakt auth completes, user is redirected to this page which prompts them to run their first syncs (Migrate Trakt Watchlist, Sync Watchlist to Letterboxd, Sync Watched to Letterboxd). All sync actions from onboarding pass `suppress_notifications=true` so the user isn't flooded with Pushover alerts during initial bulk sync. The `suppress_notifications` flag propagates from expansion jobs to individual queue items. When set, `mark_watched` jobs skip the Pushover notification. Auto-sync and manual syncs from the Tasks page do NOT suppress notifications — only onboarding syncs do.
- **2026-05-30**: Renamed queue item type badges from cryptic abbreviations (LB+WL, Migrate, etc.) to descriptive labels: Letterboxd WL, Letterboxd Watched, Trakt List, Trakt Sync, Watched Sync, Watchlist Sync.
- **2026-05-30**: Fixed factory reset crash. After factory reset, `get_config()` returned a Config object with empty data, and accessing `config.trakt_client_id` raised `ConfigError` which crashed the before_request hook. Fixed by making `get_config()` return Config object regardless of completeness (no validation), and adding `_safe_config_dict()` helper that extracts values with try/except `ConfigError` for template rendering. `check_setup_complete()` now validates only Trakt credentials (not Letterboxd/Pushover), so Trakt auth works independently. Added try/except `ConfigError` to all routes that access config properties.
- **2026-05-30**: Improved setup flow UX. Test connection buttons (Trakt, Letterboxd, Pushover) and Trakt auth link are now always visible on the setup page, even before config is saved. Trakt test connection now validates client_id without requiring OAuth authentication (hits `/users/me/lists` and interprets 401 as "valid client_id, not yet authenticated"). After saving config, the page redirects back to `/setup` (not `/trakt-auth`) with a flash message guiding the user to test connections and authenticate. Navigation bar is hidden when setup is not complete (no config or no Trakt auth), so users only see the setup page until fully configured. API test routes added to `allowed_routes` so they work during setup.
- **2026-05-30**: Fixed "Authenticate with Trakt" button UX. Two issues: (1) Button order — "Authenticate with Trakt" now comes before "Test Connection". (2) Auth button now saves the form first — was a plain `<a href="/trakt-auth">` link that didn't save the config, so filling in credentials then clicking auth gave "Please enter your Trakt Client ID first". Fixed by adding `/api/save-config` endpoint (accepts form-encoded/JSON config), converting the auth link to a `<button>` with `saveAndAuth()` JS that POSTs form data to `/api/save-config` via fetch and redirects to `/trakt-auth` on success. Endpoint added to `allowed_routes`.
- **2026-05-30**: Fixed Trakt auth poll — empty body + 400 from Trakt is not an error. Trakt's `/oauth/device/token` endpoint returns HTTP 400 with an empty body (no JSON) when the user hasn't authorized yet. The old code called `r.json()` unconditionally which crashed, and the intermediate fix treated it as `status: error` which stopped polling. Fix: set `data = {}` when JSON parsing fails, and return `{"status": "pending"}` for any HTTP 400 with empty body. JS now keeps polling on unexpected statuses instead of stopping, adds a timeout after 120 polls (~10min), and only stops after 15 consecutive network errors. Moved auth buttons above list selector on setup page. Added `.disabled-section` CSS for unauthenticated state. List selector, overflow buttons, create list modal, and fetchTraktLists all guarded by JS checks when not authed.
- **2026-05-30**: Post-auth redirect to `/setup`. After Trakt auth completes, user is auto-redirected to `/setup` (not `/onboarding`) with flash message guiding them to configure Letterboxd and Pushover. `check_setup_complete()` now requires all three services (Trakt creds + auth + Letterboxd creds + Pushover creds) to consider setup complete. Nav stays hidden until fully configured. `redirect_if_not_setup()` handles new "services" missing case by redirecting to `/setup`.
- **2026-05-30**: Fixed factory reset blocked by `redirect_if_not_setup`. `/api/factory-reset` and `/api/erase-history` were not in `allowed_routes` — when setup was incomplete, `redirect_if_not_setup` intercepted them and redirected before the handler could run. Fetch followed redirect, got HTML instead of JSON, and failed. Added both endpoints to `allowed_routes`.
- **2026-05-30**: Fixed test buttons not saving form first. All test/action buttons on setup page (Test Connection for Trakt, Letterboxd, Pushover; Authenticate with Trakt) read config from disk. If user filled in fields and clicked a button without clicking "Save Configuration" first, the saved config was stale/empty — giving errors like "Letterboxd username not configured" even though the fields had values. Fix: created shared `saveForm()` helper that POSTs to `/api/save-config`. All 4 buttons call `await saveForm()` at the start of their handler before making their API call.
- **2026-05-30**: Fixed auto-sync firing full sync on first run (causing "readonly database" errors). Auto-sync now checks if list snapshots exist before starting. If no snapshots exist (first-time setup), the scheduler sets `initial_sync_pending = True` and waits for the user to either (a) accept a full sync or (b) skip and take snapshots for incremental-only mode. Added `snapshots_exist()` to Database, `has_initial_snapshots()` and `take_initial_snapshots()` to JobQueue. Added `/api/scheduler/initial-sync/accept` and `/api/scheduler/initial-sync/skip` endpoints. Tasks page shows a prominent banner with two options. Added WAL mode (`PRAGMA journal_mode=WAL`) to Database for safer concurrent write access. `/api/scheduler/status` now actively checks snapshots live instead of relying on cached scheduler state, so the initial sync prompt appears immediately on the Tasks page (no 30s delay).
- **2026-05-30**: Queued watchlist sync deduplication. `_expand_and_enqueue_watchlist` now fetches the Trakt watched list and skips movies that the user has already watched. Since Letterboxd auto-removes from the watchlist when marking as watched, adding watched movies to the watchlist is redundant and wastes API calls. Skipped movies will instead be processed via the watched sync path.
- **2026-05-30**: Fixed setup page redirect and flash messages. Setup POST handler now does smart redirects based on actual completion state: redirects to Tasks when all services configured, to Trakt auth when only creds saved, or back to setup for remaining services. Removed redundant "authenticate with Trakt" flash when Trakt already authed. Trakt auth poll also redirects to Tasks if all services are already configured. Cleaned up misleading "Trakt is set up! Now configure..." flash message.
- **2026-05-30**: Dashboard now auto-refreshes stats and history every 5 seconds. Added `/api/recent` endpoint returning recent sync actions as JSON. History cards now rendered client-side from the API response so they update in real-time.
- **2026-05-30**: Manual sync buttons on Tasks page (Sync Watchlist, Sync Watched, Migrate Watchlist) now pass `suppress_notifications: true` so users aren't flooded with Pushover alerts when manually triggering syncs.
- **2026-05-30**: Queue ETA now based on rolling average of recent item processing times (last 30 items) instead of overall rate since start. Eliminates wild ETA fluctuations that occurred early in large syncs. Each completed item updates the running average, so ETA settles quickly and reflects actual conditions (throttling, network speed, API latency).
- **2026-05-30**: Fixed 415 error on queue enqueue endpoints — changed `request.json` to `request.get_json(silent=True)` so missing/wrong Content-Type doesn't raise errors. Fixed initial-sync banner reappearing after full sync — `accept` endpoint now saves snapshots immediately (was only saving during `incremental=True` expansions), so the banner doesn't come back on server restart.
- **2026-05-30**: Dockerized the app. Created `Dockerfile` (python:3.11-slim, curl_cffi deps, healthcheck), `docker-compose.yml` (volumes for config/data/logs, port 5050, restart policy), `.dockerignore`. Added `/api/health` endpoint for Docker healthcheck. Updated `.gitignore` with IDE, OS, and OpenCode exclusions.
- **2026-05-30**: Fixed first-time sync banner reappearing on server restart. Root cause: `take_initial_snapshots` makes live Trakt API calls that can fail. When it fails, no snapshots are saved, so on restart `snapshots_exist()` returns False and the banner shows again. Fix: `save_list_snapshot` now inserts a sentinel row (`trakt_id=0`) for empty snapshots so `snapshots_exist()` returns True even with 0 real items. `get_list_snapshot` excludes sentinels (`trakt_id > 0`). Accept and skip endpoints now save empty snapshots as fallback when `take_initial_snapshots` fails.
- **2026-05-30**: Added auto-sync countdown timer and manual trigger. Setup page App Settings section now shows "Next auto-sync in Xm Xs" countdown that updates in real-time. Added "Sync Now" button to manually trigger an incremental auto-sync cycle. Added `/api/scheduler/trigger` endpoint. Added `next_sync_time` to scheduler state (ISO timestamp, updated after each sync cycle). Reordered setup page: App Settings now above Authentication, both collapsed by default.
- **2026-05-30**: Initialized git repo and pushed to GitHub (private repo: piersjones/pwListManager). Added README with setup, configuration, architecture, API docs, and troubleshooting sections.
