"""Business logic for anime, movie, search, newly-added, and calendar APIs."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from cache import TTLCache
from crunchyroll import CrunchyrollAPIError, CrunchyrollClient

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc

LANGUAGE_NAMES = {
    "ar": "Arabic",
    "bn": "Bengali",
    "cs": "Czech",
    "da": "Danish",
    "de": "German",
    "el": "Greek",
    "en": "English",
    "es": "Spanish",
    "fi": "Finnish",
    "fr": "French",
    "he": "Hebrew",
    "hi": "Hindi",
    "hu": "Hungarian",
    "id": "Indonesian",
    "it": "Italian",
    "ja": "Japanese",
    "kn": "Kannada",
    "ko": "Korean",
    "ml": "Malayalam",
    "mr": "Marathi",
    "ms": "Malay",
    "nl": "Dutch",
    "no": "Norwegian",
    "pl": "Polish",
    "pt": "Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "sv": "Swedish",
    "ta": "Tamil",
    "te": "Telugu",
    "th": "Thai",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "ur": "Urdu",
    "vi": "Vietnamese",
    "zh": "Chinese",
}


class AnimeService:
    """High-level service used by Flask routes and future bot frontends."""

    def __init__(self, client: CrunchyrollClient, cache: TTLCache[Dict[str, Any]]) -> None:
        self.client = client
        self.cache = cache

    def search(self, query: str, content_type: str = "series", limit: int = 10) -> Dict[str, Any]:
        query = clean_query(query)
        limit = normalize_limit(limit, default=10, maximum=50)
        content_type = validate_content_type(content_type)
        cache_key = ("search", self.client.locale, content_type, query.lower(), limit)
        return self.cache.get_or_set(cache_key, lambda: self._search_uncached(query, content_type, limit))

    def anime_report(self, name: str) -> Dict[str, Any]:
        name = clean_query(name)
        cache_key = ("anime", self.client.locale, name.lower())
        return self.cache.get_or_set(cache_key, lambda: self._anime_report_uncached(name))

    def movie_report(self, name: str) -> Dict[str, Any]:
        name = clean_query(name)
        cache_key = ("movie", self.client.locale, name.lower())
        return self.cache.get_or_set(cache_key, lambda: self._movie_report_uncached(name))

    def newly_added(self, limit: int = 20) -> Dict[str, Any]:
        limit = normalize_limit(limit, default=20, maximum=50)
        cache_key = ("new", self.client.locale, limit)
        return self.cache.get_or_set(cache_key, lambda: self._newly_added_uncached(limit))

    def calendar(self, limit: int = 100) -> Dict[str, Any]:
        limit = normalize_limit(limit, default=100, maximum=200)
        cache_key = ("calendar", self.client.locale, limit)
        return self.cache.get_or_set(cache_key, lambda: self._calendar_uncached(limit))

    def _search_uncached(self, query: str, content_type: str, limit: int) -> Dict[str, Any]:
        checked_at = utc_now()
        raw = self.client.search(query, limit=limit, content_type=content_type)
        results = normalize_search_results(raw, content_type)
        return {
            "query": query,
            "type": content_type,
            "platform": "Crunchyroll",
            "results": results,
            "checked_at_utc": iso_utc(checked_at),
        }

    def _anime_report_uncached(self, name: str) -> Dict[str, Any]:
        checked_at = utc_now()
        search_raw = self.client.search(name, limit=1, content_type="series")
        search_results = normalize_search_results(search_raw, "series")
        if not search_results:
            raise CrunchyrollAPIError(f"No Crunchyroll series found for '{name}'.", status_code=404)

        top_result = search_results[0]
        series_id = top_result["id"]
        series_raw = self.client.series_info(series_id)
        seasons_raw = self.client.seasons(series_id)

        series_obj = unwrap_object(series_raw)
        title = first_present(series_obj, "title", "name") or top_result["title"]
        description = first_present(
            series_obj,
            "description",
            "extended_description",
            "short_description",
            "synopsis",
            "seo_description",
        )
        slug_title = first_present(series_obj, "slug_title") or top_result.get("slug_title") or slugify(title)

        seasons = extract_data_list(seasons_raw)
        if not seasons:
            raise CrunchyrollAPIError(
                f"Crunchyroll returned no season data for '{title}'.",
                status_code=502,
            )

        distinct_season_numbers = set()
        subtitle_locales = set()
        language_groups: Dict[str, Dict[str, Any]] = {}

        for season in seasons:
            if not isinstance(season, dict):
                continue

            season_number = first_present(season, "season_number", "season_sequence_number")
            if season_number is not None:
                distinct_season_numbers.add(str(season_number))

            for subtitle_locale in season.get("subtitle_locales") or []:
                if subtitle_locale:
                    subtitle_locales.add(str(subtitle_locale))

            audio_locale = first_present(season, "audio_locale") or "und"
            group = language_groups.setdefault(
                audio_locale,
                {
                    "locale": audio_locale,
                    "name": language_name(audio_locale),
                    "episodes": {},
                },
            )

            season_id = choose_season_id_for_episodes(season, self.client.locale)
            if not season_id:
                continue

            episodes_raw = self.client.episodes(season_id)
            for episode in extract_data_list(episodes_raw):
                if not isinstance(episode, dict):
                    continue
                normalized_episode = normalize_episode(episode)
                episode_key = make_episode_key(season_number, normalized_episode, episode)
                # Duplicates may appear via versions. Keep the newest metadata for
                # a logical episode instead of double-counting it.
                group["episodes"][episode_key] = normalized_episode

        language_reports = []
        any_upcoming = False
        for locale_code, group in language_groups.items():
            episodes = list(group["episodes"].values())
            released = [episode for episode in episodes if episode["release_dt"] and episode["release_dt"] <= checked_at]
            upcoming = [episode for episode in episodes if episode["free_publish_dt"] and episode["free_publish_dt"] > checked_at]

            latest_episode = max(released, key=lambda episode: episode["release_dt"]) if released else None
            next_episode = min(upcoming, key=lambda episode: episode["free_publish_dt"]) if upcoming else None
            if next_episode:
                any_upcoming = True

            language_reports.append(
                {
                    "locale": locale_code,
                    "language": group["name"],
                    "total_episodes": len(episodes),
                    "latest_released_episode": episode_date_summary(latest_episode, "release_dt") if latest_episode else "none released yet",
                    "next_episode": episode_date_summary(next_episode, "free_publish_dt") if next_episode else "not announced yet",
                }
            )

        language_reports.sort(key=lambda item: (language_sort_rank(item["locale"]), item["language"]))
        total_episodes = max((item["total_episodes"] for item in language_reports), default=0)

        return {
            "name": title,
            "query": name,
            "crunchyroll_url": crunchyroll_url("series", series_id, slug_title, title),
            "status": "Ongoing" if any_upcoming else "No upcoming listed",
            "total_seasons": len(distinct_season_numbers),
            "total_episodes": total_episodes,
            "languages": language_reports,
            "platforms": ["Crunchyroll"],
            "description": description,
            "subtitle_languages": [
                {"locale": locale_code, "language": language_name(locale_code)}
                for locale_code in sorted(subtitle_locales, key=lambda loc: (language_sort_rank(loc), language_name(loc)))
            ],
            "checked_at_utc": iso_utc(checked_at),
        }

    def _movie_report_uncached(self, name: str) -> Dict[str, Any]:
        checked_at = utc_now()
        search_raw = self.client.search(name, limit=1, content_type="movie_listing")
        search_results = normalize_search_results(search_raw, "movie_listing")
        if not search_results:
            raise CrunchyrollAPIError(f"No Crunchyroll movie found for '{name}'.", status_code=404)

        top_result = search_results[0]
        object_id = top_result["id"]
        object_raw = self.client.movie_object(object_id)
        object_data = unwrap_object(object_raw)
        metadata = object_data.get("movie_listing_metadata") or object_data.get("movie_metadata") or object_data

        title = first_present(metadata, "title", "name") or first_present(object_data, "title", "name") or top_result["title"]
        description = first_present(metadata, "description", "short_description", "synopsis") or first_present(
            object_data, "description", "short_description", "synopsis"
        )
        release_year = first_present(metadata, "release_year") or first_present(object_data, "release_year")
        duration_ms = first_present(metadata, "duration_ms") or first_present(object_data, "duration_ms")
        slug_title = first_present(metadata, "slug_title") or first_present(object_data, "slug_title") or top_result.get("slug_title") or slugify(title)

        dub_locales = set()
        for version in metadata.get("versions") or object_data.get("versions") or []:
            if isinstance(version, dict) and version.get("audio_locale"):
                dub_locales.add(str(version["audio_locale"]))
        if first_present(metadata, "audio_locale"):
            dub_locales.add(str(first_present(metadata, "audio_locale")))

        return {
            "title": title,
            "query": name,
            "release_year": release_year,
            "runtime_minutes": duration_minutes(duration_ms),
            "available_dub_languages": [
                {"locale": locale_code, "language": language_name(locale_code)}
                for locale_code in sorted(dub_locales, key=lambda loc: (language_sort_rank(loc), language_name(loc)))
            ],
            "description": description,
            "crunchyroll_url": crunchyroll_url("movie", object_id, slug_title, title),
            "platforms": ["Crunchyroll"],
            "checked_at_utc": iso_utc(checked_at),
        }

    def _newly_added_uncached(self, limit: int) -> Dict[str, Any]:
        checked_at = utc_now()
        raw = self.client.newly_added(limit=limit)
        items = flatten_items(raw)
        results = []
        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = first_present(item, "id", "guid")
            title = first_present(item, "title", "name")
            if not item_id or not title:
                continue
            slug_title = first_present(item, "slug_title") or slugify(title)
            results.append(
                {
                    "id": item_id,
                    "title": title,
                    "type": first_present(item, "type") or "series",
                    "description": first_present(item, "description", "short_description", "synopsis"),
                    "url": crunchyroll_url("series", item_id, slug_title, title),
                }
            )
        return {
            "platform": "Crunchyroll",
            "results": results,
            "checked_at_utc": iso_utc(checked_at),
        }

    def _calendar_uncached(self, limit: int) -> Dict[str, Any]:
        checked_at = utc_now()
        raw = self.client.calendar(limit=limit)
        items = flatten_items(raw)
        episodes = []
        for item in items:
            if not isinstance(item, dict):
                continue
            metadata = item.get("episode_metadata") or {}
            free_dt = parse_cr_time(first_present(item, "free_publish_date") or first_present(metadata, "free_publish_date"))
            upload_dt = parse_cr_time(first_present(item, "upload_date") or first_present(metadata, "upload_date"))
            audio_locale = first_present(item, "audio_locale") or first_present(metadata, "audio_locale")
            item_id = first_present(item, "id", "guid")
            title = first_present(item, "title", "episode_title") or first_present(metadata, "title", "episode_title")
            series_title = first_present(item, "series_title") or first_present(metadata, "series_title")
            slug_title = first_present(item, "slug_title") or slugify(title or series_title or item_id or "episode")
            episodes.append(
                {
                    "id": item_id,
                    "series_title": series_title,
                    "episode_title": title,
                    "episode_number": first_present(item, "episode_number") or first_present(metadata, "episode_number"),
                    "audio_locale": audio_locale,
                    "audio_language": language_name(audio_locale) if audio_locale else None,
                    "free_publish_date_utc": iso_utc(free_dt) if free_dt else None,
                    "free_publish_date_ist": format_ist(free_dt) if free_dt else None,
                    "upload_date_utc": iso_utc(upload_dt) if upload_dt else None,
                    "is_premium_only": bool(first_present(item, "is_premium_only") or first_present(metadata, "is_premium_only") or False),
                    "duration_minutes": duration_minutes(first_present(item, "duration_ms") or first_present(metadata, "duration_ms")),
                    "url": crunchyroll_url("watch", item_id, slug_title, title) if item_id else None,
                }
            )

        episodes.sort(key=lambda episode: episode.get("free_publish_date_utc") or "9999")
        return {
            "platform": "Crunchyroll",
            "episodes": episodes,
            "checked_at_utc": iso_utc(checked_at),
        }


def clean_query(value: Any) -> str:
    query = str(value or "").strip()
    if not query:
        raise CrunchyrollAPIError("Missing required search query.", status_code=400)
    if len(query) > 200:
        raise CrunchyrollAPIError("Search query is too long.", status_code=400)
    return query


def normalize_limit(value: Any, default: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(1, min(number, maximum))


def validate_content_type(value: Any) -> str:
    content_type = str(value or "series").strip()
    allowed = {"series", "movie_listing"}
    if content_type not in allowed:
        raise CrunchyrollAPIError("type must be 'series' or 'movie_listing'.", status_code=400)
    return content_type


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def parse_cr_time(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def iso_utc(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def format_ist(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    return dt.astimezone(IST).replace(microsecond=0).isoformat()


def duration_minutes(duration_ms_value: Any) -> Optional[int]:
    try:
        duration_ms = float(duration_ms_value)
    except (TypeError, ValueError):
        return None
    if duration_ms <= 0:
        return None
    return int(round(duration_ms / 60000))


def first_present(mapping: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value is not None and value != "":
            return value
    return None


def unwrap_object(raw: Dict[str, Any]) -> Dict[str, Any]:
    data = raw.get("data") if isinstance(raw, dict) else None
    if isinstance(data, dict):
        return data
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return raw if isinstance(raw, dict) else {}


def extract_data_list(raw: Dict[str, Any]) -> List[Any]:
    if not isinstance(raw, dict):
        return []
    data = raw.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if isinstance(data.get("items"), list):
            return data["items"]
        return [data]
    if isinstance(raw.get("items"), list):
        return raw["items"]
    return []


def flatten_items(raw: Dict[str, Any]) -> List[Any]:
    """Flatten common Crunchyroll response shapes into a list of objects."""

    flattened: List[Any] = []
    for item in extract_data_list(raw):
        if isinstance(item, dict) and isinstance(item.get("items"), list):
            flattened.extend(item["items"])
        else:
            flattened.append(item)
    return flattened


def normalize_search_results(raw: Dict[str, Any], content_type: str) -> List[Dict[str, Any]]:
    results = []
    for item in flatten_search_items(raw):
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("item"), dict):
            item = item["item"]
        item_id = first_present(item, "id", "guid")
        title = first_present(item, "title", "name")
        if not item_id or not title:
            continue
        slug_title = first_present(item, "slug_title") or slugify(title)
        kind = "series" if content_type == "series" else "movie"
        results.append(
            {
                "id": item_id,
                "title": title,
                "slug_title": slug_title,
                "type": content_type,
                "url": crunchyroll_url(kind, item_id, slug_title, title),
            }
        )
    return results


def flatten_search_items(raw: Dict[str, Any]) -> List[Any]:
    if not isinstance(raw, dict):
        return []
    data = raw.get("data")
    if isinstance(data, list):
        items: List[Any] = []
        for group in data:
            if isinstance(group, dict) and isinstance(group.get("items"), list):
                items.extend(group["items"])
            elif isinstance(group, dict):
                items.append(group)
        return items
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return data["items"]
    if isinstance(raw.get("items"), list):
        return raw["items"]
    return []


def slugify(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "crunchyroll-title"


def crunchyroll_url(kind: str, object_id: Any, slug_title: Optional[str], title: Optional[str]) -> str:
    object_id = str(object_id)
    slug = slug_title or slugify(title or object_id)
    if kind == "series":
        return f"https://www.crunchyroll.com/series/{object_id}/{slug}"
    # Movie listings and calendar/watch objects are best represented by /watch.
    return f"https://www.crunchyroll.com/watch/{object_id}/{slug}"


def language_name(locale_code: Optional[str]) -> str:
    if not locale_code:
        return "Unknown"
    normalized = str(locale_code).replace("_", "-")
    language_code = normalized.split("-", 1)[0].lower()
    return LANGUAGE_NAMES.get(language_code, normalized)


def language_sort_rank(locale_code: Optional[str]) -> int:
    order = {
        "ja": 0,
        "en": 1,
        "hi": 2,
        "ta": 3,
        "te": 4,
        "kn": 5,
        "ml": 6,
    }
    if not locale_code:
        return 999
    return order.get(str(locale_code).split("-", 1)[0].lower(), 100)


def choose_season_id_for_episodes(season: Dict[str, Any], preferred_locale: str = "en-US") -> Optional[str]:
    """Pick the season GUID to use for the episodes endpoint.

    Crunchyroll season entries sometimes include a versions array with GUIDs for
    different metadata locales.  The requested behavior is to use the version
    matching en-US/CR_LOCALE when available, otherwise the season's own id.
    """

    for version in season.get("versions") or []:
        if not isinstance(version, dict):
            continue
        version_locale = first_present(version, "locale", "media_locale")
        if version_locale == preferred_locale:
            version_id = first_present(version, "guid", "id", "season_id")
            if version_id:
                return str(version_id)
    season_id = first_present(season, "id", "guid")
    return str(season_id) if season_id else None


def normalize_episode(episode: Dict[str, Any]) -> Dict[str, Any]:
    metadata = episode.get("episode_metadata") or {}
    free_dt = parse_cr_time(first_present(episode, "free_publish_date") or first_present(metadata, "free_publish_date"))
    upload_dt = parse_cr_time(first_present(episode, "upload_date") or first_present(metadata, "upload_date"))
    release_dt = free_dt or upload_dt
    number = first_present(episode, "episode_number") or first_present(metadata, "episode_number")
    title = first_present(episode, "title", "episode_title") or first_present(metadata, "title", "episode_title")
    return {
        "id": first_present(episode, "id", "guid"),
        "number": normalize_episode_number(number),
        "title": title,
        "free_publish_dt": free_dt,
        "upload_dt": upload_dt,
        "release_dt": release_dt,
        "is_premium_only": bool(first_present(episode, "is_premium_only") or first_present(metadata, "is_premium_only") or False),
        "duration_minutes": duration_minutes(first_present(episode, "duration_ms") or first_present(metadata, "duration_ms")),
    }


def normalize_episode_number(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def make_episode_key(season_number: Any, normalized_episode: Dict[str, Any], raw_episode: Dict[str, Any]) -> Tuple[str, str]:
    season_key = str(season_number) if season_number is not None else "unknown-season"
    number = normalized_episode.get("number")
    if number is not None:
        return season_key, f"episode-{number}"
    episode_id = first_present(raw_episode, "id", "guid") or id(raw_episode)
    return season_key, f"id-{episode_id}"


def episode_date_summary(episode: Optional[Dict[str, Any]], date_key: str) -> Any:
    if not episode:
        return None
    dt = episode.get(date_key)
    return {
        "number": episode.get("number"),
        "title": episode.get("title"),
        "date_utc": iso_utc(dt),
        "date_ist": format_ist(dt),
        "is_premium_only": episode.get("is_premium_only"),
        "duration_minutes": episode.get("duration_minutes"),
    }
