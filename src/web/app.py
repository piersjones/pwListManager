import json
import logging
import time
import os
import threading
import requests
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from src.config import Config, ConfigError
from src.database import Database
from src.notifier import Notifier
from src.job_queue import get_queue

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "sync_state.db")
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")


def _ensure_config_path_writable():
    """Remove a directory at CONFIG_PATH if one exists (can happen with Docker bind mounts).
    When Docker mounts a non-existent file, it creates a directory instead."""
    if os.path.isdir(CONFIG_PATH):
        import shutil
        shutil.rmtree(CONFIG_PATH)


def _check_trakt_auth():
    cookie_dir = os.path.join(BASE_DIR, ".cookie")
    token_file = os.path.join(cookie_dir, "trakt_token.json")
    if not os.path.exists(token_file):
        return False
    try:
        with open(token_file, "r") as f:
            token = json.load(f)
        if "access_token" not in token:
            return False
        created_at = token.get("created_at", 0)
        expires_in = token.get("expires_in", 0)
        if time.time() > (created_at + expires_in - 86400):
            if token.get("refresh_token"):
                return True
            return False
        return True
    except Exception:
        return False


def create_app(config_path=None):
    app = Flask(__name__,
                template_folder=os.path.join(os.path.dirname(__file__), "templates"),
                static_folder=os.path.join(os.path.dirname(__file__), "static"))
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "pwlistmanager-dev-key-change-in-prod")

    def get_config():
        try:
            return Config(config_path or CONFIG_PATH)
        except (ConfigError, FileNotFoundError):
            return None

    def get_db():
        return Database(DB_PATH)

    def check_setup_complete():
        config = get_config()
        if not config:
            return False, "config"
        # Check that Trakt credentials are present (essential for any sync)
        try:
            if not config.trakt_client_id:
                return False, "config"
        except ConfigError:
            return False, "config"
        # Check Trakt authentication
        if not _check_trakt_auth():
            return False, "trakt_auth"
        # Check Letterboxd credentials
        try:
            has_letterboxd = bool(config.letterboxd_username and config.letterboxd_password)
        except ConfigError:
            has_letterboxd = False
        # Check Pushover credentials
        try:
            has_pushover = bool(config.pushover_user_key and config.pushover_api_token)
        except ConfigError:
            has_pushover = False
        if not has_letterboxd or not has_pushover:
            return False, "services"
        return True, None

    @app.before_request
    def log_request():
        logging.getLogger("pwListManager.web").debug(f"→ {request.method} {request.path}")

    @app.after_request
    def log_response(response):
        logging.getLogger("pwListManager.web").debug(f"← {request.method} {request.path} {response.status_code}")
        return response

    @app.before_request
    def redirect_if_not_setup():
        allowed_routes = ['setup', 'trakt_auth', 'trakt_auth_start', 'trakt_auth_poll', 'static', 'onboarding',
                          'api_test_trakt', 'api_test_letterboxd', 'api_test_pushover',
                          'api_trakt_lists', 'api_trakt_create_list', 'api_trakt_lists_status',
                          'api_trakt_create_missing_lists', 'api_save_config',
                          'api_factory_reset', 'api_erase_history',
                          'api_scheduler_status', 'api_scheduler_initial_sync_accept', 'api_scheduler_initial_sync_skip',
                          'api_scheduler_trigger',
                          'api_recent', 'api_status', 'api_health']
        if request.endpoint in allowed_routes:
            return None
        ok, missing = check_setup_complete()
        if not ok:
            if missing == "config":
                flash("Welcome! Let's set up pwListManager. Configure your credentials below.", "info")
                return redirect(url_for("setup"))
            elif missing == "trakt_auth":
                flash("Configuration saved! Now authenticate with Trakt to continue.", "info")
                return redirect(url_for("trakt_auth"))
            elif missing == "services":
                flash("Please configure Letterboxd and Pushover to enable full sync functionality.", "info")
                return redirect(url_for("setup"))
        return None

    @app.route("/")
    def index():
        config = get_config()
        db = get_db()
        try:
            stats = db.get_stats()
            recent = db.get_recent_actions(limit=30)
        except Exception:
            stats = {"total_movies": 0, "watchlist_synced": 0, "watched_synced": 0, "notified": 0}
            recent = []
        finally:
            db.close()

        trakt_authed = _check_trakt_auth()
        letterboxd_ok = False
        pushover_ok = False
        if config:
            try:
                letterboxd_ok = bool(config.letterboxd_username and config.letterboxd_password)
            except ConfigError:
                pass
            try:
                pushover_ok = bool(config.pushover_user_key and config.pushover_api_token)
            except ConfigError:
                pass

        return render_template("index.html",
                              config=config,
                              stats=stats,
                              recent=recent,
                              trakt_authed=trakt_authed,
                              letterboxd_ok=letterboxd_ok,
                              pushover_ok=pushover_ok)

    def _safe_config_dict(config):
        """Extract config values into a dict safe for template rendering.
        Returns None if config is None, otherwise a dict with safe defaults."""
        if not config:
            return None
        try:
            trakt_client_id = config.trakt_client_id
        except ConfigError:
            trakt_client_id = ""
        try:
            trakt_client_secret = config.trakt_client_secret
        except ConfigError:
            trakt_client_secret = ""
        try:
            custom_list_names = config.trakt_custom_list_names
        except ConfigError:
            custom_list_names = ["movie-watchlist"]
        try:
            letterboxd_username = config.letterboxd_username
        except ConfigError:
            letterboxd_username = ""
        try:
            letterboxd_password = config.letterboxd_password or ""
        except ConfigError:
            letterboxd_password = ""
        try:
            pushover_user_key = config.pushover_user_key
        except ConfigError:
            pushover_user_key = ""
        try:
            pushover_api_token = config.pushover_api_token
        except ConfigError:
            pushover_api_token = ""
        try:
            sync_interval_minutes = config.sync_interval_minutes
        except ConfigError:
            sync_interval_minutes = 15
        try:
            log_level = config.log_level
        except ConfigError:
            log_level = "INFO"
        return {
            "trakt_client_id": trakt_client_id or "",
            "trakt_client_secret": trakt_client_secret or "",
            "trakt_custom_list_names": custom_list_names,
            "letterboxd_username": letterboxd_username or "",
            "letterboxd_password": letterboxd_password or "",
            "pushover_user_key": pushover_user_key or "",
            "pushover_api_token": pushover_api_token or "",
            "sync_interval_minutes": sync_interval_minutes,
            "log_level": log_level,
        }

    @app.route("/setup", methods=["GET", "POST"])
    def setup():
        config = get_config()

        if request.method == "POST":
            config_data = {
                "trakt": {
                    "client_id": request.form.get("trakt_client_id", ""),
                    "client_secret": request.form.get("trakt_client_secret", ""),
                    "custom_list_names": request.form.get("trakt_custom_list_names", "movie-watchlist"),
                },
                "letterboxd": {
                    "username": request.form.get("letterboxd_username", ""),
                    "password": request.form.get("letterboxd_password", ""),
                },
                "pushover": {
                    "user_key": request.form.get("pushover_user_key", ""),
                    "api_token": request.form.get("pushover_api_token", ""),
                },
                "settings": {
                    "sync_interval_minutes": int(request.form.get("sync_interval_minutes", 15)),
                    "log_level": request.form.get("log_level", "INFO"),
                }
            }

            import yaml
            _ensure_config_path_writable()
            with open(CONFIG_PATH, "w") as f:
                yaml.dump(config_data, f, default_flow_style=False)

            config = get_config()
            ok, missing = check_setup_complete()
            if ok:
                flash("All services configured! Visit Tasks to start syncing.", "success")
                return redirect(url_for("tasks"))
            elif missing == "trakt_auth":
                flash("Configuration saved! Now authenticate with Trakt to continue.", "info")
                return redirect(url_for("trakt_auth"))
            elif missing == "services":
                flash("Configuration saved! Fill in the remaining service credentials above.", "info")
                return redirect(url_for("setup"))
            else:
                flash("Configuration saved!", "success")
                return redirect(url_for("setup"))

        return render_template("setup.html", config=_safe_config_dict(config), trakt_authed=_check_trakt_auth() if config else False)

    @app.route("/trakt-auth")
    def trakt_auth():
        config = get_config()
        if not config:
            flash("Please configure settings first.", "warning")
            return redirect(url_for("setup"))
        # Check that Trakt credentials are present
        try:
            if not config.trakt_client_id:
                flash("Please enter your Trakt Client ID first.", "warning")
                return redirect(url_for("setup"))
        except ConfigError:
            flash("Please enter your Trakt Client ID first.", "warning")
            return redirect(url_for("setup"))

        already_authed = _check_trakt_auth()

        return render_template("trakt_auth.html", config=config, already_authed=already_authed)

    @app.route("/trakt-auth/start")
    def trakt_auth_start():
        config = get_config()
        if not config:
            return jsonify({"error": "Not configured"}), 400
        try:
            client_id = config.trakt_client_id
        except ConfigError:
            return jsonify({"error": "Trakt Client ID not configured"}), 400
        try:
            import requests
            url = "https://api.trakt.tv/oauth/device/code"
            r = requests.post(url, json={"client_id": client_id},
                                headers={"Content-Type": "application/json"})
            data = r.json()
            return jsonify({
                "user_code": data.get("user_code"),
                "verification_url": data.get("verification_url"),
                "device_code": data.get("device_code"),
                "expires_in": data.get("expires_in", 600),
                "interval": data.get("interval", 5),
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/trakt-auth/poll", methods=["POST"])
    def trakt_auth_poll():
        config = get_config()
        if not config:
            return jsonify({"error": "Not configured"}), 400
        try:
            client_id = config.trakt_client_id
            client_secret = config.trakt_client_secret
        except ConfigError:
            return jsonify({"error": "Trakt credentials not configured"}), 400
        device_code = request.json.get("device_code")
        try:
            import requests
            url = "https://api.trakt.tv/oauth/device/token"
            payload = {
                "code": device_code,
                "client_id": config.trakt_client_id,
                "client_secret": config.trakt_client_secret,
            }
            r = requests.post(url, json=payload, headers={"Content-Type": "application/json"})
            # Parse JSON response; Trakt returns empty body for 400 in some cases
            try:
                data = r.json()
            except ValueError:
                data = {}
            if r.status_code == 200 and "access_token" in data:
                data["created_at"] = int(time.time())
                cookie_dir = os.path.join(BASE_DIR, ".cookie")
                os.makedirs(cookie_dir, exist_ok=True)
                with open(os.path.join(cookie_dir, "trakt_token.json"), "w") as f:
                    json.dump(data, f, indent=2)
                # Check if Letterboxd and Pushover are already configured
                config = get_config()
                has_letterboxd = False
                has_pushover = False
                if config:
                    try:
                        has_letterboxd = bool(config.letterboxd_username and config.letterboxd_password)
                    except ConfigError:
                        pass
                    try:
                        has_pushover = bool(config.pushover_user_key and config.pushover_api_token)
                    except ConfigError:
                        pass
                if has_letterboxd and has_pushover:
                    flash("Trakt authenticated! All services configured — visit Tasks to start syncing.", "success")
                    return jsonify({"status": "success", "redirect": url_for("tasks")})
                else:
                    flash("Trakt authenticated! Now configure Letterboxd and Pushover below.", "success")
                    return jsonify({"status": "success", "redirect": url_for("setup")})
            elif r.status_code == 400:
                error = data.get("error", "")
                if error in ("authorization_pending", "slow_down"):
                    return jsonify({"status": "pending" if error == "authorization_pending" else "slow_down"})
                # Empty body or unknown error — treat as pending (user hasn't authorized yet)
                return jsonify({"status": "pending"})
            else:
                return jsonify({"status": "error", "message": data.get("error", f"Trakt returned HTTP {r.status_code}")})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)})

    @app.route("/onboarding")
    def onboarding():
        config = get_config()
        if not config or not _check_trakt_auth():
            return redirect(url_for("setup"))
        sync_interval = config.sync_interval_minutes if config else 15
        return render_template("onboarding.html", sync_interval=sync_interval)

    @app.route("/tasks")
    def tasks():
        config = get_config()
        trakt_authed = _check_trakt_auth()

        queue = get_queue(config_path)
        queue_status = queue.get_status()

        return render_template("tasks.html",
                              config=config,
                              trakt_authed=trakt_authed,
                              queue_status=queue_status)

    # --- Health check ---
    @app.route("/api/health")
    def api_health():
        return jsonify({"status": "ok"})

    # --- Queue API endpoints (all return immediately) ---

    @app.route("/api/queue/status")
    def api_queue_status():
        queue = get_queue(config_path)
        return jsonify(queue.get_status())

    @app.route("/api/queue/enqueue/watched", methods=["POST"])
    def api_queue_enqueue_watched():
        try:
            data = request.get_json(silent=True) or {}
            suppress = data.get("suppress_notifications", False)
            queue = get_queue(config_path)
            status = queue.get_status()
            if status["running"] and not status["paused"]:
                return jsonify({"error": "Queue already running"}), 409
            queue.enqueue_watched_sync_all(suppress_notifications=suppress)
            if status["paused"]:
                return jsonify({"status": "queued", "message": "Job queued (resume the queue to process)"})
            started = queue.start()
            if not started:
                return jsonify({"error": "Failed to start queue (may be empty)"}), 500
            return jsonify({"status": "started", "message": "Fetching watched list from Trakt..."})
        except Exception as e:
            logging.getLogger("pwListManager.web").error(f"Error enqueuing watched sync: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500

    @app.route("/api/queue/enqueue/watchlist", methods=["POST"])
    def api_queue_enqueue_watchlist():
        try:
            data = request.get_json(silent=True) or {}
            suppress = data.get("suppress_notifications", False)
            queue = get_queue(config_path)
            status = queue.get_status()
            if status["running"] and not status["paused"]:
                return jsonify({"error": "Queue already running"}), 409
            queue.enqueue_watchlist_sync_all(suppress_notifications=suppress)
            if status["paused"]:
                return jsonify({"status": "queued", "message": "Job queued (resume the queue to process)"})
            started = queue.start()
            if not started:
                return jsonify({"error": "Failed to start queue (may be empty)"}), 500
            return jsonify({"status": "started", "message": "Fetching watchlist from Trakt..."})
        except Exception as e:
            logging.getLogger("pwListManager.web").error(f"Error enqueuing watchlist sync: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500

    @app.route("/api/queue/enqueue/migrate", methods=["POST"])
    def api_queue_enqueue_migrate():
        try:
            data = request.get_json(silent=True) or {}
            suppress = data.get("suppress_notifications", False)
            queue = get_queue(config_path)
            status = queue.get_status()
            if status["running"] and not status["paused"]:
                return jsonify({"error": "Queue already running"}), 409
            queue.enqueue_migrate_trakt_watchlist(suppress_notifications=suppress)
            if status["paused"]:
                return jsonify({"status": "queued", "message": "Job queued (resume the queue to process)"})
            started = queue.start()
            if not started:
                return jsonify({"error": "Failed to start queue (may be empty)"}), 500
            return jsonify({"status": "started", "message": "Migrating Trakt watchlist..."})
        except Exception as e:
            logging.getLogger("pwListManager.web").error(f"Error enqueuing migrate job: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500

    @app.route("/api/queue/pause", methods=["POST"])
    def api_queue_pause():
        queue = get_queue(config_path)
        queue.pause()
        return jsonify({"status": "paused"})

    @app.route("/api/queue/resume", methods=["POST"])
    def api_queue_resume():
        queue = get_queue(config_path)
        queue.resume()
        return jsonify({"status": "resumed"})

    @app.route("/api/queue/clear", methods=["POST"])
    def api_queue_clear():
        queue = get_queue(config_path)
        queue.clear()
        return jsonify({"status": "cleared"})

    # --- Trakt watchlist (loaded via AJAX, not on page load) ---

    @app.route("/api/trakt/watchlist")
    def api_trakt_watchlist():
        config = get_config()
        if not config:
            return jsonify({"error": "Not configured"}), 400
        try:
            from src.trakt_client import TraktClient
            logger = logging.getLogger("pwListManager.web")
            client = TraktClient(config, logger)
            movies = client.get_watchlist_movies()
            result = []
            for entry in movies:
                movie = entry.get("movie", entry)
                result.append({
                    "trakt_id": movie["ids"].get("trakt"),
                    "tmdb_id": movie["ids"].get("tmdb"),
                    "title": movie.get("title"),
                    "year": movie.get("year"),
                    "slug": movie["ids"].get("slug"),
                })
            return jsonify({"movies": result, "total": len(movies)})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # --- Direct action endpoints ---

    @app.route("/actions/add-watchlist", methods=["POST"])
    def action_add_watchlist():
        tmdb_id = request.json.get("tmdb_id")
        config = get_config()
        if not config or not tmdb_id:
            return jsonify({"error": "Missing config or tmdb_id"}), 400
        try:
            from src.letterboxd_client import LetterboxdClient
            client = LetterboxdClient(config, logging.getLogger("pwListManager.web"))
            result = client.add_to_watchlist(int(tmdb_id))
            return jsonify({"success": result})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/actions/mark-watched", methods=["POST"])
    def action_mark_watched():
        tmdb_id = request.json.get("tmdb_id")
        config = get_config()
        if not config or not tmdb_id:
            return jsonify({"error": "Missing config or tmdb_id"}), 400
        try:
            from src.letterboxd_client import LetterboxdClient
            client = LetterboxdClient(config, logging.getLogger("pwListManager.web"))
            result = client.mark_watched(int(tmdb_id))
            return jsonify({"success": result})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/actions/resolve-slug", methods=["POST"])
    def action_resolve_slug():
        tmdb_id = request.json.get("tmdb_id")
        config = get_config()
        if not config or not tmdb_id:
            return jsonify({"error": "Missing config or tmdb_id"}), 400
        try:
            from src.letterboxd_client import LetterboxdClient
            client = LetterboxdClient(config, logging.getLogger("pwListManager.web"))
            slug = client.resolve_tmdb_id_to_slug(int(tmdb_id))
            return jsonify({"slug": slug})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/logs")
    def view_logs():
        log_file = os.path.join(BASE_DIR, "logs", "app.log")
        lines = []
        if os.path.exists(log_file):
            with open(log_file, "r") as f:
                lines = f.readlines()[-300:]
        return render_template("logs.html", log_lines=lines)

    @app.route("/api/erase-history", methods=["POST"])
    def api_erase_history():
        db = get_db()
        try:
            db.erase_all()
            db.close()
            return jsonify({"status": "ok", "message": "History erased"})
        except Exception as e:
            db.close()
            return jsonify({"error": str(e)}), 500

    @app.route("/api/factory-reset", methods=["POST"])
    def api_factory_reset():
        deleted = []
        errors = []
        for path, label in [
            (CONFIG_PATH, "config.yaml"),
            (DB_PATH, "sync_state.db"),
            (os.path.join(DATA_DIR, "sync_queue.json"), "sync_queue.json"),
            (os.path.join(BASE_DIR, ".cookie", "trakt_token.json"), "trakt_token.json"),
            (os.path.join(BASE_DIR, "logs", "app.log"), "app.log"),
        ]:
            try:
                if os.path.exists(path):
                    os.remove(path)
                    deleted.append(label)
            except Exception as e:
                errors.append(f"{label}: {e}")
        return jsonify({"status": "ok", "deleted": deleted, "errors": errors,
                        "message": "Factory reset complete. Please restart the server."})

    @app.route("/api/status")
    def api_status():
        config = get_config()
        db = get_db()
        try:
            stats = db.get_stats()
        except Exception:
            stats = {"total_movies": 0, "watchlist_synced": 0, "watched_synced": 0, "notified": 0}
        finally:
            db.close()
        trakt_authed = _check_trakt_auth()
        return jsonify({
            "configured": config is not None,
            "trakt_authenticated": trakt_authed,
            "stats": stats,
        })

    @app.route("/api/recent")
    def api_recent():
        db = get_db()
        try:
            recent = db.get_recent_actions(limit=30)
        except Exception:
            recent = []
        finally:
            db.close()
        return jsonify({"recent": recent})

    @app.route("/api/trakt/lists-status")
    def api_trakt_lists_status():
        """Returns the status of all configured Trakt custom lists (name, slug, item count)."""
        config = get_config()
        if not config:
            return jsonify({"error": "Not configured"}), 400
        try:
            from src.trakt_client import TraktClient
            logger = logging.getLogger("pwListManager.web")
            client = TraktClient(config, logger)
            # Get all user lists
            url = f"https://api.trakt.tv/users/me/lists"
            r = requests.get(url, headers=client.headers)
            if r.status_code != 200:
                return jsonify({"error": f"Trakt API error: {r.status_code}"}), 500
            all_lists = r.json()
            # Build status for each configured list name
            configured_names = config.trakt_custom_list_names
            list_status = []
            existing_slugs = {lst["ids"]["slug"]: lst for lst in all_lists}
            for name in configured_names:
                target_slug = name.lower().replace(" ", "-")
                if target_slug in existing_slugs:
                    lst = existing_slugs[target_slug]
                    list_status.append({
                        "name": lst["name"],
                        "slug": lst["ids"]["slug"],
                        "item_count": lst.get("item_count", 0),
                        "exists": True,
                        "full": lst.get("item_count", 0) >= 100,
                    })
                else:
                    list_status.append({
                        "name": name,
                        "slug": target_slug,
                        "item_count": 0,
                        "exists": False,
                        "full": False,
                    })
            return jsonify({"lists": list_status, "total_configured": len(configured_names), "total_existing": len(all_lists), "list_limit_reached": len(all_lists) >= 5})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/trakt/create-missing-lists", methods=["POST"])
    def api_trakt_create_missing_lists():
        """Create any configured Trakt custom lists that don't exist yet."""
        config = get_config()
        if not config:
            return jsonify({"error": "Not configured"}), 400
        if not _check_trakt_auth():
            return jsonify({"error": "Trakt not authenticated."}), 401
        try:
            from src.trakt_client import TraktClient
            logger = logging.getLogger("pwListManager.web")
            client = TraktClient(config, logger)
            # Get existing lists
            url = "https://api.trakt.tv/users/me/lists"
            r = requests.get(url, headers=client.headers)
            if r.status_code != 200:
                return jsonify({"error": f"Trakt API error: {r.status_code}"}), 500
            all_lists = r.json()
            existing_slugs = {lst["ids"]["slug"] for lst in all_lists}
            created = []
            errors = []
            for name in config.trakt_custom_list_names:
                target_slug = name.lower().replace(" ", "-")
                if target_slug not in existing_slugs:
                    try:
                        result = client.create_custom_list_with_name(name)
                        created.append({"name": name, "slug": target_slug})
                    except Exception as e:
                        errors.append({"name": name, "error": str(e)})
            return jsonify({"created": created, "errors": errors,
                            "message": f"Created {len(created)} list(s)." if created else "All lists already exist."})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/trakt/lists")
    def api_trakt_lists():
        """Return all user's Trakt custom lists (for dropdown population)."""
        config = get_config()
        if not config:
            return jsonify({"error": "Not configured"}), 400
        if not _check_trakt_auth():
            return jsonify({"error": "Trakt not authenticated."}), 401
        try:
            from src.trakt_client import TraktClient
            logger = logging.getLogger("pwListManager.web")
            client = TraktClient(config, logger)
            url = "https://api.trakt.tv/users/me/lists"
            r = requests.get(url, headers=client.headers)
            if r.status_code != 200:
                return jsonify({"error": f"Trakt API error: {r.status_code}"}), 500
            all_lists = r.json()
            result = []
            for lst in all_lists:
                result.append({
                    "name": lst["name"],
                    "slug": lst["ids"]["slug"],
                    "item_count": lst.get("item_count", 0),
                    "description": lst.get("description", ""),
                    "privacy": lst.get("privacy", "private"),
                })
            return jsonify({"lists": result})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/trakt/create-list", methods=["POST"])
    def api_trakt_create_list():
        """Create a single Trakt custom list by name."""
        config = get_config()
        if not config:
            return jsonify({"error": "Not configured"}), 400
        if not _check_trakt_auth():
            return jsonify({"error": "Trakt not authenticated."}), 401
        name = request.json.get("name", "").strip() if request.json else ""
        if not name:
            return jsonify({"error": "List name is required."}), 400
        try:
            from src.trakt_client import TraktClient
            logger = logging.getLogger("pwListManager.web")
            client = TraktClient(config, logger)
            result = client.create_custom_list_with_name(name)
            return jsonify({"success": True, "name": name, "slug": name.lower().replace(" ", "-"), "result": result})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/test/trakt")
    def api_test_trakt():
        """Test Trakt connection by validating client_id and checking auth status."""
        config = get_config()
        if not config:
            return jsonify({"success": False, "error": "Save your Trakt credentials first."})
        try:
            from src.trakt_client import TraktClient
            logger = logging.getLogger("pwListManager.web")
            # Test 1: Validate client_id by hitting a public endpoint
            headers = {
                "Content-Type": "application/json",
                "trakt-api-version": "2",
                "trakt-api-key": config.trakt_client_id,
            }
            r = requests.get("https://api.trakt.tv/users/me/lists", headers=headers)
            if r.status_code == 401:
                # client_id is valid but not authenticated — that's expected
                authed = _check_trakt_auth()
                return jsonify({"success": True, "message": f"Client ID valid. {'Authenticated!' if authed else 'Not yet authenticated — click Authenticate with Trakt.'}"})
            elif r.status_code == 200:
                lists = r.json()
                return jsonify({"success": True, "message": f"Connected and authenticated! Found {len(lists)} custom list(s)."})
            else:
                return jsonify({"success": False, "error": f"Trakt API returned {r.status_code}: {r.text[:200]}"})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/test/letterboxd")
    def api_test_letterboxd():
        """Test Letterboxd connection by logging in."""
        config = get_config()
        if not config:
            return jsonify({"success": False, "error": "Save your Letterboxd credentials first."})
        try:
            if not config.letterboxd_username:
                return jsonify({"success": False, "error": "Letterboxd username not configured."})
        except ConfigError:
            return jsonify({"success": False, "error": "Letterboxd username not configured."})
        try:
            from src.letterboxd_client import LetterboxdClient
            logger = logging.getLogger("pwListManager.web")
            client = LetterboxdClient(config, logger)
            return jsonify({"success": True, "message": f"Logged in as '{config.letterboxd_username}'."})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/test/pushover")
    def api_test_pushover():
        """Test Pushover notification delivery."""
        config = get_config()
        if not config:
            return jsonify({"success": False, "error": "Save your Pushover credentials first."})
        try:
            notifier = Notifier(config, logging.getLogger("pwListManager.web"))
            success = notifier.send_notification(
                message="pwListManager is alive! Pushover working.",
                title="pwListManager Test",
                priority=0
            )
            return jsonify({"success": success})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    # --- Auto-sync scheduler ---
    _scheduler_state = {
        "running": False,
        "enabled": True,
        "last_sync": None,
        "interval_minutes": 15,
        "thread": None,
        "initial_sync_pending": False,
        "initial_sync_answered": False,
        "next_sync_time": None,  # ISO timestamp of next scheduled sync
    }

    @app.route("/api/scheduler/status")
    def api_scheduler_status():
        # Actively check if initial sync prompt should show —
        # don't wait for the scheduler to run its first cycle
        if not _scheduler_state["initial_sync_answered"]:
            try:
                db = get_db()
                try:
                    if not db.snapshots_exist(["watchlist", "watched", "watchlist_all"]):
                        _scheduler_state["initial_sync_pending"] = True
                    else:
                        _scheduler_state["initial_sync_pending"] = False
                        _scheduler_state["initial_sync_answered"] = True
                finally:
                    db.close()
            except Exception:
                pass
        return jsonify({
            "enabled": _scheduler_state["enabled"],
            "running": _scheduler_state["running"],
            "interval_minutes": _scheduler_state["interval_minutes"],
            "last_sync": _scheduler_state["last_sync"],
            "initial_sync_pending": _scheduler_state["initial_sync_pending"],
            "next_sync_time": _scheduler_state["next_sync_time"],
        })

    @app.route("/api/scheduler/initial-sync/accept", methods=["POST"])
    def api_scheduler_initial_sync_accept():
        """User accepted full initial sync. Enqueue full sync for all lists."""
        try:
            config = get_config()
            if not config:
                return jsonify({"error": "Not configured"}), 400
            if not _check_trakt_auth():
                return jsonify({"error": "Trakt not authenticated"}), 400
            queue = get_queue(config_path)
            logger = logging.getLogger("pwListManager.scheduler")
            # Save snapshots now so the banner doesn't reappear after server restart.
            # The full sync will process all current items; future incremental runs diff against these snapshots.
            success = queue.take_initial_snapshots(config, logger)
            if not success:
                # API calls failed — save empty snapshots as fallback so the banner
                # doesn't reappear. Empty snapshots mean "process everything" on the
                # next incremental run, but the database already skips synced items.
                logger.warning("Could not take initial snapshots via API — saving empty snapshots as fallback")
                db = get_db()
                try:
                    for key in ["watchlist", "watched", "watchlist_all"]:
                        if not db.snapshots_exist([key]):
                            db.save_list_snapshot(key, [])
                finally:
                    db.close()
            queue.enqueue_migrate_trakt_watchlist(incremental=False, suppress_notifications=False)
            queue.enqueue_watchlist_sync_all(incremental=False, suppress_notifications=False)
            queue.enqueue_watched_sync_all(incremental=False, suppress_notifications=False)
            queue.start()
            _scheduler_state["initial_sync_answered"] = True
            _scheduler_state["initial_sync_pending"] = False
            logger.info("Initial sync accepted — full sync enqueued for all lists")
            return jsonify({"status": "ok", "message": "Full sync enqueued. Auto-sync will continue incrementally after completion."})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/scheduler/initial-sync/skip", methods=["POST"])
    def api_scheduler_initial_sync_skip():
        """User wants to skip full initial sync. Take snapshots so auto-sync is incremental-only."""
        try:
            config = get_config()
            if not config:
                return jsonify({"error": "Not configured"}), 400
            if not _check_trakt_auth():
                return jsonify({"error": "Trakt not authenticated"}), 400
            queue = get_queue(config_path)
            logger = logging.getLogger("pwListManager.scheduler")
            logger.info("Initial sync skipped — taking snapshots for incremental-only auto-sync")
            success = queue.take_initial_snapshots(config, logger)
            if not success:
                # API calls failed — save empty snapshots as fallback so the banner
                # doesn't reappear. Empty snapshots mean "process everything" on the
                # next incremental run, but the database already skips synced items.
                logger.warning("Could not take initial snapshots via API — saving empty snapshots as fallback")
                db = get_db()
                try:
                    for key in ["watchlist", "watched", "watchlist_all"]:
                        if not db.snapshots_exist([key]):
                            db.save_list_snapshot(key, [])
                finally:
                    db.close()
            _scheduler_state["initial_sync_answered"] = True
            _scheduler_state["initial_sync_pending"] = False
            logger.info("Initial snapshots saved. Auto-sync will process only new items going forward.")
            return jsonify({"status": "ok", "message": "Snapshots saved. Auto-sync will only process new items going forward."})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/scheduler/toggle", methods=["POST"])
    def api_scheduler_toggle():
        data = request.json or {}
        enabled = data.get("enabled", not _scheduler_state["enabled"])
        _scheduler_state["enabled"] = enabled
        logger = logging.getLogger("pwListManager.scheduler")
        logger.info(f"Auto-sync {'enabled' if enabled else 'disabled'}")
        return jsonify({"enabled": _scheduler_state["enabled"]})

    @app.route("/api/scheduler/interval", methods=["POST"])
    def api_scheduler_interval():
        data = request.json or {}
        minutes = data.get("interval_minutes", 15)
        try:
            minutes = int(minutes)
            if minutes < 1:
                minutes = 1
        except (ValueError, TypeError):
            minutes = 15
        _scheduler_state["interval_minutes"] = minutes
        # Also update config file
        try:
            import yaml
            config = get_config()
            if config:
                config_data = {
                    "trakt": {
                        "client_id": config.trakt_client_id,
                        "client_secret": config.trakt_client_secret,
                        "custom_list_names": ", ".join(config.trakt_custom_list_names),
                    },
                    "letterboxd": {
                        "username": config.letterboxd_username,
                        "password": config.letterboxd_password or "",
                    },
                    "pushover": {
                        "user_key": config.pushover_user_key,
                        "api_token": config.pushover_api_token,
                    },
                    "settings": {
                        "sync_interval_minutes": minutes,
                        "log_level": config.log_level,
                    }
                }
                _ensure_config_path_writable()
                with open(CONFIG_PATH, "w") as f:
                    yaml.dump(config_data, f, default_flow_style=False)
        except Exception:
            pass
        return jsonify({"interval_minutes": _scheduler_state["interval_minutes"]})

    @app.route("/api/scheduler/trigger", methods=["POST"])
    def api_scheduler_trigger():
        """Manually trigger an auto-sync cycle (incremental)."""
        try:
            config = get_config()
            if not config:
                return jsonify({"error": "Not configured"}), 400
            if not _check_trakt_auth():
                return jsonify({"error": "Trakt not authenticated"}), 400
            queue = get_queue(config_path)
            status = queue.get_status()
            if status["running"] and not status["paused"]:
                return jsonify({"error": "Queue is already running"}), 409
            if _scheduler_state["running"]:
                return jsonify({"error": "Auto-sync is already running"}), 409
            logger = logging.getLogger("pwListManager.scheduler")
            logger.info("Manual auto-sync trigger — enqueuing incremental sync jobs")
            queue.enqueue_migrate_trakt_watchlist(incremental=True)
            queue.enqueue_watchlist_sync_all(incremental=True)
            queue.enqueue_watched_sync_all(incremental=True)
            started = queue.start()
            if not started:
                return jsonify({"error": "No items to sync"}), 400
            _scheduler_state["last_sync"] = time.strftime("%Y-%m-%d %H:%M:%S")
            return jsonify({"status": "started", "message": "Auto-sync triggered"})
        except Exception as e:
            logging.getLogger("pwListManager.scheduler").error(f"Error triggering auto-sync: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500

    @app.route("/api/settings/export")
    def api_settings_export():
        config = get_config()
        if not config:
            return jsonify({"error": "Not configured"}), 400
        export_data = {
            "trakt": {
                "client_id": config.trakt_client_id,
                "client_secret": config.trakt_client_secret,
                "custom_list_names": config.trakt_custom_list_names,
            },
            "letterboxd": {
                "username": config.letterboxd_username,
                "password": config.letterboxd_password or "",
            },
            "pushover": {
                "user_key": config.pushover_user_key,
                "api_token": config.pushover_api_token,
            },
            "settings": {
                "sync_interval_minutes": config.sync_interval_minutes,
                "log_level": config.log_level,
            }
        }
        return jsonify(export_data)

    @app.route("/api/save-config", methods=["POST"])
    def api_save_config():
        """Save config from form data (AJAX). Accepts JSON or form-encoded data."""
        try:
            if request.is_json:
                data = request.json
                config_data = {
                    "trakt": {
                        "client_id": data.get("trakt_client_id", ""),
                        "client_secret": data.get("trakt_client_secret", ""),
                        "custom_list_names": data.get("trakt_custom_list_names", "movie-watchlist"),
                    },
                    "letterboxd": {
                        "username": data.get("letterboxd_username", ""),
                        "password": data.get("letterboxd_password", ""),
                    },
                    "pushover": {
                        "user_key": data.get("pushover_user_key", ""),
                        "api_token": data.get("pushover_api_token", ""),
                    },
                    "settings": {
                        "sync_interval_minutes": int(data.get("sync_interval_minutes", 15)),
                        "log_level": data.get("log_level", "INFO"),
                    }
                }
            else:
                config_data = {
                    "trakt": {
                        "client_id": request.form.get("trakt_client_id", ""),
                        "client_secret": request.form.get("trakt_client_secret", ""),
                        "custom_list_names": request.form.get("trakt_custom_list_names", "movie-watchlist"),
                    },
                    "letterboxd": {
                        "username": request.form.get("letterboxd_username", ""),
                        "password": request.form.get("letterboxd_password", ""),
                    },
                    "pushover": {
                        "user_key": request.form.get("pushover_user_key", ""),
                        "api_token": request.form.get("pushover_api_token", ""),
                    },
                    "settings": {
                        "sync_interval_minutes": int(request.form.get("sync_interval_minutes", 15)),
                        "log_level": request.form.get("log_level", "INFO"),
                    }
                }
            import yaml
            _ensure_config_path_writable()
            with open(CONFIG_PATH, "w") as f:
                yaml.dump(config_data, f, default_flow_style=False)
            return jsonify({"status": "ok", "message": "Configuration saved."})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/settings/import", methods=["POST"])
    def api_settings_import():
        data = request.json
        if not data:
            return jsonify({"error": "No data provided"}), 400
        try:
            import yaml
            config_data = {
                "trakt": {
                    "client_id": data.get("trakt", {}).get("client_id", ""),
                    "client_secret": data.get("trakt", {}).get("client_secret", ""),
                    "custom_list_names": ", ".join(data.get("trakt", {}).get("custom_list_names", ["movie-watchlist"])) if isinstance(data.get("trakt", {}).get("custom_list_names"), list) else data.get("trakt", {}).get("custom_list_names", "movie-watchlist"),
                },
                "letterboxd": {
                    "username": data.get("letterboxd", {}).get("username", ""),
                    "password": data.get("letterboxd", {}).get("password", ""),
                },
                "pushover": {
                    "user_key": data.get("pushover", {}).get("user_key", ""),
                    "api_token": data.get("pushover", {}).get("api_token", ""),
                },
                "settings": {
                    "sync_interval_minutes": int(data.get("settings", {}).get("sync_interval_minutes", 15)),
                    "log_level": data.get("settings", {}).get("log_level", "INFO"),
                }
            }
            _ensure_config_path_writable()
            with open(CONFIG_PATH, "w") as f:
                yaml.dump(config_data, f, default_flow_style=False)
            return jsonify({"status": "ok", "message": "Settings imported successfully. Please restart the server for all changes to take effect."})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    def _scheduler_loop():
        logger = logging.getLogger("pwListManager.scheduler")
        logger.info("Auto-sync scheduler thread started")
        # Wait a bit on first start to let the web server settle
        time.sleep(30)
        while True:
            if not _scheduler_state["enabled"]:
                time.sleep(10)
                continue
            try:
                config = get_config()
                if config and _check_trakt_auth():
                    _scheduler_state["running"] = True
                    queue = get_queue(config_path)

                    # Check if initial snapshots exist — prompt user if not
                    if not _scheduler_state["initial_sync_answered"]:
                        if not queue.has_initial_snapshots():
                            _scheduler_state["initial_sync_pending"] = True
                            _scheduler_state["running"] = False
                            logger.info("Auto-sync: no list snapshots found — waiting for user to accept/skip initial sync")
                            interval = _scheduler_state["interval_minutes"]
                            time.sleep(interval * 60)
                            continue
                        else:
                            # Snapshots already exist from a previous run
                            _scheduler_state["initial_sync_pending"] = False
                            _scheduler_state["initial_sync_answered"] = True

                    logger.info("Auto-sync: starting scheduled sync cycle")
                    status = queue.get_status()
                    # Skip if queue is running or paused
                    if status["running"] or status["paused"]:
                        logger.info("Auto-sync: queue is active or paused, skipping")
                    else:
                        # Order: migrate new items to custom lists first,
                        # then sync to Letterboxd, then sync watched
                        # Use incremental=True so only NEW items are processed
                        queue.enqueue_migrate_trakt_watchlist(incremental=True)
                        queue.enqueue_watchlist_sync_all(incremental=True)
                        queue.enqueue_watched_sync_all(incremental=True)
                        queue.start()
                        logger.info("Auto-sync: enqueued incremental migration + watchlist + watched sync jobs")
                    _scheduler_state["last_sync"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    _scheduler_state["running"] = False
                else:
                    logger.debug("Auto-sync: skipping (not configured or not authenticated)")
            except Exception as e:
                logger.error(f"Auto-sync error: {e}")
                _scheduler_state["running"] = False
                _scheduler_state["initial_sync_pending"] = False
            interval = _scheduler_state["interval_minutes"]
            _scheduler_state["next_sync_time"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() + interval * 60))
            time.sleep(interval * 60)

    # Read initial interval from config
    config = get_config()
    if config:
        try:
            _scheduler_state["interval_minutes"] = config.sync_interval_minutes
        except ConfigError:
            pass

    @app.context_processor
    def inject_setup_complete():
        ok, _ = check_setup_complete()
        return {'setup_complete': ok}

    # Start scheduler thread (daemon so it exits with the main process)
    scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True, name="pwListManager-scheduler")
    _scheduler_state["thread"] = scheduler_thread
    scheduler_thread.start()

    return app