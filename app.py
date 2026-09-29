"""Flask web app for live Crunchyroll anime information."""

from __future__ import annotations

import logging
from typing import Any, Dict

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from cache import TTLCache
from config import get_cache_ttl_seconds, get_locale, get_port
from crunchyroll import CrunchyrollAPIError, CrunchyrollClient
from services import AnimeService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
LOGGER = logging.getLogger(__name__)


def create_app(service: AnimeService | None = None) -> Flask:
    """Create the Flask application.

    Tests pass in a fake service/client; production uses the real Crunchyroll
    client and a capped in-memory cache.
    """

    app = Flask(__name__)

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
        return jsonify({"status": "ok", "service": "anime-info", "platform": "Crunchyroll"})

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
        response = {"error": error.message}
        return jsonify(response), error.status_code

    @app.errorhandler(HTTPException)
    def http_error(error: HTTPException):
        return jsonify({"error": error.description or error.name}), error.code or 500

    @app.errorhandler(Exception)
    def unhandled_error(error: Exception):
        LOGGER.exception("Unhandled application error")
        return jsonify({"error": "Internal server error."}), 500

    def current_service() -> AnimeService:
        return app.config["ANIME_SERVICE"]

    return app


# Gunicorn imports this exact variable via Procfile: web: gunicorn app:app
app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=get_port())
