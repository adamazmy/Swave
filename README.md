# Swave

A personal playlist builder. Pick 1–5 songs that capture a vibe, get 30–50 recommendations with a reason for each, keep / remove / "more like this" until it's right, then save it to Spotify.

<!-- TODO: add docs/screenshot.png and replace this comment with ![Swave screenshot](docs/screenshot.png) -->

> **Dataset not included.** Swave is built on the [Spotify Million Playlist Dataset](https://www.aicrowd.com/challenges/spotify-million-playlist-dataset-challenge), which Spotify licenses for **non-commercial research use only** and which may not be redistributed. This repository contains only code. You must download the dataset yourself, and nothing derived from it (database tables, matrices, neighbour lists, exports) is ever committed here.

> **ListenBrainz data.** The second recommender uses listening data from [ListenBrainz](https://listenbrainz.org) (© MetaBrainz Foundation, released under [CC0](https://creativecommons.org/publicdomain/zero/1.0/)) and its MusicBrainz → Spotify id lookup (labs.api.listenbrainz.org). Thanks to MetaBrainz and the ListenBrainz community. `make lb-ingest` reads it straight from the public dump; nothing from it is stored in this repository either.

## How it works

```
web/ (Next.js + Tailwind)  ──▶  api/ (FastAPI)  ──▶  Postgres 16 (docker compose)
                                    ├─▶ Deezer + iTunes APIs (30-second previews)
                                    └─▶ Spotify API  (OAuth PKCE, save playlist)

offline:  MPD slices ─▶ make ingest ─▶ make build-index ─┐
          ListenBrainz dump ─▶ make lb-ingest ─▶ make lb-build ─┴▶ make eval
```

- **Ingest** streams the dataset slices (straight from the zip or from unzipped JSON) into Postgres: `tracks` and `playlist_tracks`. Tracks in fewer than 5 playlists are pruned.
- **Recommender v1: item-item co-occurrence.** A sparse playlist × track matrix gives, for every pair of tracks, how many playlists contain both. That count is normalised by popularity (cosine, or positive PMI), and the top-200 neighbours of each track are stored in a `neighbours` table so requests are fast.
- **Recommender v2: pluggable scorers.** Each scorer (MPD playlist co-occurrence, ListenBrainz listening-session co-occurrence) scores candidates 0–1; the ranker adds them with weights from [`scorers.json`](scorers.json), divides by `popularity^alpha` (the familiar ↔ adventurous slider) and explains each pick with its top scorer(s).
- **Spotify or Apple Music.** Spotify users import playlists and save sets with one click. Apple Music users paste songs copied from Apple Music, then take the set back with a copied song list (for transfer tools like TuneMyMusic or SongShift), a CSV, or an Open-in-Apple-Music link per song (via the free iTunes Search API; saving straight to an Apple Music library would need Apple's paid MusicKit).
- **ListenBrainz** (`make lb-ingest`, `make lb-build`): the last 3 years of public listens are read column-by-column from the remote dump (HTTP range requests, ~34 GB of the 249 GB), split into listening sessions (new session after 30 min, repeats collapsed, < 3 songs dropped) and turned into a second co-occurrence index. Songs are linked to the MPD tracks by normalised artist + title; the others (mostly post-2017 music) become new tracks, resolved to Spotify only when shown.

> Status: MVP complete (M1–M5): pipeline, evaluation, API, frontend, Spotify save, feedback report. See [docs/PLAN.md](docs/PLAN.md).

## Feedback loop

Every action in the app (shown, seeded, previewed, kept, removed, more like this, saved) is logged to the local `events` table with time, track, list position and slider value. It never leaves your machine and is never committed. `make feedback` turns it into a weekly report; each song counts once per session, and "more like this" counts as a keep.

## Using it

`make dev` starts the API (:8000) and the web app on <http://localhost:3000>.

1. **Seed**: search and pick 1–5 songs, then *Find songs*.
   Or paste a link to one of **your own** Spotify playlists (Spotify dev mode only allows playlists you own or collaborate on): its first 5 songs that are in the dataset become the seeds.
2. **Results**: 40 songs with a 30-second preview and a reason each. ✓ keeps a song, ＋ "more like this" adds it as a seed (and keeps it), ✕ removes it and pulls in the next best. The **Familiar ↔ Adventurous** slider moves the list's median popularity evenly (log scale) from fully familiar to as adventurous as that vibe allows; the API picks the alpha (0–2) that gets there. Kept songs stay on top when the list re-ranks, and survive a reload (stored for the browser tab).
3. **Save**: the bar at the bottom saves the kept songs as a Spotify playlist (*Connect Spotify* first; *Customise* sets a description and a cover you upload or generate). With a pasted playlist you can also *Add to* it instead: new songs are appended, ones already there are skipped. Note: Spotify currently makes app-created playlists public; switch them to private in the Spotify app. Saving again **updates the same playlist** (name + songs); *Save as new playlist* makes another one. *Export CSV* (Track name, Artist name, Album, Spotify URI) works for playlist-transfer tools.

## API

`make api` serves FastAPI on <http://127.0.0.1:8000> (interactive docs at `/docs`).

| Endpoint | What it does |
|---|---|
| `GET /search?q=&limit=20` | Tracks whose title/artist contain every word, most popular first |
| `POST /recommend` `{seeds[1–10], adventure 0–1 \| alpha 0–2, exclude[], n≤100}` | Ranked tracks with `seed_hits`, `co_count` and a one-line `reason`, plus the `alpha` used. `adventure` (the slider) lets the API pick alpha for this seed set. Remove → replace = call again with that `alpha` and shown/removed tracks in `exclude` |
| `POST /events` `{action, track_uri?, position?, slider?, session_id?}` | Logs shown / seeded / previewed / kept / removed / more_like_this / saved (`shown` = denominator of the keep rate). `POST /events/batch` takes a list |
| `GET /preview?track_uri=&exclude=` | Fresh 30-second preview URL from Deezer or iTunes (or `null` + why); provider ids cached in Postgres, URLs never |
| `POST /previews/resolve` | Pre-resolve previews for up to 50 tracks; says which have one |
| `POST /preview/attempts` | Log one preview click and its outcome (`make preview-report`) |
| `GET /providers` | Music services and what Swave can do with each (`can_search`, `can_read`, `can_write`) |
| `GET /providers/{spotify,apple}/open?track_uri=` | Redirect to the track on that service (or a search there) |
| `POST /import/text` | Seeds from a pasted song list (`Title - Artist` lines; Apple Music copy-paste works) |
| `GET /health` | How the neighbour index was built |
| `GET /auth/login` → `/auth/callback`, `GET /auth/status`, `POST /auth/logout` | Spotify login (Authorization Code + PKCE, no client secret) |
| `POST /save` `{name, track_uris[≤500], playlist_id?}` | New private playlist (`POST /me/playlists` + `POST /playlists/{id}/items`, batches of 100), or with `playlist_id`: rename + replace its songs (`PUT /playlists/{id}`, `PUT /playlists/{id}/items`) |
| `POST /import` `{url}` | Songs of one of your own/collaborative playlists that are in the index |

The reason's "shared playlists" count is summed over seeds, so a playlist containing two seeds counts twice.

## Setup

Requirements: Docker, Python 3.12, GNU make (Windows: `winget install ezwinports.make`), Node 20+ (frontend).

1. **Get the dataset.** Create a free account on [AIcrowd](https://www.aicrowd.com/challenges/spotify-million-playlist-dataset-challenge), download `spotify_million_playlist_dataset.zip` and put it in `data/raw/`. You can leave it zipped (saves ~33 GB) or unzip the `mpd.slice.*.json` files into `data/raw/`.
2. **Create a Spotify app** at <https://developer.spotify.com/dashboard> (development mode; the owner needs Spotify Premium): tick *Web API*, add redirect URI `http://127.0.0.1:8000/auth/callback` (a loopback IP: Spotify rejects `localhost`), and add your own account under *User Management*. Copy the **Client ID** into `SPOTIFY_CLIENT_ID`; no client secret is needed.
3. **Configure:** `cp .env.example .env` and fill in a Postgres password, `DATABASE_URL` and `SPOTIFY_CLIENT_ID`.
4. **Install:** `make setup` (Python venv, dependencies, pre-commit hooks).

## Commands

| Command | What it does |
|---|---|
| `make db` | Start Postgres (docker compose) |
| `make ingest` | Dataset → `tracks`, `playlist_tracks` (+ held-out eval set). Full dataset: ~20–40 min |
| `make build-index` | Top-K neighbours for every track → `neighbours`. Full dataset: ~1–3 h. `METHOD=cosine\|pmi K=200 MIN_CO=2` |
| `make lb-ingest` | ListenBrainz listens since `LB_SINCE` (default 2023-11-01) → compact arrays in `data/lb/` (~1 h download, ~37 GB temporary) |
| `make lb-build` | Sessions, matching, `lb_tracks`, `neighbours_lb`, LB eval hold-out |
| `make eval` | Offline evaluation → one line + a row in `eval/results.csv`. `METHOD K MIN_CO ALPHA SCORERS=cfg.json HOLDOUT=mpd\|lb` |
| `make dev` | API + web app together |
| `make api` / `make web` | Just the API (:8000) / just the web app (:3000) |
| `make feedback` | Weekly keep rate (kept ÷ shown) and most-removed tracks/artists, printed to the terminal. `WEEKS=8` |
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

Previews: 30-second clips from [Deezer](https://www.deezer.com) and the [iTunes Search API](https://performance-partners.apple.com/search-api) (each iTunes preview links back to Apple Music).

Code: [MIT](LICENSE). The MPD dataset is not included and is licensed separately by Spotify. ListenBrainz data is CC0 (MetaBrainz Foundation).
