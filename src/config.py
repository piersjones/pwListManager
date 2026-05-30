import os
import yaml
from typing import Dict, Any, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")

class ConfigError(Exception):
    """Raised when there is a critical configuration error."""
    pass

class Config:
    def __init__(self, config_path: str = CONFIG_PATH):
        self.config_path = config_path
        self.data: Dict[str, Any] = {}
        self.load()

    def load(self):
        """Loads configuration from YAML file if it exists, otherwise leaves it empty."""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    self.data = yaml.safe_load(f) or {}
            except Exception as e:
                raise ConfigError(f"Failed to read config file at {self.config_path}: {e}")
        else:
            # Not raising error here, as we might rely fully on env vars in Docker
            self.data = {}

    def get_env_or_config(self, env_name: str, config_path_keys: list, default: Any = None) -> Any:
        """Retrieves a configuration value from environment variables first, then YAML config, else returns default."""
        # 1. Check environment variables
        val = os.environ.get(env_name)
        if val is not None:
            return val

        # 2. Check configuration dictionary
        curr = self.data
        for key in config_path_keys:
            if isinstance(curr, dict) and key in curr:
                curr = curr[key]
            else:
                return default
        return curr

    @property
    def trakt_client_id(self) -> str:
        val = self.get_env_or_config("TRAKT_CLIENT_ID", ["trakt", "client_id"])
        if not val:
            raise ConfigError("Missing Trakt client_id in configuration or environment.")
        return val

    @property
    def trakt_client_secret(self) -> str:
        val = self.get_env_or_config("TRAKT_CLIENT_SECRET", ["trakt", "client_secret"])
        if not val:
            raise ConfigError("Missing Trakt client_secret in configuration or environment.")
        return val

    @property
    def trakt_custom_list_name(self) -> str:
        """Returns the primary (first) custom list name."""
        names = self.trakt_custom_list_names
        return names[0] if names else "movie-watchlist"

    @property
    def trakt_custom_list_names(self) -> list:
        """Returns a list of custom list names for overflow support.
        The first name is the primary list, subsequent names are used when the primary fills up (100 items).
        Users specify these explicitly in config (comma-separated) or via the TRAKT_CUSTOM_LIST_NAMES env var."""
        val = self.get_env_or_config("TRAKT_CUSTOM_LIST_NAMES", ["trakt", "custom_list_names"])
        if val:
            if isinstance(val, list):
                return [v.strip() for v in val if v.strip()]
            if isinstance(val, str):
                return [v.strip() for v in val.split(",") if v.strip()]
        # Fallback: check the old single-list config key
        single = self.get_env_or_config("TRAKT_CUSTOM_LIST_NAME", ["trakt", "custom_list_name"])
        if single:
            return [single.strip()] if isinstance(single, str) else [single]
        return ["movie-watchlist"]

    @property
    def letterboxd_username(self) -> str:
        val = self.get_env_or_config("LETTERBOXD_USERNAME", ["letterboxd", "username"])
        if not val:
            raise ConfigError("Missing Letterboxd username in configuration or environment.")
        return val

    @property
    def letterboxd_password(self) -> Optional[str]:
        return self.get_env_or_config("LETTERBOXD_PASSWORD", ["letterboxd", "password"])

    @property
    def letterboxd_raw_cookie_string(self) -> Optional[str]:
        return self.get_env_or_config("LETTERBOXD_RAW_COOKIE_STRING", ["letterboxd", "raw_cookie_string"])

    @property
    def letterboxd_cookies(self) -> Dict[str, str]:
        """Returns parsed letterboxd cookies configuration dictionary."""
        cookies = self.get_env_or_config("LETTERBOXD_COOKIES", ["letterboxd", "cookies"])
        if isinstance(cookies, dict):
            return {str(k): str(v) for k, v in cookies.items() if v}
        return {}

    @property
    def pushover_user_key(self) -> str:
        val = self.get_env_or_config("PUSHOVER_USER_KEY", ["pushover", "user_key"])
        if not val:
            raise ConfigError("Missing Pushover user_key in configuration or environment.")
        return val

    @property
    def pushover_api_token(self) -> str:
        val = self.get_env_or_config("PUSHOVER_API_TOKEN", ["pushover", "api_token"])
        if not val:
            raise ConfigError("Missing Pushover api_token in configuration or environment.")
        return val

    @property
    def sync_interval_minutes(self) -> int:
        val = self.get_env_or_config("SYNC_INTERVAL_MINUTES", ["settings", "sync_interval_minutes"], 15)
        try:
            return int(val)
        except ValueError:
            return 15

    @property
    def log_level(self) -> str:
        return self.get_env_or_config("LOG_LEVEL", ["settings", "log_level"], "INFO")
