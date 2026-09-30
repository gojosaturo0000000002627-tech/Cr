# Crunchyroll Anime Info Web Service + Telegram Bot

Production-ready Python Flask service that fetches **live anime information from Crunchyroll's anonymous API** and can also reply from a Telegram bot.

It does **not** use hardcoded anime data. It never invents fallback results. If Crunchyroll fails or blocks a request, the API returns a clear JSON error.

## Final Render Web Service commands

Use these exact commands in Render **Web Service** settings:

```bash
Build Command: bash build.sh
Start Command: bash start.sh
```

Then click:

```text
Manual Deploy -> Clear build cache & deploy
```

The project installs Python packages into a local `vendor/` folder during build and starts Gunicorn with `PYTHONPATH=vendor`. This avoids Render PATH/virtualenv problems such as:

```text
gunicorn: command not found
python: command not found
/usr/bin/python3: No module named gunicorn
.venv/bin/gunicorn: No such file or directory
```

## Main features

- Flask + Gunicorn, binds to `0.0.0.0:$PORT`
- Render free-tier friendly
- Crunchyroll anonymous token auth with fallback Basic tokens
- `CR_BASIC_TOKENS` env override
- Token refresh before expiry
- 401 re-auth retry
- Friendly 403 bot-protection errors
- Timeout and non-JSON handling
- In-memory response cache capped at 45 minutes
- Anime series report with dub-wise episode counts and next episode dates
- Total episodes are **not summed across dubs**; the service uses max per-language count
- IST timestamp display
- Telegram webhook bot support
- Tests with fake Crunchyroll responses

## Files

```text
app.py                 Flask routes, API, Telegram webhook routes
cache.py               In-memory TTL cache
config.py              Env configuration
crunchyroll.py         Crunchyroll anonymous API client
services.py            Anime/movie report logic
telegram_bot.py        Telegram command handling and webhook setup
templates/index.html   Mobile-friendly web UI
build.sh               Render build script
start.sh               Render start script
requirements.txt       Python dependencies
Procfile               web: gunicorn app:app
render.yaml            Render Blueprint config
gunicorn.conf.py       Gunicorn defaults
tests/test_app.py      Mock tests
```

## API endpoints

### `GET /anime/<name>`

Live anime report:

- name and Crunchyroll URL
- status: `Ongoing` if any language has a future `free_publish_date`, otherwise `No upcoming listed`
- total seasons by distinct `season_number`
- total episodes as max episode count across languages, not a sum across dubs
- every dub language with total episodes, latest released episode, and next episode in IST
- platforms list
- description and subtitle languages
- `checked_at_utc`

Example:

```bash
curl https://your-service.onrender.com/anime/One%20Piece
```

### `GET /movie/<name>`

Returns movie title, release year, runtime, available dub languages, description, URL, and `checked_at_utc`.

### Other endpoints

```text
GET /search?q=Naruto
GET /search?q=Suzume&type=movie_listing
GET /new
GET /calendar
GET /health
GET /
```

## Telegram bot setup

If `/start` in your Telegram bot does nothing, webhook is not connected yet or env variables are missing.

### 1. Create bot token

Open Telegram -> **@BotFather** -> `/newbot` -> copy the token.

### 2. Add Render environment variables

Render -> your Web Service -> **Environment**:

```text
TELEGRAM_BOT_TOKEN=your_botfather_token_here
TELEGRAM_WEBHOOK_SECRET=make-any-random-secret-like-anime12345
```

`PUBLIC_BASE_URL` is optional on Render because the code can read Render's public URL variables. If you want to set it manually:

```text
PUBLIC_BASE_URL=https://your-service.onrender.com
```

Never put your real bot token in GitHub.

### 3. Deploy again

```text
Manual Deploy -> Clear build cache & deploy
```

### 4. Check bot status

Open:

```text
https://your-service.onrender.com/telegram/status
```

If it shows `configured: true`, open the setup endpoint shown in that JSON, or manually open:

```text
https://your-service.onrender.com/telegram/setup/YOUR_TELEGRAM_WEBHOOK_SECRET
```

You should get JSON with `ok: true`.

### 5. Test in Telegram

Send:

```text
/start
```

Commands:

```text
/start
/help
/anime One Piece
/movie Suzume
/search Naruto
/new
/calendar
```

You can also send just an anime name, for example:

```text
One Piece
```

## Environment variables

| Variable | Default | Notes |
|---|---:|---|
| `PORT` | Render sets it | Gunicorn binds to `0.0.0.0:$PORT`. |
| `CR_LOCALE` | `en-US` | Crunchyroll metadata locale. |
| `CACHE_TTL_SECONDS` | `2700` | Capped to 2700 seconds / 45 minutes. Use `0` to disable. |
| `CR_BASIC_TOKENS` | built-in list | Optional comma-separated or JSON-array Basic tokens. |
| `TELEGRAM_BOT_TOKEN` | empty | Optional BotFather token. Required for Telegram bot. |
| `TELEGRAM_WEBHOOK_SECRET` | empty | Secret path segment for Telegram webhook. Required for Telegram bot. |
| `PUBLIC_BASE_URL` | auto on Render | Optional public URL override. |
| `TELEGRAM_AUTO_SET_WEBHOOK` | `true` | Auto-register webhook on startup when env vars exist. |

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest -q
python app.py
```

Open:

```text
http://localhost:8000
```

## GitHub -> Render deploy

1. Push this project to GitHub.
2. Render -> **New +** -> **Web Service**.
3. Select your GitHub repo.
4. Runtime: Python.
5. Build Command:

```bash
bash build.sh
```

6. Start Command:

```bash
bash start.sh
```

7. Add env vars if using Telegram.
8. Deploy.
9. Open `/health`, `/anime/One%20Piece`, and `/telegram/status`.

## Important Crunchyroll note

Crunchyroll has no official public API. This project uses undocumented anonymous endpoints. They can change or be blocked. If that happens, the service returns a clear error instead of fake anime data.
