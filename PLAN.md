# PLAN.md: Swave MVP

Loop: seed → recommend → refine → save. Each milestone ends with: tests + `make eval` → commit → 5-line summary → wait for go-ahead.

## Environment facts (checked 2026-11-01)

- Machine: i7-12700H (20 threads), 16 GB RAM, ~105 GB free on C:. Docker 29.3 installed (Desktop must be running). Python 3.12.2 via `py -3.12`. GNU make: to install (`winget install ezwinports.make`).
- Dataset: `spotify_million_playlist_dataset.zip` (5.8 GB zipped, **33.5 GB unzipped**, 1,000 slices). Owner chose **all 1,000 slices**.
- Repo currently holds the Vite + React + Supabase starter. It will be **replaced** by `api/` + `web/` (Supabase is not used by this spec; `.env.local` with Supabase keys stays local and ignored).

## Size estimates (full MPD)

| Thing | Rough size |
|---|---|
| playlists / track occurrences | 1M / ~66M rows |
| unique tracks → after pruning (< 5 playlists) | ~2.26M → ~0.6–0.7M |
| `playlist_tracks` in Postgres (with index) | ~4–6 GB |
| `neighbours` (≈0.65M × 200 = ~130M rows) | **~8–12 GB**, build ~1–3 h |
| Ingest | ~20–40 min |

## M1 result (2026-11-01): done

- Ingest 7.2 min: 1,000,000 playlists, 2,262,292 tracks → **594,421** after pruning, 62.8M `playlist_tracks` rows.
- Build-index 6.5 min (cosine, k=200, min_co=2): 73.9M neighbour rows (many tracks have < 200 neighbours with co-count ≥ 2), 3.6 GB. DB total 8.8 GB.
- Eval baseline (cosine, k=200, alpha=0): **hit-rate@50 0.908, recall@50 0.196**. PMI best 0.774 / 0.104 (min_co=50). k=100 costs ~0.7 pts of recall. Any alpha > 0 lowers offline scores (expected: the hidden tracks are popular-biased); alpha stays a UI choice.

## M1: Ingest + index + eval baseline (+ repo hygiene)

- Repo hygiene first: remove Vite/Supabase starter, `.gitignore`, `.env.example`, MIT `LICENSE`, README (setup, dataset note, eval explanation, screenshot placeholder), pre-commit (gitleaks + 5 MB staged-file check), Makefile, `docker-compose.yml` (postgres:16, named volume).
- `make ingest`: read `mpd.slice.*.json` one slice at a time (from `data/raw/` **or directly from the zip**), `COPY` into staging tables, compute `playlist_count`, prune < 5, add indexes + `pg_trgm` index for search.
- `make build-index`: `scipy.sparse` playlist×track CSR (held-out pids excluded), co-occurrence `XᵀX` in track chunks (bounded RAM), cosine and PMI, top-200 per track → `neighbours` via `COPY`.
- `make eval`: 1,000 held-out playlists (≥ 10 tracks, `md5(pid)` order, made by ingest), 5 random seeds per playlist (RNG seeded by pid), hit-rate@50 + recall@50 on the rest. Neighbours computed on the fly with the build-index function, so no index build is needed to compare configs. Prints one line, appends to `eval/results.csv`. Run for cosine and PMI (and a small alpha sweep); keep the winner.
- Tests: pytest on a synthetic 6-playlist fixture (scoring, pruning, metrics).

## M2 result (2026-11-01): done

- `/search` ~6 ms, `/recommend` (50 results) ~8 ms on the full index; `/events`, `/preview` (Deezer, cached), `/health`.
- `events` and `previews` are created by the API at startup and survive re-ingest (keyed by `track_uri`).
- 30 tests (13 pipeline + 17 API on a throwaway `swave_test` DB, Deezer mocked).

## M2: FastAPI

- `GET /search?q=`: trigram search on name/artist, ranked by `playlist_count`, limit 20.
- `POST /recommend {seeds[], alpha, exclude[], n=50}`: scores + reason (`seed_hits`, `co_count`), e.g. "appears with 3 of your seeds in 412 shared playlists". The co-count is summed per seed, so it's an upper bound on distinct playlists; the reason text says "shared playlists".
- `POST /events`: append-only log. `GET /preview/{track_uri}`: Deezer lookup + cache.
- "Remove → replace" = client asks `/recommend` with removed + shown tracks in `exclude`, takes the next one.
- Tests: endpoint tests against a throwaway test DB seeded from synthetic fixtures.

