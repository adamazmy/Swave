"""Album covers for the Results list, from Spotify's public oEmbed endpoint (no login needed;
the 300×300 `thumbnail_url` is the album image). Only the image URL is cached, in `covers`;
a miss (no cover, unknown track) is cached too and retried after RETRY_DAYS. A network error
or rate limit caches nothing, so the next call tries again. Never blocks results: the browser
asks for covers after the list is shown.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

import httpx
import psycopg
from psycopg_pool import ConnectionPool

log = logging.getLogger(__name__)

OEMBED = "https://open.spotify.com/oembed"
WORKERS = 8
MAX_URIS = 100
RETRY_DAYS = 30
IMAGE_HOSTS = ("https://i.scdn.co/", "https://image-cdn-ak.spotifycdn.com/",
               "https://image-cdn-fa.spotifycdn.com/", "https://mosaic.scdn.co/")

SCHEMA = """
CREATE TABLE IF NOT EXISTS covers (
    track_uri  text PRIMARY KEY,
    image_url  text,
    checked_at timestamptz NOT NULL
);
"""


def cached(conn: psycopg.Connection, uris: list[str]) -> dict[str, str | None]:
    """{uri: url or None} for the uris with a fresh cache row (misses older than RETRY_DAYS
    are left out, so they are fetched again)."""
    rows = conn.execute(
        f"""SELECT track_uri, image_url FROM covers WHERE track_uri = ANY(%s)
            AND (image_url IS NOT NULL OR checked_at > now() - interval '{RETRY_DAYS} days')""",
        (uris,)).fetchall()
    return dict(rows)


def fetch(http: httpx.Client, uri: str) -> tuple[bool, str | None]:
    """(definitive, image url). Not definitive = a network error or rate limit: don't cache."""
    track_id = uri.rsplit(":", 1)[1]
    try:
        r = http.get(OEMBED, params={"url": f"https://open.spotify.com/track/{track_id}"}, timeout=8)
    except httpx.HTTPError as e:
        log.info("cover %s: %s", uri, type(e).__name__)
        return False, None
    if r.status_code in (400, 404):
        return True, None  # unknown or unavailable track
    if r.status_code != 200:
        return False, None  # 429 / 5xx: try again next time
    url = r.json().get("thumbnail_url")
    # only Spotify's image CDN: the URL ends up in an <img src>
    return True, url if isinstance(url, str) and url.startswith(IMAGE_HOSTS) else None


def get(pool: ConnectionPool, http: httpx.Client, uris: list[str]) -> dict[str, str | None]:
    """{uri: cover url or None} for up to MAX_URIS Spotify track uris, fetching uncached ones."""
    uris = list(dict.fromkeys(uris))[:MAX_URIS]
    with pool.connection() as conn:
        out = cached(conn, uris)
    todo = [u for u in uris if u not in out]
    if todo:
        with ThreadPoolExecutor(WORKERS) as ex:
            got = list(ex.map(lambda u: (u, *fetch(http, u)), todo))
        with pool.connection() as conn:
            for u, final, url in got:
                out[u] = url
                if final:
                    conn.execute("""INSERT INTO covers VALUES (%s, %s, now())
                                    ON CONFLICT (track_uri) DO UPDATE
                                    SET image_url = EXCLUDED.image_url, checked_at = now()""",
                                 (u, url))
    return {u: out.get(u) for u in uris}
