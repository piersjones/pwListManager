import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List

from src.database import Database
from src.config import Config, ConfigError

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "sync_state.db")
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")
QUEUE_FILE = os.path.join(DATA_DIR, "sync_queue.json")

SECONDS_PER_ITEM = 4.0


class JobQueue:
    def __init__(self, config_path=None):
        self._lock = threading.RLock()
        self._queue: List[Dict[str, Any]] = []
        self._processed: List[Dict[str, Any]] = []
        self._running = False
        self._paused = False
        self._current_job: Optional[Dict[str, Any]] = None
        self._progress = 0
        self._total = 0
        self._started_at: Optional[float] = None
        self._thread: Optional[threading.Thread] = None
        self._error: Optional[str] = None
        self._recent_times: List[float] = []  # rolling window of item processing durations
        self._max_recent = 30
        self._config_path = config_path or CONFIG_PATH
        os.makedirs(DATA_DIR, exist_ok=True)
        self._load_queue()

    def _load_queue(self):
        if os.path.exists(QUEUE_FILE):
            try:
                with open(QUEUE_FILE, "r") as f:
                    data = json.load(f)
                self._queue = data.get("queue", [])
                self._processed = data.get("processed", [])
            except Exception:
                self._queue = []
                self._processed = []

    def _save_queue(self):
        with self._lock:
            data = {
                "queue": self._queue,
                "processed": self._processed[-500:],
            }
            tmp = QUEUE_FILE + ".tmp"
            try:
                with open(tmp, "w") as f:
                    json.dump(data, f, indent=2)
                os.replace(tmp, QUEUE_FILE)
            except Exception:
                pass

    def _get_config(self):
        try:
            return Config(self._config_path)
        except (ConfigError, FileNotFoundError):
            return None

    def _get_db(self):
        return Database(DB_PATH)

    def enqueue_watched_sync_all(self, incremental=False, suppress_notifications=False):
        with self._lock:
            self._queue.append({
                "type": "sync_watched_all",
                "title": "Sync All Watched from Trakt",
                "incremental": incremental,
                "suppress_notifications": suppress_notifications,
            })
            self._total = len(self._queue) + self._progress
            self.logger().info("Enqueued sync_watched_all job")
            self._save_queue()
        return True

    def enqueue_watchlist_sync_all(self, incremental=False, suppress_notifications=False):
        with self._lock:
            self._queue.append({
                "type": "sync_watchlist_all",
                "title": "Sync All Watchlist from Trakt",
                "incremental": incremental,
                "suppress_notifications": suppress_notifications,
            })
            self._total = len(self._queue) + self._progress
            self.logger().info("Enqueued sync_watchlist_all job")
            self._save_queue()
        return True

    def enqueue_migrate_trakt_watchlist(self, incremental=False, suppress_notifications=False):
        with self._lock:
            self._queue.append({
                "type": "migrate_trakt_watchlist",
                "title": "Migrate Trakt Watchlist to Custom List",
                "incremental": incremental,
                "suppress_notifications": suppress_notifications,
            })
            self._total = len(self._queue) + self._progress
            self.logger().info("Enqueued migrate_trakt_watchlist job")
            self._save_queue()
        return True

    def logger(self):
        return logging.getLogger("pwListManager.queue")

    def start(self):
        with self._lock:
            if self._running:
                self.logger().info("Queue already running, not starting again")
                return False
            if not self._queue:
                self.logger().info("Queue empty, not starting")
                return False
            self._running = True
            self._paused = False
            self._error = None
            self._progress = 0
            self._total = len(self._queue)
            self._started_at = time.time()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self.logger().info("Queue worker thread started")
        return True

    def pause(self):
        with self._lock:
            self._paused = True
        self.logger().info("Queue paused")

    def resume(self):
        with self._lock:
            self._paused = False
        self.logger().info("Queue resumed")
        # If the worker thread has exited (queue was paused and finished),
        # restart it if there are items to process
        if not self._running and self._queue:
            self.start()

    def clear(self):
        with self._lock:
            self._queue = []
            self._processed = []
            self._current_job = None
            self._progress = 0
            self._total = 0
            self._running = False
            self._paused = False
            self._error = None
            self._save_queue()
        self.logger().info("Queue cleared")

    def _avg_item_time(self) -> float:
        """Rolling average of recent item processing times (seconds).
        Falls back to SECONDS_PER_ITEM if no items processed yet."""
        if self._recent_times:
            return sum(self._recent_times) / len(self._recent_times)
        return SECONDS_PER_ITEM

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            remaining = len(self._queue)
            done = len(self._processed)
            total = max(self._total, remaining + done) if (remaining + done) > 0 else 0
            eta_seconds = remaining * self._avg_item_time()
            eta_str = str(timedelta(seconds=int(eta_seconds))) if remaining > 0 and self._running else "--:--"
            recent_processed = self._processed[-20:] if self._processed else []
            return {
                "running": self._running,
                "paused": self._paused,
                "current_job": self._current_job,
                "progress": done,
                "total": total,
                "remaining": remaining,
                "eta": eta_str,
                "error": self._error,
                "recent_processed": recent_processed,
                "queue": self._queue[:50],
            }

    def _expand_and_enqueue_watched(self, config, logger, incremental=False, suppress_notifications=False):
        from src.trakt_client import TraktClient, TraktClientError
        trakt = TraktClient(config, logger)
        logger.info("Fetching watched movies from Trakt for expansion...")
        history = trakt.get_watched_movies()
        logger.info(f"Fetched {len(history)} watched movies from Trakt")
        ratings = trakt.get_ratings()
        logger.info(f"Fetched {len(ratings)} ratings from Trakt")

        # Build set of all current trakt_ids for snapshot
        all_trakt_ids = set()
        for entry in history:
            movie = entry.get("movie", entry)
            all_trakt_ids.add(movie["ids"]["trakt"])

        db = self._get_db()
        try:
            # If incremental, diff against snapshot to find only new items
            if incremental:
                previous_ids = db.get_list_snapshot("watched")
                new_ids = all_trakt_ids - previous_ids
                logger.info(f"Incremental watched sync: {len(new_ids)} new items out of {len(all_trakt_ids)} total (snapshot had {len(previous_ids)})")
            else:
                new_ids = None  # Process all unsynced

            unsynced = []
            for entry in reversed(history):
                movie = entry.get("movie", entry)
                trakt_id = movie["ids"]["trakt"]
                # If incremental, only process items not in previous snapshot
                if incremental and trakt_id not in new_ids:
                    continue
                if db.is_watched_synced(trakt_id):
                    continue
                unsynced.append(movie)
        finally:
            db.close()

        with self._lock:
            for movie in unsynced:
                trakt_id = movie["ids"]["trakt"]
                self._queue.append({
                    "type": "mark_watched",
                    "trakt_id": trakt_id,
                    "tmdb_id": movie["ids"].get("tmdb"),
                    "title": movie.get("title", "Unknown"),
                    "year": movie.get("year"),
                    "slug": movie["ids"].get("slug"),
                    "rating": ratings.get(trakt_id),
                    "suppress_notifications": suppress_notifications,
                })
            self._total = self._progress + len(self._queue)
            self._save_queue()
        logger.info(f"Expanded watched sync: {len(unsynced)} items to process")

        # Save snapshot after expansion
        if incremental:
            db2 = self._get_db()
            try:
                db2.save_list_snapshot("watched", list(all_trakt_ids))
                logger.info(f"Saved watched snapshot with {len(all_trakt_ids)} items")
            finally:
                db2.close()

    def _expand_and_enqueue_migrate(self, config, logger, incremental=False, suppress_notifications=False):
        from src.trakt_client import TraktClient, TraktClientError
        trakt = TraktClient(config, logger)
        logger.info("[migrate] Fetching watchlist from Trakt for expansion...")
        try:
            list_slug = trakt.get_custom_list_id()
            logger.info(f"[migrate] Using custom list slug: '{list_slug}'")
        except Exception as e:
            logger.error(f"[migrate] Failed to get/create custom list: {e}")
            with self._lock:
                self._error = f"Failed to get custom list: {e}"
            return
        try:
            movies = trakt.get_watchlist_movies()
            logger.info(f"[migrate] Fetched {len(movies) if movies else 0} movies from Trakt watchlist")
        except Exception as e:
            logger.error(f"[migrate] Failed to fetch watchlist: {e}")
            with self._lock:
                self._error = f"Failed to fetch watchlist: {e}"
            return

        if not movies:
            logger.info("[migrate] No movies on Trakt watchlist to migrate.")
            return

        # Build set of all current trakt_ids for snapshot
        all_trakt_ids = set()
        for entry in movies:
            movie = entry.get("movie", entry)
            all_trakt_ids.add(movie["ids"]["trakt"])

        # Filter out movies already migrated (watchlist_synced means already on custom list)
        db = self._get_db()
        try:
            # If incremental, also diff against snapshot to find only new items
            if incremental:
                previous_ids = db.get_list_snapshot("watchlist")
                new_ids = all_trakt_ids - previous_ids
                logger.info(f"[migrate] Incremental: {len(new_ids)} new items out of {len(all_trakt_ids)} total (snapshot had {len(previous_ids)})")
            else:
                new_ids = None  # Process all

            to_migrate = []
            for entry in movies:
                movie = entry.get("movie", entry)
                trakt_id = movie["ids"]["trakt"]
                # If incremental, only process items not in previous snapshot
                if incremental and trakt_id not in new_ids:
                    continue
                if db.is_watchlist_synced(trakt_id):
                    logger.debug(f"[migrate] Skipping '{movie.get('title', 'Unknown')}' — already migrated.")
                    continue
                to_migrate.append(entry)
        finally:
            db.close()

        if not to_migrate:
            logger.info("[migrate] All watchlist movies already migrated. Nothing to do.")
            # Save snapshot even if nothing to migrate
            if incremental:
                db2 = self._get_db()
                try:
                    db2.save_list_snapshot("watchlist", list(all_trakt_ids))
                finally:
                    db2.close()
            return

        with self._lock:
            for entry in to_migrate:
                movie = entry.get("movie", entry)
                self._queue.append({
                    "type": "migrate_movie",
                    "trakt_id": movie["ids"]["trakt"],
                    "tmdb_id": movie["ids"].get("tmdb"),
                    "title": movie.get("title", "Unknown"),
                    "year": movie.get("year"),
                    "slug": movie["ids"].get("slug"),
                    "list_slug": list_slug,
                    "suppress_notifications": suppress_notifications,
                })
            self._total = self._progress + len(self._queue)
            self._save_queue()
        logger.info(f"[migrate] Expanded migration: {len(to_migrate)} new movies to process (skipped {len(movies) - len(to_migrate)} already migrated)")

        # Save snapshot after expansion
        if incremental:
            db2 = self._get_db()
            try:
                db2.save_list_snapshot("watchlist", list(all_trakt_ids))
                logger.info(f"[migrate] Saved watchlist snapshot with {len(all_trakt_ids)} items")
            finally:
                db2.close()

    def _expand_and_enqueue_watchlist(self, config, logger, incremental=False, suppress_notifications=False):
        from src.trakt_client import TraktClient, TraktClientError
        trakt = TraktClient(config, logger)
        logger.info("Fetching watchlist from Trakt for expansion...")
        trakt_data = trakt.get_watchlist_movies()
        logger.info(f"Fetched {len(trakt_data)} watchlist items from Trakt")

        # Also fetch movies from configured custom lists
        custom_list_data = []
        for list_name in config.trakt_custom_list_names:
            list_slug = list_name.lower().replace(" ", "-")
            try:
                list_items = trakt.get_custom_list_movies(list_slug)
                logger.info(f"Fetched {len(list_items)} items from custom list '{list_name}' ({list_slug})")
                custom_list_data.extend(list_items)
            except Exception as e:
                logger.warning(f"Could not fetch custom list '{list_name}': {e}")

        # Deduplicate by trakt_id across all sources
        seen_ids = set()
        all_entries = []
        for entry in trakt_data + custom_list_data:
            movie = entry.get("movie", entry)
            trakt_id = movie["ids"]["trakt"]
            if trakt_id not in seen_ids:
                seen_ids.add(trakt_id)
                all_entries.append(entry)

        logger.info(f"Total unique movies from watchlist + custom lists: {len(all_entries)}")

        # Deduplicate: skip movies that are already watched on Trakt —
        # they'll be marked watched on Letterboxd (which auto-removes from watchlist),
        # so adding them to the watchlist is redundant
        try:
            watched_history = trakt.get_watched_movies()
            watched_ids = set()
            for entry in watched_history:
                movie = entry.get("movie", entry)
                watched_ids.add(movie["ids"]["trakt"])
            before = len(all_entries)
            all_entries = [e for e in all_entries if e.get("movie", e)["ids"]["trakt"] not in watched_ids]
            skipped = before - len(all_entries)
            if skipped:
                logger.info(f"Dedup: skipped {skipped} watched movies from watchlist sync (will be marked watched instead)")
        except Exception as e:
            logger.warning(f"Could not fetch watched list for watchlist dedup: {e}")

        # If incremental, diff against snapshot to find only new items
        if incremental:
            db_snap = self._get_db()
            try:
                previous_ids = db_snap.get_list_snapshot("watchlist_all")
                new_ids = seen_ids - previous_ids
                logger.info(f"Incremental watchlist sync: {len(new_ids)} new items out of {len(seen_ids)} total (snapshot had {len(previous_ids)})")
            finally:
                db_snap.close()
        else:
            new_ids = None  # Process all

        db = self._get_db()
        try:
            unsynced = []
            for entry in all_entries:
                movie = entry.get("movie", entry)
                trakt_id = movie["ids"]["trakt"]
                # If incremental, only process items not in previous snapshot
                if incremental and trakt_id not in new_ids:
                    continue
                if db.is_watchlist_synced(trakt_id):
                    continue
                unsynced.append(entry)
        finally:
            db.close()

        with self._lock:
            for entry in unsynced:
                movie = entry.get("movie", entry)
                self._queue.append({
                    "type": "add_to_letterboxd_watchlist",
                    "trakt_id": movie["ids"]["trakt"],
                    "tmdb_id": movie["ids"].get("tmdb"),
                    "title": movie.get("title", "Unknown"),
                    "year": movie.get("year"),
                    "slug": movie["ids"].get("slug"),
                    "suppress_notifications": suppress_notifications,
                })
            self._total = self._progress + len(self._queue)
            self._save_queue()
        logger.info(f"Expanded watchlist sync: {len(unsynced)} items to process")

        # Save snapshot after expansion
        if incremental:
            db2 = self._get_db()
            try:
                db2.save_list_snapshot("watchlist_all", list(seen_ids))
                logger.info(f"Saved watchlist_all snapshot with {len(seen_ids)} items")
            finally:
                db2.close()

    def has_initial_snapshots(self) -> bool:
        """Check if all required list snapshots exist for incremental auto-sync."""
        db = self._get_db()
        try:
            return db.snapshots_exist(["watchlist", "watched", "watchlist_all"])
        finally:
            db.close()

    def take_initial_snapshots(self, config, logger):
        """Fetch all current lists from Trakt and save snapshots WITHOUT processing any items.
        This allows the user to skip the full initial sync and start incremental mode immediately."""
        from src.trakt_client import TraktClient
        trakt = TraktClient(config, logger)
        db = self._get_db()
        try:
            # 1. Watchlist snapshot (for migration)
            try:
                movies = trakt.get_watchlist_movies()
                watchlist_ids = set()
                for entry in movies:
                    movie = entry.get("movie", entry)
                    watchlist_ids.add(movie["ids"]["trakt"])
                db.save_list_snapshot("watchlist", list(watchlist_ids))
                logger.info(f"Snapshot saved: watchlist ({len(watchlist_ids)} items)")
            except Exception as e:
                logger.warning(f"Could not take watchlist snapshot: {e}")
                return False

            # 2. Watched snapshot
            try:
                history = trakt.get_watched_movies()
                watched_ids = set()
                for entry in history:
                    movie = entry.get("movie", entry)
                    watched_ids.add(movie["ids"]["trakt"])
                db.save_list_snapshot("watched", list(watched_ids))
                logger.info(f"Snapshot saved: watched ({len(watched_ids)} items)")
            except Exception as e:
                logger.warning(f"Could not take watched snapshot: {e}")
                return False

            # 3. Watchlist_all snapshot (watchlist + custom lists)
            try:
                all_ids = set(watchlist_ids)
                for list_name in config.trakt_custom_list_names:
                    try:
                        list_slug = list_name.lower().replace(" ", "-")
                        items = trakt.get_custom_list_movies(list_slug)
                        for entry in items:
                            movie = entry.get("movie", entry)
                            all_ids.add(movie["ids"]["trakt"])
                    except Exception:
                        pass
                db.save_list_snapshot("watchlist_all", list(all_ids))
                logger.info(f"Snapshot saved: watchlist_all ({len(all_ids)} items)")
            except Exception as e:
                logger.warning(f"Could not take watchlist_all snapshot: {e}")
                return False

            return True
        finally:
            db.close()

    def _run(self):
        logger = self.logger()
        config = self._get_config()
        if not config:
            with self._lock:
                self._error = "Not configured"
                self._running = False
            return

        from src.letterboxd_client import LetterboxdClient, LetterboxdClientError
        from src.trakt_client import TraktClient, TraktClientError
        from src.rate_limiter import throttle
        from src.notifier import Notifier

        logger.info(f"Job queue starting with {len(self._queue)} top-level jobs")

        lb = None
        notifier = Notifier(config, logger)

        while True:
            with self._lock:
                if not self._queue:
                    break
                if not self._running:
                    break
                job = self._queue.pop(0)
                self._current_job = job
                self._progress += 1
                self._save_queue()

            # Wait if paused
            while True:
                with self._lock:
                    if not self._paused:
                        break
                time.sleep(0.5)

            try:
                job_start = time.time()

                # Expansion jobs: fetch from Trakt and enqueue individual items
                if job["type"] == "sync_watched_all":
                    incremental = job.get("incremental", False)
                    suppress = job.get("suppress_notifications", False)
                    with self._lock:
                        self._current_job = {"type": "sync_watched_all", "title": "Fetching watched list from Trakt..."}
                    self._expand_and_enqueue_watched(config, logger, incremental=incremental, suppress_notifications=suppress)
                    with self._lock:
                        self._progress = 0
                        self._total = len(self._queue)
                        self._started_at = time.time()
                        self._current_job = None
                    continue

                elif job["type"] == "sync_watchlist_all":
                    incremental = job.get("incremental", False)
                    suppress = job.get("suppress_notifications", False)
                    with self._lock:
                        self._current_job = {"type": "sync_watchlist_all", "title": "Fetching watchlist from Trakt..."}
                    self._expand_and_enqueue_watchlist(config, logger, incremental=incremental, suppress_notifications=suppress)
                    with self._lock:
                        self._progress = 0
                        self._total = len(self._queue)
                        self._started_at = time.time()
                        self._current_job = None
                    continue

                elif job["type"] == "migrate_trakt_watchlist":
                    incremental = job.get("incremental", False)
                    suppress = job.get("suppress_notifications", False)
                    # Expansion job: fetch watchlist and enqueue individual migrate_movie items
                    with self._lock:
                        self._current_job = {"type": "migrate_trakt_watchlist", "title": "Fetching Trakt watchlist for migration..."}
                    self._expand_and_enqueue_migrate(config, logger, incremental=incremental, suppress_notifications=suppress)
                    with self._lock:
                        self._progress = 0
                        self._total = len(self._queue)
                        self._started_at = time.time()
                        self._current_job = None
                    continue

                # Per-item Trakt migration — no Letterboxd needed
                if job["type"] == "migrate_movie":
                    trakt_id = job["trakt_id"]
                    title = job.get("title", "Unknown")
                    year = job.get("year")
                    list_slug = job.get("list_slug")
                    trakt = TraktClient(config, logger)
                    # Step 1: Add to custom list (with overflow to next list if full)
                    added = trakt.add_single_to_custom_list(list_slug, trakt_id, title)
                    if added:
                        # Step 2: Only remove from watchlist if add succeeded
                        trakt.remove_single_from_watchlist(trakt_id, title)
                        logger.info(f"[{self._progress}/{self._total}] Migrated '{title}' to custom list")
                    else:
                        logger.warning(f"[{self._progress}/{self._total}] Skipped '{title}' — could not add to custom list (Trakt limit). Movie stays on default watchlist.")
                    # Update database
                    db = self._get_db()
                    try:
                        db.upsert_watchlist_state(trakt_id, job.get("tmdb_id") or 0, title, year or 0, added, None, job.get("slug"))
                    finally:
                        db.close()
                    elapsed = time.time() - job_start
                    with self._lock:
                        self._recent_times.append(elapsed)
                        if len(self._recent_times) > self._max_recent:
                            self._recent_times = self._recent_times[-self._max_recent:]
                    job["status"] = "done"
                    with self._lock:
                        self._processed.append(job)
                        self._save_queue()
                    continue

                # Per-item jobs need Letterboxd — lazy-login
                if lb is None:
                    try:
                        logger.info("Logging into Letterboxd (first Letterboxd job)...")
                        lb = LetterboxdClient(config, logger)
                        logger.info("Letterboxd login successful")
                    except Exception as e:
                        logger.error(f"Letterboxd login failed: {e}")
                        with self._lock:
                            self._error = f"Letterboxd login failed: {e}"
                            self._running = False
                        break

                db = self._get_db()
                try:
                    if job["type"] == "add_to_letterboxd_watchlist":
                        trakt_id = job["trakt_id"]
                        tmdb_id = job.get("tmdb_id")
                        title = job.get("title", "Unknown")
                        year = job.get("year")
                        lb_slug = None
                        if tmdb_id:
                            try:
                                throttle(3.0, 1.0)
                                lb_slug = lb.resolve_tmdb_id_to_slug(tmdb_id)
                                # Check if already on Letterboxd watchlist before adding
                                already_on_wl = False
                                try:
                                    status = lb.get_film_status(lb_slug)
                                    if status.get("on_watchlist") is True:
                                        logger.info(f"'{title}' is already on Letterboxd watchlist — skipping add")
                                        already_on_wl = True
                                except LetterboxdClientError as e:
                                    logger.warning(f"Could not check watchlist status for '{title}': {e} — will attempt add anyway")
                                if not already_on_wl:
                                    throttle(3.0, 1.0)
                                    lb.add_to_watchlist(tmdb_id, slug=lb_slug)
                            except LetterboxdClientError as e:
                                logger.error(f"Letterboxd watchlist add failed for '{title}': {e}")
                        db.upsert_watchlist_state(trakt_id, tmdb_id or 0, title, year or 0, True, lb_slug,
                                                  job.get("slug"))
                        logger.info(f"[{self._progress}/{self._total}] Added '{title}' to Letterboxd watchlist")

                    elif job["type"] == "mark_watched":
                        trakt_id = job["trakt_id"]
                        tmdb_id = job.get("tmdb_id")
                        title = job.get("title", "Unknown")
                        year = job.get("year") or 0
                        rating = job.get("rating")
                        lb_slug = None
                        lb_uid = None
                        if tmdb_id:
                            try:
                                throttle(3.0, 1.0)
                                lb_slug = lb.resolve_tmdb_id_to_slug(tmdb_id)
                                # Check if already watched on Letterboxd before marking
                                already_watched = False
                                try:
                                    status = lb.get_film_status(lb_slug)
                                    lb_uid = status.get("uid")
                                    if status.get("watched") is True:
                                        logger.info(f"'{title}' is already watched on Letterboxd — skipping mark")
                                        already_watched = True
                                except LetterboxdClientError as e:
                                    logger.warning(f"Could not check watched status for '{title}': {e} — will attempt mark anyway")
                                if not already_watched:
                                    throttle(3.0, 1.0)
                                    lb.mark_watched(tmdb_id, slug=lb_slug, uid=lb_uid)
                            except LetterboxdClientError as e:
                                logger.error(f"Letterboxd watched mark failed for '{title}': {e}")
                            if rating and lb_slug:
                                try:
                                    throttle(3.0, 1.0)
                                    lb.rate_film(lb_slug, rating, uid=lb_uid)
                                except LetterboxdClientError as e:
                                    logger.warning(f"Letterboxd rating failed for '{title}': {e}")
                        suppress_notif = job.get("suppress_notifications", False)
                        if not suppress_notif:
                            try:
                                notifier.send_movie_watched_notification(title, year, letterboxd_slug=lb_slug)
                            except Exception:
                                pass
                        db.upsert_watched_state(trakt_id, tmdb_id or 0, title, year, True, True,
                                                lb_slug, job.get("slug"), rating)
                        logger.info(f"[{self._progress}/{self._total}] Marked '{title}' as watched on Letterboxd")

                finally:
                    db.close()

                # Record processing time for rolling ETA (non-expansion items only)
                elapsed = time.time() - job_start
                with self._lock:
                    self._recent_times.append(elapsed)
                    if len(self._recent_times) > self._max_recent:
                        self._recent_times = self._recent_times[-self._max_recent:]

                job["status"] = "done"
                with self._lock:
                    self._processed.append(job)
                    self._save_queue()

            except Exception as e:
                logger.error(f"Job failed: {job.get('title', job.get('type'))}: {e}")
                job["status"] = "error"
                job["error"] = str(e)[:200]
                with self._lock:
                    self._processed.append(job)
                    self._save_queue()
                # If Letterboxd session died, try to re-login
                if lb is not None and ("must.be.logged.in" in str(e) or "403" in str(e) or "401" in str(e)):
                    logger.warning("Letterboxd session may have expired, attempting re-login...")
                    try:
                        lb = LetterboxdClient(config, logger)
                        logger.info("Letterboxd re-login successful")
                    except Exception as re_login_err:
                        logger.error(f"Letterboxd re-login failed: {re_login_err}")
                        with self._lock:
                            self._error = f"Letterboxd re-login failed: {re_login_err}"
                            self._running = False
                        break

        logger.info("Job queue complete")
        with self._lock:
            self._running = False
            self._current_job = None


_queue_instance: Optional[JobQueue] = None

def get_queue(config_path=None) -> JobQueue:
    global _queue_instance
    if _queue_instance is None:
        _queue_instance = JobQueue(config_path)
    return _queue_instance