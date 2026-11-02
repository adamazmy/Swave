# CLAUDE.md: Swave

Personal playlist builder. One loop: **seed → recommend → refine → save**.
Single user (the owner), runs locally. **The GitHub repo is PUBLIC**: read "Public repo rules" before every commit.

## Architecture

```
web/ (Next.js App Router + Tailwind)  ──HTTP──▶  api/ (FastAPI, Python 3.12)  ──▶  Postgres 16 (docker compose)
                                                     │
                                                     ├─▶ Deezer public API   (30s previews, cached in DB)
                                                     └─▶ Spotify Web API     (OAuth PKCE, create playlist + add items)

Offline pipeline (api/swave/pipeline/):  MPD zip/slices ─▶ ingest ─▶ build-index ─┐
                                          ListenBrainz dump ─▶ lb-ingest ─▶ lb-build ─┴▶ eval
```

- **Postgres is the only store.** Tables:
  - `tracks(track_uri PK, track_id, name, artist, album, playlist_count)`: pruned to `playlist_count >= 5`; `track_id` is a dense int (0 = most popular) used by the matrix and `neighbours`
  - `playlist_tracks(pid, track_uri)`
  - `eval_holdout(pid)`: the 1,000 held-out playlists
  - `neighbours(track_id, neighbour_id, sim, co_count)`: top-200 per track, precomputed (ints keep this ~130M-row table small)
  - `index_meta(method, k, min_co, built_at, git)`: how `neighbours` was built
  - `events(id, ts, action, track_uri, position, slider, session_id)`: actions: shown, seeded, previewed, kept, removed, more_like_this, saved (`shown` = keep-rate denominator)
  - `previews(track_uri PK, deezer_id, matched, checked_at)`: Deezer match cache (misses cached too)
  - `spotify_auth(id=1, refresh_token, expires_at)`: single row, owner's token
  - `lb_tracks(track_id, song, name, artist, recording_mbid, listeners, pop_est)`: ListenBrainz songs NOT in the MPD; `track_id`s continue after MPD's (594,421+), `song` = `matching.song_hash` of the normalised title+artist; `pop_est` = listeners mapped onto the playlist_count scale
  - `neighbours_lb(track_id, neighbour_id, sim, co_count)`: top-100 per track from listening sessions (ids = MPD or lb_tracks); `lb_holdout(sess, pos, track_id)`, `lb_meta(built_at, git, stats)`
  - `lb_spotify(song PK, track_uri NULL, checked_at)`: app-owned cache of resolved Spotify URIs for LB songs (misses cached, retried after 30 days), survives `lb-build`
  - view `all_tracks` = `tracks` ∪ resolved `lb_tracks` (album ''): use it for anything keyed by `track_uri`. `lb-build` drops lb_tracks with CASCADE and recreates the view (`lb.VIEW`); the API also creates the LB tables/view at startup (`lb.SCHEMA`)
- **Recommender v1** = item-item co-occurrence. `sim` is cosine `c_ij / sqrt(n_i·n_j)` or PMI (whichever evaluates better, recorded in `eval/results.csv`).
  Score for seed set S: `Σ_{s∈S} sim(s, t) / playlist_count(t)^alpha`, seeds excluded. The familiar↔adventurous slider (`adventure` 0..1) does **not** map to a fixed alpha: `recommend.alpha_for` binary-searches alpha ∈ [0, 2] so the top-n's median popularity moves that fraction of the way (log scale) from alpha 0 to alpha 2 for *this* seed set. A fixed mapping felt dead for popular seeds and collapsed mid-popularity ones (measured on real sessions, 2026-11-01). Replacements reuse the returned `alpha`. Eval still uses raw `alpha`.
  Known limit: popular seeds can't get very adventurous: their top-200 cosine neighbours are themselves popular (pool saturates ~alpha 1.5). Deeper/alternative neighbour lists = recommender v2.
