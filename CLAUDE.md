# CLAUDE.md: Swave

Personal playlist builder. One loop: **seed → recommend → refine → save**.
Single user (the owner), runs locally. **The GitHub repo is PUBLIC**: read "Public repo rules" before every commit.

## Architecture

```
web/ (Next.js App Router + Tailwind)  ──HTTP──▶  api/ (FastAPI, Python 3.12)  ──▶  Postgres 16 (docker compose)
                                                     │
                                                     ├─▶ Deezer public API   (30s previews, cached in DB)
                                                     └─▶ Spotify Web API     (OAuth PKCE, create playlist + add items)

Offline pipeline (api/swave/pipeline/):  MPD zip/slices ─▶ ingest ─▶ build-index ─▶ eval
```

- **Postgres is the only store.** Tables:
  - `tracks(track_uri PK, track_id, name, artist, album, playlist_count)`: pruned to `playlist_count >= 5`; `track_id` is a dense int (0 = most popular) used by the matrix and `neighbours`
  - `playlist_tracks(pid, track_uri)`
  - `eval_holdout(pid)`: the 1,000 held-out playlists
  - `neighbours(track_id, neighbour_id, sim, co_count)`: top-200 per track, precomputed (ints keep this ~130M-row table small)
  - `index_meta(method, k, min_co, built_at, git)`: how `neighbours` was built
  - `events(id, ts, action, track_uri, position, slider, session_id)`: actions: seeded, previewed, kept, removed, more_like_this, saved
  - `previews(track_uri PK, deezer_id, matched, checked_at)`: Deezer match cache (misses cached too)
  - `spotify_auth(id=1, refresh_token, expires_at)`: single row, owner's token
- **Recommender v1** = item-item co-occurrence. `sim` is cosine `c_ij / sqrt(n_i·n_j)` or PMI (whichever evaluates better, recorded in `eval/results.csv`).
  Score for seed set S: `Σ_{s∈S} sim(s, t) / playlist_count(t)^alpha`, seeds excluded. `alpha` comes from the familiar↔adventurous slider (0 = no popularity penalty).
- **Held-out eval set** (`eval_holdout`, made by ingest): 1,000 playlists with ≥ 10 tracks, chosen by `md5(pid)` order. They are excluded from the training matrix and `neighbours`, so eval never leaks.
- **Eval doesn't need the index**: it computes neighbours for its ~5k seed tracks on the fly with the same `cooc.neighbours()` function (same top-k truncation) as build-index, so comparing methods/k/alpha takes minutes, not hours. The training matrix is cached in `data/cache/` (deleted by ingest).

## Layout

```
api/                 Python package + FastAPI app
  swave/
    config.py        env/.env, paths, DB connection
    cooc.py          playlist×track matrix, top-k neighbours (cosine / PPMI)
    pipeline/        ingest.py, build_index.py, evaluate.py, feedback.py  (CLI entry points)
    recommend.py     scoring a seed set (shared by API and eval)
    deezer.py        preview lookup + cache
    spotify.py       PKCE OAuth + playlist save
    main.py          FastAPI routes: /search /recommend /events /auth/* /save
  tests/             pytest, synthetic fixtures only
web/                 Next.js app (2 screens: Seed, Results)
eval/results.csv     metrics history (no track data)
data/raw/            dataset (gitignored, never committed)
docker-compose.yml   postgres:16
Makefile
```

## Commands

```
docker compose up -d     # Postgres
make ingest              # dataset → tracks, playlist_tracks (prunes < 5 playlists)
make build-index         # sparse matrix → neighbours table (METHOD=cosine|pmi)
make eval                # hit-rate@50, recall@50 on held-out playlists → eval/results.csv
make dev                 # API on :8000 + web on :3000
make test                # pytest + web typecheck/lint
make feedback            # weekly keep rate, most-removed tracks/artists
```

## Conventions

- Keep it simple: plain functions and SQL. No ORM, no repository layers, no abstractions for features we don't have.
- Python: type hints, `psycopg` 3, bulk loads via `COPY`, scipy.sparse for matrices. Config from env vars only (`os.environ`), loaded from `.env`.
- TypeScript strict; Tailwind; dark mode by default; no state library unless one is really needed.
- API errors: proper HTTP status + `{"detail": "..."}`. Validate inputs with Pydantic.
- Every algorithm change → `make eval` → a new row in `eval/results.csv`, committed with the change.
- Milestone loop: run tests + eval → commit → 5-line summary (works / doesn't / eval number) → wait for go-ahead.
- Ask the owner before hard-to-undo decisions (schema changes that need re-ingest, deleting data, new external services).

## Spotify (dev mode, rules since Feb 2026)

- Auth: Authorization Code + **PKCE** (no client secret). Redirect URI must be a loopback IP (`http://127.0.0.1:8000/auth/callback`), not `localhost`.
- Create playlist: `POST /me/playlists` (`POST /users/{id}/playlists` is removed). Add tracks: `POST /playlists/{id}/items` (`/tracks` is removed).
- Reading a pasted playlist (`GET /playlists/{id}/items`) works only for playlists the owner owns or collaborates on.
- Search max `limit` is 10; track `popularity` is removed (we use `playlist_count` instead). App owner needs Premium.
- If save fails in dev mode → fallback: export `exports/<name>.csv` (gitignored) for a playlist-transfer tool.
- MPD `track_uri`s are real Spotify URIs, so they go straight into `/items`.

## Deezer previews

`GET https://api.deezer.com/search?q=artist:"…" track:"…"` → cache `deezer_id`. Deezer preview URLs are **signed and expire**, so cache the id and fetch a fresh URL from `/track/{id}` on demand. Misses are cached (`matched=false`) and re-tried after 30 days. Never block results on Deezer.

## Data licence

The **Spotify Million Playlist Dataset** (from AIcrowd) is licensed for **non-commercial research use only and may not be redistributed**. The raw files and **anything derived from them** (DB tables, matrices, neighbour lists, dumps, exported playlists, track-level samples) must never leave this machine via git.

## Public repo rules (strict)

1. `.gitignore` covers: `data/`, `*.parquet`, `*.npz`, `*.npy`, `*.sql` dumps, `exports/`, `.env*` (except `.env.example`), `node_modules`, `__pycache__`, `.venv`, the dataset zip.
2. Never commit dataset files, derived artefacts or anything containing track-level data. Commit the scripts that rebuild them.
3. Secrets (Spotify client ID, DB password, tokens) only from env vars. `.env.example` has blank values. No secrets in code, tests, logs or fixtures. Never log tokens.
4. Tests use **synthetic** fixtures only (made-up playlists and IDs like `spotify:track:fake001`), never dataset samples.
5. pre-commit: gitleaks + reject staged files > 5 MB.
6. **Before every commit:** run `git status` and confirm nothing from `data/`, `exports/` or `.env` is staged. `eval/results.csv` holds metrics only (date, git short hash, method, params, scores).
