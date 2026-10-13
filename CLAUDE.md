# Statify — project notes

Local Flask app exploring Spotify data, enriched with MusicBrainz. SQLite (WAL) storage, three processes: web app, MusicBrainz worker, IA worker.

## Commands

- Setup: `python3 -m venv venv && venv/bin/python -m pip install -r requirements-dev.txt`
- Tests: `venv/bin/python -m pytest -q` (run through `python -m` so `app` is importable; some tests need `node`, IA training tests need scikit-learn)
- Lint: `venv/bin/ruff check .` (config in `pyproject.toml`)
- Run locally: `venv/bin/gunicorn --bind 127.0.0.1:5000 --workers 1 --threads 4 main:app`, plus `venv/bin/python -m app.services.musicbrainz_worker` and `venv/bin/python -m app.services.ia_worker` in other terminals. Data goes to `instance/`.
- Docker: `docker compose up --build` (services `statify`, `musicbrainz-worker`, `ia-worker`; data in the `statify_data` volume at `/data`). Logs: `docker compose logs -f musicbrainz-worker`.
- Browser checks (need Playwright + Chromium, `STATIFY_TEST_CHROMIUM` to pick a binary): `python tests/rating_session_browser.py`, `python scripts/ia_ui_smoke.py`; `python scripts/ui_smoke_server.py` serves the UI with a simulated Spotify account.
- CI: `.github/workflows/ci.yml` runs ruff and pytest on every push to `main` and on PRs.

## Layout

- `app/routes/`: HTTP blueprints. `app/services/`: domain logic. `app/storage.py`: SQLite schema and helpers. `app/spotify_auth.py`: OAuth.
- MusicBrainz: `mb_client.py` (rate-limited, cached HTTP client), `recording_matching.py`, `artist_matching.py`, `artist_sites.py`, `spotify_catalogue.py`, `mb_queue.py` (job queue and per-user state), `musicbrainz_worker.py` (worker loop).
- Front-end: vanilla JS, one file per page in `app/static/` (`common.js` is shared and loaded by `base.html`); page scripts are added through each template's `{% block scripts %}`.
- `work/` is a local scratch folder (ignored by Git and Docker). Keep reusable scripts in `scripts/`.

## Conventions

- UI text is French with an English variant (`T('fr', 'en')` in JS, `data-en` attributes in templates). Docs (README, audit) are in French.
- Code, comments, identifiers and commit messages are in English. Commit messages: `type: summary` (feat, fix, chore, style, test, ci, docs, refactor, perf).
- Tests patch module attributes (`monkeypatch.setattr(mb_client, "request", ...)`): import modules, not names, between services so patches reach every caller.
- MusicBrainz allows one request per second across all processes (`mb_rate_limit` table); never bypass `mb_client.request`.
