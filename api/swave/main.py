"""FastAPI app. Run: make api  (uvicorn swave.main:create_app --factory)."""

from __future__ import annotations

import os
import secrets
import time
from contextlib import asynccontextmanager
from typing import Annotated, Literal
from urllib.parse import urlencode, urlsplit

import httpx
import numpy as np
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field, StringConstraints

from swave import covers, itunes, lb, licences, related, styles, matching, paste, previews, providers, recommend as rk, resolve, scorers, spotify
from swave.config import database_url
from swave.recommend import MAX_ALPHA

TRACK_URI = r"^spotify:track:[A-Za-z0-9]{22}$"
RESOLVE_SPARE = 25  # extra ranked rows so dropped (unresolvable) ListenBrainz tracks get replaced
MAX_PER_ARTIST = 5  # default cap per primary artist (listening sessions are often album runs)
ACTIONS = ("shown", "seeded", "previewed", "kept", "removed", "more_like_this", "saved")
Action = Literal["shown", "seeded", "previewed", "kept", "removed", "more_like_this", "saved"]

# App-owned tables. Unlike tracks/neighbours they survive a re-ingest (keyed by track_uri).
# `shown` (one row per track displayed) is the denominator of the keep rate.
APP_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS events (
    id         bigserial PRIMARY KEY,
    ts         timestamptz NOT NULL DEFAULT now(),
    session_id text,
    action     text NOT NULL,
    track_uri  text,
    position   int,
    slider     real
);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts);
ALTER TABLE events DROP CONSTRAINT IF EXISTS events_action_check;
ALTER TABLE events ADD CONSTRAINT events_action_check
    CHECK (action IN ({", ".join(f"'{a}'" for a in ACTIONS)}));
