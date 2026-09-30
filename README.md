# Crunchyroll Anime Info Web Service

## FINAL Render commands that work

Use these exact commands in Render Settings:

```bash
Build Command: pip install -r requirements.txt
Start Command: .venv/bin/gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120
```

Why this is needed: Render installs packages into `.venv`, so the start command must call `.venv/bin/gunicorn` directly. Do not use `gunicorn app:app`, `python -m gunicorn`, or `python3 -m gunicorn` on Render for this service.

After changing commands, click **Manual Deploy → Clear build cache & deploy**.


A production-ready Python + Flask web service that fetches **live anime information from Crunchyroll's anonymous API**. It does not use hardcoded anime data and never invents fallback results. If Crunchyroll blocks or fails, the API returns a clear JSON error.

## What it includes

- Flask app with `gunicorn app:app`
- Anonymous Crunchyroll auth with multiple fallback Basic tokens
- Token refresh about 5 minutes before expiry
- 401 re-auth retry, friendly 403 bot-protection errors, timeout and non-JSON handling
- In-memory response cache capped at **45 minutes max**
- Per-language anime episode counts and next episode dates in IST
- Movie lookup
- Search, newly added, calendar, health check, and a simple mobile-friendly web UI
- Mock tests for Crunchyroll token/search/series/seasons/episodes/movie/new/calendar flows


## Render start-command fix

If Render logs show either of these errors:

```text
gunicorn: command not found
python: command not found
/usr/bin/python3: No module named gunicorn
```

it means Render installed dependencies inside its virtualenv at `/opt/render/project/src/.venv`, but your Start Command is using the system Python instead of that virtualenv.

Use these exact commands in Render **Settings**:

```bash
Build Command: pip install -r requirements.txt
Start Command: .venv/bin/gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120
```

Or use this direct Start Command:

```bash
/opt/render/project/src/.venv/bin/python -m gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 60
```

After changing the commands, click **Manual Deploy → Clear build cache & deploy**.

## Files

```text
app.py              Flask routes and error handling
cache.py            Small in-memory TTL cache
config.py           Environment configuration helpers
crunchyroll.py      Crunchyroll anonymous API client
services.py         Report-building business logic
templates/index.html Web UI
requirements.txt    Python dependencies
Procfile            Render/Gunicorn process command
render.yaml         Render Blueprint configuration
gunicorn.conf.py    Binds Gunicorn to 0.0.0.0:$PORT
.gitignore          Git ignore rules
tests/test_app.py   Mock tests
```

## API endpoints

### `GET /anime/<name>`
Builds a live Crunchyroll series report.

Example:

```bash
curl https://your-service.onrender.com/anime/One%20Piece
```

Returns:

- name and Crunchyroll URL
- status: `Ongoing` if any language has a future `free_publish_date`, otherwise `No upcoming listed`
- total seasons by **distinct season_number**
- total episodes as the **maximum per-language episode count**, not a sum across dubs
- each dub language with total episodes, latest released episode, and next episode date/time in IST
- subtitle languages
- description
- `checked_at_utc`

### `GET /movie/<name>`
Searches Crunchyroll movie listings and returns title, release year, runtime, dub languages, description, and URL.

### `GET /search?q=<query>`
Series search results. Optional parameters:

- `type=series` or `type=movie_listing`
- `limit=10`

### `GET /new`
Newly added Crunchyroll series.

### `GET /calendar`
Upcoming Crunchyroll episodes.

### `GET /health`
Simple health check for uptime monitors or keep-alive pings.

### `GET /`
Mobile-friendly web page with a search box.

## Environment variables

| Variable | Default | Notes |
|---|---:|---|
| `PORT` | `8000` locally | Render sets this automatically. Gunicorn binds to `0.0.0.0:$PORT`. |
| `CR_LOCALE` | `en-US` | Crunchyroll metadata locale. |
| `CACHE_TTL_SECONDS` | `2700` | Response cache TTL. Values above 2700 are capped to 2700. Use `0` to disable response caching. |
| `CR_BASIC_TOKENS` | built-in fallback list | Optional override. Use comma-separated tokens or a JSON array. Do not include `Authorization: Basic`; just the Base64 values are enough. |

## Local setup

1. Install Python 3.11 or newer.
2. Create and activate a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Run tests:

```bash
pytest -q
```

5. Start the app locally:

```bash
python app.py
```

Open `http://localhost:8000`.

You can also run it exactly like Render/Gunicorn:

```bash
PORT=8000 bash start.sh
```

## Deploy from GitHub to Render.com

### Step 1: Create a GitHub repository

1. Go to GitHub and create a new repository.
2. Put all files from this project into the repository.
3. Commit and push:

```bash
git init
git add .
git commit -m "Initial Crunchyroll anime info service"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git push -u origin main
```

### Step 2: Create the Render web service

1. Log in to Render.
2. Click **New +**.
3. Choose **Blueprint** if you want Render to read `render.yaml`, then select your GitHub repo.
4. Or choose **Web Service**, select the repo, and use:
   - Build command: `pip install -r requirements.txt`
   - Start command: `.venv/bin/gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120`
5. Select the free plan if desired.
6. Render will set `PORT` automatically. `gunicorn.conf.py` makes Gunicorn bind to `0.0.0.0:$PORT`.
7. Click **Deploy**.

### Step 3: Optional environment settings

In Render's **Environment** tab you can set:

- `CACHE_TTL_SECONDS=2700`
- `CR_LOCALE=en-US`
- `CR_BASIC_TOKENS=token1,token2,token3` if Crunchyroll blocks the default tokens and you have fresh working anonymous Basic tokens

### Step 4: Test after deploy

Visit:

```text
https://YOUR-SERVICE.onrender.com/health
https://YOUR-SERVICE.onrender.com/anime/One%20Piece
```

If Render's free tier sleeps, the first request after sleep may take longer. The app survives restarts because it does not rely on local disk or an in-memory login session; it simply re-authenticates anonymously when needed.

## Notes for future bot frontends

A Discord, Telegram, or other bot can call these HTTP APIs directly. The report-building logic is in `services.py`, and the HTTP responses are plain JSON, so a bot does not need to know Crunchyroll internals.

## Important Crunchyroll note

Crunchyroll has no official public API. This project uses undocumented anonymous endpoints that are community-known and can change or be blocked. When Crunchyroll returns errors or bot protection, the service returns a clear JSON error instead of fake data.


## Render troubleshooting: `gunicorn: command not found`

If Render logs show `bash: line 1: gunicorn: command not found`, the Python package installed correctly but Render did not put the console script on `PATH`. Use this Start Command instead:

```bash
python -m gunicorn app:app
```

Use this Build Command to make sure `pip` belongs to the same Python runtime:

```bash
python -m pip install --upgrade pip && python -m pip install -r requirements.txt
```

Then click **Manual Deploy → Clear build cache & deploy**.


## Render troubleshooting: `python: command not found`

Some Render images expose Python as `python3`, not `python`, and sometimes the Gunicorn console script is installed but not added to `PATH`. This repo includes `build.sh` and `start.sh` to handle those cases.

Use these commands in Render:

```bash
Build Command: pip install -r requirements.txt
Start Command: .venv/bin/gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120
```

Then click **Manual Deploy → Clear build cache & deploy**.
