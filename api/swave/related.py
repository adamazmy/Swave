"""Related-artists scorer: songs by the artists Deezer calls related to the seeds' artists.

Co-occurrence (playlists, listening sessions) is thin for niche or post-2018 music, so a seed's
list can drift into one-off links from a single eclectic listener. Deezer's "related artists"
is current and worldwide, so it adds candidates that match the seed's vibe and, since scorers
add up, lifts co-occurrence candidates by those artists above the one-off links.

  seed artist → Deezer artist id (/search/artist, exact loose name match) → /artist/{id}/related
  → the most popular catalogue songs of each related artist (`artist_tracks`, make artist-index)

Cached in `deezer_related(artist_key, deezer_id, related, checked_at)`: related artist keys
in Deezer's order; an artist Deezer doesn't know is cached as a miss and retried after
RETRY_DAYS. A Deezer error caches nothing (the scorer just has fewer candidates this time).

Second source, commercial-safe (CC0): `lb_similar_artists` = ListenBrainz's session-based
similar artists (labs API), via the artist's MusicBrainz id (MusicBrainz search, 1 req/s),
cached the same way in `lb_similar`. Both sources share the scoring below.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Callable

import httpx
import numpy as np
import psycopg

from swave import deezer, previews
from swave.scorers import EMPTY, Candidates

log = logging.getLogger(__name__)

RETRY_DAYS = 30
RELATED = 20  # related artists per seed artist
PER_ARTIST = 20  # songs per related artist
SELF_WEIGHT = 0.5  # the seed's own artist, relative to its most related artist (1.0)
RANK_DECAY = 5.0  # artist weight = 1 / (1 + rank / RANK_DECAY)

SCHEMA = """
CREATE TABLE IF NOT EXISTS deezer_related (
    artist_key text PRIMARY KEY,
    deezer_id  bigint,
    related    jsonb,
    checked_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS lb_similar (
    artist_key  text PRIMARY KEY,
    artist_mbid text,
    related     jsonb,
    checked_at  timestamptz NOT NULL
);
"""
MB_SEARCH = "https://musicbrainz.org/ws/2/artist/"
LB_SIMILAR = "https://labs.api.listenbrainz.org/similar-artists/json"
LB_ALGORITHM = "session_based_days_7500_session_300_contribution_5_threshold_10_limit_100_filter_True_skip_30"
USER_AGENT = "Swave/0.6 (personal playlist builder; https://github.com/adamazmy/Swave)"
_mb_lock, _mb_last = threading.Lock(), [0.0]


def _mb_wait() -> None:
    """MusicBrainz allows 1 request per second per client."""
    with _mb_lock:
        gap = time.time() - _mb_last[0]
        if gap < 1.05:
            time.sleep(1.05 - gap)
        _mb_last[0] = time.time()


def fetch_deezer(http: httpx.Client, key: str, name: str) -> tuple[bool, int | None, list[str]]:
    """(definitive, deezer id, related artist keys) for an artist. Not definitive = Deezer error."""
    try:
        hits = deezer.search_artists(http, name)
        match = next((a for a in hits if previews.loose_artist(a.get("name", "")) == key), None)
        if match is None:
            return True, None, []
        rel = deezer.related_artists(http, int(match["id"]), RELATED)
    except deezer.DeezerError as e:
        log.info("related %s: %s", key, e)
        return False, None, []
    keys = [k for k in (previews.loose_artist(a.get("name", "")) for a in rel) if k and k != key]
    return True, int(match["id"]), list(dict.fromkeys(keys))


def fetch_lb(http: httpx.Client, key: str, name: str) -> tuple[bool, str | None, list[str]]:
    """(definitive, MusicBrainz artist id, similar artist keys) from ListenBrainz (CC0)."""
    try:
        _mb_wait()
        r = http.get(MB_SEARCH, params={"query": 'artist:"%s"' % name.replace('"', ""), "fmt": "json",
                                        "limit": 5}, headers={"User-Agent": USER_AGENT}, timeout=15)
        if r.status_code != 200:
            return False, None, []
        match = next((a for a in r.json().get("artists", [])
                      if previews.loose_artist(a.get("name", "")) == key), None)
        if match is None:
            return True, None, []
        sim = http.get(LB_SIMILAR, params={"artist_mbids": match["id"], "algorithm": LB_ALGORITHM},
                       headers={"User-Agent": USER_AGENT}, timeout=30)
        if sim.status_code != 200:
            return False, None, []
        rows = sim.json()
        rows = rows if isinstance(rows, list) else []
    except (httpx.HTTPError, ValueError) as e:
        log.info("lb similar %s: %s", key, e)
        return False, None, []
    rows = sorted(rows, key=lambda x: -(x.get("score") or 0))[:RELATED]
    keys = [k for k in (previews.loose_artist(a.get("name", "")) for a in rows) if k and k != key]
    return True, match["id"], list(dict.fromkeys(keys))


# scorer name -> similar-artist source
SCORERS = {"related_artists": "deezer", "lb_similar_artists": "listenbrainz"}
# source -> (cache table, its id column, fetch function)
SOURCES = {"deezer": ("deezer_related", "deezer_id", fetch_deezer),
           "listenbrainz": ("lb_similar", "artist_mbid", fetch_lb)}


def related(conn: psycopg.Connection, http: httpx.Client, names: dict[str, str],
            source: str = "deezer") -> dict[str, list[str]]:
    """{artist key: related artist keys} for {artist key: a display name}, fetching uncached ones."""
    table, id_col, fetch = SOURCES[source]
    keys = list(names)
    rows = conn.execute(
        f"""SELECT artist_key, related FROM {table} WHERE artist_key = ANY(%s)
            AND ({id_col} IS NOT NULL OR checked_at > now() - interval '{RETRY_DAYS} days')""",
        (keys,)).fetchall()
    out = {k: r or [] for k, r in rows}
    for k in keys:
        if k in out:
            continue
        final, ext_id, rel = fetch(http, k, names[k])
        out[k] = rel
        if final:
            conn.execute(f"""INSERT INTO {table} VALUES (%s, %s, %s, now())
                             ON CONFLICT (artist_key) DO UPDATE SET {id_col} = EXCLUDED.{id_col},
                             related = EXCLUDED.related, checked_at = now()""",
                         (k, ext_id, json.dumps(rel)))
    conn.commit()
    return out


class RelatedArtists:
    """score(t) = Σ_seed artists weight(artist of t) · (0.5 + 0.5 · pop(t) / pop(best of its artist)),
    weight = 1 / (1 + rank / RANK_DECAY) for Deezer's related artists, SELF_WEIGHT for the seed's
    own artist; averaged over the seed artists."""

    def __init__(self, name: str, artists_of: Callable[[np.ndarray], dict[int, tuple[str, str]]],
                 related_of: Callable[[dict[str, str]], dict[str, list[str]]],
                 tracks_of: Callable[[list[str]], list[tuple[str, int, int]]]):
        self.name = name
        self.artists_of = artists_of  # track ids → {id: (artist key, display name)}
        self.related_of = related_of  # {key: name} → {key: related keys}
        self.tracks_of = tracks_of  # artist keys → [(key, track id, pop)], top songs per artist

    def candidates(self, seeds: np.ndarray, adventure: float | None) -> Candidates:
        names = {k: n for k, n in self.artists_of(seeds).values() if k}
        if not names:
            return EMPTY
        rel = self.related_of(names)
        weight: dict[str, float] = {}
        own: set[str] = set(names)
        for k in names:
            weight[k] = weight.get(k, 0.0) + SELF_WEIGHT
            for rank, r in enumerate(rel.get(k, [])):
                weight[r] = weight.get(r, 0.0) + 1.0 / (1.0 + rank / RANK_DECAY)
        rows = self.tracks_of(list(weight))
        if not rows:
            return EMPTY
        best: dict[str, int] = {}
        for k, _, p in rows:
            best[k] = max(best.get(k, 0), p)
        ids, score, is_own = [], [], []
        for k, t, p in rows:
            ids.append(t)
            score.append(weight[k] / len(names) * (0.5 + 0.5 * p / max(best[k], 1)))
            is_own.append(k in own)
        ids_a, score_a, own_a = np.array(ids, np.int64), np.array(score), np.array(is_own)
        o = np.argsort(ids_a, kind="stable")
        ids_a, score_a, own_a = ids_a[o], score_a[o], own_a[o]
        first = np.r_[True, ids_a[1:] != ids_a[:-1]]  # one row per track (an artist appears once)
        ids_a, score_a, own_a = ids_a[first], score_a[first], own_a[first]
        return Candidates(ids_a, score_a, float(score_a.max()), {"own_artist": own_a.astype(np.int64)})

    def reason(self, data: dict[str, float], n_seeds: int) -> str:
        return "by your seed's artist" if data.get("own_artist") else "by an artist similar to your seeds'"


def db_lookups(conn: psycopg.Connection, http: httpx.Client, name: str = "related_artists",
               source: str = "deezer") -> RelatedArtists:
    """The scorer on the live catalogue (`tracks`, `lb_tracks`, `artist_tracks`)."""

    def artists_of(ids: np.ndarray) -> dict[int, tuple[str, str]]:
        tl = [int(i) for i in ids]
        rows = conn.execute("""SELECT track_id, artist FROM tracks WHERE track_id = ANY(%s)
                               UNION ALL SELECT track_id, artist FROM lb_tracks
                               WHERE track_id = ANY(%s)""", (tl, tl)).fetchall()
        return {t: (previews.loose_artist(a), a) for t, a in rows}

    def tracks_of(keys: list[str]) -> list[tuple[str, int, int]]:
        return conn.execute(
            """SELECT artist_key, track_id, pop FROM (
                   SELECT artist_key, track_id, pop,
                          row_number() OVER (PARTITION BY artist_key ORDER BY pop DESC, track_id) rn
                   FROM artist_tracks WHERE artist_key = ANY(%s)) x WHERE rn <= %s""",
            (keys, PER_ARTIST)).fetchall()

    return RelatedArtists(name, artists_of, lambda names: related(conn, http, names, source), tracks_of)
