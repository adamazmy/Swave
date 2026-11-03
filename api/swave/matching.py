"""Map Spotify playlist songs onto dataset tracks.

The MPD is a 2018 snapshot, so the same song often has a different Spotify track id today
(remasters, re-releases, single vs album versions). Matching, best first:
  1. exact track_uri
  2. same artist + same normalised title  ("Wonderwall - Remastered 2014" == "Wonderwall")
  3. same artist + one title is a prefix of the other covering >= 60% of the longer one
Among several dataset versions the most popular one wins.
  4. a ListenBrainz-only track (`lb_tracks`) with the same song key; the playlist's own Spotify
     URI is then remembered for that song in `lb_spotify`.
"""

from __future__ import annotations

import hashlib
from typing import NamedTuple

import psycopg

from swave.deezer import norm

PREFIX_MIN_RATIO = 0.6

Row = tuple[str, str, str, str, int]  # track_uri, name, artist, album, playlist_count


class Item(NamedTuple):
    uri: str
    name: str
    artists: list[str]


class Match(NamedTuple):
    item: Item
    row: Row | None
    how: str  # "exact" | "title" | "fuzzy" | "lb" | "none"


def song_key(name: str, artist: str) -> tuple[str, str]:
    return norm(name), norm(artist)


def song_hash(name: str, artist: str) -> int:
    """Stable signed 64-bit id of a song = hash of its normalised key (`lb_tracks.song`)."""
    t, a = song_key(name, artist)
    return int.from_bytes(hashlib.blake2b(f"{t}\x1f{a}".encode(), digest_size=8).digest(),
                          "little", signed=True)


def _prefix_match(a: str, b: str) -> bool:
    short, long_ = sorted((a, b), key=len)
    return bool(short) and long_.startswith(short) and len(short) / len(long_) >= PREFIX_MIN_RATIO


def map_items(conn: psycopg.Connection, items: list[Item], lb: bool = True) -> list[Match]:
    """Items → dataset tracks: exact id, then artist + normalised title, then title prefix; then
    (lb=True) ListenBrainz-only songs, which take the item's own uri (a real Spotify uri). Pasted
    lines have no uri: they pass lb=False and resolve LB songs themselves (paste.py)."""
    rows = conn.execute(
        "SELECT track_uri, name, artist, album, playlist_count FROM tracks WHERE track_uri = ANY(%s)",
        ([i.uri for i in items],),
    ).fetchall()
    by_uri = {r[0]: r for r in rows}

    pending = [i for i in items if i.uri not in by_uri]
    by_key: dict[tuple[str, str], Row] = {}
    by_artist: dict[str, list[Row]] = {}
    artists = sorted({a.lower() for i in pending for a in i.artists if a})
    if artists:
        for r in conn.execute(
            """SELECT track_uri, name, artist, album, playlist_count FROM tracks
               WHERE lower(artist) = ANY(%s)""",
            (artists,),
        ).fetchall():
            k = song_key(r[1], r[2])
            if k not in by_key or r[4] > by_key[k][4]:
                by_key[k] = r
            by_artist.setdefault(k[1], []).append(r)

    out = []
    for item in items:
        if item.uri in by_uri:
            out.append(Match(item, by_uri[item.uri], "exact"))
            continue
        title = norm(item.name)
        found: Match | None = None
        for artist in item.artists:
            if (row := by_key.get((title, norm(artist)))) is not None:
                found = Match(item, row, "title")
                break
        if found is None:
            candidates = [r for a in item.artists for r in by_artist.get(norm(a), [])
                          if _prefix_match(title, norm(r[1]))]
            if candidates:
                found = Match(item, max(candidates, key=lambda r: r[4]), "fuzzy")
        out.append(found or Match(item, None, "none"))
    return _match_lb(conn, out) if lb else out


def _match_lb(conn: psycopg.Connection, matches: list[Match]) -> list[Match]:
    """Unmatched songs → ListenBrainz-only tracks by song key (any of the item's artists)."""
    todo = {song_hash(m.item.name, a): i for i, m in enumerate(matches) if m.row is None
            for a in m.item.artists if a}
    if not todo:
        return matches
    rows = conn.execute("SELECT song, name, artist, pop_est FROM lb_tracks WHERE song = ANY(%s)",
                        (list(todo),)).fetchall()
    known = []
    for song, name, artist, pop in rows:
        i = todo[song]
        if matches[i].row is None:
            item = matches[i].item
            matches[i] = Match(item, (item.uri, name, artist, "", pop), "lb")
            known.append((song, item.uri))
    if known:
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO lb_spotify (song, track_uri, checked_at) VALUES (%s, %s, now())
                   ON CONFLICT (song) DO UPDATE SET track_uri = EXCLUDED.track_uri,
                   checked_at = now()""", known)
    return matches
