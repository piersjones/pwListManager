import unittest
from unittest.mock import patch, MagicMock, call
import logging
from src.config import Config
from src.database import Database
from src.main import SyncOrchestrator
from src.trakt_client import TraktClientError
from src.letterboxd_client import LetterboxdClientError

class TestSyncOrchestrator(unittest.TestCase):
    def setUp(self):
        logging.basicConfig(level=logging.CRITICAL)
        self.logger = logging.getLogger("test_orchestrator")
        self.mock_config = MagicMock(spec=Config)

    @patch("src.main.Database")
    @patch("src.main.LetterboxdClient")
    @patch("src.main.TraktClient")
    @patch("src.main.Notifier")
    def _create_orchestrator(self, MockNotifier, MockTrakt, MockLetterboxd, MockDB):
        self.db = MockDB.return_value
        self.trakt = MockTrakt.return_value
        self.letterboxd = MockLetterboxd.return_value
        self.notifier = MockNotifier.return_value
        orch = SyncOrchestrator(self.mock_config, self.logger)
        orch.db = self.db
        orch.trakt = self.trakt
        orch.letterboxd = self.letterboxd
        orch.notifier = self.notifier
        return orch

    def test_sync_watchlist_no_movies(self):
        orch = self._create_orchestrator()
        self.trakt.get_watchlist_movies.return_value = []

        orch.sync_watchlist()

        self.trakt.get_watchlist_movies.assert_called_once()
        self.trakt.add_to_custom_list.assert_not_called()
        self.trakt.remove_from_watchlist.assert_not_called()

    @patch("src.main.Notifier")
    @patch("src.main.LetterboxdClient")
    @patch("src.main.TraktClient")
    @patch("src.main.Database")
    def test_sync_watchlist_all_already_synced(self, MockDB, MockTrakt, MockLetterboxd, MockNotifier):
        db = MockDB.return_value
        trakt = MockTrakt.return_value
        letterboxd = MockLetterboxd.return_value
        notifier = MockNotifier.return_value

        db.is_watchlist_synced.return_value = True
        trakt.get_watchlist_movies.return_value = [
            {"movie": {"title": "Inception", "year": 2010, "ids": {"trakt": 1, "tmdb": 27205}}}
        ]

        orch = SyncOrchestrator(self.mock_config, self.logger)
        orch.db = db
        orch.trakt = trakt
        orch.letterboxd = letterboxd
        orch.notifier = notifier

        orch.sync_watchlist()

        trakt.add_to_custom_list.assert_not_called()

    @patch("src.main.Notifier")
    @patch("src.main.LetterboxdClient")
    @patch("src.main.TraktClient")
    @patch("src.main.Database")
    def test_sync_watchlist_new_movies(self, MockDB, MockTrakt, MockLetterboxd, MockNotifier):
        db = MockDB.return_value
        trakt = MockTrakt.return_value
        letterboxd = MockLetterboxd.return_value
        notifier = MockNotifier.return_value

        db.is_watchlist_synced.return_value = False
        trakt.get_watchlist_movies.return_value = [
            {"movie": {"title": "Inception", "year": 2010, "ids": {"trakt": 1, "tmdb": 27205}}},
            {"movie": {"title": "The Matrix", "year": 1999, "ids": {"trakt": 2, "tmdb": 603}}}
        ]
        trakt.get_custom_list_id.return_value = "movie-watchlist"
        trakt.add_to_custom_list.return_value = True
        trakt.remove_from_watchlist.return_value = True
        letterboxd.add_to_watchlist.return_value = True

        orch = SyncOrchestrator(self.mock_config, self.logger)
        orch.db = db
        orch.trakt = trakt
        orch.letterboxd = letterboxd
        orch.notifier = notifier

        orch.sync_watchlist()

        trakt.add_to_custom_list.assert_called_once()
        trakt.remove_from_watchlist.assert_called_once()
        self.assertEqual(letterboxd.add_to_watchlist.call_count, 2)
        self.assertEqual(db.upsert_watchlist_state.call_count, 2)

    @patch("src.main.Notifier")
    @patch("src.main.LetterboxdClient")
    @patch("src.main.TraktClient")
    @patch("src.main.Database")
    def test_sync_watchlist_skips_movie_without_tmdb_id(self, MockDB, MockTrakt, MockLetterboxd, MockNotifier):
        db = MockDB.return_value
        trakt = MockTrakt.return_value
        letterboxd = MockLetterboxd.return_value
        notifier = MockNotifier.return_value

        db.is_watchlist_synced.return_value = False
        trakt.get_watchlist_movies.return_value = [
            {"movie": {"title": "Obscure Film", "year": 2020, "ids": {"trakt": 99}}}
        ]
        trakt.get_custom_list_id.return_value = "movie-watchlist"
        trakt.add_to_custom_list.return_value = True
        trakt.remove_from_watchlist.return_value = True

        orch = SyncOrchestrator(self.mock_config, self.logger)
        orch.db = db
        orch.trakt = trakt
        orch.letterboxd = letterboxd
        orch.notifier = notifier

        orch.sync_watchlist()

        letterboxd.add_to_watchlist.assert_not_called()
        db.upsert_watchlist_state.assert_called_once()
        self.assertEqual(db.upsert_watchlist_state.call_args[0][2], "Obscure Film")

    @patch("src.main.Notifier")
    @patch("src.main.LetterboxdClient")
    @patch("src.main.TraktClient")
    @patch("src.main.Database")
    def test_sync_watchlist_letterboxd_error_sends_alert(self, MockDB, MockTrakt, MockLetterboxd, MockNotifier):
        db = MockDB.return_value
        trakt = MockTrakt.return_value
        letterboxd = MockLetterboxd.return_value
        notifier = MockNotifier.return_value

        db.is_watchlist_synced.return_value = False
        trakt.get_watchlist_movies.return_value = [
            {"movie": {"title": "Inception", "year": 2010, "ids": {"trakt": 1, "tmdb": 27205}}}
        ]
        trakt.get_custom_list_id.return_value = "movie-watchlist"
        trakt.add_to_custom_list.return_value = True
        trakt.remove_from_watchlist.return_value = True
        letterboxd.add_to_watchlist.side_effect = LetterboxdClientError("Cookie expired")

        orch = SyncOrchestrator(self.mock_config, self.logger)
        orch.db = db
        orch.trakt = trakt
        orch.letterboxd = letterboxd
        orch.notifier = notifier

        orch.sync_watchlist()

        notifier.send_error_notification.assert_called_once()
        self.assertIn("Cookie expired", notifier.send_error_notification.call_args[0][0])
        db.upsert_watchlist_state.assert_called_once()

    @patch("src.main.Notifier")
    @patch("src.main.LetterboxdClient")
    @patch("src.main.TraktClient")
    @patch("src.main.Database")
    def test_sync_watched_no_movies(self, MockDB, MockTrakt, MockLetterboxd, MockNotifier):
        db = MockDB.return_value
        trakt = MockTrakt.return_value
        letterboxd = MockLetterboxd.return_value
        notifier = MockNotifier.return_value

        trakt.get_watched_movies.return_value = []

        orch = SyncOrchestrator(self.mock_config, self.logger)
        orch.db = db
        orch.trakt = trakt
        orch.letterboxd = letterboxd
        orch.notifier = notifier

        orch.sync_watched()

        letterboxd.mark_watched.assert_not_called()
        notifier.send_movie_watched_notification.assert_not_called()

    @patch("src.main.Notifier")
    @patch("src.main.LetterboxdClient")
    @patch("src.main.TraktClient")
    @patch("src.main.Database")
    def test_sync_watched_new_movies(self, MockDB, MockTrakt, MockLetterboxd, MockNotifier):
        db = MockDB.return_value
        trakt = MockTrakt.return_value
        letterboxd = MockLetterboxd.return_value
        notifier = MockNotifier.return_value

        db.is_watched_synced.return_value = False
        trakt.get_watched_movies.return_value = [
            {"movie": {"title": "Inception", "year": 2010, "ids": {"trakt": 1, "tmdb": 27205}}}
        ]
        letterboxd.mark_watched.return_value = True
        notifier.send_movie_watched_notification.return_value = True

        orch = SyncOrchestrator(self.mock_config, self.logger)
        orch.db = db
        orch.trakt = trakt
        orch.letterboxd = letterboxd
        orch.notifier = notifier

        orch.sync_watched()

        letterboxd.mark_watched.assert_called_once_with(27205)
        notifier.send_movie_watched_notification.assert_called_once_with("Inception", 2010)
        db.upsert_watched_state.assert_called_once()
        self.assertEqual(db.upsert_watched_state.call_args[0], (1, 27205, "Inception", 2010, True, True))

    @patch("src.main.Notifier")
    @patch("src.main.LetterboxdClient")
    @patch("src.main.TraktClient")
    @patch("src.main.Database")
    def test_sync_watched_all_already_synced(self, MockDB, MockTrakt, MockLetterboxd, MockNotifier):
        db = MockDB.return_value
        trakt = MockTrakt.return_value
        letterboxd = MockLetterboxd.return_value
        notifier = MockNotifier.return_value

        db.is_watched_synced.return_value = True
        trakt.get_watched_movies.return_value = [
            {"movie": {"title": "Inception", "year": 2010, "ids": {"trakt": 1, "tmdb": 27205}}}
        ]

        orch = SyncOrchestrator(self.mock_config, self.logger)
        orch.db = db
        orch.trakt = trakt
        orch.letterboxd = letterboxd
        orch.notifier = notifier

        orch.sync_watched()

        letterboxd.mark_watched.assert_not_called()

    @patch("src.main.Notifier")
    @patch("src.main.LetterboxdClient")
    @patch("src.main.TraktClient")
    @patch("src.main.Database")
    def test_sync_watched_pushover_failure_still_marks_synced(self, MockDB, MockTrakt, MockLetterboxd, MockNotifier):
        db = MockDB.return_value
        trakt = MockTrakt.return_value
        letterboxd = MockLetterboxd.return_value
        notifier = MockNotifier.return_value

        db.is_watched_synced.return_value = False
        trakt.get_watched_movies.return_value = [
            {"movie": {"title": "Inception", "year": 2010, "ids": {"trakt": 1, "tmdb": 27205}}}
        ]
        letterboxd.mark_watched.return_value = True
        notifier.send_movie_watched_notification.side_effect = Exception("Pushover down")

        orch = SyncOrchestrator(self.mock_config, self.logger)
        orch.db = db
        orch.trakt = trakt
        orch.letterboxd = letterboxd
        orch.notifier = notifier

        orch.sync_watched()

        db.upsert_watched_state.assert_called_once()
        self.assertEqual(db.upsert_watched_state.call_args[0][5], False)

    @patch("src.main.Notifier")
    @patch("src.main.LetterboxdClient")
    @patch("src.main.TraktClient")
    @patch("src.main.Database")
    def test_sync_watched_trakt_error_sends_alert(self, MockDB, MockTrakt, MockLetterboxd, MockNotifier):
        db = MockDB.return_value
        trakt = MockTrakt.return_value
        notifier = MockNotifier.return_value

        trakt.get_watched_movies.side_effect = TraktClientError("API 503")

        orch = SyncOrchestrator(self.mock_config, self.logger)
        orch.db = db
        orch.trakt = trakt
        orch.notifier = notifier

        orch.sync_watched()

        notifier.send_error_notification.assert_called_once()
        self.assertIn("API 503", notifier.send_error_notification.call_args[0][0])

    @patch("src.main.Notifier")
    @patch("src.main.LetterboxdClient")
    @patch("src.main.TraktClient")
    @patch("src.main.Database")
    def test_run_sync_calls_both(self, MockDB, MockTrakt, MockLetterboxd, MockNotifier):
        db = MockDB.return_value
        trakt = MockTrakt.return_value
        letterboxd = MockLetterboxd.return_value
        notifier = MockNotifier.return_value

        trakt.get_watchlist_movies.return_value = []
        trakt.get_watched_movies.return_value = []

        orch = SyncOrchestrator(self.mock_config, self.logger)
        orch.db = db
        orch.trakt = trakt
        orch.letterboxd = letterboxd
        orch.notifier = notifier

        orch.run_sync()

        trakt.get_watchlist_movies.assert_called_once()
        trakt.get_watched_movies.assert_called_once()