- **v2 scoring is pluggable** (`scorers.py` + `recommend.py`): a *scorer* turns seeds into candidates with a raw score, its `top` (score = raw/top, normalised 0–1) and reason data; it knows nothing about popularity or exclusions. The *ranker* (`recommend.combine` → `alpha_for` → `penalise`) sums `w_i · score_i` with weights from **`scorers.json`** (repo root, or `$SWAVE_SCORERS`; read once at API startup, so restart the API after editing), divides by `playlist_count^alpha`, drops seeds/excluded. The sum is rescaled so the first scorer keeps its raw values: with one scorer the ranking is bit-for-bit v1 (dividing by `top` merged scores 1 ulp apart into ties and swapped 2/5,000 eval lists). Reason line = the scorers giving ≥ half of the top contribution (max 2), joined by ` · `. Scorers: `cooccurrence_mpd` (the v1 index) and `cooccurrence_lb` (ListenBrainz sessions); default weights 0.5 / 0.5 (owner, 2026-11-02). `make eval SCORERS=path.json` logs the weights in the `scorers` column.
- **ListenBrainz scorer** (`cooccurrence_lb`, reason "N listening sessions"): `make lb-ingest` range-reads 5 columns of the remote Spark dump (an uncompressed .tar of time-ordered Parquet files; `lb.walk_back` lists it from the end) for listens since `LB_SINCE`, per listen (user, ts, song hash) in 64 user buckets under `data/lb/`. `make lb-build`: sessions (30-min gap, repeats collapsed, < 3 songs dropped, > 200 cut), vocabulary ≥ 20 listeners (1.5 M songs; ≥ 10 = 2.7 M was too big), match to MPD (exact song key, then title prefix ≥ 60%), cosine top-100. RAM: the session matrix is capped at 200 M entries (`--max-nnz`, hash sample of whole sessions: 39% of 32.9 M); the first uncapped build was killed by memory pressure. LB-only tracks have no Spotify id: `resolve.py` resolves them when shown (labs `spotify-id-from-mbid`, batched, then Spotify search ≤ 10/request) and `/recommend` ranks `RESOLVE_SPARE` extra rows to replace unresolvable ones. Playlist import matches LB-only songs too and stores the playlist's own URI in `lb_spotify`. Sessions are often album/artist runs, so LB alone over-recommends the seed's artist.
- **Held-out eval set** (`eval_holdout`, made by ingest): 1,000 playlists with ≥ 10 tracks, chosen by `md5(pid)` order. They are excluded from the training matrix and `neighbours`, so eval never leaks.
- **Eval doesn't need the index**: it computes neighbours for its ~5k seed tracks on the fly with the same `cooc.neighbours()` function (same top-k truncation) as build-index, so comparing methods/k/alpha takes minutes, not hours. The training matrix is cached in `data/cache/` (deleted by ingest).

## Layout

```
api/                 Python package + FastAPI app
  swave/
    config.py        env/.env, paths, DB connection
    cooc.py          playlist×track matrix, top-k neighbours (cosine / PPMI)
    pipeline/        ingest.py, build_index.py, evaluate.py, feedback.py,
                     lb_ingest.py, lb_build.py, lb_report.py  (CLI entry points)
    scorers.py       Scorer interface + co-occurrence scorer (neighbour lookup from DB or memory)
    lb.py            ListenBrainz: remote tar/Parquet range reads, sessions, LB tables + all_tracks view
    resolve.py       lazy Spotify URIs for ListenBrainz-only tracks (labs lookup, Spotify search)
    recommend.py     ranker: weighted sum of scorers, popularity penalty, slider alpha, reasons
    deezer.py        preview lookup + cache
    spotify.py       PKCE OAuth + playlist save
    main.py          FastAPI routes: /search /recommend /events /auth/* /save
  tests/             pytest, synthetic fixtures only
web/                 Next.js 16 app (2 screens: Seed, Results). Read web/AGENTS.md: this Next.js version
                     differs from older ones, check node_modules/next/dist/docs before using an API
  app/               routes: / (seed), /results (client-only, inside <Suspense> for useSearchParams)
                     cacheComponents keeps visited pages alive in a hidden <Activity>: state is NOT
                     reset on navigation and effects re-run when a page is shown again. Results is
                     keyed by a `run` URL param (new per vibe) and its first-load effect is a no-op
                     when the list already exists. Read web/node_modules/next/dist/docs/01-app/02-guides/preserving-ui-state.md
  components/        seed-screen, results-screen, track-row, save-panel, icons, night (v3 theme pieces)
  lib/               api.ts (typed client), playlist.ts (pure list logic + vitest), session.ts
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
make lb-ingest           # ListenBrainz listens since LB_SINCE → data/lb/ (~2 h, ~31 GB)
make lb-build            # sessions, matching, lb_tracks, neighbours_lb, lb_holdout (~20 min from cache)
make lb-report           # match rate + post-2017 share of new tracks (MusicBrainz API, 1 req/s)
make eval                # hit-rate@50, recall@50 → eval/results.csv (SCORERS=cfg.json HOLDOUT=mpd|lb);
                         # evaluate.py takes several --scorers configs (eval/scorers/*.json)
make dev                 # API on :8000 + web on :3000
make test                # pytest + web typecheck/lint
make feedback            # weekly keep rate, most-removed tracks/artists (WEEKS=8); counts once
                         # per (session, track), kept = kept OR more_like_this
```

## Conventions

