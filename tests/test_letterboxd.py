import unittest
from unittest.mock import patch, MagicMock
import json
import logging
from src.config import Config
from src.letterboxd_client import LetterboxdClient, LetterboxdClientError

class TestLetterboxdClient(unittest.TestCase):
    def setUp(self):
        logging.basicConfig(level=logging.CRITICAL)
        self.logger = logging.getLogger("test")
        self.mock_config = MagicMock(spec=Config)
        self.mock_config.letterboxd_username = "testuser"
        self.mock_config.letterboxd_password = "testpass"

    @patch("src.letterboxd_client.cf_requests.Session")
    def test_login_success(self, MockSession):
        mock_session = MockSession.return_value
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = json.dumps({"result": "success", "csrf": "test_csrf_123", "messages": []})
        mock_session.post.return_value = mock_response
        mock_session.get.return_value = mock_response
        mock_session.cookies.items.return_value = [("com.xk72.webparts.csrf", "page_csrf")]

        client = LetterboxdClient(self.mock_config, self.logger)
        self.assertTrue(client._authenticated)
        self.assertEqual(client._csrf_token, "test_csrf_123")

    @patch("src.letterboxd_client.cf_requests.Session")
    def test_login_failure(self, MockSession):
        mock_session = MockSession.return_value
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = json.dumps({"result": "fail", "messages": ["Invalid credentials"]})
        mock_session.get.return_value = mock_response
        mock_session.post.return_value = mock_response
        mock_session.cookies.items.return_value = [("com.xk72.webparts.csrf", "page_csrf")]

        with self.assertRaises(LetterboxdClientError):
            LetterboxdClient(self.mock_config, self.logger)

    @patch("src.letterboxd_client.cf_requests.Session")
    def test_resolve_tmdb_id_to_slug(self, MockSession):
        mock_session = MockSession.return_value
        mock_login = MagicMock()
        mock_login.status_code = 200
        mock_login.text = json.dumps({"result": "success", "csrf": "csrf123"})

        mock_redirect = MagicMock()
        mock_redirect.status_code = 200
        mock_redirect.url = "https://letterboxd.com/film/interstellar/"

        mock_session.get.side_effect = [mock_login, mock_redirect]
        mock_session.post.return_value = mock_login
        mock_session.cookies.items.return_value = [("com.xk72.webparts.csrf", "csrf123")]

        client = LetterboxdClient(self.mock_config, self.logger)
        slug = client.resolve_tmdb_id_to_slug(157336)
        self.assertEqual(slug, "interstellar")

    @patch("src.letterboxd_client.cf_requests.Session")
    def test_add_to_watchlist(self, MockSession):
        mock_session = MockSession.return_value
        mock_login = MagicMock()
        mock_login.status_code = 200
        mock_login.text = json.dumps({"result": "success", "csrf": "csrf123"})

        mock_resolve = MagicMock()
        mock_resolve.status_code = 200
        mock_resolve.url = "https://letterboxd.com/film/inception/"

        mock_wl_response = MagicMock()
        mock_wl_response.status_code = 200
        mock_wl_response.text = json.dumps({"result": True, "csrf": "csrf456", "messages": ["Added to watchlist"]})

        mock_session.get.side_effect = [mock_login, mock_resolve]
        mock_session.post.side_effect = [mock_login, mock_wl_response]
        mock_session.cookies.items.return_value = [("com.xk72.webparts.csrf", "csrf123")]

        client = LetterboxdClient(self.mock_config, self.logger)
        result = client.add_to_watchlist(27205)
        self.assertTrue(result)

    @patch("src.letterboxd_client.cf_requests.Session")
    def test_mark_watched(self, MockSession):
        mock_session = MockSession.return_value
        mock_login = MagicMock()
        mock_login.status_code = 200
        mock_login.text = json.dumps({"result": "success", "csrf": "csrf123"})

        mock_resolve = MagicMock()
        mock_resolve.status_code = 200
        mock_resolve.url = "https://letterboxd.com/film/the-dark-knight/"

        mock_film_page = MagicMock()
        mock_film_page.status_code = 200
        mock_film_page.text = '<script id="production-data" type="application/json">{"identifier":{"uid":"film:51896","lid":"2b0k","type":"film"},"name":"The Dark Knight","nameAndYear":"The Dark Knight (2008)"}</script>'

        mock_watched_response = MagicMock()
        mock_watched_response.status_code = 200
        mock_watched_response.text = json.dumps({"result": True, "csrf": "csrf789", "watched": True, "watchable": {"id": 51896}})

        mock_session.get.side_effect = [mock_login, mock_resolve, mock_film_page]
        mock_session.post.side_effect = [mock_login, mock_watched_response]
        mock_session.cookies.items.return_value = [("com.xk72.webparts.csrf", "csrf123")]

        client = LetterboxdClient(self.mock_config, self.logger)
        result = client.mark_watched(155)
        self.assertTrue(result)

    @patch("src.letterboxd_client.cf_requests.Session")
    def test_resolve_tmdb_not_found(self, MockSession):
        mock_session = MockSession.return_value
        mock_login = MagicMock()
        mock_login.status_code = 200
        mock_login.text = json.dumps({"result": "success", "csrf": "csrf123"})

        mock_redirect = MagicMock()
        mock_redirect.status_code = 200
        mock_redirect.url = "https://letterboxd.com/film/notarealfilmslug/"
        mock_session.get.side_effect = [mock_login, mock_redirect]
        mock_session.post.return_value = mock_login
        mock_session.cookies.items.return_value = [("com.xk72.webparts.csrf", "csrf123")]

        client = LetterboxdClient(self.mock_config, self.logger)
        slug = client.resolve_tmdb_id_to_slug(99999999)
        self.assertEqual(slug, "notarealfilmslug")

if __name__ == "__main__":
    unittest.main()