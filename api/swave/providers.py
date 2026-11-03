"""Music services behind one adapter interface, so a service can gain abilities without the rest
of the app changing: the UI asks `GET /providers` what each one can do.

Capabilities:
  search  find a Swave track in the service's catalogue (→ a link to open it there)
  read    read the user's playlists from a link (import seeds)
  write   create/update playlists in the user's library (save the set)

  spotify  search + read + write (OAuth PKCE; the existing /import, /save, /playlists routes)
  apple    search only: the free iTunes Search API, whose track ids ARE Apple Music song ids.
           Reading/writing an Apple Music library needs MusicKit (a paid Apple Developer key):
           a MusicKit adapter would implement read/write and flip those flags. None exists yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol
from urllib.parse import quote

import httpx
import psycopg

from swave import itunes, spotify

RETRY_MISS_AFTER = timedelta(days=30)

SCHEMA = """
CREATE TABLE IF NOT EXISTS provider_tracks (
    track_uri   text NOT NULL,
    provider    text NOT NULL,
    provider_id text,              -- NULL = not found in that catalogue at checked_at
    checked_at  timestamptz NOT NULL,
    PRIMARY KEY (track_uri, provider)
);
"""


class Unsupported(Exception):
    """The provider can't do this (capability off)."""


@dataclass(frozen=True)
class Capabilities:
    search: bool
    read: bool
    write: bool


@dataclass(frozen=True)
class Found:
    provider: str
    id: str
    url: str  # the track's page at the provider


class Provider(Protocol):
    name: str
    label: str
    caps: Capabilities

    def configured(self) -> bool:
        """Usable on this install (credentials present, if any are needed)."""
        ...

    def find(self, conn: psycopg.Connection, http: httpx.Client, track_uri: str) -> Found | None:
        """The track in this catalogue (search capability). LookupError for unknown tracks."""
        ...

    def search_url(self, name: str, artist: str) -> str:
        """A search page at the provider, for when `find` has no match."""
        ...

    def read_playlist(self, conn: psycopg.Connection, http: httpx.Client, ref: str) -> tuple[str, list[dict]]:
        """(name, items [{uri, name, artists}]) of a playlist (read capability)."""
        ...

    def write_playlist(self, conn: psycopg.Connection, http: httpx.Client, name: str, description: str,
                       track_uris: list[str]) -> dict:
        """Create a playlist with these tracks; {"id", "url"} (write capability)."""
        ...


# ── Spotify ───────────────────────────────────────────────────────────────────────────────────

class SpotifyProvider:
    name = "spotify"
    label = "Spotify"
    caps = Capabilities(search=True, read=True, write=True)

    def configured(self) -> bool:
        try:
            return bool(spotify.client_id())
        except spotify.SpotifyError:  # SPOTIFY_CLIENT_ID missing
            return False

    def find(self, conn, http, track_uri):
        # Swave's track ids ARE Spotify URIs.
        tid = track_uri.rsplit(":", 1)[1]
        return Found(self.name, tid, f"https://open.spotify.com/track/{tid}")

    def search_url(self, name, artist):
        return f"https://open.spotify.com/search/{quote(f'{artist} {name}')}"

    def read_playlist(self, conn, http, ref):
        pid = spotify.parse_playlist_id(ref)
        if not pid:
            raise ValueError("That doesn't look like a Spotify playlist link")
        return spotify.playlist_name(conn, http, pid), spotify.playlist_items(conn, http, pid)

    def write_playlist(self, conn, http, name, description, track_uris):
        return spotify.create_playlist(conn, http, name, description, track_uris)


# ── Apple Music (free: iTunes Search API, links only) ─────────────────────────────────────────

class AppleProvider:
    name = "apple"
    label = "Apple Music"
    caps = Capabilities(search=True, read=False, write=False)

    def configured(self) -> bool:
        return True  # the iTunes Search API needs no key

    def find(self, conn, http, track_uri):
        """Cached Apple id, else one iTunes search (queued: ~20 calls/min) matched like previews."""
        from swave import previews  # previews uses this adapter for its iTunes step

        row = conn.execute(
            "SELECT provider_id, checked_at FROM provider_tracks WHERE track_uri = %s AND provider = %s",
            (track_uri, self.name)).fetchone()
        if row and (row[0] or datetime.now(timezone.utc) - row[1] < RETRY_MISS_AFTER):
            return Found(self.name, row[0], itunes.view_url(row[0])) if row[0] else None
        # An iTunes preview found earlier already names the Apple Music song.
        seen = conn.execute("SELECT provider_id FROM preview_sources WHERE track_uri = %s "
                            "AND source = 'itunes' AND provider_id IS NOT NULL", (track_uri,)).fetchone()
        if seen:
            hit_id = seen[0]
        else:
            hit = self.match(http, previews.meta(conn, http, track_uri), timeout=10.0)
            hit_id = hit.id if hit else None
        remember(conn, track_uri, self.name, hit_id)
        return Found(self.name, hit_id, itunes.view_url(hit_id)) if hit_id else None

    def match(self, http: httpx.Client, m, timeout: float):
        """Best iTunes catalogue candidate for a track's metadata (also the previews' iTunes step).
        Raises itunes.ItunesError on transient failures."""
        from swave import previews

        results = itunes.search(http, previews.loose_artist(m.artist), previews.loose_title(m.name),
                                timeout=timeout)
        cands = [previews.Candidate(str(r["trackId"]), r.get("trackName", ""), r.get("artistName", ""),
                                    r.get("trackTimeMillis"), r.get("previewUrl") or None)
                 for r in results]
        return previews.choose(cands, m.artist, m.name, m.duration_ms)

    def search_url(self, name, artist):
        return f"https://music.apple.com/search?term={quote(f'{artist} {name}')}"

    def read_playlist(self, conn, http, ref):
        raise Unsupported("Swave can't read Apple Music playlists (that needs MusicKit). "
                          "Copy the songs in Apple Music and paste them as a list instead.")

    def write_playlist(self, conn, http, name, description, track_uris):
        raise Unsupported("Swave can't save to Apple Music (that needs MusicKit). "
                          "Export the set for a transfer tool instead.")


def remember(conn: psycopg.Connection, track_uri: str, provider: str, provider_id: str | None) -> None:
    conn.execute(
        """INSERT INTO provider_tracks VALUES (%s, %s, %s, now())
           ON CONFLICT (track_uri, provider) DO UPDATE SET provider_id = EXCLUDED.provider_id,
             checked_at = now()""", (track_uri, provider, provider_id))


PROVIDERS: dict[str, Provider] = {p.name: p for p in (SpotifyProvider(), AppleProvider())}


def describe() -> list[dict]:
    """What each provider can do, for the UI."""
    return [{"name": p.name, "label": p.label, "configured": p.configured(),
             "can_search": p.caps.search, "can_read": p.caps.read, "can_write": p.caps.write}
            for p in PROVIDERS.values()]
