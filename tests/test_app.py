from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app
from cache import TTLCache
from crunchyroll import CrunchyrollClient
from services import AnimeService


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else ""

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeCrunchyrollSession:
    """Fakes Crunchyroll's token, search, series, seasons, episodes, movie, new, and calendar APIs."""

    def __init__(self):
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.auth_calls = 0
        self.request_calls = []

    def iso_days(self, days):
        return (self.now + timedelta(days=days)).isoformat().replace("+00:00", "Z")

    def post(self, url, headers=None, data=None, timeout=None):
        assert url.endswith("/auth/v1/token")
        assert headers["Authorization"].startswith("Basic ")
        assert headers["ETP-Anonymous-ID"]
        assert data == {"grant_type": "client_id", "scope": "offline_access"}
        self.auth_calls += 1
        return FakeResponse(200, {"access_token": "fake-access-token", "expires_in": 3600})

    def request(self, method, url, headers=None, params=None, timeout=None):
        assert method == "GET"
        assert headers["Authorization"] == "Bearer fake-access-token"
        path = urlparse(url).path
        params = params or {}
        self.request_calls.append((path, dict(params)))

        if path == "/content/v2/discover/search":
            query = params.get("q", "")
            content_type = params.get("type")
            if query.lower() == "nothing":
                return FakeResponse(200, {"data": [{"items": []}]})
            if content_type == "movie_listing":
                return FakeResponse(
                    200,
                    {
                        "data": [
                            {
                                "items": [
                                    {
                                        "id": "MOVIE123",
                                        "title": "Mock Movie",
                                        "slug_title": "mock-movie",
                                    }
                                ]
                            }
                        ]
                    },
                )
            return FakeResponse(
                200,
                {
                    "data": [
                        {
                            "items": [
                                {
                                    "id": "SERIES123",
                                    "title": "Mock Hero",
                                    "slug_title": "mock-hero",
                                }
                            ]
                        }
                    ]
                },
            )

        if path == "/content/v2/cms/series/SERIES123":
            return FakeResponse(
                200,
                {
                    "data": {
                        "id": "SERIES123",
                        "title": "Mock Hero",
                        "slug_title": "mock-hero",
                        "description": "A mocked anime used for backend tests.",
                    }
                },
            )

        if path == "/content/v2/cms/series/SERIES123/seasons":
            return FakeResponse(
                200,
                {
                    "data": [
                        {
                            "id": "season-ja",
                            "title": "Mock Hero Season 1",
                            "season_number": 1,
                            "audio_locale": "ja-JP",
                            "subtitle_locales": ["en-US", "hi-IN"],
                            "number_of_episodes": 4,
                            "versions": [{"locale": "en-US", "guid": "season-ja-en-metadata"}],
                        },
                        {
                            "id": "season-hi",
                            "title": "Mock Hero Season 1 (Hindi Dub)",
                            "season_number": 1,
                            "audio_locale": "hi-IN",
                            "subtitle_locales": ["en-US"],
                            "number_of_episodes": 3,
                        },
                        {
                            "id": "season-en",
                            "title": "Mock Hero Season 1 (English Dub)",
                            "season_number": 1,
                            "audio_locale": "en-US",
                            "subtitle_locales": ["en-US"],
                            "number_of_episodes": 3,
                            "versions": [{"locale": "en-US", "guid": "season-en-en-metadata"}],
                        },
                    ]
                },
            )

        if path == "/content/v2/cms/seasons/season-ja-en-metadata/episodes":
            return FakeResponse(200, {"data": self.episodes("ja", count=4, future_episode=4)})
        if path == "/content/v2/cms/seasons/season-hi/episodes":
            return FakeResponse(200, {"data": self.episodes("hi", count=3, future_episode=None)})
        if path == "/content/v2/cms/seasons/season-en-en-metadata/episodes":
            return FakeResponse(200, {"data": self.episodes("en", count=3, future_episode=None)})

        if path == "/content/v2/cms/objects/MOVIE123":
            return FakeResponse(
                200,
                {
                    "data": {
                        "id": "MOVIE123",
                        "movie_listing_metadata": {
                            "title": "Mock Movie",
                            "description": "A mocked movie listing.",
                            "release_year": 2025,
                            "duration_ms": 7_200_000,
                            "versions": [
                                {"audio_locale": "ja-JP"},
                                {"audio_locale": "hi-IN"},
                            ],
                        },
                    }
                },
            )

        if path == "/content/v2/discover/browse":
            return FakeResponse(
                200,
                {
                    "data": [
                        {
                            "id": "NEW123",
                            "title": "New Mock Anime",
                            "slug_title": "new-mock-anime",
                            "description": "Freshly added mock title.",
                        }
                    ]
                },
            )

        if path == "/content/v2/discover/calendar":
            return FakeResponse(
                200,
                {
                    "data": [
                        {
                            "id": "CAL123",
                            "title": "Mock Hero Episode 4",
                            "series_title": "Mock Hero",
                            "episode_number": 4,
                            "audio_locale": "ja-JP",
                            "free_publish_date": self.iso_days(7),
                            "duration_ms": 1_440_000,
                        }
                    ]
                },
            )

        return FakeResponse(404, {"error": f"not found: {path}"})

    def episodes(self, prefix, count, future_episode=None):
        data = []
        for number in range(1, count + 1):
            days = 7 if number == future_episode else -10 + number
            data.append(
                {
                    "id": f"{prefix}-{number}",
                    "episode_number": number,
                    "title": f"Episode {number}",
                    "free_publish_date": self.iso_days(days),
                    "upload_date": self.iso_days(days),
                    "is_premium_only": False,
                    "duration_ms": 1_440_000,
                }
            )
        return data


