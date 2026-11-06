"""Lazy Spotify URIs for ListenBrainz-only tracks (`lb_tracks` has no Spotify id).

Resolved only when a track is about to be shown (search, recommendations), then cached in
`lb_spotify` (keyed by song, so it survives `make lb-build`):
  1. ListenBrainz labs `spotify-id-from-mbid` (one batched request, no auth) for tracks with a
     MusicBrainz recording id;
  2. otherwise Spotify search (limit 10) checked by artist + title, at most SEARCH_CAP per call.
Misses are cached too and retried after 30 days. Network errors are never cached.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import httpx
import psycopg

from swave import spotify

LABS_URL = "https://labs.api.listenbrainz.org/spotify-id-from-mbid/json"
RETRY_MISS_AFTER = timedelta(days=30)
SEARCH_CAP = 10
SPOTIFY_ID = re.compile(r"^[A-Za-z0-9]{22}$")


def labs_lookup(http: httpx.Client, mbids: list[str]) -> dict[str, str | None] | None:
    """{mbid: spotify uri | None}; None if the service failed (nothing should be cached)."""
    try:
        r = http.post(LABS_URL, json=[{"recording_mbid": m} for m in mbids], timeout=8.0)
        r.raise_for_status()
        rows = r.json()
    except (httpx.HTTPError, ValueError):
        return None
    out: dict[str, str | None] = {m: None for m in mbids}
    for row in rows if isinstance(rows, list) else []:
        ids = [i for i in row.get("spotify_track_ids") or [] if SPOTIFY_ID.match(i or "")]
        if row.get("recording_mbid") in out and ids:
            out[row["recording_mbid"]] = f"spotify:track:{ids[0]}"
    return out


def resolve(conn: psycopg.Connection, http: httpx.Client, track_ids: list[int]) -> dict[int, str]:
    """{track_id: spotify uri} for the given lb_tracks ids that have (or now get) a Spotify URI."""
    if not track_ids:
        return {}
    rows = conn.execute(
        """SELECT l.track_id, l.song, l.name, l.artist, l.recording_mbid, s.track_uri, s.checked_at
           FROM lb_tracks l LEFT JOIN lb_spotify s USING (song) WHERE l.track_id = ANY(%s)""",
        (list(track_ids),)).fetchall()
    now = datetime.now(timezone.utc)
    out = {r[0]: r[5] for r in rows if r[5]}
    todo = [r for r in rows if not r[5] and (r[6] is None or now - r[6] > RETRY_MISS_AFTER)]
    if not todo:
        return out

    answers: dict[int, str | None] = {}  # song -> uri | None (definitive answers only)
    mbids = sorted({r[4] for r in todo if r[4]})
    labs = labs_lookup(http, mbids) if mbids else {}
    searched = 0
    for tid, song, name, artist, mbid, _, _ in todo:
        uri = labs.get(mbid) if labs and mbid else None
        if uri is None:  # a miss is cached only after Spotify search itself found nothing
            if searched >= SEARCH_CAP:
                continue
            searched += 1
            try:
                uri = spotify.search_track(conn, http, spotify.ANY, name, artist)
            except (spotify.NotConnected, spotify.SpotifyError):
                continue  # no definitive answer: try again next time
            if uri is None and mbid and labs is None:
                continue  # labs was down: it may still know this one, don't cache the miss
        answers[song] = uri
        if uri:
            out[tid] = uri
    if answers:
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO lb_spotify (song, track_uri, checked_at) VALUES (%s, %s, now())
                   ON CONFLICT (song) DO UPDATE SET track_uri = EXCLUDED.track_uri,
                   checked_at = now()""",
                list(answers.items()))
    return out
