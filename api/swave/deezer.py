"""30-second previews from Deezer's public API, matched by artist + title and cached in `previews`.

Deezer preview URLs are signed and expire, so we cache the Deezer track id and fetch a fresh URL
each time. Misses are cached too and retried after 30 days. Network errors are never cached.
Note: Deezer's advanced syntax (artist:"…" track:"…") currently returns nothing, so we do a plain
search and pick the best candidate ourselves.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import httpx
import psycopg

API = "https://api.deezer.com"
RETRY_MISS_AFTER = timedelta(days=30)


class DeezerError(Exception):
    """Transient Deezer failure (network, quota): don't cache, just show no preview."""


def norm(s: str) -> str:
    """Lowercase, drop bracketed/after-dash suffixes (remix, live, remaster…) and punctuation."""
    s = s.casefold()
    s = re.sub(r"\s*[(\[].*?[)\]]", "", s)
    s = s.split(" - ")[0]
    return re.sub(r"[^\w\s]", "", s).strip()


def pick(candidates: list[dict], artist: str, title: str) -> dict | None:
    """Best candidate: artist must match; then exact title > same title without (Live)/remaster
    suffixes > title that merely starts the same."""
    a, t = norm(artist), norm(title)
    same_artist = [c for c in candidates if norm(c.get("artist", {}).get("name", "")) == a]
    for c in same_artist:
        if c.get("title", "").casefold() == title.casefold():
            return c
    for c in same_artist:
        if norm(c.get("title", "")) == t:
            return c
    for c in same_artist:
        if norm(c.get("title", "")).startswith(t) or t.startswith(norm(c.get("title", ""))):
            return c
    return None


def _get(client: httpx.Client, path: str, **params) -> dict:
    try:
        r = client.get(f"{API}{path}", params=params)
        r.raise_for_status()
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise DeezerError(str(e)) from e
    if "error" in data:  # Deezer reports quota/errors with HTTP 200
        raise DeezerError(str(data["error"]))
    return data


def search(client: httpx.Client, artist: str, title: str) -> tuple[int, str | None] | None:
    """(deezer_id, fresh preview url) for the best match, or None if no match."""
    data = _get(client, "/search", q=f"{artist} {title}", limit=10)
    best = pick(data.get("data", []), artist, title)
    return (int(best["id"]), best.get("preview") or None) if best else None


def track_preview(client: httpx.Client, deezer_id: int) -> str | None:
    return _get(client, f"/track/{deezer_id}").get("preview") or None


def get_preview(conn: psycopg.Connection, client: httpx.Client, track_uri: str) -> str | None:
    """Preview URL for a track (None if Deezer has no match). Raises LookupError for unknown tracks."""
    row = conn.execute(
        "SELECT deezer_id, matched, checked_at FROM previews WHERE track_uri = %s", (track_uri,)
    ).fetchone()
    if row:
        deezer_id, matched, checked_at = row
        if matched:
            return track_preview(client, deezer_id)
        if datetime.now(timezone.utc) - checked_at < RETRY_MISS_AFTER:
            return None

    meta = conn.execute(
        "SELECT name, artist FROM tracks WHERE track_uri = %s", (track_uri,)
    ).fetchone()
    if not meta:
        raise LookupError(track_uri)
    hit = search(client, meta[1], meta[0])
    conn.execute(
        """INSERT INTO previews (track_uri, deezer_id, matched, checked_at) VALUES (%s, %s, %s, now())
           ON CONFLICT (track_uri) DO UPDATE
           SET deezer_id = EXCLUDED.deezer_id, matched = EXCLUDED.matched, checked_at = now()""",
        (track_uri, hit[0] if hit else None, hit is not None),
    )
    return hit[1] if hit else None
