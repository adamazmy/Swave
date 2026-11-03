"""iTunes Search API: a second source of 30-second previews (Apple terms: link back to Apple).

Unauthenticated and rate limited (~20 calls/minute per IP, 403 when exceeded), so every call
goes through one process-wide queue that spaces them out.
"""

from __future__ import annotations

import threading
import time

import httpx

API = "https://itunes.apple.com"
PER_MINUTE = 20


class ItunesError(Exception):
    """Transient failure (network, rate limit, queue full): don't cache."""


class RateLimiter:
    """Spaces calls at least 60/per_minute seconds apart, across threads (a FIFO-ish queue)."""

    def __init__(self, per_minute: int = PER_MINUTE, clock=time.monotonic, sleep=time.sleep):
        self.interval = 60.0 / per_minute
        self.clock, self.sleep = clock, sleep
        self.lock = threading.Lock()
        self.next_slot = 0.0

    def acquire(self, timeout: float) -> None:
        """Wait for a slot; raise ItunesError if it is more than `timeout` seconds away."""
        with self.lock:
            now = self.clock()
            slot = max(now, self.next_slot)
            if slot - now > timeout:
                raise ItunesError("rate limit queue full")
            self.next_slot = slot + self.interval
        if slot > now:
            self.sleep(slot - now)


limiter = RateLimiter()


def _get(client: httpx.Client, path: str, timeout: float, **params) -> dict:
    limiter.acquire(timeout)
    try:
        r = client.get(f"{API}{path}", params=params)
        if r.status_code in (403, 429):
            raise ItunesError(f"rate limited ({r.status_code})")
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise ItunesError(str(e)) from e


def search(client: httpx.Client, artist: str, title: str, timeout: float = 10.0) -> list[dict]:
    """Song results (trackId, trackName, artistName, trackTimeMillis, previewUrl, trackViewUrl)."""
    data = _get(client, "/search", timeout, term=f"{artist} {title}", entity="song",
                media="music", limit=15)
    return [r for r in data.get("results", []) if r.get("kind") == "song"]


def preview_url(client: httpx.Client, track_id: str, timeout: float = 10.0) -> str | None:
    """Fresh preview URL for an iTunes track id (None if it has none any more)."""
    results = _get(client, "/lookup", timeout, id=track_id).get("results", [])
    return (results[0].get("previewUrl") or None) if results else None


def view_url(track_id: str) -> str:
    """Apple Music page for the track (shown next to iTunes previews, per Apple's terms)."""
    return f"https://music.apple.com/song/{track_id}"
