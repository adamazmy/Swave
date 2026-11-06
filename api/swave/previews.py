"""30-second previews: a resolver chain over several sources, a cache of provider track ids, and
a log of every preview attempt.

Per track, sources are tried in order (SOURCES):
  deezer   1. ISRC lookup (`/track/isrc:…`; the ISRC comes from Spotify, cached in `track_meta`)
           2. search with loosely normalised artist + title, duration within ±3 s when known
              (advanced syntax first; it returned nothing in 2026-11, so plain search after it)
  itunes   iTunes Search API, same matching rules, rate limited through a queue (itunes.py)

Only provider ids are cached (`preview_sources`, one row per track and source; a NULL id = no
match, retried after 30 days). Preview URLs are signed/expiring, so a fresh one is fetched every
time a preview is played. The browser reports each attempt's final outcome (`preview_attempts`):
it re-asks once on a playback error, then excludes that source to fall back to the next one.
"""

from __future__ import annotations

import os
import re
import unicodedata
from difflib import SequenceMatcher
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

import httpx
import psycopg
from psycopg_pool import ConnectionPool

from swave import deezer, itunes, spotify

KNOWN_SOURCES = ("deezer", "itunes")
# Tried in this order; SWAVE_PREVIEW_SOURCES=itunes drops Deezer (non-commercial terms: public
# mode refuses it, see licences.py).
SOURCES = tuple(s.strip() for s in os.environ.get("SWAVE_PREVIEW_SOURCES", ",".join(KNOWN_SOURCES)).split(",")
                if s.strip())
if not SOURCES or set(SOURCES) - set(KNOWN_SOURCES):
    raise ValueError(f"SWAVE_PREVIEW_SOURCES: expected a list from {KNOWN_SOURCES}, got {SOURCES}")
