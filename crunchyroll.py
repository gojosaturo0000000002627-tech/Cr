"""Crunchyroll anonymous API client.

Crunchyroll does not publish an official public API.  This client uses the same
anonymous-token flow and beta-api endpoints used by Crunchyroll web/app clients.
It never returns made-up data: if Crunchyroll is unavailable, blocked, or
returns unexpected data, callers receive a CrunchyrollAPIError.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional

import requests

LOGGER = logging.getLogger(__name__)


class CrunchyrollAPIError(Exception):
    """Exception raised for upstream Crunchyroll/API problems."""

    def __init__(self, message: str, status_code: int = 502, details: Optional[Any] = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details


class CrunchyrollClient:
    """Tiny HTTP client for Crunchyroll's beta API."""

    BASE_URL = "https://beta-api.crunchyroll.com"

    DEFAULT_BASIC_TOKENS = [
        "Y3Jfd2ViOg==",
        "bm9haWhkZXZtXzZpeWcwYThsMHE6",
        "bHF0ai11YmY1aHF4dGdvc2ZsYXQ6N2JIY3hfYnI0czJubWE1bVdrdHdKZEY0ZTU2UU5neFQ=",
    ]

    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    )

    def __init__(
        self,
        locale: str = "en-US",
        basic_tokens: Optional[Iterable[str]] = None,
        timeout_seconds: float = 12.0,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.locale = locale or "en-US"
        self.basic_tokens = list(basic_tokens) if basic_tokens is not None else self.basic_tokens_from_env()
        if not self.basic_tokens:
            self.basic_tokens = list(self.DEFAULT_BASIC_TOKENS)

        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()
        self.anonymous_id = str(uuid.uuid4())
        self._access_token: Optional[str] = None
        self._token_expires_at = 0.0

    @classmethod
    def basic_tokens_from_env(cls) -> List[str]:
        """Return CR_BASIC_TOKENS override or the built-in fallback tokens.

        CR_BASIC_TOKENS may be a JSON array, or a comma/space/newline separated
        list.  Values may optionally include a leading "Basic ".
        """

        raw = os.getenv("CR_BASIC_TOKENS", "").strip()
        if not raw:
            return list(cls.DEFAULT_BASIC_TOKENS)

        values: List[str]
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                values = [str(item) for item in parsed]
            else:
                values = [str(parsed)]
        except json.JSONDecodeError:
            values = re.split(r"[\s,;]+", raw)

        tokens: List[str] = []
        for value in values:
            token = value.strip()
            if not token:
                continue
            if token.lower().startswith("basic "):
                token = token[6:].strip()
            if token:
                tokens.append(token)
        return tokens

    def authenticate(self, force: bool = False) -> str:
        """Get an anonymous bearer token, refreshing before expiry.

        Tokens are cached until roughly five minutes before their advertised
        expiry.  If one Basic credential is rejected, the client silently tries
        the next fallback token.
        """

        now = time.time()
        if not force and self._access_token and self._token_expires_at > now:
            return self._access_token

        last_error: Optional[CrunchyrollAPIError] = None
        auth_url = f"{self.BASE_URL}/auth/v1/token"

        for basic_token in self.basic_tokens:
            headers = {
                "Authorization": f"Basic {basic_token}",
                "ETP-Anonymous-ID": self.anonymous_id,
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": self.USER_AGENT,
            }
            data = {"grant_type": "client_id", "scope": "offline_access"}

            try:
                response = self.session.post(
                    auth_url,
                    headers=headers,
                    data=data,
                    timeout=self.timeout_seconds,
                )
            except requests.Timeout as exc:
                last_error = CrunchyrollAPIError(
                    "Timed out while authenticating with Crunchyroll.",
                    status_code=504,
                    details=str(exc),
                )
                continue
            except requests.RequestException as exc:
                last_error = CrunchyrollAPIError(
                    "Could not connect to Crunchyroll while authenticating.",
                    status_code=502,
                    details=str(exc),
                )
                continue

            if response.status_code in (401, 403):
                last_error = CrunchyrollAPIError(
                    "Crunchyroll rejected one anonymous auth token; trying another token.",
                    status_code=response.status_code,
                    details=self._safe_text(response),
                )
                continue

            if response.status_code >= 400:
                last_error = CrunchyrollAPIError(
                    f"Crunchyroll authentication failed with HTTP {response.status_code}.",
                    status_code=502 if response.status_code >= 500 else response.status_code,
                    details=self._safe_text(response),
                )
                continue

            try:
                payload = response.json()
            except ValueError:
                last_error = CrunchyrollAPIError(
                    "Crunchyroll authentication returned a non-JSON response.",
                    status_code=502,
                    details=self._safe_text(response),
                )
                continue

            access_token = payload.get("access_token")
            if not access_token:
                last_error = CrunchyrollAPIError(
                    "Crunchyroll authentication response did not include an access token.",
                    status_code=502,
                    details=payload,
                )
                continue

            try:
                expires_in = int(payload.get("expires_in", 3600))
            except (TypeError, ValueError):
                expires_in = 3600

            self._access_token = access_token
            # Refresh five minutes early; never use a negative cache lifetime.
            self._token_expires_at = time.time() + max(60, expires_in - 300)
            LOGGER.debug("Authenticated with Crunchyroll anonymous API")
            return access_token

        if last_error and last_error.status_code == 403:
            raise CrunchyrollAPIError(
                "Crunchyroll blocked anonymous authentication with bot protection. Try again shortly or provide fresh CR_BASIC_TOKENS.",
                status_code=403,
                details=last_error.details,
            )
        raise last_error or CrunchyrollAPIError("Unable to authenticate with Crunchyroll.", status_code=502)

    def request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None, retry_auth: bool = True) -> Dict[str, Any]:
        """Send an authenticated request and return parsed JSON."""

        token = self.authenticate()
        url = path if path.startswith("http") else f"{self.BASE_URL}{path}"
        headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": self.USER_AGENT,
            "Accept": "application/json",
        }

        try:
            response = self.session.request(
                method.upper(),
                url,
                headers=headers,
                params=params,
                timeout=self.timeout_seconds,
            )
        except requests.Timeout as exc:
            raise CrunchyrollAPIError(
                "Timed out while fetching live data from Crunchyroll.",
                status_code=504,
                details=str(exc),
            ) from exc
        except requests.RequestException as exc:
            raise CrunchyrollAPIError(
                "Could not connect to Crunchyroll.",
                status_code=502,
                details=str(exc),
            ) from exc

        if response.status_code == 401 and retry_auth:
            # Token may have been invalidated early. Re-auth once, then retry.
            self.authenticate(force=True)
            return self.request(method, path, params=params, retry_auth=False)

        if response.status_code == 401:
            raise CrunchyrollAPIError(
                "Crunchyroll rejected the anonymous bearer token after refresh.",
                status_code=502,
                details=self._safe_text(response),
            )

        if response.status_code == 403:
            raise CrunchyrollAPIError(
                "Crunchyroll blocked this request with bot protection. Try again shortly.",
                status_code=403,
                details=self._safe_text(response),
            )

        if response.status_code >= 400:
            status_code = response.status_code if response.status_code < 500 else 502
            raise CrunchyrollAPIError(
                f"Crunchyroll returned HTTP {response.status_code}.",
                status_code=status_code,
                details=self._response_details(response),
            )

        try:
            parsed = response.json()
        except ValueError as exc:
            raise CrunchyrollAPIError(
                "Crunchyroll returned a non-JSON response.",
                status_code=502,
                details=self._safe_text(response),
            ) from exc

        if not isinstance(parsed, dict):
            raise CrunchyrollAPIError(
                "Crunchyroll returned JSON in an unexpected format.",
                status_code=502,
                details=parsed,
            )
        return parsed

    def search(self, query: str, limit: int = 10, content_type: str = "series") -> Dict[str, Any]:
        return self.request(
            "GET",
            "/content/v2/discover/search",
            params={"q": query, "n": limit, "locale": self.locale, "type": content_type},
        )

    def series_info(self, series_id: str) -> Dict[str, Any]:
        return self.request("GET", f"/content/v2/cms/series/{series_id}", params={"locale": self.locale})

    def seasons(self, series_id: str) -> Dict[str, Any]:
        return self.request("GET", f"/content/v2/cms/series/{series_id}/seasons", params={"locale": self.locale})

    def episodes(self, season_id: str) -> Dict[str, Any]:
        return self.request("GET", f"/content/v2/cms/seasons/{season_id}/episodes", params={"locale": self.locale})

    def movie_object(self, object_id: str) -> Dict[str, Any]:
        return self.request(
            "GET",
            f"/content/v2/cms/objects/{object_id}",
            params={"ratings": "true", "locale": self.locale},
        )

    def newly_added(self, limit: int = 20) -> Dict[str, Any]:
        return self.request(
            "GET",
            "/content/v2/discover/browse",
            params={"sort_by": "newly_added", "type": "series", "n": limit, "locale": self.locale},
        )

    def calendar(self, limit: int = 100) -> Dict[str, Any]:
        return self.request(
            "GET",
            "/content/v2/discover/calendar",
            params={"limit": limit, "locale": self.locale},
        )

    @staticmethod
    def _safe_text(response: requests.Response, limit: int = 600) -> str:
        try:
            return (response.text or "")[:limit]
        except Exception:  # pragma: no cover - extremely defensive
            return ""

    def _response_details(self, response: requests.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return self._safe_text(response)
