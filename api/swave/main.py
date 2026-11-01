"""FastAPI app. Run: make api  (uvicorn swave.main:create_app --factory)."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Literal

import httpx
import numpy as np
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

from swave import deezer
from swave.config import database_url
from swave.recommend import score

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
    alpha: float = Field(0.0, ge=0.0, le=1.0, description="0 = familiar … 1 = adventurous")
    exclude: list[str] = Field(default_factory=list, max_length=1000)
    n: int = Field(50, ge=1, le=100)


class RecommendOut(BaseModel):
    results: list[Recommendation]
    seeds: list[Track]  # known seeds, in request order
    unknown_seeds: list[str]


class EventIn(BaseModel):
    action: Action
    track_uri: str | None = Field(None, pattern=TRACK_URI)
    position: int | None = Field(None, ge=0)
    slider: float | None = Field(None, ge=0.0, le=1.0)
    session_id: str | None = Field(None, max_length=64)


class PreviewOut(BaseModel):
    track_uri: str
    preview_url: str | None


def reason(hits: int, n_seeds: int, co: int) -> str:
    seeds = "your seed" if n_seeds == 1 else f"{hits} of your {n_seeds} seeds"
    return f"appears with {seeds} in {co:,} shared playlists"


def escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def create_app(dsn: str | None = None) -> FastAPI:
    pool = ConnectionPool(dsn or database_url(), min_size=1, max_size=8, open=False)
    http = httpx.Client(timeout=5.0)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        pool.open(wait=True)
        with pool.connection() as conn:
            conn.execute(APP_SCHEMA)
        yield
        pool.close()
        http.close()

    app = FastAPI(title="Swave", lifespan=lifespan)
    origins = os.environ.get("WEB_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in origins.split(",") if o.strip()],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

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
            res = score(arr[:, 0].astype(np.int64), arr[:, 1], arr[:, 2], arr[:, 3],
                        body.alpha, exclude, body.n)
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
        return RecommendOut(results=results, seeds=seeds, unknown_seeds=unknown)

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

    return app
