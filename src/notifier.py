import logging
import urllib.parse
from typing import Optional
import requests
from src.config import Config

class NotifierError(Exception):
    """Custom exception for Notifier errors."""
    pass

class Notifier:
    def __init__(self, config: Config, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.api_url = "https://api.pushover.net/1/messages.json"

    def send_notification(
        self,
        message: str,
        title: Optional[str] = None,
        url: Optional[str] = None,
        url_title: Optional[str] = None,
        priority: int = 0
    ) -> bool:
        """
        Sends a notification via Pushover API.
        """
        payload = {
            "token": self.config.pushover_api_token,
            "user": self.config.pushover_user_key,
            "message": message,
            "priority": priority
        }
        
        if title:
            payload["title"] = title
        if url:
            payload["url"] = url
        if url_title:
            payload["url_title"] = url_title
            
        self.logger.info(f"Sending Pushover notification (priority={priority}): '{title or ''} - {message}'")
        
        try:
            # Pushover API expects application/x-www-form-urlencoded
            r = requests.post(self.api_url, data=payload, timeout=15)
            r.raise_for_status()
            
            data = r.json()
            if data.get("status") == 1:
                self.logger.debug("Pushover notification delivered successfully.")
                return True
            else:
                self.logger.error(f"Pushover returned failure status: {data}")
                return False
        except Exception as e:
            self.logger.error(f"Failed to send Pushover notification: {e}")
            return False

    def send_movie_watched_notification(self, movie_title: str, movie_year: int,
                                          letterboxd_slug: Optional[str] = None) -> bool:
        """
        Sends a notification to rate and review a movie on Letterboxd on iOS.
        
        If a letterboxd_slug is provided, uses the Letterboxd web URL which opens
        directly in the Letterboxd app on iOS (via Universal Links), taking the user
        to the exact film page where they can tap "Log" to rate/review.
        
        Falls back to the x-callback-url deep link with the movie name as a search
        query if no slug is available.
        """
        if letterboxd_slug:
            # Universal Link — opens directly in the Letterboxd app on iOS
            # to the exact film page. User can then tap "Log" to rate/review.
            url = f"https://letterboxd.com/film/{letterboxd_slug}/"
            url_title = "Open in Letterboxd"
            message = f"You watched '{movie_title}' ({movie_year}) on Trakt. Tap to open in Letterboxd and rate it!"
        else:
            # Fallback: x-callback-url with name search (requires manual confirmation)
            encoded_title = urllib.parse.quote_plus(f"{movie_title} {movie_year}")
            url = f"letterboxd://x-callback-url/log?name={encoded_title}"
            url_title = "Log & Review on Letterboxd"
            message = f"You watched '{movie_title}' ({movie_year}) on Trakt. Tap to rate and review it in the Letterboxd app!"
        
        title = "Movie Watched"
        
        return self.send_notification(
            message=message,
            title=title,
            url=url,
            url_title=url_title,
            priority=0
        )

    def send_error_notification(self, error_message: str) -> bool:
        """
        Sends a high-priority notification to alert the user of a critical sync error.
        Priority = 1 bypasses quiet hours and makes it stand out.
        """
        message = f"Critical Error Alert: {error_message}"
        title = "pwListManager Sync Broken"
        
        return self.send_notification(
            message=message,
            title=title,
            priority=1
        )