class TestDatabaseIntegration(unittest.TestCase):
    def setUp(self):
        self.db = Database(db_path=":memory:")

    def tearDown(self):
        self.db.close()

    def test_upsert_watched_then_watchlist_same_movie(self):
        self.db.upsert_watchlist_state(1, 27205, "Inception", 2010, True)
        state = self.db.get_movie_state(1)
        self.assertEqual(state["watchlist_synced"], 1)
        self.assertEqual(state["watched_synced"], 0)

        self.db.upsert_watched_state(1, 27205, "Inception", 2010, True, True)
        state = self.db.get_movie_state(1)
        self.assertEqual(state["watchlist_synced"], 1)
        self.assertEqual(state["watched_synced"], 1)
        self.assertEqual(state["notified"], 1)

    def test_multiple_movies(self):
        self.db.upsert_watchlist_state(1, 27205, "Inception", 2010, True)
        self.db.upsert_watchlist_state(2, 603, "The Matrix", 1999, True)
        self.db.upsert_watchlist_state(3, 155, "The Dark Knight", 2008, False)

        self.assertTrue(self.db.is_watchlist_synced(1))
        self.assertTrue(self.db.is_watchlist_synced(2))
        self.assertFalse(self.db.is_watchlist_synced(3))

    def test_nonexistent_movie_returns_none(self):
        self.assertIsNone(self.db.get_movie_state(99999))
        self.assertFalse(self.db.is_watchlist_synced(99999))
        self.assertFalse(self.db.is_watched_synced(99999))
        self.assertFalse(self.db.is_notified(99999))

    def test_update_preserves_other_fields(self):
        self.db.upsert_watchlist_state(1, 27205, "Inception", 2010, True)
        self.db.upsert_watched_state(1, 27205, "Inception", 2010, True, False)

        state = self.db.get_movie_state(1)
        self.assertEqual(state["watchlist_synced"], 1)
        self.assertEqual(state["watched_synced"], 1)
        self.assertEqual(state["notified"], 0)

        self.db.upsert_watched_state(1, 27205, "Inception", 2010, True, True)
        state = self.db.get_movie_state(1)
        self.assertEqual(state["notified"], 1)

if __name__ == "__main__":
    unittest.main()