@pytest.fixture()
def flask_client():
    fake_session = FakeCrunchyrollSession()
    crunchyroll_client = CrunchyrollClient(
        locale="en-US",
        basic_tokens=["fake-basic-token"],
        session=fake_session,
    )
    service = AnimeService(crunchyroll_client, TTLCache(ttl_seconds=0))
    app = create_app(service=service)
    app.config.update(TESTING=True)
    return app.test_client(), fake_session


def test_anime_report_counts_next_episode_and_no_double_counting(flask_client):
    client, _session = flask_client
    response = client.get("/anime/Mock%20Hero")
    assert response.status_code == 200
    data = response.get_json()

    assert data["name"] == "Mock Hero"
    assert data["total_seasons"] == 1
    assert data["total_episodes"] == 4  # max per language, not 4 + 3 + 3
    assert data["status"] == "Ongoing"

    languages = {item["locale"]: item for item in data["languages"]}
    assert languages["ja-JP"]["total_episodes"] == 4
    assert languages["hi-IN"]["total_episodes"] == 3
    assert languages["en-US"]["total_episodes"] == 3

    assert languages["ja-JP"]["next_episode"]["number"] == "4"
    assert languages["ja-JP"]["next_episode"]["date_ist"].endswith("+05:30")
    assert languages["hi-IN"]["next_episode"] == "not announced yet"
    assert languages["en-US"]["next_episode"] == "not announced yet"


def test_all_success_endpoints_return_json(flask_client):
    client, _session = flask_client
    paths = [
        "/health",
        "/search?q=Mock%20Hero",
        "/search?q=Mock%20Movie&type=movie_listing",
        "/anime/Mock%20Hero",
        "/movie/Mock%20Movie",
        "/new",
        "/calendar",
    ]
    for path in paths:
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.is_json, path
        assert isinstance(response.get_json(), dict), path


def test_error_endpoints_return_json_and_correct_codes(flask_client):
    client, _session = flask_client

    response = client.get("/anime")
    assert response.status_code == 400
    assert response.is_json
    assert "error" in response.get_json()

    response = client.get("/anime/Nothing")
    assert response.status_code == 404
    assert response.is_json
    assert "No Crunchyroll series found" in response.get_json()["error"]

    response = client.get("/search?q=Mock&type=invalid")
    assert response.status_code == 400
    assert response.is_json
    assert "type must be" in response.get_json()["error"]

    response = client.get("/does-not-exist")
    assert response.status_code == 404
    assert response.is_json
    assert "error" in response.get_json()


def test_movie_endpoint_returns_expected_fields(flask_client):
    client, _session = flask_client
    response = client.get("/movie/Mock%20Movie")
    assert response.status_code == 200
    data = response.get_json()
    assert data["title"] == "Mock Movie"
    assert data["release_year"] == 2025
    assert data["runtime_minutes"] == 120
    assert {item["locale"] for item in data["available_dub_languages"]} == {"ja-JP", "hi-IN"}
