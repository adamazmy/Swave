# Swave

A personal playlist builder. Pick 1–5 songs that capture a vibe, get 30–50 recommendations with a reason for each, keep / remove / "more like this" until it's right, then save it to Spotify.

![Swave screenshot](docs/screenshot.png) <!-- placeholder: screenshot comes with the frontend (M3) -->

> **Dataset not included.** Swave is built on the [Spotify Million Playlist Dataset](https://www.aicrowd.com/challenges/spotify-million-playlist-dataset-challenge), which Spotify licenses for **non-commercial research use only** and which may not be redistributed. This repository contains only code. You must download the dataset yourself, and nothing derived from it (database tables, matrices, neighbour lists, exports) is ever committed here.

## How it works

```
web/ (Next.js + Tailwind)  ──▶  api/ (FastAPI)  ──▶  Postgres 16 (docker compose)
                                    ├─▶ Deezer API   (30-second previews)
                                    └─▶ Spotify API  (OAuth PKCE, save playlist)

offline:  MPD slices ─▶ make ingest ─▶ make build-index ─▶ make eval
```

- **Ingest** streams the dataset slices (straight from the zip or from unzipped JSON) into Postgres: `tracks` and `playlist_tracks`. Tracks in fewer than 5 playlists are pruned.
- **Recommender v1: item-item co-occurrence.** A sparse playlist × track matrix gives, for every pair of tracks, how many playlists contain both. That count is normalised by popularity (cosine, or positive PMI), and the top-200 neighbours of each track are stored in a `neighbours` table so requests are fast.
- **Scoring** a seed set: `Σ over seeds of sim(seed, track) / playlist_count(track)^alpha`. `alpha` is the familiar ↔ adventurous slider: 0 = no penalty for popular songs.

> Status: M4 (pipeline, evaluation, API, frontend, Spotify save). Feedback scripts next; see [PLAN.md](PLAN.md).

## Using it

`make dev` starts the API (:8000) and the web app on <http://localhost:3000>.

1. **Seed**: search and pick 1–5 songs, then *Find songs*.
   Or paste a link to one of **your own** Spotify playlists (Spotify dev mode only allows playlists you own or collaborate on): its first 5 songs that are in the dataset become the seeds.
2. **Results**: 40 songs with a 30-second preview and a reason each. ✓ keeps a song, ＋ "more like this" adds it as a seed (and keeps it), ✕ removes it and pulls in the next best. The **Familiar ↔ Adventurous** slider sets the popularity penalty (alpha 0 → 1); kept songs stay on top when the list re-ranks. Your kept/removed songs survive a reload (stored for the browser tab).
3. **Save**: the bar at the bottom saves the kept songs as a **private** Spotify playlist (*Connect Spotify* first), or exports a CSV (Track name, Artist name, Album, Spotify URI) for playlist-transfer tools.

## API

`make api` serves FastAPI on <http://127.0.0.1:8000> (interactive docs at `/docs`).

| Endpoint | What it does |
|---|---|
| `GET /search?q=&limit=20` | Tracks whose title/artist contain every word, most popular first |
| `POST /recommend` `{seeds[1–10], alpha 0–1, exclude[], n≤100}` | Ranked tracks with `seed_hits`, `co_count` and a one-line `reason`. Remove → replace = call again with shown/removed tracks in `exclude` |
| `POST /events` `{action, track_uri?, position?, slider?, session_id?}` | Logs shown / seeded / previewed / kept / removed / more_like_this / saved (`shown` = denominator of the keep rate). `POST /events/batch` takes a list |
| `GET /preview?track_uri=` | 30-second Deezer preview URL (or `null`), match cached in Postgres |
| `GET /health` | How the neighbour index was built |
| `GET /auth/login` → `/auth/callback`, `GET /auth/status`, `POST /auth/logout` | Spotify login (Authorization Code + PKCE, no client secret) |
| `POST /save` `{name, track_uris[≤500]}` | Private playlist via `POST /me/playlists` + `POST /playlists/{id}/items` (batches of 100) |
| `POST /import` `{url}` | Songs of one of your own/collaborative playlists that are in the index |

The reason's "shared playlists" count is summed over seeds, so a playlist containing two seeds counts twice.

## Setup

Requirements: Docker, Python 3.12, GNU make (Windows: `winget install ezwinports.make`), Node 20+ (frontend, from M3).

1. **Get the dataset.** Create a free account on [AIcrowd](https://www.aicrowd.com/challenges/spotify-million-playlist-dataset-challenge), download `spotify_million_playlist_dataset.zip` and put it in `data/raw/`. You can leave it zipped (saves ~33 GB) or unzip the `mpd.slice.*.json` files into `data/raw/`.
2. **Create a Spotify app** at <https://developer.spotify.com/dashboard> (development mode; the owner needs Spotify Premium): tick *Web API*, add redirect URI `http://127.0.0.1:8000/auth/callback` (a loopback IP: Spotify rejects `localhost`), and add your own account under *User Management*. Copy the **Client ID** into `SPOTIFY_CLIENT_ID`; no client secret is needed.
3. **Configure:** `cp .env.example .env` and fill in a Postgres password, `DATABASE_URL` and (later) `SPOTIFY_CLIENT_ID`.
4. **Install:** `make setup` (Python venv, dependencies, pre-commit hooks).

## Commands

| Command | What it does |
|---|---|
| `make db` | Start Postgres (docker compose) |
| `make ingest` | Dataset → `tracks`, `playlist_tracks` (+ held-out eval set). Full dataset: ~20–40 min |
| `make build-index` | Top-K neighbours for every track → `neighbours`. Full dataset: ~1–3 h. `METHOD=cosine\|pmi K=200 MIN_CO=2` |
| `make eval` | Offline evaluation → one line + a row in `eval/results.csv`. `METHOD K MIN_CO ALPHA` |
| `make dev` | API + web app together |
| `make api` / `make web` | Just the API (:8000) / just the web app (:3000) |
| `make test` | Pipeline + API tests (throwaway `swave_test` DB), web typecheck, lint and unit tests. Synthetic data only |

## Evaluation

`make ingest` sets aside 1,000 pseudo-random playlists with at least 10 tracks (`eval_holdout`). They are **excluded** from the co-occurrence matrix, so the evaluation never sees its own answers.

For each held-out playlist, 5 tracks are drawn as seeds (fixed random seed per playlist) and the rest are hidden. The recommender returns 50 tracks:

- **hit-rate@50**: share of playlists where at least one hidden track is in the 50.
- **recall@50**: share of hidden tracks found, averaged over playlists.

Each run prints one summary line and appends a metrics-only row (date, git short hash, method, parameters, scores; no track data) to [`eval/results.csv`](eval/results.csv), so every algorithm change has a number.

## Public repo hygiene

This repo is public. `.gitignore` blocks `data/`, exports, dumps, matrices and `.env`, and pre-commit hooks block secrets (detect-secrets), files over 5 MB, and anything under `data/` or `exports/`. Tests use made-up playlists only. Details in [CLAUDE.md](CLAUDE.md).

## License

Code: [MIT](LICENSE). The dataset is not included and is licensed separately by Spotify.