## M3 result (2026-11-01): done

- Next.js 16.4 (App Router, cacheComponents) + Tailwind 4, dark. Seed screen (debounced search, 1–5 seeds) and Results (40 rows, preview, keep / more-like-this / remove-and-replace, slider re-rank with kept songs pinned on top, URL keeps seeds + slider).
- Added event `shown` (+ `POST /events/batch`): needed for keep rate = kept ÷ shown. `/recommend` now also returns seed metadata.
- "More like this" also keeps the song (it's excluded from results once it's a seed, so otherwise it would vanish).
- Slider maps linearly to alpha 0–1. Offline eval says alpha 0.5 is already very adventurous; tune by feel.

## M3: Frontend (Next.js + Tailwind, dark)

- Screen 1 **Seed**: search box, pick 1–5 seeds, or paste a Spotify playlist link (only works after M4 and only for own/collaborative playlists).
- Screen 2 **Results**: 30–50 rows: title/artist, preview player, reason, keep / remove / more-like-this, familiar↔adventurous slider (debounced re-rank), "Save to Spotify".
- Every action posts to `/events`.

## M4 result (2026-11-01): done, verified against real Spotify

- **Dev-mode save works**: real test with the owner's account created a private playlist with the 2 kept songs in order (read back via `/import`). No CSV fallback needed; CSV export is still offered as a secondary button.
- PKCE login round-trip keeps the Results state (sessionStorage per tab; Results is client-only). Tokens in `spotify_auth`, committed immediately so an error can't roll back a rotated refresh token.
- Paste-a-playlist on the Seed screen (own/collaborative playlists only; 403 explained).
- Found in browser testing: the slider effect re-ranked the list on open in dev StrictMode; fixed by driving it from onChange.

## Owner test round 1 (2026-11-01): fixed

1. *"To change the vibe I need to refresh"*: Next's <Activity> reused the hidden Results page. Fixed with a per-vibe `run` key; the page also no longer reloads when shown again (that had logged duplicate `shown` events: the owner's session has 674).
2. *"Slider stays the same until it gets extreme"*: measured on the owner's 4 seed sets; fixed alpha behaved very differently per set. Now `adventure` → per-set alpha (`alpha_for`): first step swaps ~7/40 songs on popular vibes (was 1–2), mid vibes no longer collapse at 0.5.
3. *"Every save creates a new playlist"*: saves now update the vibe's playlist (rename + replace songs); "Save as new playlist" is explicit. Double-click verified: one playlist, songs replaced.

Follow-ups (not now): recommender v2 deeper candidate pool so popular vibes can get more adventurous.

## M4: Spotify OAuth + save

- PKCE login → refresh token in `spotify_auth`. Save = `POST /me/playlists` + `POST /playlists/{id}/items` (batches of 100).
- First task: a real test save in dev mode. If it fails → CSV export to `exports/` (gitignored) and tell the owner.
- Paste-a-playlist seeding via `GET /playlists/{id}/items`; tracks not in our index are skipped with a note.

## M5 result (2026-11-01): done

- `make feedback [WEEKS=8]`: per ISO week (UTC): sessions, shown, kept, keep rate, removed, sessions that saved; then most-removed tracks and artists. Terminal only, reads `events`.
- Counting: once per (session, track) for every action (re-ranks re-log `shown`: the owner's first session had 954 shown rows for 367 distinct songs); kept = ✓ or ＋ more-like-this (the owner's 3 saved songs all came from ＋).
- First real report: 6 sessions, 367 shown, 3 kept (0.8%), 5 removed; most-removed artist Kesha (2×).

## M5: Feedback scripts

- `make feedback`: weekly keep rate (kept ÷ shown) and the most-removed tracks/artists, printed to the terminal only.

## Decisions (owner, 2026-11-01)

1. Spotify Premium: **yes**, so dev-mode save is possible (verified in M4).
2. Dataset: **read straight from the zip** in `data/raw/` (no 33.5 GB unzip).
3. Neighbours: **build top-200**, and evaluate top-100 too.
4. Vite + Supabase starter: **deleted** (in git history).
