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

> Status: M1 (data pipeline + evaluation). API, frontend and Spotify save are in progress; see [PLAN.md](PLAN.md).

## Setup

Requirements: Docker, Python 3.12, GNU make (Windows: `winget install ezwinports.make`), Node 20+ (frontend, from M3).

1. **Get the dataset.** Create a free account on [AIcrowd](https://www.aicrowd.com/challenges/spotify-million-playlist-dataset-challenge), download `spotify_million_playlist_dataset.zip` and put it in `data/raw/`. You can leave it zipped (saves ~33 GB) or unzip the `mpd.slice.*.json` files into `data/raw/`.
2. **Create a Spotify app** (needed from M4) at <https://developer.spotify.com/dashboard>. Use development mode, add redirect URI `http://127.0.0.1:8000/auth/callback` and add your own account as a user. Development mode requires the app owner to have Spotify Premium.
3. **Configure:** `cp .env.example .env` and fill in a Postgres password, `DATABASE_URL` and (later) `SPOTIFY_CLIENT_ID`.
4. **Install:** `make setup` (Python venv, dependencies, pre-commit hooks).

## Commands

| Command | What it does |
|---|---|
| `make db` | Start Postgres (docker compose) |
| `make ingest` | Dataset → `tracks`, `playlist_tracks` (+ held-out eval set). Full dataset: ~20–40 min |
| `make build-index` | Top-K neighbours for every track → `neighbours`. Full dataset: ~1–3 h. `METHOD=cosine\|pmi K=200 MIN_CO=2` |
| `make eval` | Offline evaluation → one line + a row in `eval/results.csv`. `METHOD K MIN_CO ALPHA` |
| `make test` | Unit tests (synthetic data only) |

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
