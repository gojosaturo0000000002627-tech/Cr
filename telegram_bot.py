"""Telegram bot frontend for the Crunchyroll anime service.

The Flask app exposes a Telegram webhook endpoint.  Telegram sends updates to
that endpoint, this module builds a reply, and the reply is sent with the
Telegram Bot API.  No separate worker process is required, which keeps the
project suitable for Render's free Web Service plan.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, Iterable, List, Optional

import requests

from crunchyroll import CrunchyrollAPIError
from services import AnimeService

LOGGER = logging.getLogger(__name__)
TELEGRAM_API_BASE = "https://api.telegram.org"
MAX_TELEGRAM_MESSAGE = 3900

BOT_COMMANDS = [
    {"command": "start", "description": "Show help"},
    {"command": "anime", "description": "Anime report, e.g. /anime One Piece"},
    {"command": "movie", "description": "Movie report, e.g. /movie Suzume"},
    {"command": "search", "description": "Search Crunchyroll"},
    {"command": "new", "description": "Newly added anime"},
    {"command": "calendar", "description": "Upcoming episodes"},
]


def bot_configured() -> bool:
    """Return True when enough Telegram settings exist to receive and reply."""

    return bool(get_bot_token() and get_webhook_secret())


def get_bot_token() -> str:
    return os.getenv("TELEGRAM_BOT_TOKEN", "").strip()


def get_webhook_secret() -> str:
    return os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()


def public_base_url() -> str:
    """Return the public Render URL.

    Prefer PUBLIC_BASE_URL when the user sets it.  On Render, fall back to the
    default runtime variables if available.
    """

    explicit = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if explicit:
        return explicit

    render_external_url = os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    if render_external_url:
        return render_external_url

    render_hostname = os.getenv("RENDER_EXTERNAL_HOSTNAME", "").strip().strip("/")
    if render_hostname:
        return f"https://{render_hostname}"

    return ""


def expected_webhook_url() -> Optional[str]:
    base_url = public_base_url()
    secret = get_webhook_secret()
    if not base_url or not secret:
        return None
    return f"{base_url}/telegram/webhook/{secret}"


def telegram_api(method: str, payload: Optional[Dict[str, Any]] = None, timeout: float = 12) -> Dict[str, Any]:
    """Call Telegram Bot API and return JSON, raising RuntimeError on failure."""

    token = get_bot_token()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set.")

    try:
        response = requests.post(
            f"{TELEGRAM_API_BASE}/bot{token}/{method}",
            json=payload or {},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Telegram API request failed: {exc}") from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(f"Telegram API returned non-JSON HTTP {response.status_code}: {response.text[:300]}") from exc

    if response.status_code >= 400 or not data.get("ok", False):
        description = data.get("description") or response.text[:300]
        raise RuntimeError(f"Telegram API {method} failed: {description}")

    return data


def set_webhook(drop_pending_updates: bool = False) -> Dict[str, Any]:
    """Register this Render service as the Telegram webhook.

    drop_pending_updates defaults to False so a Render restart does not delete a
    user's /start message that woke the free service from sleep.
    """

    webhook_url = expected_webhook_url()
    if not webhook_url:
        raise RuntimeError(
            "Cannot set webhook. Set TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET, and PUBLIC_BASE_URL "
            "or deploy on Render where RENDER_EXTERNAL_URL/RENDER_EXTERNAL_HOSTNAME is available."
        )

    set_commands_result = telegram_api("setMyCommands", {"commands": BOT_COMMANDS})
    webhook_result = telegram_api(
        "setWebhook",
        {
            "url": webhook_url,
            "allowed_updates": ["message", "edited_message"],
            "drop_pending_updates": drop_pending_updates,
        },
    )
    return {
        "ok": True,
        "webhook_url": webhook_url,
        "set_commands": set_commands_result.get("result"),
        "set_webhook": webhook_result.get("result"),
        "description": webhook_result.get("description"),
    }


def delete_webhook() -> Dict[str, Any]:
    result = telegram_api("deleteWebhook", {"drop_pending_updates": True})
    return {"ok": True, "result": result.get("result"), "description": result.get("description")}


def get_webhook_info() -> Dict[str, Any]:
    result = telegram_api("getWebhookInfo")
    return {"ok": True, "result": result.get("result")}


def auto_set_webhook_if_configured() -> None:
    """Set Telegram webhook in a background thread when env vars are present.

    This makes the bot work after deploy without the user having to manually open
    Telegram's setWebhook URL.  Failures are logged but never stop the web app.
    """

    enabled = os.getenv("TELEGRAM_AUTO_SET_WEBHOOK", "true").strip().lower() not in {"0", "false", "no", "off"}
    if not enabled or not bot_configured() or not public_base_url():
        return

    def worker() -> None:
        try:
            result = set_webhook()
            LOGGER.info("Telegram webhook configured: %s", result.get("webhook_url"))
        except Exception:
            LOGGER.exception("Could not auto-configure Telegram webhook")

    threading.Thread(target=worker, name="telegram-webhook-setup", daemon=True).start()


def handle_telegram_update_async(update: Dict[str, Any], service: AnimeService) -> None:
    """Handle an update in a daemon thread so Telegram receives webhook ACK fast."""

    threading.Thread(
        target=handle_telegram_update,
        args=(update, service),
        name="telegram-update-handler",
        daemon=True,
    ).start()


def handle_telegram_update(update: Dict[str, Any], service: AnimeService) -> None:
    """Process one Telegram update and send a reply when it contains a message."""

    message = update.get("message") or update.get("edited_message")
    if not isinstance(message, dict):
        return

    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return

    text = str(message.get("text") or "").strip()
    if not text:
        send_message(chat_id, "Send an anime name, or use /anime One Piece")
        return

    try:
        # Show Telegram's typing indicator while Crunchyroll live data is fetched.
        send_chat_action(chat_id, "typing")
        reply = build_reply(text, service)
    except CrunchyrollAPIError as exc:
        reply = f"Error: {exc.message}"
    except Exception:  # pragma: no cover - defensive for live bot safety
        LOGGER.exception("Telegram bot failed while handling update")
        reply = "Sorry, something went wrong while fetching live anime data. Try again shortly."

    send_long_message(chat_id, reply)


def build_reply(text: str, service: AnimeService) -> str:
    """Return the Telegram text reply for a user command/message."""

    raw_text = text.strip()
    simple = raw_text.lower().strip()
    if simple in {"start", "help", "hi", "hello", "menu"}:
        return start_message()

    command, argument = split_command(raw_text)

    if command in {"/start", "/help"}:
        return start_message()

    if command == "/anime":
        if not argument:
            return "Usage: /anime One Piece"
        return format_anime_report(service.anime_report(argument))

    if command == "/movie":
        if not argument:
            return "Usage: /movie Suzume"
        return format_movie_report(service.movie_report(argument))

    if command == "/search":
        if not argument:
            return "Usage: /search Naruto"
        return format_search_results(service.search(argument, content_type="series", limit=8))

    if command == "/new":
        return format_new_results(service.newly_added(limit=10))

    if command == "/calendar":
        return format_calendar(service.calendar(limit=10))

    if command.startswith("/"):
        return "Unknown command. Use /help to see commands."

    # Beginner-friendly default: plain text is treated as an anime report.
    return format_anime_report(service.anime_report(raw_text))


def split_command(text: str) -> tuple[str, str]:
    parts = text.strip().split(maxsplit=1)
    command = parts[0].split("@", 1)[0].lower() if parts else ""
    argument = parts[1].strip() if len(parts) > 1 else ""
    return command, argument


def start_message() -> str:
    return (
        "Namaste! Main Crunchyroll se live anime info laata hoon.\n\n"
        "Commands:\n"
        "/anime One Piece - series report with dub-wise next episodes\n"
        "/movie Suzume - movie info\n"
        "/search Naruto - search Crunchyroll\n"
        "/new - newly added anime\n"
        "/calendar - upcoming Crunchyroll episodes\n\n"
        "Tip: aap simple anime name bhi bhej sakte ho, jaise: One Piece"
    )


def format_anime_report(data: Dict[str, Any]) -> str:
    lines: List[str] = [
        f"Anime: {data.get('name')}",
        f"Status: {data.get('status')}",
        f"Seasons: {data.get('total_seasons')}",
        f"Total episodes: {data.get('total_episodes')}",
        "",
        "Dub languages:",
    ]

    for language in data.get("languages") or []:
        latest = format_episode_summary(language.get("latest_released_episode"))
        next_episode = format_episode_summary(language.get("next_episode"))
        lines.extend(
            [
                f"- {language.get('language')} ({language.get('locale')}): {language.get('total_episodes')} episodes",
                f"  Latest: {latest}",
                f"  Next: {next_episode}",
            ]
        )

    subtitles = data.get("subtitle_languages") or []
    if subtitles:
        sub_text = ", ".join(item.get("language", "Unknown") for item in subtitles[:12])
        if len(subtitles) > 12:
            sub_text += f", +{len(subtitles) - 12} more"
        lines.extend(["", f"Subtitles: {sub_text}"])

    if data.get("description"):
        lines.extend(["", shorten(str(data["description"]), 700)])

    lines.extend(
        [
            "",
            f"URL: {data.get('crunchyroll_url')}",
            f"Checked UTC: {data.get('checked_at_utc')}",
        ]
    )
    return "\n".join(lines)


def format_episode_summary(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return "not announced yet"
    number = value.get("number") or "?"
    date_ist = value.get("date_ist") or value.get("date_utc") or "date unknown"
    title = value.get("title")
    title_text = f" - {title}" if title else ""
    return f"Episode {number}{title_text} on {date_ist}"


def format_movie_report(data: Dict[str, Any]) -> str:
    dubs = ", ".join(item.get("language", "Unknown") for item in data.get("available_dub_languages") or [])
    if not dubs:
        dubs = "No dub data returned"
    return "\n".join(
        [
            f"Movie: {data.get('title')}",
            f"Year: {data.get('release_year') or 'Unknown'}",
            f"Runtime: {data.get('runtime_minutes') or 'Unknown'} minutes",
            f"Dubs: {dubs}",
            "",
            shorten(str(data.get("description") or "No description returned."), 900),
            "",
            f"URL: {data.get('crunchyroll_url')}",
            f"Checked UTC: {data.get('checked_at_utc')}",
        ]
    )


def format_search_results(data: Dict[str, Any]) -> str:
    results = data.get("results") or []
    if not results:
        return "No Crunchyroll results found."
    lines = [f"Search results for: {data.get('query')}"]
    for index, item in enumerate(results, start=1):
        lines.append(f"{index}. {item.get('title')}\n   {item.get('url')}")
    return "\n".join(lines)


def format_new_results(data: Dict[str, Any]) -> str:
    results = data.get("results") or []
    if not results:
        return "No newly added anime returned by Crunchyroll."
    lines = ["Newly added on Crunchyroll:"]
    for index, item in enumerate(results[:10], start=1):
        lines.append(f"{index}. {item.get('title')}\n   {item.get('url')}")
    lines.append(f"Checked UTC: {data.get('checked_at_utc')}")
    return "\n".join(lines)


def format_calendar(data: Dict[str, Any]) -> str:
    episodes = data.get("episodes") or []
    if not episodes:
        return "No calendar episodes returned by Crunchyroll."
    lines = ["Upcoming Crunchyroll calendar:"]
    for index, episode in enumerate(episodes[:10], start=1):
        title = episode.get("series_title") or episode.get("episode_title") or "Unknown title"
        number = episode.get("episode_number") or "?"
        date_ist = episode.get("free_publish_date_ist") or "date unknown"
        lang = episode.get("audio_language") or episode.get("audio_locale") or "Unknown language"
        lines.append(f"{index}. {title} Ep {number} ({lang})\n   {date_ist}")
    lines.append(f"Checked UTC: {data.get('checked_at_utc')}")
    return "\n".join(lines)


def send_long_message(chat_id: Any, text: str) -> None:
    for chunk in chunk_text(text, MAX_TELEGRAM_MESSAGE):
        send_message(chat_id, chunk)


def send_message(chat_id: Any, text: str) -> None:
    token = get_bot_token()
    if not token:
        LOGGER.warning("TELEGRAM_BOT_TOKEN is missing; cannot send Telegram reply")
        return

    try:
        response = requests.post(
            f"{TELEGRAM_API_BASE}/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=10,
        )
        if response.status_code >= 400:
            LOGGER.warning("Telegram sendMessage failed: %s %s", response.status_code, response.text[:400])
    except requests.RequestException:
        LOGGER.exception("Could not send Telegram message")


def send_chat_action(chat_id: Any, action: str = "typing") -> None:
    token = get_bot_token()
    if not token:
        return
    try:
        requests.post(
            f"{TELEGRAM_API_BASE}/bot{token}/sendChatAction",
            json={"chat_id": chat_id, "action": action},
            timeout=5,
        )
    except requests.RequestException:
        LOGGER.debug("Could not send Telegram chat action", exc_info=True)


def chunk_text(text: str, max_length: int) -> Iterable[str]:
    text = text or ""
    if len(text) <= max_length:
        yield text
        return

    remaining = text
    while len(remaining) > max_length:
        split_at = remaining.rfind("\n", 0, max_length)
        if split_at < 1000:
            split_at = max_length
        yield remaining[:split_at].strip()
        remaining = remaining[split_at:].strip()
    if remaining:
        yield remaining


def shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."
