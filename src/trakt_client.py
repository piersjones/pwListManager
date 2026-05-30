import json
import logging
import os
import time
import random
from typing import Dict, Any, List, Optional
import requests
from src.config import Config
from src.rate_limiter import throttle

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COOKIE_DIR = os.path.join(BASE_DIR, ".cookie")
TOKEN_FILE = os.path.join(COOKIE_DIR, "trakt_token.json")
API_BASE_URL = "https://api.trakt.tv"

class TraktClientError(Exception):
    """Custom exception for Trakt Client errors."""
    pass

class TraktClient:
    def __init__(self, config: Config, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.token: Dict[str, Any] = {}
        self.headers: Dict[str, str] = {}
        os.makedirs(COOKIE_DIR, exist_ok=True)
        self.load_token()

    def load_token(self):
        """Loads the token from disk if it exists."""
        if os.path.exists(TOKEN_FILE):
            try:
                with open(TOKEN_FILE, "r", encoding="utf-8") as f:
                    self.token = json.load(f)
                    self.logger.debug("Trakt token loaded from disk.")
                    self._update_headers()
            except Exception as e:
                self.logger.error(f"Failed to load Trakt token: {e}")

    def save_token(self):
        """Saves the current token to disk."""
        try:
            with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                json.dump(self.token, f, indent=2)
                self.logger.debug("Trakt token saved to disk.")
        except Exception as e:
            self.logger.error(f"Failed to save Trakt token to disk: {e}")

    def _update_headers(self):
        """Helper to build API headers based on loaded token."""
        self.headers = {
            "Content-Type": "application/json",
            "trakt-api-version": "2",
            "trakt-api-key": self.config.trakt_client_id
        }
        if "access_token" in self.token:
            self.headers["Authorization"] = f"Bearer {self.token['access_token']}"

    def is_authenticated(self) -> bool:
        """Checks if a valid token is loaded."""
        if not self.token or "access_token" not in self.token:
            return False
        
        # Check if expired (or close to expiring, e.g., within a day)
        created_at = self.token.get("created_at", 0)
        expires_in = self.token.get("expires_in", 0)
        now = time.time()
        
        if now > (created_at + expires_in - 86400):
            # Token is expired or expiring soon, attempt refresh
            self.logger.info("Trakt token is expired or expiring soon. Attempting refresh...")
            return self.refresh_token()
            
        return True

    def authenticate(self):
        """Ensures the client has authenticated API access, prompts Device Flow if not."""
        if self.is_authenticated():
            return

        self.logger.info("Initiating Trakt Device Authorization flow...")
        
        # 1. Request Device Code
        url = f"{API_BASE_URL}/oauth/device/code"
        payload = {"client_id": self.config.trakt_client_id}
        
        try:
            r = requests.post(url, json=payload, headers={"Content-Type": "application/json"})
            r.raise_for_status()
        except Exception as e:
            raise TraktClientError(f"Failed to get device code from Trakt: {e}")
            
        data = r.json()
        device_code = data["device_code"]
        user_code = data["user_code"]
        verification_url = data["verification_url"]
        interval = data.get("interval", 5)
        expires_in = data.get("expires_in", 600)
        
        self.logger.warning(
            f"\n=========================================================\n"
            f"ACTION REQUIRED:\n"
            f"Please go to: {verification_url}\n"
            f"And enter the code: {user_code}\n"
            f"=========================================================\n"
        )
        
        # 2. Poll for token
        poll_url = f"{API_BASE_URL}/oauth/device/token"
        poll_payload = {
            "code": device_code,
            "client_id": self.config.trakt_client_id,
            "client_secret": self.config.trakt_client_secret
        }
        
        start_time = time.time()
        while time.time() - start_time < expires_in:
            time.sleep(interval)
            try:
                res = requests.post(poll_url, json=poll_payload, headers={"Content-Type": "application/json"})
                if res.status_code == 200:
                    token_data = res.json()
                    self.token = token_data
                    # Add created_at if not present
                    if "created_at" not in self.token:
                        self.token["created_at"] = int(time.time())
                    self.save_token()
                    self._update_headers()
                    self.logger.info("Trakt authentication successful!")
                    return
                elif res.status_code == 400:
                    err = res.json().get("error")
                    if err == "authorization_pending":
                        self.logger.debug("Authorization pending...")
                        continue
                    elif err == "slow_down":
                        interval += 2
                        self.logger.warning(f"Slowing down polling. New interval: {interval}s")
                    elif err in ["expired_token", "invalid_device_code"]:
                        raise TraktClientError("Device authorization code expired or invalid.")
                    else:
                        raise TraktClientError(f"Failed during polling: {err}")
                else:
                    res.raise_for_status()
            except requests.exceptions.RequestException as e:
                self.logger.error(f"Network error during Trakt token polling: {e}")
                
        raise TraktClientError("Device authorization timed out.")

    def refresh_token(self) -> bool:
        """Refreshes an expired access token."""
        if "refresh_token" not in self.token:
            self.logger.error("No refresh token available to perform refresh.")
            return False

        url = f"{API_BASE_URL}/oauth/token"
        payload = {
            "refresh_token": self.token["refresh_token"],
            "client_id": self.config.trakt_client_id,
            "client_secret": self.config.trakt_client_secret,
            "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
            "grant_type": "refresh_token"
        }
        
        try:
            r = requests.post(url, json=payload, headers={"Content-Type": "application/json"})
            if r.status_code == 200:
                self.token = r.json()
                if "created_at" not in self.token:
                    self.token["created_at"] = int(time.time())
                self.save_token()
                self._update_headers()
                self.logger.info("Trakt token refreshed successfully.")
                return True
            else:
                self.logger.error(f"Failed to refresh Trakt token: {r.status_code} - {r.text}")
                return False
        except Exception as e:
            self.logger.error(f"Exception during Trakt token refresh: {e}")
            return False

    def get_watchlist_movies(self) -> List[Dict[str, Any]]:
        """Retrieves movies from the user's default watchlist."""
        self.authenticate()
        url = f"{API_BASE_URL}/sync/watchlist/movies"
        self.logger.info(f"[Trakt] get_watchlist_movies: Fetching from {url}")
        try:
            throttle(1.0, 0.3)
            r = requests.get(url, headers=self.headers)
            if r.status_code == 429:
                retry_after = int(r.headers.get("Retry-After", 1))
                self.logger.warning(f"Trakt rate limited (429). Waiting {retry_after}s...")
                time.sleep(retry_after + random.uniform(0.5, 1.5))
                r = requests.get(url, headers=self.headers)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            raise TraktClientError(f"Failed to fetch watchlist movies: {e}")

    def create_custom_list(self) -> Dict[str, Any]:
        """Creates the custom Trakt list if it doesn't already exist."""
        return self.create_custom_list_with_name(self.config.trakt_custom_list_name)

    def create_custom_list_with_name(self, list_name: str) -> Dict[str, Any]:
        """Creates a custom Trakt list with the given name.
        Returns the API response on success.
        Raises TraktClientError on failure, including 420 (account limit exceeded)."""
        self.authenticate()
        url = f"{API_BASE_URL}/users/me/lists"
        payload = {
            "name": list_name,
            "description": "Custom watchlist managed by pwListManager to bypass the 100-item free limit.",
            "privacy": "private"
        }
        try:
            r = requests.post(url, json=payload, headers=self.headers)
            if r.status_code == 420:
                self.logger.error(f"Trakt account limit reached — cannot create list '{list_name}'. Free accounts are limited to 5 custom lists.")
                raise TraktClientError(
                    f"Cannot create list '{list_name}': Trakt free accounts are limited to 5 custom lists. "
                    f"Delete an existing list on trakt.tv or use an existing list instead."
                )
            if r.status_code not in (200, 201):
                self.logger.error(f"Failed to create custom list '{list_name}'. Status: {r.status_code}, Response: {r.text[:300]}")
            r.raise_for_status()
            result = r.json()
            self.logger.info(f"Custom list '{list_name}' created on Trakt with slug: {result.get('ids', {}).get('slug', 'unknown')}")
            return result
        except TraktClientError:
            raise
        except Exception as e:
            raise TraktClientError(f"Failed to create custom list '{list_name}': {e}")

    def get_custom_list_id(self) -> str:
        """Retrieves the slug/ID of the configured custom list, creating it if needed."""
        self.authenticate()
        url = f"{API_BASE_URL}/users/me/lists"
        try:
            r = requests.get(url, headers=self.headers)
            if r.status_code != 200:
                self.logger.error(f"Failed to get user lists. Status: {r.status_code}, Response: {r.text[:300]}")
            r.raise_for_status()
            lists = r.json()
        except Exception as e:
            raise TraktClientError(f"Failed to retrieve user lists: {e}")
            
        target_name = self.config.trakt_custom_list_name.lower().replace(" ", "-")
        self.logger.debug(f"Looking for list matching '{target_name}' among {len(lists)} user lists")
        for lst in lists:
            slug = lst["ids"]["slug"]
            name = lst["name"].lower().replace(" ", "-")
            self.logger.debug(f"  Found list: name='{lst['name']}', slug='{slug}', items={lst.get('item_count', '?')}")
            if slug == target_name or name == target_name:
                self.logger.info(f"Found existing custom list: '{lst['name']}' (slug: {slug}, items: {lst.get('item_count', '?')})")
                return slug
                
        # List doesn't exist, create it
        self.logger.info(f"No existing list found matching '{target_name}', creating new list...")
        new_list = self.create_custom_list()
        return new_list["ids"]["slug"]

    def get_list_with_room(self, list_names: list = None) -> tuple:
        """Finds a custom list with room for more items (< 100), or creates a new one.
        Returns (slug, list_name) tuple. Creates overflow lists as needed."""
        if list_names is None:
            list_names = self.config.trakt_custom_list_names

        self.authenticate()
        url = f"{API_BASE_URL}/users/me/lists"
        try:
            r = requests.get(url, headers=self.headers)
            if r.status_code != 200:
                self.logger.error(f"Failed to get user lists. Status: {r.status_code}, Response: {r.text[:300]}")
            r.raise_for_status()
            existing_lists = r.json()
        except Exception as e:
            raise TraktClientError(f"Failed to retrieve user lists: {e}")

        # Build a lookup of existing lists by slug
        existing_by_slug = {}
        for lst in existing_lists:
            slug = lst["ids"]["slug"]
            existing_by_slug[slug] = lst

        # Try each configured list name in order
        for list_name in list_names:
            target_slug = list_name.lower().replace(" ", "-")
            if target_slug in existing_by_slug:
                lst = existing_by_slug[target_slug]
                item_count = lst.get("item_count", 0)
                self.logger.info(f"[Trakt] Found list '{lst['name']}' (slug: {target_slug}, items: {item_count})")
                if item_count < 100:
                    self.logger.info(f"[Trakt] Using list '{lst['name']}' — {item_count}/100 items, {100 - item_count} slots available")
                    return target_slug, lst['name']
                else:
                    self.logger.info(f"[Trakt] List '{lst['name']}' is full ({item_count}/100), trying next list...")
                    continue
            else:
                # List doesn't exist yet, create it
                self.logger.info(f"[Trakt] List '{list_name}' not found, creating it...")
                try:
                    new_list = self.create_custom_list_with_name(list_name)
                    return new_list["ids"]["slug"], list_name
                except TraktClientError as e:
                    self.logger.error(f"[Trakt] Failed to create list '{list_name}': {e}")
                    continue

        # All configured lists are full and we can't create more
        self.logger.error(f"[Trakt] All {len(list_names)} configured lists are full (100 items each). Cannot add more movies.")
        return None, None

    def add_to_custom_list(self, list_slug: str, movies: List[Dict[str, Any]]) -> bool:
        """Adds a list of movies (using Trakt structures) to a custom list, in batches of 50."""
        if not movies:
            return True

        self.authenticate()
        url = f"{API_BASE_URL}/users/me/lists/{list_slug}/items"
        self.logger.info(f"[Trakt] add_to_custom_list: Adding {len(movies)} movies to list '{list_slug}'")

        # Reformat movies to match Trakt input schema
        movies_payload = []
        for m in movies:
            movie_obj = m.get("movie", m)
            movies_payload.append({
                "ids": {
                    "trakt": movie_obj["ids"]["trakt"]
                }
            })

        # Batch in groups of 50 to avoid large payloads
        batch_size = 50
        total_added = 0
        total_skipped = 0
        for i in range(0, len(movies_payload), batch_size):
            batch = movies_payload[i:i + batch_size]
            payload = {"movies": batch}
            self.logger.info(f"[Trakt] add_to_custom_list: Sending batch {i//batch_size + 1} ({len(batch)} movies) to {url}")
            try:
                throttle(1.0, 0.3)
                r = requests.post(url, json=payload, headers=self.headers)
                self.logger.info(f"[Trakt] add_to_custom_list: Response status={r.status_code}, body={r.text[:200]}")

                # Handle rate limiting (429)
                if r.status_code == 429:
                    retry_after = int(r.headers.get("Retry-After", 1))
                    self.logger.warning(f"Trakt rate limited (429). Waiting {retry_after}s...")
                    time.sleep(retry_after + random.uniform(0.5, 1.5))
                    r = requests.post(url, json=payload, headers=self.headers)

                # Handle account limit exceeded (420) — fall back to single-item adds
                if r.status_code == 420:
                    self.logger.warning(f"Trakt 420 (account limit) on batch {i//batch_size + 1}. Response: {r.text[:300]}")
                    self.logger.warning("Falling back to single-item adds. Some items may exceed your Trakt free account limits.")
                    time.sleep(2)
                    for single in batch:
                        single_payload = {"movies": [single]}
                        throttle(1.0, 0.3)
                        sr = requests.post(url, json=single_payload, headers=self.headers)
                        if sr.status_code == 429:
                            retry_after = int(sr.headers.get("Retry-After", 1))
                            self.logger.warning(f"Trakt rate limited (429) on single item. Waiting {retry_after}s...")
                            time.sleep(retry_after + random.uniform(0.5, 1.5))
                            sr = requests.post(url, json=single_payload, headers=self.headers)
                        if sr.status_code == 420:
                            self.logger.warning(f"Skipped movie (trakt_id={single['ids']['trakt']}): Trakt account limit reached (420). Response: {sr.text[:200]}")
                            total_skipped += 1
                        elif sr.status_code not in (200, 201, 204):
                            self.logger.warning(f"Failed to add movie (trakt_id={single['ids']['trakt']}): {sr.status_code} {sr.text[:200]}")
                            total_skipped += 1
                        else:
                            total_added += 1
                elif r.status_code not in (200, 201, 204):
                    self.logger.error(f"Trakt API error {r.status_code} adding to custom list: {r.text[:300]}")
                    r.raise_for_status()
                else:
                    total_added += len(batch)

            except requests.exceptions.HTTPError as e:
                raise TraktClientError(f"Failed to add movies to custom list '{list_slug}' (batch starting at {i}): {e}")
            except Exception as e:
                raise TraktClientError(f"Failed to add movies to custom list '{list_slug}' (batch starting at {i}): {e}")

        if total_skipped > 0:
            self.logger.warning(f"Skipped {total_skipped} movies due to Trakt account limits. Free accounts are limited to 100 items per list.")
        self.logger.info(f"Added {total_added} movies to custom list '{list_slug}' (skipped {total_skipped}).")
        return True

    def remove_from_watchlist(self, movies: List[Dict[str, Any]]) -> bool:
        """Removes a list of movies from the default Trakt watchlist, in batches of 50."""
        if not movies:
            return True

        self.authenticate()
        url = f"{API_BASE_URL}/sync/watchlist/remove"

        movies_payload = []
        for m in movies:
            movie_obj = m.get("movie", m)
            movies_payload.append({
                "ids": {
                    "trakt": movie_obj["ids"]["trakt"]
                }
            })

        batch_size = 50
        total_removed = 0
        total_skipped = 0
        for i in range(0, len(movies_payload), batch_size):
            batch = movies_payload[i:i + batch_size]
            payload = {"movies": batch}
            try:
                throttle(1.0, 0.3)
                r = requests.post(url, json=payload, headers=self.headers)

                # Handle rate limiting (429)
                if r.status_code == 429:
                    retry_after = int(r.headers.get("Retry-After", 1))
                    self.logger.warning(f"Trakt rate limited (429). Waiting {retry_after}s...")
                    time.sleep(retry_after + random.uniform(0.5, 1.5))
                    r = requests.post(url, json=payload, headers=self.headers)

                # Handle account limit exceeded (420)
                if r.status_code == 420:
                    self.logger.warning(f"Trakt 420 (account limit) removing from watchlist batch {i//batch_size + 1}. Response: {r.text[:300]}")
                    self.logger.warning("Falling back to single-item removes.")
                    time.sleep(2)
                    for single in batch:
                        single_payload = {"movies": [single]}
                        throttle(1.0, 0.3)
                        sr = requests.post(url, json=single_payload, headers=self.headers)
                        if sr.status_code == 429:
                            retry_after = int(sr.headers.get("Retry-After", 1))
                            time.sleep(retry_after + random.uniform(0.5, 1.5))
                            sr = requests.post(url, json=single_payload, headers=self.headers)
                        if sr.status_code == 420:
                            self.logger.warning(f"Skipped remove (trakt_id={single['ids']['trakt']}): Trakt account limit (420). Response: {sr.text[:200]}")
                            total_skipped += 1
                        elif sr.status_code not in (200, 201, 204):
                            self.logger.warning(f"Failed to remove movie (trakt_id={single['ids']['trakt']}): {sr.status_code} {sr.text[:200]}")
                            total_skipped += 1
                        else:
                            total_removed += 1
                elif r.status_code not in (200, 201, 204):
                    self.logger.error(f"Trakt API error {r.status_code} removing from watchlist: {r.text[:300]}")
                    r.raise_for_status()
                else:
                    total_removed += len(batch)

            except requests.exceptions.HTTPError as e:
                raise TraktClientError(f"Failed to remove movies from watchlist (batch starting at {i}): {e}")
            except Exception as e:
                raise TraktClientError(f"Failed to remove movies from watchlist (batch starting at {i}): {e}")

        if total_skipped > 0:
            self.logger.warning(f"Skipped {total_skipped} movies during watchlist removal due to Trakt limits.")
        self.logger.info(f"Removed {total_removed} movies from default watchlist (skipped {total_skipped}).")
        return True

    def get_custom_list_movies(self, list_slug: str) -> List[Dict[str, Any]]:
        """Retrieves items inside a custom list."""
        self.authenticate()
        url = f"{API_BASE_URL}/users/me/lists/{list_slug}/items/movies"
        try:
            r = requests.get(url, headers=self.headers)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            raise TraktClientError(f"Failed to fetch movies from custom list '{list_slug}': {e}")

    def add_single_to_custom_list(self, list_slug: str, trakt_id: int, title: str = "") -> bool:
        """Adds a single movie to a custom list by trakt_id. Returns True if successful.
        If the list is full (420), automatically tries overflow lists."""
        self.authenticate()
        url = f"{API_BASE_URL}/users/me/lists/{list_slug}/items"
        payload = {"movies": [{"ids": {"trakt": trakt_id}}]}
        try:
            throttle(1.0, 0.3)
            r = requests.post(url, json=payload, headers=self.headers)
            if r.status_code == 429:
                retry_after = int(r.headers.get("Retry-After", 1))
                self.logger.warning(f"[Trakt] Rate limited (429) adding '{title}' (trakt_id={trakt_id}). Waiting {retry_after}s...")
                time.sleep(retry_after + random.uniform(0.5, 1.5))
                r = requests.post(url, json=payload, headers=self.headers)
            if r.status_code == 420:
                # List is full — try overflow lists
                self.logger.warning(f"[Trakt] List '{list_slug}' is full (420). Trying overflow lists for '{title}'...")
                list_names = self.config.trakt_custom_list_names
                overflow_slug, overflow_name = self.get_list_with_room(list_names)
                if overflow_slug and overflow_slug != list_slug:
                    self.logger.info(f"[Trakt] Using overflow list '{overflow_name}' (slug: {overflow_slug}) for '{title}'")
                    return self.add_single_to_custom_list(overflow_slug, trakt_id, title)
                else:
                    self.logger.warning(f"[Trakt] Skipped '{title}' (trakt_id={trakt_id}): all lists full (420). Response: {r.text[:200]}")
                    return False
            if r.status_code not in (200, 201, 204):
                self.logger.warning(f"[Trakt] Failed to add '{title}' (trakt_id={trakt_id}): {r.status_code} {r.text[:200]}")
                return False
            self.logger.info(f"[Trakt] Added '{title}' to custom list '{list_slug}'")
            return True
        except Exception as e:
            self.logger.error(f"[Trakt] Error adding '{title}' (trakt_id={trakt_id}) to custom list: {e}")
            return False

    def remove_single_from_watchlist(self, trakt_id: int, title: str = "") -> bool:
        """Removes a single movie from the default watchlist by trakt_id. Returns True if successful."""
        self.authenticate()
        url = f"{API_BASE_URL}/sync/watchlist/remove"
        payload = {"movies": [{"ids": {"trakt": trakt_id}}]}
        try:
            throttle(1.0, 0.3)
            r = requests.post(url, json=payload, headers=self.headers)
            if r.status_code == 429:
                retry_after = int(r.headers.get("Retry-After", 1))
                self.logger.warning(f"[Trakt] Rate limited (429) removing '{title}' (trakt_id={trakt_id}). Waiting {retry_after}s...")
                time.sleep(retry_after + random.uniform(0.5, 1.5))
                r = requests.post(url, json=payload, headers=self.headers)
            if r.status_code not in (200, 201, 204):
                self.logger.warning(f"[Trakt] Failed to remove '{title}' (trakt_id={trakt_id}) from watchlist: {r.status_code} {r.text[:200]}")
                return False
            self.logger.info(f"[Trakt] Removed '{title}' from default watchlist")
            return True
        except Exception as e:
            self.logger.error(f"[Trakt] Error removing '{title}' (trakt_id={trakt_id}) from watchlist: {e}")
            return False

    def get_watched_movies(self) -> List[Dict[str, Any]]:
        """Retrieves the user's watched history for movies."""
        self.authenticate()
        url = f"{API_BASE_URL}/sync/watched/movies"
        try:
            throttle(1.0, 0.3)
            r = requests.get(url, headers=self.headers)
            if r.status_code == 429:
                retry_after = int(r.headers.get("Retry-After", 1))
                self.logger.warning(f"Trakt rate limited (429). Waiting {retry_after}s...")
                time.sleep(retry_after + random.uniform(0.5, 1.5))
                r = requests.get(url, headers=self.headers)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            raise TraktClientError(f"Failed to fetch watched history: {e}")

    def get_ratings(self) -> Dict[int, int]:
        """Retrieves the user's movie ratings from Trakt. Returns {trakt_id: rating}."""
        self.authenticate()
        url = f"{API_BASE_URL}/users/me/ratings/movies"
        try:
            throttle(1.0, 0.3)
            r = requests.get(url, headers=self.headers)
            if r.status_code == 429:
                retry_after = int(r.headers.get("Retry-After", 1))
                self.logger.warning(f"Trakt rate limited (429). Waiting {retry_after}s...")
                time.sleep(retry_after + random.uniform(0.5, 1.5))
                r = requests.get(url, headers=self.headers)
            r.raise_for_status()
            ratings = r.json()
            result = {}
            for entry in ratings:
                movie = entry.get("movie", {})
                trakt_id = movie.get("ids", {}).get("trakt")
                rating = entry.get("rating")
                if trakt_id and rating:
                    result[trakt_id] = rating
            return result
        except Exception as e:
            raise TraktClientError(f"Failed to fetch ratings: {e}")