OUTCOMES = ("ok", "no_match", "matched_no_preview", "expired_url", "playback_error", "source_error")
RETRY_MISS_AFTER = timedelta(days=30)
DURATION_TOLERANCE_MS = 3000
FUZZY_TITLE = 0.8  # similarity for transliterations (Wahi/Wahy), only with a matching duration
PREFETCH_MAX = 50
PREFETCH_WORKERS = 4

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS preview_sources (
    track_uri   text NOT NULL,
    source      text NOT NULL,
    provider_id text,                 -- NULL = no match at checked_at
    has_preview boolean NOT NULL,     -- the match had a preview when last seen
    checked_at  timestamptz NOT NULL,
    PRIMARY KEY (track_uri, source)
);
CREATE TABLE IF NOT EXISTS track_meta (
    track_uri   text PRIMARY KEY,
    isrc        text,
    duration_ms int,
    checked_at  timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS preview_attempts (
    id         bigserial PRIMARY KEY,
    ts         timestamptz NOT NULL DEFAULT now(),
    session_id text,
    track_uri  text NOT NULL,
    outcome    text NOT NULL CHECK (outcome IN ({", ".join(f"'{o}'" for o in OUTCOMES)})),
    source     text,
    steps      text                   -- e.g. "deezer:expired_url>deezer:ok"
);
CREATE INDEX IF NOT EXISTS preview_attempts_ts ON preview_attempts (ts);
-- v1-v3 cached Deezer matches carry over (their old misses are re-tried with the new chain).
DO $$ BEGIN
  IF to_regclass('previews') IS NOT NULL THEN
    INSERT INTO preview_sources (track_uri, source, provider_id, has_preview, checked_at)
    SELECT track_uri, 'deezer', deezer_id::text, true, checked_at FROM previews WHERE matched
    ON CONFLICT DO NOTHING;
  END IF;
END $$;
"""


class SourceError(Exception):
    """A source failed transiently (network, quota, rate limit): nothing is cached."""


# ── Matching ─────────────────────────────────────────────────────────────────────────────────

_FEAT = re.compile(r"\s+(?:feat\.?|ft\.?|featuring|with)\s+.*$", re.I)
_VERSION = re.compile(r"\s+-\s+.*$")  # " - Remastered 2011", " - Live at …", " - Radio Edit"
_BRACKETS = re.compile(r"\s*[(\[].*?[)\]]")


def fold(s: str) -> str:
    """Casefold and strip accents (Beyoncé → beyonce, Ñ → n)."""
    s = unicodedata.normalize("NFKD", s.casefold())
    return "".join(c for c in s if not unicodedata.combining(c))


def loose_title(s: str) -> str:
    """Title without brackets, ' - version' suffixes, feat. credits, accents and punctuation."""
    s = _BRACKETS.sub("", fold(s))
    s = _VERSION.sub("", s)
    s = _FEAT.sub("", s)
    return " ".join(re.sub(r"[^\w\s]", " ", s).split())


def loose_artist(s: str) -> str:
    """Primary artist: before feat./,/&/x, folded, punctuation dropped."""
    s = _FEAT.sub("", fold(s))
    s = re.split(r"\s*(?:,|&|\bx\b|\band\b|;)\s*", s)[0]
    return " ".join(re.sub(r"[^\w\s]", " ", s).split())


@dataclass
class Candidate:
    id: str
    title: str
    artist: str
    duration_ms: int | None
    preview: str | None


def choose(cands: list[Candidate], artist: str, title: str, duration_ms: int | None) -> Candidate | None:
    """Best candidate: primary artist must match; duration within ±3 s when both are known; then
    exact title > loose title > one loose title starting the other > (duration confirmed) a title
    at least 80% similar. Ties: the one with a preview."""
    a, t = loose_artist(artist), loose_title(title)
    if not a or not t:
        return None

    def ok(c: Candidate) -> bool:
        ca = loose_artist(c.artist)
        if not (ca == a or ca.startswith(a + " ") or a.startswith(ca + " ")):
            return False
        if duration_ms and c.duration_ms:
            return abs(c.duration_ms - duration_ms) <= DURATION_TOLERANCE_MS
        return True

    pool = sorted((c for c in cands if ok(c)), key=lambda c: c.preview is None)  # stable
    tiers: list[Callable[[Candidate], bool]] = [
        lambda c: fold(c.title) == fold(title),
        lambda c: loose_title(c.title) == t,
        lambda c: bool(loose_title(c.title)) and (loose_title(c.title).startswith(t)
                                                  or t.startswith(loose_title(c.title))),
        lambda c: bool(duration_ms and c.duration_ms)
        and SequenceMatcher(None, loose_title(c.title), t).ratio() >= FUZZY_TITLE,
    ]
    for tier in tiers:
        for c in pool:
            if tier(c):
                return c
    return None


# ── Track metadata (ISRC + duration from Spotify, cached) ────────────────────────────────────

@dataclass
class Meta:
    name: str
    artist: str
    isrc: str | None
    duration_ms: int | None


def meta(conn: psycopg.Connection, http: httpx.Client, track_uri: str) -> Meta:
    """Name/artist from all_tracks; ISRC/duration from Spotify (one call per track, cached forever;
    skipped while Spotify isn't connected). Raises LookupError for unknown tracks."""
    row = conn.execute("SELECT name, artist FROM all_tracks WHERE track_uri = %s LIMIT 1",
                       (track_uri,)).fetchone()
    if not row:
        raise LookupError(track_uri)
    cached = conn.execute("SELECT isrc, duration_ms FROM track_meta WHERE track_uri = %s",
                          (track_uri,)).fetchone()
    if cached:
        return Meta(row[0], row[1], cached[0], cached[1])
    isrc = duration = None
    try:
        r = spotify.call(conn, http, "GET", f"/tracks/{track_uri.rsplit(':', 1)[1]}").json()
        isrc, duration = (r.get("external_ids") or {}).get("isrc"), r.get("duration_ms")
    except spotify.SpotifyError as e:
        if e.status != 404:
            return Meta(row[0], row[1], None, None)  # transient: don't cache
    except spotify.NotConnected:
        return Meta(row[0], row[1], None, None)
    conn.execute(
        """INSERT INTO track_meta VALUES (%s, %s, %s, now())
           ON CONFLICT (track_uri) DO UPDATE SET isrc = EXCLUDED.isrc,
             duration_ms = EXCLUDED.duration_ms, checked_at = now()""",
        (track_uri, isrc, duration))
    return Meta(row[0], row[1], isrc, duration)


# ── Sources ──────────────────────────────────────────────────────────────────────────────────

def _deezer_cand(d: dict) -> Candidate:
    return Candidate(str(d["id"]), d.get("title", ""), (d.get("artist") or {}).get("name", ""),
                     d["duration"] * 1000 if d.get("duration") else None, d.get("preview") or None)


def isrc_ok(c: Candidate, m: Meta) -> bool:
    """An ISRC names the recording, but artist credits differ between catalogues (soundtrack vs
    singer): accept a matching artist OR a matching title, as a guard against bad ISRC data."""
    return bool(choose([c], m.artist, m.name, None)
                or loose_title(c.title) == loose_title(m.name))


def resolve_deezer(http: httpx.Client, m: Meta, wait: float) -> Candidate | None:
    """ISRC first; if that version has no preview (region locks), search for another version of
    the same song that has one, and keep the ISRC match only as the fallback."""
    try:
        by_isrc = None
        if m.isrc:
            hit = deezer.by_isrc(http, m.isrc)
            if hit and hit.get("id") and isrc_ok(c := _deezer_cand(hit), m):
                if c.preview:
                    return c
                by_isrc = c
        artist, title = loose_artist(m.artist), loose_title(m.name)
        found = None
        for q in (f'artist:"{artist}" track:"{title}"', f"{artist} {title}"):
            cands = [_deezer_cand(d) for d in deezer.search_raw(http, q)]
            if cands:
                found = choose(cands, m.artist, m.name, m.duration_ms)
                break
        if found and found.preview:
            return found
        return by_isrc or found
    except deezer.DeezerError as e:
        raise SourceError(f"deezer: {e}") from e


def resolve_itunes(http: httpx.Client, m: Meta, wait: float) -> Candidate | None:
    """iTunes Search via the Apple Music provider adapter (one matcher for previews and links)."""
    from swave.providers import PROVIDERS  # providers imports previews lazily too

    try:
        return PROVIDERS["apple"].match(http, m, timeout=wait)
    except itunes.ItunesError as e:
        raise SourceError(f"itunes: {e}") from e


def fresh_url(http: httpx.Client, source: str, provider_id: str, wait: float) -> str | None:
    try:
        if source == "deezer":
            return deezer.track_preview(http, int(provider_id))
        return itunes.preview_url(http, provider_id, timeout=wait)
    except (deezer.DeezerError, itunes.ItunesError) as e:
        raise SourceError(f"{source}: {e}") from e


RESOLVERS: dict[str, Callable[[httpx.Client, Meta, float], Candidate | None]] = {
    "deezer": resolve_deezer, "itunes": resolve_itunes}


def open_url(source: str | None, provider_id: str | None) -> str | None:
    """Page to open the track at the preview's source (Apple requires the link back)."""
    if source == "itunes" and provider_id:
        return itunes.view_url(provider_id)
    if source == "deezer" and provider_id:
        return f"https://www.deezer.com/track/{provider_id}"
    return None


# ── Cache ────────────────────────────────────────────────────────────────────────────────────

def cached(conn: psycopg.Connection, track_uri: str) -> dict[str, tuple[str | None, bool, datetime]]:
    rows = conn.execute("SELECT source, provider_id, has_preview, checked_at FROM preview_sources "
                        "WHERE track_uri = %s", (track_uri,)).fetchall()
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


def store(conn: psycopg.Connection, track_uri: str, source: str, provider_id: str | None,
          has_preview: bool) -> None:
    conn.execute(
        """INSERT INTO preview_sources VALUES (%s, %s, %s, %s, now())
           ON CONFLICT (track_uri, source) DO UPDATE SET provider_id = EXCLUDED.provider_id,
             has_preview = EXCLUDED.has_preview, checked_at = now()""",
        (track_uri, source, provider_id, has_preview))


def _stale(row: tuple[str | None, bool, datetime] | None) -> bool:
    return row is None or (row[0] is None and datetime.now(timezone.utc) - row[2] >= RETRY_MISS_AFTER)


@dataclass
class Result:
    preview_url: str | None
    source: str | None
    provider_id: str | None
    outcome: str  # ok | no_match | matched_no_preview | source_error


def get(conn: psycopg.Connection, http: httpx.Client, track_uri: str,
        exclude: tuple[str, ...] = (), wait: float = 8.0) -> Result:
    """A fresh preview URL from the first source that has one. Raises LookupError (unknown track).
    `exclude`: sources whose URL failed to play in the browser (fall back to the next one)."""
    rows = cached(conn, track_uri)
    m: Meta | None = None
    matched = errored = False
    for source in SOURCES:
        if source in exclude:
            continue
        row = rows.get(source)
        try:
            if _stale(row):
                m = m or meta(conn, http, track_uri)
                hit = RESOLVERS[source](http, m, wait)
                store(conn, track_uri, source, hit.id if hit else None, bool(hit and hit.preview))
                if not hit:
                    continue
                matched = True
                if hit.preview:  # straight from the search response: fresh
                    return Result(hit.preview, source, hit.id, "ok")
                continue
            pid = row[0]
            if pid is None:
                continue
            matched = True
            url = fresh_url(http, source, pid, wait)
            if url:
                if not row[1]:
                    store(conn, track_uri, source, pid, True)
                return Result(url, source, pid, "ok")
            store(conn, track_uri, source, pid, False)
        except SourceError:
            errored = True
    if errored and not matched:
        return Result(None, None, None, "source_error")
    return Result(None, None, None, "matched_no_preview" if matched else "no_match")


def availability(conn: psycopg.Connection, uris: list[str]) -> dict[str, dict]:
    """From the cache only: {uri: {"status": "yes"|"no"|"unknown", "source", "open_url"}}.
    "no" = every source checked recently and none has a preview."""
    rows = conn.execute("SELECT track_uri, source, provider_id, has_preview, checked_at "
                        "FROM preview_sources WHERE track_uri = ANY(%s)", (uris,)).fetchall()
    by: dict[str, dict[str, tuple]] = {}
    for u, s, pid, has, at in rows:
        by.setdefault(u, {})[s] = (pid, has, at)
    out = {}
    for u in uris:
        r = by.get(u, {})
        best = next((s for s in SOURCES if s in r and r[s][0] and r[s][1]), None)
        if best:
            out[u] = {"status": "yes", "source": best, "open_url": open_url(best, r[best][0])}
        elif all(s in r and not _stale(r[s]) for s in SOURCES):
            out[u] = {"status": "no", "source": None, "open_url": None}
        else:
            out[u] = {"status": "unknown", "source": None, "open_url": None}
    return out


def prefetch(pool: ConnectionPool, http: httpx.Client, uris: list[str], wait: float = 30.0) -> dict:
    """Resolve the uncached tracks among `uris` (a few in parallel; iTunes calls queue up), then
    report availability for all of them. Only provider ids are stored, no URLs."""
    uris = list(dict.fromkeys(uris))[:PREFETCH_MAX]
    with pool.connection() as conn:
        todo = [u for u, a in availability(conn, uris).items() if a["status"] == "unknown"]

    def one(u: str) -> None:
        with pool.connection() as conn:
            try:
                resolve_all(conn, http, u, wait)
            except LookupError:
                pass

    with ThreadPoolExecutor(PREFETCH_WORKERS) as ex:
        list(ex.map(one, todo))
    with pool.connection() as conn:
        return availability(conn, uris)


def resolve_all(conn: psycopg.Connection, http: httpx.Client, track_uri: str, wait: float) -> None:
    """Fill the cache for a track: sources in order until one has a preview (no URL fetched)."""
    rows = cached(conn, track_uri)
    m: Meta | None = None
    for source in SOURCES:
        row = rows.get(source)
        if not _stale(row):
            if row[0] and row[1]:
                return
            continue
        m = m or meta(conn, http, track_uri)
        try:
            hit = RESOLVERS[source](http, m, wait)
        except SourceError:
            continue
        store(conn, track_uri, source, hit.id if hit else None, bool(hit and hit.preview))
        if hit and hit.preview:
            return


def log_attempt(conn: psycopg.Connection, session_id: str | None, track_uri: str, outcome: str,
                source: str | None, steps: str | None) -> None:
    conn.execute("INSERT INTO preview_attempts (session_id, track_uri, outcome, source, steps) "
                 "VALUES (%s, %s, %s, %s, %s)", (session_id, track_uri, outcome, source, steps))


def coverage(conn: psycopg.Connection, days: int = 7) -> dict:
    """Coverage % (attempts that played) and the outcome / source breakdown, last `days` days."""
    rows = conn.execute(
        """SELECT outcome, coalesce(source, ''), (steps LIKE '%%>%%') AS recovered, count(*)
           FROM preview_attempts WHERE ts > now() - make_interval(days => %s)
           GROUP BY 1, 2, 3""", (days,)).fetchall()
    total = sum(r[3] for r in rows)
    outcomes: dict[str, int] = {}
    sources: dict[str, int] = {}
    recovered = 0
    for outcome, source, rec, n in rows:
        outcomes[outcome] = outcomes.get(outcome, 0) + n
        if outcome == "ok":
            sources[source] = sources.get(source, 0) + n
            recovered += n if rec else 0
    ok = outcomes.get("ok", 0)
    return {"days": days, "attempts": total, "ok": ok,
            "coverage": ok / total if total else None, "outcomes": outcomes,
            "ok_by_source": sources, "recovered": recovered}