- Keep it simple: plain functions and SQL. No ORM, no repository layers, no abstractions for features we don't have.
- Python: type hints, `psycopg` 3, bulk loads via `COPY`, scipy.sparse for matrices. Config from env vars only (`os.environ`), loaded from `.env`.
- TypeScript strict; Tailwind; no state library unless one is really needed. Light + dark themes: colours are CSS variables behind the Tailwind tokens (`app/globals.css`, `[data-theme="light"]` swaps them); `data-theme` is set before paint from localStorage `swave-theme`, else the system setting. Use the tokens (bg-ink, text-muted…), never raw hex or white/zinc classes.
- API errors: proper HTTP status + `{"detail": "..."}`. Validate inputs with Pydantic.
- Every algorithm change → `make eval` → a new row in `eval/results.csv`, committed with the change.
- Milestone loop: run tests + eval → commit → 5-line summary (works / doesn't / eval number) → wait for go-ahead.
- Ask the owner before hard-to-undo decisions (schema changes that need re-ingest, deleting data, new external services).

## Spotify (dev mode, rules since Feb 2026)

- Auth: Authorization Code + **PKCE** (no client secret). Redirect URI must be a loopback IP (`http://127.0.0.1:8000/auth/callback`), not `localhost`.
- Create playlist: `POST /me/playlists` (`POST /users/{id}/playlists` is removed). Add tracks: `POST /playlists/{id}/items` (`/tracks` is removed).
- Reading a pasted playlist (`GET /playlists/{id}/items`) works only for playlists the owner owns or collaborates on.
- Search max `limit` is 10; track `popularity` is removed (we use `playlist_count` instead). App owner needs Premium.
- **Public/private can't be set**: Spotify ignores `public: false` on create AND on `PUT /playlists/{id}` for this app (verified 2026-11-01, known community issue). The UI has no public toggle and never says "private"; it tells the user to use Spotify → ⋯ → Make private.
- **Covers**: `PUT /playlists/{id}/images`, base64 JPEG ≤ 256 KB, scope `ugc-image-upload` (verified working). Logins from before that scope report `can_upload_cover: false` → UI asks to reconnect. Cover is uploaded after the playlist is saved; a cover failure returns `cover_error` but keeps the playlist.
- **Add to a pasted playlist**: `POST /playlists/{id}/add` appends only songs not already in it, by id *or* by normalised title+artist (reads the playlist first); never removes or reorders. The source playlist is remembered per vibe `run` (localStorage `swave-source`).
- **Importing a playlist** (`api/swave/matching.py`): today's Spotify ids often differ from the 2018 MPD ids (≈ half of popular songs). Match exact id → artist + normalised title (`deezer.norm`) → artist + title prefix ≥ 60%; most popular version wins. Needs index `tracks_artist` on `lower(artist)` (created by ingest).

## Client-side persistence (web)

localStorage (this browser only): `swave-seed-draft` (Seed screen), `swave-results` (the vibe: list, kept, removed, name, cover, saved playlist…), `swave-source`. "Start over" (`clearAll`) wipes all three. Seed and Results screens are browser-only (`ssr: false`) because they read storage in their initial state; both are keyed by a URL param (`fresh` / `run`) to defeat Next's kept-alive <Activity> pages.
- Save **verified working in dev mode** (2026-11-01). Fallback kept: client-side CSV export (Track name, Artist name, Album, Spotify URI), downloaded by the browser, never written into the repo.
- Code: `api/swave/spotify.py`. Tokens: `spotify_auth` (one row, local DB only, never logged). OAuth `state` → verifier is kept in memory (10 min), so restarting the API mid-login means logging in again.
- MPD `track_uri`s are real Spotify URIs, so they go straight into `/items`.

## Deezer previews

Deezer's advanced syntax (`q=artist:"…" track:"…"`) returns **0 results** as of 2026-11, so search plain `q="<artist> <title>"` and pick the candidate ourselves (`deezer.pick`: same artist; exact title > title without (Live)/remaster suffix > prefix match; otherwise no match) → cache `deezer_id`. Deezer preview URLs are **signed and expire**, so cache the id and fetch a fresh URL from `/track/{id}` on demand. Misses are cached (`matched=false`) and re-tried after 30 days. Never block results on Deezer.

## Data licence

The **Spotify Million Playlist Dataset** (from AIcrowd) is licensed for **non-commercial research use only and may not be redistributed**. The raw files and **anything derived from them** (DB tables, matrices, neighbour lists, dumps, exported playlists, track-level samples) must never leave this machine via git.

**ListenBrainz** data is **CC0** (MetaBrainz Foundation; attribution in the README). It is still kept out of git like the MPD data: dump files, `data/lb/` and everything derived stay local (the derived tables also contain MPD-linked ids).

## Public repo rules (strict)

1. `.gitignore` covers: `data/`, `*.parquet`, `*.npz`, `*.npy`, `*.sql` dumps, `exports/`, `.env*` (except `.env.example`), `node_modules`, `__pycache__`, `.venv`, the dataset zip.
2. Never commit dataset files, derived artefacts or anything containing track-level data. Commit the scripts that rebuild them.
3. Secrets (Spotify client ID, DB password, tokens) only from env vars. `.env.example` has blank values. No secrets in code, tests, logs or fixtures. Never log tokens.
4. Tests use **synthetic** fixtures only (made-up playlists and IDs like `spotify:track:fake001`), never dataset samples.
5. pre-commit: gitleaks + reject staged files > 5 MB.
6. **Before every commit:** run `git status` and confirm nothing from `data/`, `exports/` or `.env` is staged. `eval/results.csv` holds metrics only (date, git short hash, method, params, scores).
