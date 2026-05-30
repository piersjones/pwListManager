import os
import sqlite3
from typing import Optional, Dict, Any

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_FILE = os.path.join(DATA_DIR, "sync_state.db")

class Database:
    def __init__(self, db_path: str = DB_FILE):
        self.db_path = db_path
        self._is_memory = (db_path == ":memory:")
        if not self._is_memory:
            os.makedirs(DATA_DIR, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self.initialize_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def initialize_db(self):
        conn = self._get_conn()
        # Enable WAL mode for better concurrent read/write from multiple threads
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sync_state (
                trakt_id INTEGER PRIMARY KEY,
                tmdb_id INTEGER,
                title TEXT NOT NULL,
                year INTEGER,
                trakt_slug TEXT,
                letterboxd_slug TEXT,
                rating INTEGER,
                watchlist_synced INTEGER DEFAULT 0,
                watched_synced INTEGER DEFAULT 0,
                notified INTEGER DEFAULT 0,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        for col, coltype in [("letterboxd_slug", "TEXT"), ("trakt_slug", "TEXT"), ("rating", "INTEGER")]:
            try:
                conn.execute(f"ALTER TABLE sync_state ADD COLUMN {col} {coltype}")
            except sqlite3.OperationalError:
                pass
        conn.execute("""
            CREATE TABLE IF NOT EXISTS list_snapshots (
                list_key TEXT NOT NULL,
                trakt_id INTEGER NOT NULL,
                PRIMARY KEY (list_key, trakt_id)
            )
        """)
        conn.commit()

    def get_movie_state(self, trakt_id: int) -> Optional[Dict[str, Any]]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM sync_state WHERE trakt_id = ?",
            (trakt_id,)
        ).fetchone()
        return dict(row) if row else None

    def upsert_watchlist_state(self, trakt_id: int, tmdb_id: int, title: str, year: int,
                              watchlist_synced: bool, letterboxd_slug: str = None,
                              trakt_slug: str = None):
        wl_val = 1 if watchlist_synced else 0
        conn = self._get_conn()
        existing = self.get_movie_state(trakt_id)
        if existing and letterboxd_slug is None:
            letterboxd_slug = existing.get("letterboxd_slug")
        if existing and trakt_slug is None:
            trakt_slug = existing.get("trakt_slug")
        conn.execute("""
            INSERT INTO sync_state (trakt_id, tmdb_id, title, year, trakt_slug, letterboxd_slug, watchlist_synced, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(trakt_id) DO UPDATE SET
                tmdb_id = excluded.tmdb_id,
                title = excluded.title,
                year = excluded.year,
                trakt_slug = COALESCE(excluded.trakt_slug, sync_state.trakt_slug),
                letterboxd_slug = COALESCE(excluded.letterboxd_slug, sync_state.letterboxd_slug),
                watchlist_synced = excluded.watchlist_synced,
                updated_at = CURRENT_TIMESTAMP
        """, (trakt_id, tmdb_id, title, year, trakt_slug, letterboxd_slug, wl_val))
        conn.commit()

    def upsert_watched_state(self, trakt_id: int, tmdb_id: int, title: str, year: int,
                              watched_synced: bool, notified: bool,
                              letterboxd_slug: str = None, trakt_slug: str = None,
                              rating: int = None):
        wt_val = 1 if watched_synced else 0
        nt_val = 1 if notified else 0
        conn = self._get_conn()
        existing = self.get_movie_state(trakt_id)
        if existing and letterboxd_slug is None:
            letterboxd_slug = existing.get("letterboxd_slug")
        if existing and trakt_slug is None:
            trakt_slug = existing.get("trakt_slug")
        if existing and rating is None:
            rating = existing.get("rating")
        conn.execute("""
            INSERT INTO sync_state (trakt_id, tmdb_id, title, year, trakt_slug, letterboxd_slug, watched_synced, notified, rating, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(trakt_id) DO UPDATE SET
                tmdb_id = excluded.tmdb_id,
                title = excluded.title,
                year = excluded.year,
                trakt_slug = COALESCE(excluded.trakt_slug, sync_state.trakt_slug),
                letterboxd_slug = COALESCE(excluded.letterboxd_slug, sync_state.letterboxd_slug),
                watched_synced = excluded.watched_synced,
                notified = excluded.notified,
                rating = COALESCE(excluded.rating, sync_state.rating),
                updated_at = CURRENT_TIMESTAMP
        """, (trakt_id, tmdb_id, title, year, trakt_slug, letterboxd_slug, wt_val, nt_val, rating))
        conn.commit()

    def update_letterboxd_slug(self, trakt_id: int, slug: str):
        conn = self._get_conn()
        conn.execute("""
            UPDATE sync_state SET letterboxd_slug = ?, updated_at = CURRENT_TIMESTAMP
            WHERE trakt_id = ?
        """, (slug, trakt_id))
        conn.commit()

    def is_watchlist_synced(self, trakt_id: int) -> bool:
        state = self.get_movie_state(trakt_id)
        return bool(state and state.get("watchlist_synced") == 1)

    def is_watched_synced(self, trakt_id: int) -> bool:
        state = self.get_movie_state(trakt_id)
        return bool(state and state.get("watched_synced") == 1)

    def is_notified(self, trakt_id: int) -> bool:
        state = self.get_movie_state(trakt_id)
        return bool(state and state.get("notified") == 1)

    def get_recent_actions(self, limit: int = 25) -> list:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM sync_state ORDER BY updated_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def erase_all(self):
        conn = self._get_conn()
        conn.execute("DELETE FROM sync_state")
        conn.commit()

    def get_stats(self) -> Dict[str, Any]:
        conn = self._get_conn()
        total = conn.execute("SELECT COUNT(*) FROM sync_state").fetchone()[0]
        wl_synced = conn.execute("SELECT COUNT(*) FROM sync_state WHERE watchlist_synced = 1").fetchone()[0]
        wt_synced = conn.execute("SELECT COUNT(*) FROM sync_state WHERE watched_synced = 1").fetchone()[0]
        notified = conn.execute("SELECT COUNT(*) FROM sync_state WHERE notified = 1").fetchone()[0]
        return {
            "total_movies": total,
            "watchlist_synced": wl_synced,
            "watched_synced": wt_synced,
            "notified": notified,
        }

    # --- List snapshot methods (for incremental auto-sync) ---

    def save_list_snapshot(self, list_key: str, trakt_ids: list):
        """Save a snapshot of trakt_ids for a given list key.
        Replaces any previous snapshot for the same list_key."""
        conn = self._get_conn()
        conn.execute("DELETE FROM list_snapshots WHERE list_key = ?", (list_key,))
        conn.executemany(
            "INSERT INTO list_snapshots (list_key, trakt_id) VALUES (?, ?)",
            [(list_key, tid) for tid in trakt_ids]
        )
        conn.commit()

    def get_list_snapshot(self, list_key: str) -> set:
        """Get the set of trakt_ids from the last snapshot for a given list_key."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT trakt_id FROM list_snapshots WHERE list_key = ?",
            (list_key,)
        ).fetchall()
        return {row[0] for row in rows}

    def snapshots_exist(self, keys: list) -> bool:
        """Check if snapshots exist for ALL of the given list keys."""
        conn = self._get_conn()
        for key in keys:
            row = conn.execute(
                "SELECT COUNT(*) FROM list_snapshots WHERE list_key = ?",
                (key,)
            ).fetchone()
            if row[0] == 0:
                return False
        return True