CREATE TABLE IF NOT EXISTS previews (
    track_uri  text PRIMARY KEY,
    deezer_id  bigint,
    matched    boolean NOT NULL,
    checked_at timestamptz NOT NULL
);
"""


class Track(BaseModel):
    track_uri: str
    name: str
    artist: str
    album: str
    playlist_count: int


class Recommendation(Track):
    score: float
    seed_hits: int
    co_count: int
    reason: str


class RecommendIn(BaseModel):
    seeds: list[str] = Field(min_length=1, max_length=10)
    alpha: float = Field(0.0, ge=0.0, le=MAX_ALPHA, description="popularity exponent (raw)")
    adventure: float | None = Field(
        None, ge=0.0, le=1.0,
        description="slider 0 = familiar … 1 = adventurous; overrides alpha (see alpha_for)")
    exclude: list[str] = Field(default_factory=list, max_length=1000)
    # Songs to leave out by song, not only by id: any version with the same artist +
    # normalised title is excluded too (e.g. the songs of the pasted playlist).
    exclude_songs: list[str] = Field(default_factory=list, max_length=1000)
    # Leave out every artist of the seeds (and of exclude_songs): only artists new to the set.
    new_artists: bool = False
    # At most this many results per primary artist ("X feat. Y" counts as X); 0 = no cap.
    max_per_artist: int = Field(MAX_PER_ARTIST, ge=0, le=100)
    # Tracks already on the list: they count toward the cap (replacing one removed song).
    listed: list[str] = Field(default_factory=list, max_length=1000)
    n: int = Field(50, ge=1, le=100)


class RecommendOut(BaseModel):
    results: list[Recommendation]
    seeds: list[Track]  # known seeds, in request order
    unknown_seeds: list[str]
    alpha: float  # the exponent actually used (reuse it for replacements)


class EventIn(BaseModel):
    action: Action
    track_uri: str | None = Field(None, pattern=TRACK_URI)
    position: int | None = Field(None, ge=0)
    slider: float | None = Field(None, ge=0.0, le=1.0)
    session_id: str | None = Field(None, max_length=64)


class PreviewOut(BaseModel):
    track_uri: str
    preview_url: str | None
    source: str | None = None  # deezer | itunes
    outcome: str  # ok | no_match | matched_no_preview | source_error
    open_url: str | None = None  # the track's page at `source` (Apple asks for the link back)


PreviewSource = Literal["deezer", "itunes"]


class CoversIn(BaseModel):
    track_uris: list[Annotated[str, StringConstraints(pattern=TRACK_URI)]] = Field(
        min_length=1, max_length=covers.MAX_URIS)


class PreviewResolveIn(BaseModel):
    track_uris: list[Annotated[str, StringConstraints(pattern=TRACK_URI)]] = Field(
        min_length=1, max_length=previews.PREFETCH_MAX)


class PreviewAttemptIn(BaseModel):
    track_uri: Annotated[str, StringConstraints(pattern=TRACK_URI)]
    outcome: Literal["ok", "no_match", "matched_no_preview", "expired_url", "playback_error",
                     "source_error"]
    source: PreviewSource | None = None
    steps: str | None = Field(default=None, max_length=300)
    session_id: str | None = Field(default=None, max_length=64)


TrackUri = Annotated[str, StringConstraints(pattern=TRACK_URI)]


class SaveIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field("Built with Swave", max_length=300)
    track_uris: list[TrackUri] = Field(min_length=1, max_length=500)
    # Set to update a playlist Swave saved before (rename + replace its songs) instead of
    # creating a new one.
    playlist_id: str | None = Field(None, pattern=r"^[A-Za-z0-9]{22}$")
    public: bool = False
    # Custom cover: base64 JPEG without the data: prefix, max 256 KB (Spotify's limit).
    cover_jpeg: str | None = Field(None, min_length=100, max_length=spotify.MAX_COVER_B64,
                                   pattern=r"^[A-Za-z0-9+/]+=*$")


class SaveOut(BaseModel):
    id: str
    url: str | None
    added: int
    cover_error: str | None = None  # the playlist is saved even if the cover upload fails


class AddIn(BaseModel):
    track_uris: list[TrackUri] = Field(min_length=1, max_length=500)


class AddOut(BaseModel):
    id: str
    url: str
    added: int
    skipped: int  # already in the playlist


class ImportIn(BaseModel):
    url: str = Field(min_length=10, max_length=300)


class SourcePlaylist(BaseModel):
    id: str
    name: str
    provider: Literal["spotify", "paste"] = "spotify"  # "paste": a pasted song list, no playlist


class PasteIn(BaseModel):
    text: str = Field(min_length=3, max_length=40_000)


class ImportOut(BaseModel):
    playlist: SourcePlaylist
    tracks: list[Track]  # tracks of the playlist that are in our index, playlist order
    total: int  # tracks in the Spotify playlist
    missing: int  # not in the dataset index
    loose: int  # matched by artist + title rather than the exact Spotify id
    unmatched: list[str]  # "Title · Artist" of songs not found (first 50)


class AuthStatus(BaseModel):
    configured: bool
    connected: bool
    display_name: str | None
    can_upload_cover: bool  # false for logins made before the ugc-image-upload scope existed


def escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def with_param(url: str, **params: str) -> str:
    return url + ("&" if "?" in url else "?") + urlencode(params)


def create_app(dsn: str | None = None, http: httpx.Client | None = None,
               weights: dict[str, float] | None = None) -> FastAPI:
    # Scorer weights are read once at startup (restart the API after editing scorers.json).
    weights = weights or rk.load_weights()
    # Public mode refuses non-commercial data at startup (licences.py); personal mode loads all.
    licences.check([f"scorer:{n}" for n in weights] + [f"previews:{s}" for s in previews.SOURCES]
                   + ["covers:spotify", "catalogue:mpd"])
    pool = ConnectionPool(dsn or database_url(), min_size=1, max_size=8, open=False)
    http = http or httpx.Client(timeout=10.0)
    origins = [o.strip() for o in os.environ.get(
        "WEB_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",") if o.strip()]
    # OAuth state -> (code_verifier, return_to, created). In memory: single user, 10 min TTL.
    pending: dict[str, tuple[str, str, float]] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        pool.open(wait=True)
        with pool.connection() as conn:
            conn.execute(APP_SCHEMA)
            conn.execute(spotify.SCHEMA)
            conn.execute(lb.SCHEMA)
            conn.execute(lb.VIEW)
            conn.execute(previews.SCHEMA)
            conn.execute(providers.SCHEMA)
            conn.execute(covers.SCHEMA)
            conn.execute(related.SCHEMA)
        yield
        pool.close()
        http.close()

    app = FastAPI(title="Swave", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    def safe_return(url: str | None) -> str:
        """Only redirect back to the web app's own origins (no open redirect)."""
        if url:
            parts = urlsplit(url)
            if f"{parts.scheme}://{parts.netloc}" in origins:
                return url
        return origins[0] + "/"

    def http_error(e: Exception) -> HTTPException:
        """Map Spotify failures to clean HTTP errors."""
        if isinstance(e, spotify.NotConnected):
            return HTTPException(401, "Connect Spotify first")
        assert isinstance(e, spotify.SpotifyError)
        hint = (" (dev-mode apps only work for the owner and allow-listed users; "
                "check the app's User Management)") if e.status == 403 else ""
        return HTTPException(503 if e.status == 503 else 502, f"Spotify: {e.message}{hint}")

    @app.get("/health")
    def health() -> dict:
        with pool.connection() as conn:
            meta = conn.execute("SELECT method, k, min_co, built_at FROM index_meta").fetchone()
        return {"ok": True, "index": None if meta is None else
                {"method": meta[0], "k": meta[1], "min_co": meta[2], "built_at": meta[3]}}

    @app.get("/search", response_model=list[Track])
    def search(q: str = Query(min_length=2, max_length=100), limit: int = Query(20, ge=1, le=50)):
        """Every word must appear in title or artist; most popular first. Includes ListenBrainz-only
        tracks (resolved to Spotify on the fly; unresolvable ones are left out)."""
        words = q.lower().split()[:6]
        if not words:
            raise HTTPException(422, "Query is empty")
        like = [f"%{escape_like(w)}%" for w in words]

        def cond(t: str) -> str:
            return " AND ".join([f"lower({t}.name || ' ' || {t}.artist) LIKE %s"] * len(words))

        with pool.connection() as conn:
            rows = conn.execute(
                f"""SELECT track_id, track_uri, name, artist, album, pc FROM (
                      (SELECT t.track_id, t.track_uri, t.name, t.artist, t.album,
                              t.playlist_count AS pc FROM tracks t WHERE {cond("t")}
                       ORDER BY t.playlist_count DESC LIMIT %s)
                      UNION ALL
                      (SELECT l.track_id, s.track_uri, l.name, l.artist, '', l.pop_est
                       FROM lb_tracks l LEFT JOIN lb_spotify s USING (song)
                       WHERE {cond("l")} AND (s.track_uri IS NOT NULL OR s.checked_at IS NULL
                                              OR s.checked_at < now() - interval '30 days')
                       ORDER BY l.pop_est DESC LIMIT %s)) x
                    ORDER BY pc DESC, track_id LIMIT %s""",
                like + [limit] + like + [limit] + [limit],
            ).fetchall()
            uris = resolve.resolve(conn, http, [r[0] for r in rows if r[1] is None])
        out, seen = [], set()
        for tid, uri, name, artist, album, pc in rows:
            uri = uri or uris.get(tid)
            if uri and uri not in seen:
                seen.add(uri)
                out.append(Track(track_uri=uri, name=name, artist=artist, album=album,
                                 playlist_count=pc))
        return out

    def scorer_set(conn) -> list[tuple[scorers.Scorer, float]]:  # noqa: ANN001
        tables = {"cooccurrence_mpd": ("neighbours", "shared playlists"),
                  "cooccurrence_lb": ("neighbours_lb", "listening sessions")}
        return [(related.db_lookups(conn, http, name, related.SCORERS[name]) if name in related.SCORERS
                 else styles.db_lookups(conn) if name == "discogs_style" else
                 scorers.Cooccurrence(name, scorers.from_db(conn, tables[name][0]), unit=tables[name][1]), w)
                for name, w in weights.items()]

    @app.post("/recommend", response_model=RecommendOut)
    def recommend(body: RecommendIn):
        with pool.connection() as conn:
            # uri -> track ids, MPD first (a resolved ListenBrainz track may share its Spotify URI
            # with an MPD track)
            ids: dict[str, list[int]] = {}
            for u, t in conn.execute(
                    "SELECT track_uri, track_id FROM all_tracks WHERE track_uri = ANY(%s) "
                    "ORDER BY track_id", (list(set(body.seeds) | set(body.exclude)),)).fetchall():
                ids.setdefault(u, []).append(t)
            seed_ids = sorted({ids[u][0] for u in body.seeds if u in ids})
            unknown = [u for u in body.seeds if u not in ids]
            if not seed_ids:
                raise HTTPException(404, "None of the seed tracks are in the index")
            active = scorer_set(conn)

            def pop_of(tids: np.ndarray) -> np.ndarray:
                tl = tids.astype(np.int64).tolist()
                pc = dict(conn.execute(
                    """SELECT track_id, playlist_count FROM tracks WHERE track_id = ANY(%s)
                       UNION ALL SELECT track_id, pop_est FROM lb_tracks WHERE track_id = ANY(%s)""",
                    (tl, tl)).fetchall())
                return np.array([pc.get(int(t), 0) for t in tids], dtype=np.float64)

            comb = rk.combine(active, np.array(seed_ids), body.adventure, pop_of)
            excluded = [t for u in set(body.seeds) | set(body.exclude) for t in ids.get(u, [])]
            cands = []
            if (body.exclude_songs or body.new_artists or body.max_per_artist) and len(comb.track):
                tl = comb.track.tolist()
                cands = conn.execute(
                    """SELECT track_id, name, artist FROM tracks WHERE track_id = ANY(%s)
                       UNION ALL SELECT track_id, name, artist FROM lb_tracks
                       WHERE track_id = ANY(%s)""", (tl, tl)).fetchall()
            if (body.exclude_songs or body.new_artists) and len(comb.track):
                known = conn.execute(
                    "SELECT name, artist FROM all_tracks WHERE track_uri = ANY(%s)",
                    (body.exclude_songs,)).fetchall() if body.exclude_songs else []
                keys = {matching.song_key(n, a) for n, a in known}
                excluded += [t for t, n, a in cands if matching.song_key(n, a) in keys]
                if body.new_artists:
                    # Primary artist, loosely ("X feat. Y", "X & Y" → "x"), so features count too.
                    old = {previews.loose_artist(a) for _, a in known} | {
                        previews.loose_artist(r[0]) for r in conn.execute(
                            "SELECT artist FROM all_tracks WHERE track_uri = ANY(%s)",
                            (body.seeds,)).fetchall()}
                    excluded += [t for t, _, a in cands if previews.loose_artist(a) in old]
            exclude = np.array(excluded)
            alpha = body.alpha if body.adventure is None else rk.alpha_for(
                body.adventure, comb, exclude, body.n)
            # A few spare rows: ListenBrainz-only tracks without a Spotify match are dropped.
            if body.max_per_artist:
                artist_of = {t: previews.loose_artist(a) for t, _, a in cands}
                already: dict[str, int] = {}
                rows = conn.execute(
                    "SELECT DISTINCT ON (track_uri) artist FROM all_tracks WHERE track_uri = ANY(%s)",
                    (list(set(body.listed)),)).fetchall() if body.listed else []
                for (a,) in rows:
                    key = previews.loose_artist(a)
                    already[key] = already.get(key, 0) + 1
                res = rk.cap_artists(rk.penalise(comb, alpha, exclude, len(comb.track)), artist_of,
                                     body.max_per_artist, body.n + RESOLVE_SPARE, already)
            else:
                res = rk.penalise(comb, alpha, exclude, body.n + RESOLVE_SPARE)
            why = rk.reasons(active, comb, res, len(seed_ids))
            mpd = comb.data.get("cooccurrence_mpd", {})
            zeros = np.zeros(len(comb.track), dtype=np.int64)
            hits, co = mpd.get("seed_hits", zeros)[res.rows], mpd.get("co_count", zeros)[res.rows]
            tl = res.track.tolist() + seed_ids
            meta = {r[0]: r[1:] for r in conn.execute(
                """SELECT track_id, track_uri, name, artist, album, playlist_count FROM tracks
                   WHERE track_id = ANY(%s)""", (tl,)).fetchall()}
            lb_ids = [t for t in tl if t not in meta]
            if lb_ids:
                uris = resolve.resolve(conn, http, lb_ids)
                for r in conn.execute("SELECT track_id, name, artist, pop_est FROM lb_tracks "
                                      "WHERE track_id = ANY(%s)", (lb_ids,)).fetchall():
                    if r[0] in uris:
                        meta[r[0]] = (uris[r[0]], r[1], r[2], "", r[3])
        seen: set[str] = set()
        seeds = []
        for u in body.seeds:
            if u in ids and u not in seen and ids[u][0] in meta:
                seen.add(u)
                m = meta[ids[u][0]]
                seeds.append(Track(track_uri=u, name=m[1], artist=m[2], album=m[3],
                                   playlist_count=m[4]))
        taken = set(body.seeds) | set(body.exclude)
        results = []
        for t, s, h, c, w in zip(res.track.tolist(), res.score, hits, co, why):
            if t not in meta or meta[t][0] in taken:
                continue  # no Spotify match, or the same Spotify track as a seed/earlier result
            taken.add(meta[t][0])
            results.append(Recommendation(
                track_uri=meta[t][0], name=meta[t][1], artist=meta[t][2], album=meta[t][3],
                playlist_count=meta[t][4], score=float(s), seed_hits=int(h), co_count=int(c),
                reason=w))
            if len(results) == body.n:
                break
        return RecommendOut(results=results, seeds=seeds, unknown_seeds=unknown, alpha=alpha)

    insert_event = """INSERT INTO events (session_id, action, track_uri, position, slider)
                      VALUES (%s, %s, %s, %s, %s)"""

    def row(ev: EventIn) -> tuple:
        return (ev.session_id, ev.action, ev.track_uri, ev.position, ev.slider)

    @app.post("/events", status_code=201)
    def log_event(ev: EventIn) -> dict:
        with pool.connection() as conn:
            (event_id,) = conn.execute(insert_event + " RETURNING id", row(ev)).fetchone()
        return {"id": event_id}

    @app.post("/events/batch", status_code=201)
    def log_events(evs: list[EventIn] = Body(min_length=1, max_length=200)) -> dict:
        with pool.connection() as conn, conn.cursor() as cur:
            cur.executemany(insert_event, [row(e) for e in evs])
        return {"logged": len(evs)}

    @app.get("/preview", response_model=PreviewOut)
    def preview(track_uri: str = Query(pattern=TRACK_URI),
                exclude: Annotated[list[PreviewSource], Query()] = []):  # noqa: B006
        """A fresh preview URL (fetched now: provider URLs expire). `exclude` = sources whose URL
        failed to play in the browser, to fall back to the next source."""
        with pool.connection() as conn:
            try:
                r = previews.get(conn, http, track_uri, tuple(exclude))
            except LookupError:
                raise HTTPException(404, "Unknown track") from None
        return PreviewOut(track_uri=track_uri, preview_url=r.preview_url, source=r.source,
                          outcome=r.outcome, open_url=previews.open_url(r.source, r.provider_id))

    @app.post("/covers")
    def get_covers(body: CoversIn) -> dict[str, str | None]:
        """{uri: album cover URL or null}. Cached; called by the UI after the list is shown."""
        return covers.get(pool, http, body.track_uris)

    @app.post("/previews/resolve")
    def previews_resolve(body: PreviewResolveIn) -> dict:
        """Pre-resolve (cache provider ids, no URLs) and report which tracks have a preview:
        {uri: {status: yes|no|unknown, source, open_url}}. Called in the background by the UI."""
        return previews.prefetch(pool, http, body.track_uris)

    @app.post("/preview/attempts", status_code=201)
    def preview_attempt(a: PreviewAttemptIn) -> dict:
        with pool.connection() as conn:
            previews.log_attempt(conn, a.session_id, a.track_uri, a.outcome, a.source, a.steps)
        return {"logged": 1}

    # ── Spotify ──────────────────────────────────────────────────────────────────────────────

    @app.get("/auth/login")
    def auth_login(return_to: str | None = None):
        now = time.time()
        for s in [s for s, (_, _, t) in pending.items() if now - t > 600]:
            del pending[s]
        verifier, challenge = spotify.pkce_pair()
        state = secrets.token_urlsafe(24)
        try:
            url = spotify.authorize_url(state, challenge)
        except spotify.SpotifyError as e:
            raise HTTPException(503, e.message) from None
        pending[state] = (verifier, safe_return(return_to), now)
        return RedirectResponse(url, status_code=302)

    @app.get("/auth/callback")
    def auth_callback(state: str = "", code: str | None = None, error: str | None = None):
        entry = pending.pop(state, None)
        if entry is None:
            return RedirectResponse(with_param(safe_return(None), spotify_error="Login expired, try again"), 302)
        verifier, back, _ = entry
        if error or not code:
            return RedirectResponse(with_param(back, spotify_error=error or "Login cancelled"), 302)
        try:
            with pool.connection() as conn:
                spotify.connect(conn, http, code, verifier)
        except (spotify.SpotifyError, spotify.NotConnected) as e:
            msg = e.message if isinstance(e, spotify.SpotifyError) else "Login failed"
            return RedirectResponse(with_param(back, spotify_error=msg), 302)
        return RedirectResponse(with_param(back, spotify="connected"), 302)

    @app.get("/auth/status", response_model=AuthStatus)
    def auth_status():
        with pool.connection() as conn:
            st = spotify.status(conn)
        return AuthStatus(configured=bool(os.environ.get("SPOTIFY_CLIENT_ID", "").strip()),
                          connected=st is not None, display_name=st[0] if st else None,
                          can_upload_cover=bool(st and st[1]))

    @app.post("/auth/logout", status_code=204)
    def auth_logout():
        with pool.connection() as conn:
            spotify.disconnect(conn)

    @app.post("/save", response_model=SaveOut, status_code=201)
    def save(body: SaveIn):
        uris = list(dict.fromkeys(body.track_uris))  # dedupe, keep order
        with pool.connection() as conn:
            try:
                if body.playlist_id:
                    out = SaveOut(**spotify.update_playlist(
                        conn, http, body.playlist_id, body.name, body.description, uris, body.public))
                else:
                    out = SaveOut(**spotify.create_playlist(
                        conn, http, body.name, body.description, uris, body.public))
            except spotify.SpotifyError as e:
                if body.playlist_id and e.status == 404:
                    raise HTTPException(404, "That playlist no longer exists; save it as new") from None
                raise http_error(e) from None
            except spotify.NotConnected as e:
                raise http_error(e) from None
            if body.cover_jpeg:
                try:
                    spotify.upload_cover(conn, http, out.id, body.cover_jpeg)
                except spotify.SpotifyError as e:
                    out.cover_error = ("Reconnect Spotify to allow cover uploads" if e.status in (401, 403)
                                       else f"Cover upload failed: {e.message}")
                except spotify.NotConnected:
                    out.cover_error = "Reconnect Spotify to upload the cover"
            return out

    @app.post("/playlists/{playlist_id}/add", response_model=AddOut, status_code=201)
    def add_to_playlist(playlist_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9]{22}$")],
                        body: AddIn):
        """Append kept songs to one of your playlists, skipping songs already in it."""
        with pool.connection() as conn:
            keys = {r[0]: matching.song_key(r[1], r[2]) for r in conn.execute(
                "SELECT track_uri, name, artist FROM tracks WHERE track_uri = ANY(%s)",
                (body.track_uris,)).fetchall()}
            try:
                return AddOut(**spotify.add_new_items(conn, http, playlist_id, body.track_uris, keys))
            except spotify.SpotifyError as e:
                if e.status in (403, 404):
                    raise HTTPException(403, "Spotify only lets Swave change playlists you own or "
                                             "collaborate on") from None
                raise http_error(e) from None
            except spotify.NotConnected as e:
                raise http_error(e) from None

    @app.post("/import", response_model=ImportOut)
    def import_playlist(body: ImportIn):
        if "music.apple.com" in body.url or "itunes.apple.com" in body.url:
            try:  # capability check: the Apple adapter can't read playlists (no MusicKit)
                providers.PROVIDERS["apple"].read_playlist(None, http, body.url)
            except providers.Unsupported as e:
                raise HTTPException(422, str(e)) from None
        pid = spotify.parse_playlist_id(body.url)
        if not pid:
            raise HTTPException(422, "That doesn't look like a Spotify playlist link")
        with pool.connection() as conn:
            try:
                items = spotify.playlist_items(conn, http, pid)
                name = spotify.playlist_name(conn, http, pid)
            except spotify.SpotifyError as e:
                if e.status in (403, 404):  # dev mode: only playlists you own or collaborate on
                    raise HTTPException(403, "Spotify only lets Swave read playlists you own or "
                                             "collaborate on") from None
                raise http_error(e) from None
            except spotify.NotConnected as e:
                raise http_error(e) from None
            unique = list({i["uri"]: i for i in items}.values())
            matches = matching.map_items(
                conn, [matching.Item(i["uri"], i["name"], i["artists"]) for i in unique])
        tracks, seen = [], set()
        for m in matches:
            if m.row and m.row[0] not in seen:  # two Spotify versions can map to one track
                seen.add(m.row[0])
                tracks.append(Track(track_uri=m.row[0], name=m.row[1], artist=m.row[2],
                                    album=m.row[3], playlist_count=m.row[4]))
        unmatched = [f"{m.item.name} · {', '.join(m.item.artists)}" for m in matches if m.row is None]
        return ImportOut(playlist=SourcePlaylist(id=pid, name=name), tracks=tracks,
                         total=len(unique), missing=len(unmatched),
                         loose=sum(m.how in ("title", "fuzzy") for m in matches),
                         unmatched=unmatched[:50])

    @app.post("/import/text", response_model=ImportOut)
    def import_text(body: PasteIn):
        """Seeds from a pasted song list (one per line; Apple Music copy-paste works)."""
        with pool.connection() as conn:
            rows, unmatched, loose = paste.match(conn, http, body.text)
        tracks = [Track(track_uri=r[0], name=r[1], artist=r[2], album=r[3], playlist_count=r[4])
                  for r in rows]
        total = len(paste.lines(body.text))
        return ImportOut(playlist=SourcePlaylist(id="", name="your pasted list", provider="paste"),
                         tracks=tracks, total=total, missing=len(unmatched), loose=loose,
                         unmatched=unmatched[:50])

    # ── Providers (Spotify, Apple Music…) ────────────────────────────────────────────────────

    @app.get("/providers")
    def list_providers() -> list[dict]:
        """Music services and what Swave can do with each (search/read/write)."""
        return providers.describe()

    @app.get("/providers/{name}/open")
    def open_in(name: str, track_uri: str = Query(pattern=TRACK_URI)):
        """Redirect to the track on that service, or to a search there when it isn't found."""
        p = providers.PROVIDERS.get(name)
        if not p or not p.caps.search:
            raise HTTPException(404, "Unknown music service")
        with pool.connection() as conn:
            meta = conn.execute("SELECT name, artist FROM all_tracks WHERE track_uri = %s LIMIT 1",
                                (track_uri,)).fetchone()
            if not meta:
                raise HTTPException(404, "Unknown track")
            try:
                found = p.find(conn, http, track_uri)
            except itunes.ItunesError:
                found = None  # rate limited / down: the search page still gets you there
        return RedirectResponse(found.url if found else p.search_url(meta[0], meta[1]), status_code=302)

    return app
