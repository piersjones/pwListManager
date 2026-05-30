import json
import logging
import re
import time
import random
import urllib.parse
from typing import Any, Optional, Tuple
from curl_cffi import requests as cf_requests
from src.config import Config
from src.rate_limiter import throttle, retry_with_backoff

class LetterboxdClientError(Exception):
    pass

class LetterboxdClient:
    def __init__(self, config: Config, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.session = cf_requests.Session(impersonate="chrome")
        self._csrf_token: Optional[str] = None
        self._authenticated = False
        self._login_attempts = 0
        self._max_login_attempts = 3
        self._login()

    def _login(self):
        self._login_attempts += 1
        if self._login_attempts > self._max_login_attempts:
            raise LetterboxdClientError(f"Exceeded max login attempts ({self._max_login_attempts}).")

        self.logger.info("Visiting Letterboxd homepage to establish Cloudflare session...")
        try:
            r = self.session.get("https://letterboxd.com/", timeout=15)
            if r.status_code == 403:
                raise LetterboxdClientError("Cloudflare is blocking requests (403). Try again later or switch impersonation profile.")
            if r.status_code != 200:
                raise LetterboxdClientError(f"Failed to reach Letterboxd homepage (status {r.status_code}).")
            self.logger.info("Cloudflare session established.")
        except LetterboxdClientError:
            raise
        except Exception as e:
            raise LetterboxdClientError(f"Failed to reach Letterboxd: {e}")

        throttle(2.0, 0.5)

        csrf_token = None
        for name, value in self.session.cookies.items():
            if name == "com.xk72.webparts.csrf":
                csrf_token = value
                break
        if not csrf_token:
            raise LetterboxdClientError("Could not obtain CSRF token from Letterboxd homepage.")

        password = self.config.letterboxd_password
        if not password:
            raise LetterboxdClientError(
                "Letterboxd password not configured. Set letterboxd.password in config.yaml or LETTERBOXD_PASSWORD env var."
            )

        self.logger.info(f"Logging into Letterboxd as '{self.config.letterboxd_username}'...")
        login_url = "https://letterboxd.com/user/login.do"
        headers = {
            "Referer": "https://letterboxd.com/sign-in/",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://letterboxd.com",
        }
        payload = {
            "__csrf": csrf_token,
            "username": self.config.letterboxd_username,
            "password": password,
            "authenticationCode": "",
        }

        try:
            r = self.session.post(login_url, data=payload, headers=headers, timeout=15, allow_redirects=False)
        except Exception as e:
            raise LetterboxdClientError(f"Login request failed: {e}")

        if r.status_code == 429:
            retry_after = int(r.headers.get("Retry-After", 60))
            self.logger.warning(f"Rate limited during login (429). Retry-After: {retry_after}s")
            raise LetterboxdClientError(f"Rate limited during login. Retry after {retry_after}s.")

        if r.status_code != 200:
            raise LetterboxdClientError(f"Login failed with status {r.status_code}.")

        try:
            data = json.loads(r.text)
        except json.JSONDecodeError:
            raise LetterboxdClientError(f"Login returned non-JSON response: {r.text[:200]}")

        if data.get("result") != "success":
            messages = data.get("messages", [])
            raise LetterboxdClientError(f"Login failed: {messages}")

        new_csrf = data.get("csrf")
        if new_csrf:
            self._csrf_token = new_csrf
        self.logger.info("Letterboxd login successful.")
        self._authenticated = True
        self._login_attempts = 0

    def _relogin(self):
        """Re-authenticate after session expiry."""
        self.logger.warning("Re-authenticating to Letterboxd...")
        self.session = cf_requests.Session(impersonate="chrome")
        self._csrf_token = None
        self._authenticated = False
        self._login()

    def _refresh_csrf(self) -> str:
        if self._csrf_token:
            return self._csrf_token
        for name, value in self.session.cookies.items():
            if name == "com.xk72.webparts.csrf":
                self._csrf_token = value
                return value
        raise LetterboxdClientError("CSRF token not found in session.")

    def _post_action(self, url: str, payload: dict, retry_on_auth: bool = True) -> dict:
        csrf = self._refresh_csrf()
        payload["__csrf"] = csrf
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://letterboxd.com/",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": "https://letterboxd.com",
        }
        try:
            r = self.session.post(url, data=payload, headers=headers, timeout=15, allow_redirects=False)
            if r.status_code == 429:
                retry_after = int(r.headers.get("Retry-After", 60))
                self.logger.warning(f"Rate limited (429) on {url}. Retry-After: {retry_after}s")
                time.sleep(retry_after + random.uniform(1, 3))
                return self._post_action(url, payload, retry_on_auth=False)

            if r.status_code in (401, 403) and retry_on_auth:
                self.logger.warning(f"Auth error ({r.status_code}) on {url}. Re-authenticating...")
                self._relogin()
                throttle(2.0, 0.5)
                return self._post_action(url, payload, retry_on_auth=False)

            r.raise_for_status()
            time.sleep(random.uniform(3.0, 5.0))
            data = json.loads(r.text)
            new_csrf = data.get("csrf")
            if new_csrf:
                self._csrf_token = new_csrf

            error_codes = data.get("errorCodes", [])
            if "must.be.logged.in" in error_codes and retry_on_auth:
                self.logger.warning("Session expired (must.be.logged.in). Re-authenticating...")
                self._relogin()
                throttle(2.0, 0.5)
                return self._post_action(url, payload, retry_on_auth=False)

            return data
        except json.JSONDecodeError:
            raise LetterboxdClientError(f"Non-JSON response from {url}: {r.text[:200]}")
        except Exception as e:
            raise LetterboxdClientError(f"POST to {url} failed: {e}")

    def _get_with_retry(self, url: str, **kwargs) -> cf_requests.Response:
        """GET request with retry on transient failures and 429 handling."""
        def _do_get():
            r = self.session.get(url, timeout=15, **kwargs)
            if r.status_code == 429:
                retry_after = int(r.headers.get("Retry-After", 60))
                self.logger.warning(f"Rate limited (429) on GET {url}. Waiting {retry_after}s...")
                time.sleep(retry_after + random.uniform(1, 3))
                return self._get_with_retry(**kwargs)
            if r.status_code in (401, 403):
                self.logger.warning(f"Auth error ({r.status_code}) on GET {url}. May need re-login.")
            r.raise_for_status()
            throttle(1.0, 0.5)
            return r
        return _do_get()

    def resolve_tmdb_id_to_slug(self, tmdb_id: Any) -> str:
        url = f"https://letterboxd.com/tmdb/{tmdb_id}"
        self.logger.info(f"Resolving TMDb ID {tmdb_id} to Letterboxd slug...")
        try:
            r = retry_with_backoff(
                lambda: self._get_with_retry(url, allow_redirects=True),
                max_retries=3,
                base_delay=5.0,
                logger_instance=self.logger,
            )
            final_url = r.url
            self.logger.debug(f"Resolved URL: {final_url}")
            parsed_path = urllib.parse.urlparse(final_url).path
            parts = [p for p in parsed_path.split("/") if p]
            if len(parts) >= 2 and parts[0] == "film":
                slug = parts[1]
                self.logger.info(f"TMDb ID {tmdb_id} resolved to slug: '{slug}'")
                return slug
            else:
                raise LetterboxdClientError(f"Could not parse film slug from redirect URL: {final_url}")
        except LetterboxdClientError:
            raise
        except Exception as e:
            raise LetterboxdClientError(f"Failed to resolve TMDb ID {tmdb_id} to Letterboxd slug: {e}")

    def _get_film_uid(self, slug: str) -> str:
        url = f"https://letterboxd.com/film/{slug}/"
        try:
            r = retry_with_backoff(
                lambda: self._get_with_retry(url),
                max_retries=3,
                base_delay=5.0,
                logger_instance=self.logger,
            )
            html = r.text
            match = re.search(r'<script[^>]*id="production-data"[^>]*type="application/json"[^>]*>(.*?)</script>', html, re.DOTALL)
            if match:
                data = json.loads(match.group(1))
                uid = data.get("identifier", {}).get("uid", "")
                if uid:
                    self.logger.debug(f"Extracted UID for '{slug}': {uid}")
                    return uid
            raise LetterboxdClientError(f"Could not extract film UID from page for '{slug}'.")
        except LetterboxdClientError:
            raise
        except Exception as e:
            raise LetterboxdClientError(f"Failed to fetch film page for UID extraction '{slug}': {e}")

    def get_film_status(self, slug: str) -> dict:
        """Fetch film page and extract user interaction status and UID.

        Returns dict with:
            uid: str or None - Film UID (e.g., "film:51896")
            on_watchlist: bool or None - True if on user's watchlist, False if not, None if unknown
            watched: bool or None - True if watched by user, False if not, None if unknown
        """
        url = f"https://letterboxd.com/film/{slug}/"
        self.logger.info(f"Fetching film status for '{slug}'...")
        try:
            r = retry_with_backoff(
                lambda: self._get_with_retry(url),
                max_retries=3,
                base_delay=5.0,
                logger_instance=self.logger,
            )
            html = r.text

            # Extract UID from production-data
            uid = None
            prod_match = re.search(
                r'<script[^>]*id="production-data"[^>]*type="application/json"[^>]*>(.*?)</script>',
                html, re.DOTALL
            )
            if prod_match:
                try:
                    data = json.loads(prod_match.group(1))
                    uid = data.get("identifier", {}).get("uid", "")
                except json.JSONDecodeError:
                    pass

            # Check watchlist status from HTML
            # When logged in, the film page includes interaction buttons that reflect current state.
            # If film is on watchlist: page contains "remove-from-watchlist" action
            # If film is NOT on watchlist: page contains "add-to-watchlist" action
            on_watchlist = None
            has_remove_wl = bool(re.search(r'remove-from-watchlist', html, re.IGNORECASE))
            has_add_wl = bool(re.search(r'add-to-watchlist', html, re.IGNORECASE))
            if has_remove_wl and not has_add_wl:
                on_watchlist = True
            elif has_add_wl and not has_remove_wl:
                on_watchlist = False
            elif has_remove_wl:
                # Both present or remove takes precedence — likely on watchlist
                on_watchlist = True
            self.logger.debug(f"Watchlist check for '{slug}': remove_wl={has_remove_wl}, add_wl={has_add_wl} → on_watchlist={on_watchlist}")

            # Check watched status from HTML
            # When logged in, the film page shows watched state via data attributes or class names.
            # Look for data-state="included" near watched/seen indicators.
            watched = None
            # Pattern: data-state="included" near "watched" or "seen" context
            watched_included = bool(re.search(
                r'(?:watched|seen)[^>]*data-state=["\']included["\']', html, re.IGNORECASE
            )) or bool(re.search(
                r'data-state=["\']included["\'][^>]*(?:watched|seen)', html, re.IGNORECASE
            ))
            watched_excluded = bool(re.search(
                r'(?:watched|seen)[^>]*data-state=["\']excluded["\']', html, re.IGNORECASE
            )) or bool(re.search(
                r'data-state=["\']excluded["\'][^>]*(?:watched|seen)', html, re.IGNORECASE
            ))
            if watched_included:
                watched = True
            elif watched_excluded:
                watched = False
            self.logger.debug(f"Watched check for '{slug}': included={watched_included}, excluded={watched_excluded} → watched={watched}")

            self.logger.info(f"Film status for '{slug}': uid={uid}, on_watchlist={on_watchlist}, watched={watched}")
            return {
                "uid": uid,
                "on_watchlist": on_watchlist,
                "watched": watched,
            }
        except LetterboxdClientError:
            raise
        except Exception as e:
            raise LetterboxdClientError(f"Failed to get film status for '{slug}': {e}")

    def add_to_watchlist(self, tmdb_id: Any, slug: Optional[str] = None) -> bool:
        if not slug:
            slug = self.resolve_tmdb_id_to_slug(tmdb_id)
        self.logger.info(f"Adding '{slug}' to Letterboxd watchlist...")
        throttle(3.0, 1.0)
        try:
            data = self._post_action(
                f"https://letterboxd.com/film/{slug}/add-to-watchlist/",
                {}
            )
            if data.get("result") is True:
                self.logger.info(f"Successfully added '{slug}' to Letterboxd watchlist.")
                return True
            else:
                messages = data.get("messages", [])
                msg_text = " ".join(str(m) for m in messages) if messages else str(data)
                if "already" in msg_text.lower():
                    self.logger.info(f"Film '{slug}' was already in watchlist.")
                    return True
                self.logger.warning(f"Unexpected watchlist response: {data}")
                return False
        except LetterboxdClientError as e:
            if "404" in str(e):
                raise LetterboxdClientError(f"Film '{slug}' not found on Letterboxd. TMDb ID may not have a matching Letterboxd entry.")
            raise

    def remove_from_watchlist(self, tmdb_id: Any) -> bool:
        slug = self.resolve_tmdb_id_to_slug(tmdb_id)
        self.logger.info(f"Removing '{slug}' from Letterboxd watchlist...")
        throttle(3.0, 1.0)
        try:
            data = self._post_action(
                f"https://letterboxd.com/film/{slug}/remove-from-watchlist/",
                {}
            )
            if data.get("result") is True:
                self.logger.info(f"Successfully removed '{slug}' from Letterboxd watchlist.")
                return True
            else:
                self.logger.warning(f"Unexpected remove-from-watchlist response: {data}")
                return False
        except LetterboxdClientError:
            raise

    def mark_watched(self, tmdb_id: Any, slug: Optional[str] = None, uid: Optional[str] = None) -> bool:
        if not slug:
            slug = self.resolve_tmdb_id_to_slug(tmdb_id)
        if not uid:
            uid = self._get_film_uid(slug)
        self.logger.info(f"Marking '{slug}' as watched on Letterboxd...")
        throttle(3.0, 1.0)
        try:
            data = self._post_action(
                f"https://letterboxd.com/s/{uid}/watch/",
                {"watched": "true"}
            )
            if data.get("result") is True:
                self.logger.info(f"Successfully marked '{slug}' as watched on Letterboxd.")
                return True
            else:
                self.logger.warning(f"Unexpected watched response: {data}")
                return True
        except LetterboxdClientError:
            raise

    def unmark_watched(self, tmdb_id: Any) -> bool:
        slug = self.resolve_tmdb_id_to_slug(tmdb_id)
        uid = self._get_film_uid(slug)
        self.logger.info(f"Unmarking '{slug}' as watched on Letterboxd...")
        throttle(3.0, 1.0)
        try:
            data = self._post_action(
                f"https://letterboxd.com/s/{uid}/watch/",
                {"watched": "false"}
            )
            if data.get("result") is True:
                self.logger.info(f"Successfully unmarked '{slug}' as watched on Letterboxd.")
                return True
            else:
                self.logger.warning(f"Unexpected unmark-watched response: {data}")
                return False
        except LetterboxdClientError:
            raise

    def rate_film(self, slug: str, trakt_rating: int, uid: Optional[str] = None) -> bool:
        """Rate a film on Letterboxd. Trakt uses 1-10; we convert to 0.5-5 stars (half-star steps)."""
        if not (1 <= trakt_rating <= 10):
            self.logger.warning(f"Invalid Trakt rating {trakt_rating} for '{slug}'. Skipping.")
            return False
        lb_rating = trakt_rating / 2.0
        if not uid:
            uid = self._get_film_uid(slug)
        self.logger.info(f"Rating '{slug}' {lb_rating} stars on Letterboxd (Trakt rating: {trakt_rating})...")
        throttle(3.0, 1.0)
        try:
            data = self._post_action(
                f"https://letterboxd.com/s/{uid}/rate/",
                {"rating": f"{lb_rating}"}
            )
            if data.get("result") is True:
                self.logger.info(f"Successfully rated '{slug}' {lb_rating} stars on Letterboxd.")
                return True
            else:
                self.logger.warning(f"Unexpected rating response for '{slug}': {data}")
                return False
        except LetterboxdClientError:
            raise