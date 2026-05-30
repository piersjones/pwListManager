import unittest
from src.database import Database

class TestDatabase(unittest.TestCase):
    def setUp(self):
        self.db = Database(db_path=":memory:")

    def tearDown(self):
        self.db.close()

    def test_initialize_table(self):
        conn = self.db._get_conn()
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sync_state'")
        table = cursor.fetchone()
        self.assertIsNotNone(table)
        self.assertEqual(table["name"], "sync_state")

    def test_upsert_watchlist_state(self):
        # Insert new watchlist state
        self.db.upsert_watchlist_state(
            trakt_id=1001,
            tmdb_id=2001,
            title="Inception",
            year=2010,
            watchlist_synced=True
        )
        
        state = self.db.get_movie_state(1001)
        self.assertIsNotNone(state)
        self.assertEqual(state["title"], "Inception")
        self.assertEqual(state["watchlist_synced"], 1)
        self.assertEqual(state["watched_synced"], 0) # default value

        # Update watchlist state
        self.db.upsert_watchlist_state(
            trakt_id=1001,
            tmdb_id=2001,
            title="Inception",
            year=2010,
            watchlist_synced=False
        )
        
        state = self.db.get_movie_state(1001)
        self.assertEqual(state["watchlist_synced"], 0)

    def test_upsert_watched_state(self):
        # Insert watched state
        self.db.upsert_watched_state(
            trakt_id=1002,
            tmdb_id=2002,
            title="Batman Begins",
            year=2005,
            watched_synced=True,
            notified=True
        )
        
        state = self.db.get_movie_state(1002)
        self.assertIsNotNone(state)
        self.assertEqual(state["watched_synced"], 1)
        self.assertEqual(state["notified"], 1)

    def test_helper_queries(self):
        trakt_id = 1003
        
        # Initially false
        self.assertFalse(self.db.is_watchlist_synced(trakt_id))
        self.assertFalse(self.db.is_watched_synced(trakt_id))
        self.assertFalse(self.db.is_notified(trakt_id))
        
        # Save watchlist synced
        self.db.upsert_watchlist_state(trakt_id, 3003, "Avatar", 2009, True)
        self.assertTrue(self.db.is_watchlist_synced(trakt_id))
        self.assertFalse(self.db.is_watched_synced(trakt_id))
        
        # Save watched synced and notified
        self.db.upsert_watched_state(trakt_id, 3003, "Avatar", 2009, True, True)
        self.assertTrue(self.db.is_watched_synced(trakt_id))
        self.assertTrue(self.db.is_notified(trakt_id))

if __name__ == "__main__":
    unittest.main()
