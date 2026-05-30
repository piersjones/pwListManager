import unittest
from unittest.mock import patch, MagicMock
import logging
from src.config import Config
from src.notifier import Notifier

class TestNotifier(unittest.TestCase):
    def setUp(self):
        logging.basicConfig(level=logging.CRITICAL)
        self.logger = logging.getLogger("test")
        
        # Mock Config
        self.mock_config = MagicMock(spec=Config)
        self.mock_config.pushover_user_key = "user_key_123"
        self.mock_config.pushover_api_token = "api_token_123"

    @patch("src.notifier.requests.post")
    def test_send_notification_success(self, mock_post):
        notifier = Notifier(self.mock_config, self.logger)
        
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": 1, "request": "req_123"}
        mock_post.return_value = mock_resp
        
        success = notifier.send_notification("Test Message", "Test Title", priority=0)
        self.assertTrue(success)
        
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "https://api.pushover.net/1/messages.json")
        self.assertEqual(kwargs["data"]["message"], "Test Message")
        self.assertEqual(kwargs["data"]["title"], "Test Title")
        self.assertEqual(kwargs["data"]["token"], "api_token_123")
        self.assertEqual(kwargs["data"]["user"], "user_key_123")
        self.assertEqual(kwargs["data"]["priority"], 0)

    @patch("src.notifier.requests.post")
    def test_send_movie_watched_notification_with_slug(self, mock_post):
        notifier = Notifier(self.mock_config, self.logger)
        
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": 1}
        mock_post.return_value = mock_resp
        
        success = notifier.send_movie_watched_notification("Everything Everywhere All at Once", 2022, letterboxd_slug="everything-everywhere-all-at-once-2022")
        self.assertTrue(success)
        
        # Check that the URL uses the Letterboxd web URL with slug (Universal Link)
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(kwargs["data"]["url"], "https://letterboxd.com/film/everything-everywhere-all-at-once-2022/")
        self.assertEqual(kwargs["data"]["url_title"], "Open in Letterboxd")
        self.assertEqual(kwargs["data"]["priority"], 0)

    @patch("src.notifier.requests.post")
    def test_send_movie_watched_notification_without_slug(self, mock_post):
        notifier = Notifier(self.mock_config, self.logger)
        
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": 1}
        mock_post.return_value = mock_resp
        
        success = notifier.send_movie_watched_notification("Everything Everywhere All at Once", 2022)
        self.assertTrue(success)
        
        # Check that the URL falls back to x-callback-url with name search
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertIn("letterboxd://x-callback-url/log?name=", kwargs["data"]["url"])
        self.assertIn("Everything+Everywhere+All+at+Once+2022", kwargs["data"]["url"])
        self.assertEqual(kwargs["data"]["url_title"], "Log & Review on Letterboxd")

    @patch("src.notifier.requests.post")
    def test_send_error_notification(self, mock_post):
        notifier = Notifier(self.mock_config, self.logger)
        
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": 1}
        mock_post.return_value = mock_resp
        
        success = notifier.send_error_notification("Trakt API 401 Unauthorized")
        self.assertTrue(success)
        
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(kwargs["data"]["priority"], 1)
        self.assertEqual(kwargs["data"]["title"], "pwListManager Sync Broken")
        self.assertIn("Trakt API 401 Unauthorized", kwargs["data"]["message"])

if __name__ == "__main__":
    unittest.main()
