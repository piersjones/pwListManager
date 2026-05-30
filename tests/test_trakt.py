import unittest
from unittest.mock import patch, MagicMock, mock_open
import os
import json
import logging
from src.config import Config
from src.trakt_client import TraktClient, TraktClientError

class TestTraktClient(unittest.TestCase):
    def setUp(self):
        # Configure logging to suppress verbose outputs during test
        logging.basicConfig(level=logging.CRITICAL)
        self.logger = logging.getLogger("test")
        
        # Setup mock config
        self.mock_config = MagicMock(spec=Config)
        self.mock_config.trakt_client_id = "fake_client_id"
        self.mock_config.trakt_client_secret = "fake_client_secret"
        self.mock_config.trakt_custom_list_name = "test-custom-watchlist"

    @patch("src.trakt_client.os.path.exists")
    @patch("src.trakt_client.open", new_callable=mock_open, read_data='{"access_token": "token123", "created_at": 9999999999, "expires_in": 3600}')
    def test_load_token_success(self, mock_file, mock_exists):
        mock_exists.return_value = True
        client = TraktClient(self.mock_config, self.logger)
        self.assertEqual(client.token["access_token"], "token123")
        self.assertEqual(client.headers["Authorization"], "Bearer token123")

    @patch("src.trakt_client.os.path.exists")
    @patch("src.trakt_client.requests.post")
    def test_device_auth_flow(self, mock_post, mock_exists):
        mock_exists.return_value = False
        
        # Mock Response for device code
        mock_code_resp = MagicMock()
        mock_code_resp.status_code = 200
        mock_code_resp.json.return_value = {
            "device_code": "dev_code",
            "user_code": "USRCODE",
            "verification_url": "https://trakt.tv/activate",
            "interval": 1,
            "expires_in": 5
        }
        
        # Mock Response for token polling (first pending, then success)
        mock_token_resp_pending = MagicMock()
        mock_token_resp_pending.status_code = 400
        mock_token_resp_pending.json.return_value = {"error": "authorization_pending"}
        
        mock_token_resp_success = MagicMock()
        mock_token_resp_success.status_code = 200
        mock_token_resp_success.json.return_value = {
            "access_token": "access_token_123",
            "refresh_token": "refresh_token_123",
            "expires_in": 7200
        }
        
        mock_post.side_effect = [mock_code_resp, mock_token_resp_pending, mock_token_resp_success]
        
        with patch("src.trakt_client.open", mock_open()) as mock_file:
            client = TraktClient(self.mock_config, self.logger)
            client.authenticate()
            
            self.assertEqual(client.token["access_token"], "access_token_123")
            self.assertEqual(client.token["refresh_token"], "refresh_token_123")
            self.assertIn("Authorization", client.headers)
            self.assertEqual(client.headers["Authorization"], "Bearer access_token_123")

    @patch("src.trakt_client.os.path.exists")
    @patch("src.trakt_client.requests.get")
    def test_get_watchlist_movies(self, mock_get, mock_exists):
        mock_exists.return_value = True
        
        # Setup pre-existing token
        with patch("src.trakt_client.open", mock_open(read_data='{"access_token": "tok", "created_at": 9999999999, "expires_in": 3600}')):
            client = TraktClient(self.mock_config, self.logger)
            
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"movie": {"title": "Inception", "ids": {"trakt": 12}}}]
        mock_get.return_value = mock_resp
        
        movies = client.get_watchlist_movies()
        self.assertEqual(len(movies), 1)
        self.assertEqual(movies[0]["movie"]["title"], "Inception")
        mock_get.assert_called_once_with("https://api.trakt.tv/sync/watchlist/movies", headers=client.headers)

    @patch("src.trakt_client.os.path.exists")
    @patch("src.trakt_client.requests.post")
    @patch("src.trakt_client.requests.get")
    def test_add_to_custom_list(self, mock_get, mock_post, mock_exists):
        mock_exists.return_value = True
        
        with patch("src.trakt_client.open", mock_open(read_data='{"access_token": "tok", "created_at": 9999999999, "expires_in": 3600}')):
            client = TraktClient(self.mock_config, self.logger)
            
        # Mock get lists response
        mock_lists_resp = MagicMock()
        mock_lists_resp.status_code = 200
        mock_lists_resp.json.return_value = [{"name": "test-custom-watchlist", "ids": {"slug": "test-custom-watchlist"}}]
        mock_get.return_value = mock_lists_resp
        
        # Mock post to list response
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 201
        mock_post.return_value = mock_post_resp
        
        movies_to_add = [{"movie": {"title": "Batman", "ids": {"trakt": 99}}}]
        list_slug = client.get_custom_list_id()
        self.assertEqual(list_slug, "test-custom-watchlist")
        
        success = client.add_to_custom_list(list_slug, movies_to_add)
        self.assertTrue(success)
        mock_post.assert_called_once()
        # Verify the trakt payload matches what we expect
        args, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"], {"movies": [{"ids": {"trakt": 99}}]})

if __name__ == "__main__":
    unittest.main()
