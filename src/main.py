import sys
import time
import logging
from typing import Optional
import schedule
from src.config import Config, ConfigError
from src.database import Database
from src.logger import setup_logger
from src.trakt_client import TraktClient, TraktClientError
from src.letterboxd_client import LetterboxdClient, LetterboxdClientError
from src.notifier import Notifier
from src.rate_limiter import throttle

THROTTLE_SECONDS = 3

class SyncOrchestrator:
    def __init__(self, config: Config, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.db = Database()
        self.trakt = TraktClient(config, logger)
        self.letterboxd = LetterboxdClient(config, logger)
        self.notifier = Notifier(config, logger)
        self._list_slug: Optional[str] = None

    def _get_list_slug(self) -> str:
        if self._list_slug is None:
            self._list_slug = self.trakt.get_custom_list_id()
        return self._list_slug

    def sync_watchlist(self):
        self.logger.info("=== Starting watchlist sync ===")
        try:
            movies = self.trakt.get_watchlist_movies()
            if not movies:
                self.logger.info("No movies on default Trakt watchlist.")
                return

            list_slug = self._get_list_slug()
            unsynced = []
            for entry in movies:
                movie = entry.get("movie", entry)
                trakt_id = movie["ids"]["trakt"]
                tmdb_id = movie["ids"].get("tmdb")
                title = movie.get("title", "Unknown")
                year = movie.get("year")

                if self.db.is_watchlist_synced(trakt_id):
                    self.logger.debug(f"Skipping '{title}' — already watchlist-synced.")
                    continue

                unsynced.append(entry)

            if not unsynced:
                self.logger.info("All watchlist movies already synced.")
                return

            added = self.trakt.add_to_custom_list(list_slug, unsynced)
            if not added:
                self.logger.error("Failed to add movies to custom Trakt list.")
                return

            removed = self.trakt.remove_from_watchlist(unsynced)
            if not removed:
                self.logger.error("Failed to remove movies from default watchlist.")
                return

            for entry in unsynced:
                movie = entry.get("movie", entry)
                trakt_id = movie["ids"]["trakt"]
                tmdb_id = movie["ids"].get("tmdb")
                title = movie.get("title", "Unknown")
                year = movie.get("year")

                try:
                    if tmdb_id:
                        self.logger.info(f"Adding '{title}' ({year}) to Letterboxd watchlist...")
                        self.letterboxd.add_to_watchlist(tmdb_id)
                    else:
                        self.logger.warning(f"No TMDb ID for '{title}'. Skipping Letterboxd watchlist add.")
                except LetterboxdClientError as e:
                    self.logger.error(f"Letterboxd watchlist error for '{title}': {e}")
                    self.notifier.send_error_notification(f"Letterboxd watchlist add failed for '{title}': {e}")

                self.db.upsert_watchlist_state(trakt_id, tmdb_id or 0, title, year or 0, True)
                throttle(THROTTLE_SECONDS, 1.0)

            self.logger.info(f"Watchlist sync complete. Processed {len(unsynced)} movie(s).")

        except (TraktClientError, LetterboxdClientError) as e:
            self.logger.error(f"Watchlist sync failed: {e}")
            self.notifier.send_error_notification(f"Watchlist sync failed: {e}")

    def sync_watched(self):
        self.logger.info("=== Starting watched sync ===")
        try:
            history = self.trakt.get_watched_movies()
            if not history:
                self.logger.info("No watched movies found on Trakt.")
                return

            new_watched = []
            for entry in history:
                movie = entry.get("movie", entry)
                trakt_id = movie["ids"]["trakt"]
                tmdb_id = movie["ids"].get("tmdb")
                title = movie.get("title", "Unknown")
                year = movie.get("year")

                if self.db.is_watched_synced(trakt_id):
                    continue

                new_watched.append(entry)

            if not new_watched:
                self.logger.info("All watched movies already synced.")
                return

            for entry in new_watched:
                movie = entry.get("movie", entry)
                trakt_id = movie["ids"]["trakt"]
                tmdb_id = movie["ids"].get("tmdb")
                title = movie.get("title", "Unknown")
                year = movie.get("year")

                try:
                    if tmdb_id:
                        self.logger.info(f"Marking '{title}' ({year}) as watched on Letterboxd...")
                        self.letterboxd.mark_watched(tmdb_id)
                    else:
                        self.logger.warning(f"No TMDb ID for '{title}'. Skipping Letterboxd watched mark.")
                except LetterboxdClientError as e:
                    self.logger.error(f"Letterboxd watched error for '{title}': {e}")
                    self.notifier.send_error_notification(f"Letterboxd watched mark failed for '{title}': {e}")

                notified = False
                try:
                    self.notifier.send_movie_watched_notification(title, year or 0)
                    notified = True
                except Exception as e:
                    self.logger.error(f"Pushover notification failed for '{title}': {e}")

                self.db.upsert_watched_state(trakt_id, tmdb_id or 0, title, year or 0, True, notified)
                throttle(THROTTLE_SECONDS, 1.0)

            self.logger.info(f"Watched sync complete. Processed {len(new_watched)} movie(s).")

        except (TraktClientError, LetterboxdClientError) as e:
            self.logger.error(f"Watched sync failed: {e}")
            self.notifier.send_error_notification(f"Watched sync failed: {e}")

    def run_sync(self):
        self.logger.info("--- Sync cycle starting ---")
        self.sync_watchlist()
        self.sync_watched()
        self.logger.info("--- Sync cycle complete ---")

    def close(self):
        self.db.close()


def main():
    try:
        config = Config()
    except ConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)

    logger = setup_logger(config.log_level)
    logger.info("pwListManager starting...")

    orchestrator = SyncOrchestrator(config, logger)

    try:
        orchestrator.trakt.authenticate()
    except TraktClientError as e:
        logger.critical(f"Trakt authentication failed: {e}")
        orchestrator.notifier.send_error_notification(f"Trakt auth failed: {e}")
        sys.exit(1)

    interval = config.sync_interval_minutes
    logger.info(f"Sync interval set to {interval} minute(s).")

    orchestrator.run_sync()

    schedule.every(interval).minutes.do(orchestrator.run_sync)

    logger.info(f"Scheduler running. Next sync in {interval} minute(s). Press Ctrl+C to exit.")

    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down pwListManager...")
    finally:
        orchestrator.close()
        logger.info("Goodbye!")


if __name__ == "__main__":
    main()