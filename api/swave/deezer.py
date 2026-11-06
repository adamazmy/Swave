"""Deezer public API: search, ISRC lookup and fresh (signed, expiring) preview URLs.

The preview resolver chain and its cache live in `previews.py`. `norm`/`pick` are also used for
playlist matching and the ListenBrainz song key (`matching.song_hash`): don't change `norm`
without rebuilding those. Note: Deezer's advanced syntax (artist:"…" track:"…") returned nothing
in 2026-11, so callers fall back to a plain search.
"""

from __future__ import annotations

import re
import time

import httpx

API = "https://api.deezer.com"


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


QUOTA_CODE = 4  # "Quota limit exceeded" (50 requests / 5 s per IP)
QUOTA_RETRIES = 2


def _get(client: httpx.Client, path: str, **params) -> dict:
    for attempt in range(QUOTA_RETRIES + 1):
        try:
            r = client.get(f"{API}{path}", params=params)
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise DeezerError(str(e)) from e
        err = data.get("error") if isinstance(data, dict) else None
        if not err:
            return data
        if err.get("code") == QUOTA_CODE and attempt < QUOTA_RETRIES:
            time.sleep(1.0 + attempt)  # quota window is 5 s: back off and try again
            continue
        raise DeezerError(str(err))  # Deezer reports quota/errors with HTTP 200
    raise AssertionError("unreachable")


def search(client: httpx.Client, artist: str, title: str) -> tuple[int, str | None] | None:
    """(deezer_id, fresh preview url) for the best match, or None if no match."""
    data = _get(client, "/search", q=f"{artist} {title}", limit=10)
    best = pick(data.get("data", []), artist, title)
    return (int(best["id"]), best.get("preview") or None) if best else None


def track_preview(client: httpx.Client, deezer_id: int) -> str | None:
    return _get(client, f"/track/{deezer_id}").get("preview") or None


def by_isrc(client: httpx.Client, isrc: str) -> dict | None:
    """The Deezer track with this ISRC, or None. (`/track/isrc:…` answers "no data" with code 800.)"""
    try:
        return _get(client, f"/track/isrc:{isrc}")
    except DeezerError as e:
        if "800" in str(e) or "no data" in str(e).casefold():
            return None
        raise


def search_raw(client: httpx.Client, q: str, limit: int = 15) -> list[dict]:
    """Search results as Deezer returns them (each has id, title, artist.name, duration, preview)."""
    return _get(client, "/search", q=q, limit=limit).get("data", [])


def search_artists(client: httpx.Client, name: str, limit: int = 5) -> list[dict]:
    """Artist search results (each has id, name, nb_fan)."""
    return _get(client, "/search/artist", q=name, limit=limit).get("data", [])


def related_artists(client: httpx.Client, artist_id: int, limit: int = 20) -> list[dict]:
    """Deezer's "related artists" for an artist id, most related first (each has id, name)."""
    return _get(client, f"/artist/{artist_id}/related", limit=limit).get("data", [])
