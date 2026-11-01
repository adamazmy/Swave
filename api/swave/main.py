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

from swave import deezer, spotify
from swave.config import database_url
from swave.recommend import MAX_ALPHA, alpha_for, score

TRACK_URI = r"^spotify:track:[A-Za-z0-9]{22}$"
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


class ImportOut(BaseModel):
    playlist: SourcePlaylist
    tracks: list[Track]  # tracks of the playlist that are in our index, playlist order
    total: int  # tracks in the Spotify playlist
    missing: int  # not in the dataset index


class AuthStatus(BaseModel):
    configured: bool
    connected: bool
    display_name: str | None
    can_upload_cover: bool  # false for logins made before the ugc-image-upload scope existed


def reason(hits: int, n_seeds: int, co: int) -> str:
    seeds = "your seed" if n_seeds == 1 else f"{hits} of your {n_seeds} seeds"
    return f"appears with {seeds} in {co:,} shared playlists"


def escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def with_param(url: str, **params: str) -> str:
    return url + ("&" if "?" in url else "?") + urlencode(params)


def create_app(dsn: str | None = None, http: httpx.Client | None = None) -> FastAPI:
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
        """Every word must appear in title or artist; most popular first."""
        words = q.lower().split()[:6]
        if not words:
            raise HTTPException(422, "Query is empty")
        where = " AND ".join(["lower(name || ' ' || artist) LIKE %s"] * len(words))
        with pool.connection() as conn:
            rows = conn.execute(
                f"""SELECT track_uri, name, artist, album, playlist_count FROM tracks
                    WHERE {where} ORDER BY playlist_count DESC, track_uri LIMIT %s""",
                [f"%{escape_like(w)}%" for w in words] + [limit],
            ).fetchall()
        return [Track(track_uri=r[0], name=r[1], artist=r[2], album=r[3], playlist_count=r[4])
                for r in rows]

    @app.post("/recommend", response_model=RecommendOut)
    def recommend(body: RecommendIn):
        with pool.connection() as conn:
            ids = dict(conn.execute(
                "SELECT track_uri, track_id FROM tracks WHERE track_uri = ANY(%s)",
                (list(set(body.seeds) | set(body.exclude)),),
            ).fetchall())
            seed_ids = sorted({ids[u] for u in body.seeds if u in ids})
            unknown = [u for u in body.seeds if u not in ids]
            if not seed_ids:
                raise HTTPException(404, "None of the seed tracks are in the index")
            rows = conn.execute(
                """SELECT n.neighbour_id, n.sim, n.co_count, t.playlist_count
                   FROM neighbours n JOIN tracks t ON t.track_id = n.neighbour_id
                   WHERE n.track_id = ANY(%s)""",
                (seed_ids,),
            ).fetchall()
            arr = np.array(rows, dtype=np.float64).reshape(-1, 4)
            exclude = np.array(seed_ids + [ids[u] for u in body.exclude if u in ids])
            cand = (arr[:, 0].astype(np.int64), arr[:, 1], arr[:, 2], arr[:, 3])
            alpha = body.alpha if body.adventure is None else alpha_for(
                body.adventure, *cand, exclude, body.n)
            res = score(*cand, alpha, exclude, body.n)
            meta = {r[0]: r[1:] for r in conn.execute(
                """SELECT track_id, track_uri, name, artist, album, playlist_count
                   FROM tracks WHERE track_id = ANY(%s)""",
                (res.track.tolist() + seed_ids,),
            ).fetchall()}
        seen: set[str] = set()
        seeds = []
        for u in body.seeds:
            if u in ids and u not in seen:
                seen.add(u)
                m = meta[ids[u]]
                seeds.append(Track(track_uri=m[0], name=m[1], artist=m[2], album=m[3],
                                   playlist_count=m[4]))
        results = [
            Recommendation(
                track_uri=meta[t][0], name=meta[t][1], artist=meta[t][2], album=meta[t][3],
                playlist_count=meta[t][4], score=float(s), seed_hits=int(h), co_count=int(c),
                reason=reason(int(h), len(seed_ids), int(c)),
            )
            for t, s, h, c in zip(res.track.tolist(), res.score, res.seed_hits, res.co_count)
        ]
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
    def preview(track_uri: str = Query(pattern=TRACK_URI)):
        with pool.connection() as conn:
            try:
                url = deezer.get_preview(conn, http, track_uri)
            except LookupError:
                raise HTTPException(404, "Unknown track") from None
            except deezer.DeezerError:
                url = None  # transient: show no preview, try again next time
        return PreviewOut(track_uri=track_uri, preview_url=url)

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
            try:
                return AddOut(**spotify.add_new_items(conn, http, playlist_id, body.track_uris))
            except spotify.SpotifyError as e:
                if e.status in (403, 404):
                    raise HTTPException(403, "Spotify only lets Swave change playlists you own or "
                                             "collaborate on") from None
                raise http_error(e) from None
            except spotify.NotConnected as e:
                raise http_error(e) from None

    @app.post("/import", response_model=ImportOut)
    def import_playlist(body: ImportIn):
        pid = spotify.parse_playlist_id(body.url)
        if not pid:
            raise HTTPException(422, "That doesn't look like a Spotify playlist link")
        with pool.connection() as conn:
            try:
                uris = spotify.playlist_track_uris(conn, http, pid)
                name = spotify.playlist_name(conn, http, pid)
            except spotify.SpotifyError as e:
                if e.status in (403, 404):  # dev mode: only playlists you own or collaborate on
                    raise HTTPException(403, "Spotify only lets Swave read playlists you own or "
                                             "collaborate on") from None
                raise http_error(e) from None
            except spotify.NotConnected as e:
                raise http_error(e) from None
            uris = list(dict.fromkeys(uris))
            rows = conn.execute(
                """SELECT track_uri, name, artist, album, playlist_count FROM tracks
                   WHERE track_uri = ANY(%s)""", (uris,)).fetchall()
        known = {r[0]: r for r in rows}
        tracks = [Track(track_uri=r[0], name=r[1], artist=r[2], album=r[3], playlist_count=r[4])
                  for u in uris if (r := known.get(u))]
        return ImportOut(playlist=SourcePlaylist(id=pid, name=name), tracks=tracks,
                         total=len(uris), missing=len(uris) - len(tracks))

    return app
