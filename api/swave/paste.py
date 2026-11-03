"""Seeds from a pasted song list: the free way in for Apple Music users (and anyone else).

One song per line, in whatever shape it was copied: "Title - Artist", "Artist - Title",
"Title by Artist", or the tab-separated columns Apple Music / iTunes put on the clipboard when
you select songs and copy (Name, Time, Artist, Album, …). Each line is tried in every plausible
(title, artist) reading and the first one that matches a Swave track wins:
MPD tracks (matching.map_items), then ListenBrainz-only songs, whose Spotify uri is looked up
like for recommendations (resolve.py).
"""

from __future__ import annotations

import re
from itertools import permutations

import httpx
import psycopg

from swave import matching, resolve

MAX_LINES = 200
_SPLIT = re.compile(r"\t+|\s+[-–—|]\s+|\s+by\s+", re.I)
_NOISE = re.compile(r"^(?:\d{1,3}[.)]?|\d{1,2}:\d{2}(?::\d{2})?|explicit|clean|e)$", re.I)


def lines(text: str) -> list[str]:
    out = [ln.strip() for ln in text.replace("\r", "\n").split("\n")]
    return [ln for ln in out if ln][:MAX_LINES]


def readings(line: str) -> list[tuple[str, str]]:
    """Plausible (title, artist) pairs for a line, most likely first. Durations, track numbers
    and 'Explicit' columns are dropped; at most the first 4 remaining fields are combined."""
    line = re.sub(r"^\s*\d{1,3}[.)]\s+", "", line)  # "1. Song - Artist"
    parts = [p.strip(" \"'") for p in _SPLIT.split(line)]
    parts = [p for p in parts if p and not _NOISE.match(p)][:4]
    if len(parts) < 2:
        return []
    # Title first is the most common order (and Apple's column order); then reversed.
    return [(a, b) for a, b in permutations(parts, 2)]


def match(conn: psycopg.Connection, http: httpx.Client, text: str) -> tuple[list[tuple], list[str], int]:
    """(rows [(track_uri, name, artist, album, playlist_count)] in paste order without
    duplicates, unmatched lines, number matched loosely). Lines with no title/artist split are
    unmatched."""
    src = lines(text)
    cands = [readings(ln) for ln in src]
    items = [matching.Item(f"paste:{i}:{k}", t, [a]) for i, rs in enumerate(cands)
             for k, (t, a) in enumerate(rs)]
    by_item = {m.item.uri: m for m in matching.map_items(conn, items, lb=False)} if items else {}

    found: dict[int, tuple] = {}
    loose = 0
    for i, rs in enumerate(cands):
        for k in range(len(rs)):
            m = by_item[f"paste:{i}:{k}"]
            if m.row:
                found[i] = m.row
                loose += m.how == "fuzzy"
                break

    # ListenBrainz-only songs for the rest: by song key, then a Spotify uri via resolve.py.
    todo = {matching.song_hash(t, a): i for i, rs in enumerate(cands) if i not in found
            for t, a in rs}
    if todo:
        lb = conn.execute("SELECT song, track_id, name, artist, pop_est FROM lb_tracks "
                          "WHERE song = ANY(%s)", (list(todo),)).fetchall()
        first = {}
        for song, tid, name, artist, pop in lb:
            i = todo[song]
            first.setdefault(i, (tid, name, artist, pop))
        uris = resolve.resolve(conn, http, [v[0] for v in first.values()]) if first else {}
        for i, (tid, name, artist, pop) in first.items():
            if uri := uris.get(tid):
                found[i] = (uri, name, artist, "", int(pop or 0))

    rows, seen = [], set()
    for i in sorted(found):
        if found[i][0] not in seen:
            seen.add(found[i][0])
            rows.append(found[i])
    unmatched = [src[i] for i in range(len(src)) if i not in found]
    return rows, unmatched, loose
