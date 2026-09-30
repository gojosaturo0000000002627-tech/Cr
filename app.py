"""Flask web app for live Crunchyroll anime information.

The same Flask service exposes:
- public JSON API endpoints (/anime, /movie, /search, /new, /calendar)
- a mobile-friendly web page (/)
- optional Telegram bot webhook endpoints (/telegram/...)
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from cache import TTLCache
from config import get_cache_ttl_seconds, get_locale, get_port
from crunchyroll import CrunchyrollAPIError, CrunchyrollClient
from services import AnimeService
from telegram_bot import (
    auto_set_webhook_if_configured,
    bot_configured,
    delete_webhook,
    expected_webhook_url,
    get_bot_token,
    get_webhook_info,
    get_webhook_secret,
    handle_telegram_update_async,
    public_base_url,
    set_webhook,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
LOGGER = logging.getLogger(__name__)


def create_app(service: AnimeService | None = None) -> Flask:
    """Create the Flask application.

    Tests pass in a fake service/client; production uses the real Crunchyroll
    client and a capped in-memory cache.
    """

    app = Flask(__name__)

    production_service = service is None
    if service is None:
        client = CrunchyrollClient(locale=get_locale())
        cache = TTLCache[Dict[str, Any]](ttl_seconds=get_cache_ttl_seconds())
        service = AnimeService(client=client, cache=cache)

    app.config["ANIME_SERVICE"] = service

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/health")
    def health():
        return jsonify(
            {
                "status": "ok",
                "service": "anime-info",
                "platform": "Crunchyroll",
                "telegram_configured": bot_configured(),
            }
        )

    @app.get("/telegram/status")
    def telegram_status():
        secret = get_webhook_secret()
        return jsonify(
            {
                "configured": bot_configured(),
                "bot_token_set": bool(get_bot_token()),
                "webhook_secret_set": bool(secret),
                "public_base_url": public_base_url() or None,
                "expected_webhook_url": expected_webhook_url(),
                "setup_endpoint": f"/telegram/setup/{secret}" if secret else None,
                "webhook_info_endpoint": f"/telegram/info/{secret}" if secret else None,
                "message": (
                    "Telegram bot ready. If /start still does not reply, open the setup_endpoint once."
                    if bot_configured()
                    else "Set TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_SECRET in Render Environment. PUBLIC_BASE_URL is optional on Render."
                ),
            }
        )

    @app.get("/telegram/setup/<secret>")
    def telegram_setup(secret: str):
        if not _valid_telegram_secret(secret):
            return jsonify({"error": "Invalid Telegram webhook secret."}), 403
        try:
            drop_pending = str(request.args.get("drop_pending", "0")).lower() in {"1", "true", "yes"}
            return jsonify(set_webhook(drop_pending_updates=drop_pending))
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.get("/telegram/info/<secret>")
    def telegram_info(secret: str):
        if not _valid_telegram_secret(secret):
            return jsonify({"error": "Invalid Telegram webhook secret."}), 403
        try:
            return jsonify(get_webhook_info())
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/telegram/delete/<secret>")
    @app.get("/telegram/delete/<secret>")
    def telegram_delete(secret: str):
        if not _valid_telegram_secret(secret):
            return jsonify({"error": "Invalid Telegram webhook secret."}), 403
        try:
            return jsonify(delete_webhook())
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/telegram/webhook/<secret>")
    def telegram_webhook(secret: str):
        if not _valid_telegram_secret(secret):
            return jsonify({"error": "Invalid Telegram webhook secret."}), 403
        if not bot_configured():
            return jsonify({"error": "Telegram bot is not configured. Set TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_SECRET."}), 503
        update = request.get_json(silent=True) or {}
        # Telegram expects a quick 200 response.  The actual Crunchyroll fetch
        # and sendMessage happen in a background thread.
        handle_telegram_update_async(update, current_service())
        return jsonify({"ok": True})

    @app.get("/search")
    def search():
        query = request.args.get("q", "")
        content_type = request.args.get("type", "series")
        limit = request.args.get("limit", request.args.get("n", 10))
        return jsonify(current_service().search(query, content_type=content_type, limit=limit))

    @app.get("/anime")
    def anime_from_query():
        name = request.args.get("name") or request.args.get("q") or ""
        return jsonify(current_service().anime_report(name))

    @app.get("/anime/<path:name>")
    def anime(name: str):
        return jsonify(current_service().anime_report(name))

    @app.get("/movie")
    def movie_from_query():
        name = request.args.get("name") or request.args.get("q") or ""
        return jsonify(current_service().movie_report(name))

    @app.get("/movie/<path:name>")
    def movie(name: str):
        return jsonify(current_service().movie_report(name))

    @app.get("/new")
    def newly_added():
        limit = request.args.get("limit", request.args.get("n", 20))
        return jsonify(current_service().newly_added(limit=limit))

    @app.get("/calendar")
    def calendar():
        limit = request.args.get("limit", 100)
        return jsonify(current_service().calendar(limit=limit))

    @app.errorhandler(CrunchyrollAPIError)
    def crunchyroll_error(error: CrunchyrollAPIError):
        return jsonify({"error": error.message}), error.status_code

    @app.errorhandler(HTTPException)
    def http_error(error: HTTPException):
        return jsonify({"error": error.description or error.name}), error.code or 500

    @app.errorhandler(Exception)
    def unhandled_error(error: Exception):
        LOGGER.exception("Unhandled application error")
        return jsonify({"error": "Internal server error."}), 500

    def current_service() -> AnimeService:
        return app.config["ANIME_SERVICE"]

    def _valid_telegram_secret(secret: str) -> bool:
        expected_secret = get_webhook_secret()
        return bool(expected_secret and secret == expected_secret)

    # Auto-set Telegram webhook after production app startup when env vars exist.
    # Disabled during tests because tests inject a fake service.
    if production_service:
        auto_set_webhook_if_configured()

    return app


# Gunicorn imports this exact variable via Procfile: web: gunicorn app:app
app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=get_port())
