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

## Owner requests round 2 (2026-11-01): done, verified on real Spotify

1. Pasted playlist → pick seeds from its in-dataset songs (scrollable list), not the first 5.
2. "Add to “<pasted playlist>”" save tab: appends new songs only (owner's choice); 2nd click added 0, "(2 already there)".
3. New playlist "Customise": description + cover (upload → 640×640 JPEG under 256 KB, or generated gradient with the name). Cover verified in Spotify after reconnecting for `ugc-image-upload`.
   - Dropped the "public/private" option: Spotify ignores `public: false` for app-created playlists (create and update both verified). Users switch to private in the Spotify app.
4. Found while testing: the source playlist leaked into other vibes of the same tab (Back); now tied to the vibe's run id.

## Owner bug round 3 (2026-11-01)

1. *Pasted playlist only finds some songs*: matching was exact Spotify id only, but the MPD is a 2018 snapshot. Measured: 4 of 8 well-known songs (Mr. Brightside, Smells Like Teen Spirit, Seven Nation Army, Somebody That I Used To Know) have a different Spotify id today. Now `matching.map_items`: exact id → same artist + normalised title → same artist + title prefix (≥ 60%), most popular version wins; the UI shows how many matched loosely and lists the songs not found. "Add to playlist" also skips songs present under another id.
2. *Refresh loses everything*: only Results was saved, per tab. Now the seed draft (seeds, pasted playlist + its songs), the vibe and the source playlist live in localStorage; Seed screen shows "Your last vibe → Continue" and a "Start over" button (also on Results). Same seeds after "Edit seeds" continue the existing vibe instead of wiping its kept songs.

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

---

# Swave v2

v1 is tagged `v1` (370d069). Out of scope for now: personalisation, Spotify playlist picker, branding.

## V2-M1 result (2026-11-02): pluggable scoring, no behaviour change

- `scorers.py`: `Scorer` interface (`candidates(seeds, adventure)` → track, raw, top, reason data; `reason()`), first scorer `cooccurrence_mpd` (neighbour lookup from the DB for the API, from memory for eval).
- `recommend.py`: ranker = weighted sum of normalised scores (weights in `scorers.json`) → popularity penalty from the slider (`alpha_for` unchanged) → seeds/excluded removed. Reason = top contributing scorer(s), ` · `-joined.
- Eval takes `--scorers` (`make eval SCORERS=…`), logs weights in a new `scorers` column (earlier rows back-filled `cooccurrence_mpd=1`).
- **Proof of no change**: (1) 121 captured v1 `/recommend` requests (1–5 seeds, n 1–100, raw alpha + slider 0/0.35/0.7/1, exclude, exclude_songs, unknown seed) replayed: 5,752 rows, 0 differences in order, score (bit-for-bit), alpha, reason, counts. (2) v1 `score()` vs ranker on all 1,000 eval seed sets × alpha {0, 0.1, 0.5, 1, 2}: 0/5,000 lists differ; slider alpha 0/2,000 differ. (3) `make eval`: 0.9080 / 0.1960 (alpha 0), 0.8810 / 0.1852 (alpha 0.1), same as v1.
- Found on the way: normalising by the top score merged 1-ulp-apart scores into ties (2/5,000 lists swapped neighbours). Fixed by ranking on the sum rescaled to the first scorer's raw units.

## V2-M2 plan: ListenBrainz scorer (awaiting owner approval, nothing large downloaded yet)

### What's available (measured 2026-11-02)

- Latest full export `listenbrainz-dump-2723-20261015-000002-full` on data.metabrainz.org (CC0, `COPYING` checked). Two listen formats: JSON `.tar.zst` (241 GB, compressed → must be read end to end) and **Spark `.tar` (249 GB, uncompressed)**: 1,512 Parquet files of ~180 MB / ~2.2M listens each, in time order (`0.parquet` = 2002-10 … `1511.parquet` = 2026-10-14). The server supports HTTP range requests, so single files, and single **columns** inside them, can be read without downloading the dump.
- Parquet columns: `user_id, listened_at, artist_name, recording_name, recording_mbid` (what we need, **38% of the bytes**) + msid, release, credit ids, `created` (not needed).
- Volume: ~1.4M listens/day now, ~1.0M/day in 2024.

| Window | Files | Full size | Bytes we read | Listens |
|---|---|---|---|---|
| 2 years (2024-11 → 2026-10) | ~400 | 67 GB | **~26 GB** | ~0.84 B |
| 3 years (2023-11 → 2026-10) | ~525 | 90 GB | **~34 GB** | ~1.10 B |

Download speed measured ~13 MB/s → 3 years ≈ 45–60 min.

- **Match sample** (one row group, 1.05M listens, 23.7k users, 2026-10-12/13), exact normalised artist + title (`matching.song_key`) against the 594k MPD tracks: 77% of listens carry a `recording_mbid`; 19.5% of listens, 15.7% of distinct songs, **47.5% of songs with ≥ 5 listeners** match an MPD track. Expect ~45–55% of the LB vocabulary to match (more with the existing loose title-prefix rule), the rest are new tracks (mostly post-2017).
- **MBID → Spotify id**: `POST labs.api.listenbrainz.org/spotify-id-from-mbid/json` works (batch, no auth, verified). Spotify search (limit 10) only as fallback.

### Approach

1. **`make lb-ingest`** (`pipeline/lb_ingest.py`): for each Parquet file in the window, range-read only the 5 columns, one row group at a time (~30 MB), nothing raw kept. Per listen keep `(user_id int32, ts int32, song int64)` where `song` = 64-bit hash of `song_key(title, artist)` (so MBID variants and remasters collapse, same as playlist import). Written to `data/lb/listens/` split into 64 buckets by user (~18 GB total, temporary). Per file, the distinct `(song, artist, title, mbid)` rows go to `data/lb/names/` (~10 GB, temporary). Resumable per file.
2. **Sessions** (`pipeline/lb_sessions.py`): per bucket, sort by (user, ts); new session after a 30-min gap; collapse repeats of a song within a session; drop sessions < 3 songs; split sessions > 200 songs into chunks of 200 (bounds the pair count). Count distinct listeners per song (buckets have disjoint users, so counts just add).
3. **Vocabulary**: songs with ≥ 10 distinct listeners (MPD used ≥ 5 playlists; LB users ≈ playlists of one person). I'll report the size before building; if it's above ~1.5M songs I stop and ask.
4. **Matching** to `tracks`: exact `song_key` → same artist + title prefix ≥ 60% (the `matching.py` rules); most popular MPD version wins. Unmatched songs become **new tracks** in a new table `lb_tracks(track_id, name, artist, recording_mbid, listeners, pop_est, track_uri NULL, resolved_at)` with `track_id`s continuing after MPD's (594,421+), so scorers and ranker share one id space. `tracks` is not altered. `pop_est` = LB listener count mapped to the `playlist_count` scale (fit on matched songs), so the slider's popularity penalty works for new tracks.
5. **Index `cooccurrence_lb`**: session × track matrix → same `cooc.neighbours()` (cosine, top-k, min_co 2) → table `neighbours_lb`. k = 200 like MPD, or 100 if the vocabulary is large (≈ 100–200M rows, 5–10 GB). Scorer = `scorers.Cooccurrence("cooccurrence_lb", …, unit="listening sessions")`.
6. **Lazy Spotify resolution**: when an `lb_tracks` row is about to be shown or saved and has no `track_uri`: labs `spotify-id-from-mbid` (batched), else Spotify search (artist + title, `deezer.pick`-style check). Result (or miss) cached in `lb_tracks`; unresolvable tracks are skipped in results. Search on the Seed screen also includes `lb_tracks` (so post-2017 songs can be seeds), resolved the same way.
7. **Eval**: (a) the MPD holdout as now; (b) **new LB holdout**: 1,000 sessions from the last month (≥ 10 in-vocabulary songs, chosen by hash), excluded from the LB matrix; 5 seeds, hit-rate@50 / recall@50 on the rest. Runs: MPD-only, LB-only, and LB/MPD weightings 0.25 / 0.5 / 0.75, on both holdouts → table here + rows in `eval/results.csv` (new `holdout` column). I recommend weights; `scorers.json` stays MPD-only until you approve.
8. **Reports**: match rate (exact / loose / new), number of new tracks, and **how many are post-2017**: estimated from a random 1,000 new tracks via the MusicBrainz API (`first-release-date`, 1 req/s ≈ 17 min).
9. **Repo hygiene**: everything under `data/lb/` (already gitignored via `data/`); add `listenbrainz-*.tar*` / `*.listens` patterns to `.gitignore` and the pre-commit block; README gets "ListenBrainz data © MetaBrainz Foundation, CC0". Tests on synthetic listens only (sessionising, dedupe, gap rule, matching, lazy resolution with mocked HTTP).

### Cost

- Disk: ~30 GB temporary in `data/lb/` (deleted after the build), +5–10 GB in Postgres. 91 GB free now.
- Time: download ~1 h, sessions + matrix + index ~1 h.
- New external services: data.metabrainz.org (download), labs.api.listenbrainz.org (lazy MBID → Spotify), musicbrainz.org API (one-off release-year sample).

## V2-M2 result (2026-11-02)

Data and build:
- `make lb-ingest`: 551 Parquet files (2023-11-01 → 2026-10-15), **1.08 B listens**, 32.4 M distinct songs, 112 min (3 workers, ~34 GB read of the 249 GB dump), 31 GB in `data/lb/`.
- `make lb-build` (20 min from cache):
  - Vocabulary: owner chose **≥ 20 listeners, top-100**; ≥ 10 would give 2.74 M songs, too big. Result: 1,495,212 songs, 74,604 users, 32.9 M sessions (519 M song-in-session entries).
  - **RAM**: the first build was killed by memory pressure. It is now capped at 200 M matrix entries, i.e. a hash sample of **39% of sessions**, collected per bucket.
  - `neighbours_lb`: 115.7 M rows.
- **Match rate**: 219,569 exact + 2,183 loose = **14.8% of LB songs** (= **33.9% weighted by listeners**). The MPD only has 594 k tracks, so most of the long tail can't match. **1,273,460 new tracks** in `lb_tracks`. `make lb-report`: release year from MusicBrainz for a random 1,000 (972 known): **58% ± 3% first released 2018+ → ≈ 739 k post-2017 tracks**. By year: 2018: 49, 2019: 54, 2020: 35, 2021: 70, 2022: 52, 2023: 90, 2024: 94, 2025: 78, 2026: 42, ≤ 2017: 408. The other ~42% are older songs the MPD never had (non-English, niche, or catalogue missing in 2017).
- `pop_est` for new tracks: log(playlist_count) = 0.77 + 0.59·log(listeners). Known issue: big post-2017 hits look only moderately popular (Blinding Lights ≈ 520, MPD top ≈ 45 k), because LB has only 75 k users. Quantile mapping would be better (follow-up).
- Lazy resolution works on the live API: "Blinding Lights" / "Espresso" are found by seed search and resolved through the labs lookup in ~2 s the first time, then cached.

Eval (5 seeds, @50, alpha 0; 1,000 MPD playlists / 1,000 recent LB sessions held out of `neighbours_lb`):

| Scorer weights (MPD / LB) | MPD hit-rate | MPD recall | LB hit-rate | LB recall |
|---|---|---|---|---|
| 1 / 0 (current default) | **0.908** | 0.196 | 0.255 | 0.072 |
| 0 / 1 | 0.675 | 0.082 | 0.655 | 0.276 |
| 0.75 / 0.25 | 0.900 | **0.196** | 0.587 | 0.230 |
| 0.5 / 0.5 | 0.884 | 0.176 | 0.642 | 0.263 |
| 0.25 / 0.75 | 0.817 | 0.124 | 0.651 | 0.276 |

Findings:
- **Recommendation: 0.75 MPD / 0.25 LB.** MPD recall stays at 0.196 (hit-rate −0.8 pt), while recall on recent listening triples (0.072 → 0.230). 0.5 / 0.5 gains a little more on LB (0.263) but costs 2 pts of MPD recall. `scorers.json` stays MPD-only until the owner approves.
- **Caveat**: listening sessions are often album or artist runs. LB-only, "Mr. Brightside" gives 15 / 40 songs by The Killers (MPD-only: 5 / 40), and an "Espresso" seed gives almost only Sabrina Carpenter. This also flatters the LB hold-out (hidden songs are often from the same album). An artist-diversity cap in the ranker is the obvious next step.
- **Owner decision (2026-11-02): default weights 0.5 MPD / 0.5 LB** (`scorers.json`): more recent music (LB recall 0.263) at a 2-point cost in MPD recall (0.176). Temporary `data/lb/` (31 GB) kept for quick rebuilds.

## V4: preview coverage (branch `v4-previews`, 2026-11-03)

Before: Deezer only, plain search + `deezer.pick`, misses cached 30 days, no logging.
After: chain Deezer ISRC → Deezer search (loose names, ±3 s) → iTunes (queued); provider ids
cached, fresh URL per play; browser retries once then falls back to the next source; every click
logged (`preview_attempts`, `make preview-report`); top 20 pre-resolved in the background;
"Open in Spotify" when there's no preview; optional "Previewable only" filter (off by default).

Coverage, same 300 tracks the app had shown (last 30 days, md5 order), live APIs, `make preview-bench`:

| Resolver | Has a preview | Breakdown |
|---|---|---|
| old (v1–v3) | 265/300 = **88.3%** | Deezer 265, none 35 |
| new (v4) | 290/300 = **96.7%** | Deezer 288, iTunes 2, none 10 |

What did it: ISRC lookup + "ISRC version has no preview → search another version", accent/feat.
folding, fuzzy titles with a duration check. iTunes adds little (US store, same catalogue gaps).
Live coverage from real clicks starts now: `make preview-report` (0 attempts at merge time).
The old cache (`previews`, 77% of 258 tried tracks matched) only reflected tracks clicked.

