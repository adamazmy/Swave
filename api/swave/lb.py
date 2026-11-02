"""ListenBrainz helpers: reading the remote Spark dump, song keys, sessions.

The full Spark dump is one uncompressed .tar of time-ordered Parquet files on data.metabrainz.org.
The server supports HTTP range requests, so we list the tar from its END (each Parquet footer gives
the file's length, the 1 MB before a file holds its tar header and the previous file's footer) and
read single columns of single files without downloading the dump.
"""

from __future__ import annotations

import io
import struct
from typing import NamedTuple

import httpx
import numpy as np
import pyarrow.parquet as pq

from swave.matching import song_hash  # noqa: F401 (re-exported)

DUMP_URL = ("https://data.metabrainz.org/pub/musicbrainz/listenbrainz/fullexport/"
            "listenbrainz-dump-2723-20261015-000002-full/"
            "listenbrainz-spark-dump-2723-20261015-000002-full.tar")
COLUMNS = ["user_id", "listened_at", "artist_name", "recording_name", "recording_mbid"]
SESSION_GAP = 30 * 60  # seconds
MIN_SESSION = 3
MAX_SESSION = 200  # longer sessions are cut into chunks (bounds co-occurrence pairs)
BUCKETS = 64  # listens are partitioned by user so a bucket can be sessionised in memory
WINDOW = 1 << 20


# Tables the API needs even before the first `make lb-build` (empty until then). `lb_build`
# replaces lb_tracks / neighbours_lb; `lb_spotify` (resolved Spotify ids, misses cached too) is
# app-owned and survives rebuilds because it is keyed by `song`. `all_tracks` = every track that has
# a Spotify URI: MPD tracks + resolved ListenBrainz-only tracks (album unknown, popularity = pop_est).
SCHEMA = """
CREATE TABLE IF NOT EXISTS lb_tracks (
    track_id int PRIMARY KEY, song bigint NOT NULL UNIQUE, name text NOT NULL, artist text NOT NULL,
    recording_mbid text, listeners int NOT NULL, pop_est int NOT NULL);
CREATE TABLE IF NOT EXISTS neighbours_lb (
    track_id int NOT NULL, neighbour_id int NOT NULL, sim real NOT NULL, co_count int NOT NULL);
CREATE TABLE IF NOT EXISTS lb_spotify (
    song bigint PRIMARY KEY, track_uri text, checked_at timestamptz NOT NULL);
CREATE INDEX IF NOT EXISTS lb_spotify_uri ON lb_spotify (track_uri);
"""
VIEW = """
CREATE OR REPLACE VIEW all_tracks AS
    SELECT track_uri, track_id, name, artist, album, playlist_count FROM tracks
    UNION ALL
    SELECT s.track_uri, l.track_id, l.name, l.artist, '' AS album, l.pop_est AS playlist_count
    FROM lb_tracks l JOIN lb_spotify s USING (song) WHERE s.track_uri IS NOT NULL;
"""


class Member(NamedTuple):
    name: str
    offset: int  # byte offset of the file's data inside the tar
    size: int


class Remote(io.RawIOBase):
    """Seekable read-only view of bytes [offset, offset + size) of a remote file."""

    def __init__(self, client: httpx.Client, url: str, offset: int, size: int):
        self.c, self.url, self.base, self.size, self.pos = client, url, offset, size, 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, pos: int, whence: int = 0) -> int:
        self.pos = {0: pos, 1: self.pos + pos, 2: self.size + pos}[whence]
        return self.pos

    def read(self, n: int = -1) -> bytes:
        n = self.size - self.pos if n is None or n < 0 else min(n, self.size - self.pos)
        if n <= 0:
            return b""
        data = get_range(self.c, self.url, self.base + self.pos, self.base + self.pos + n - 1)
        self.pos += len(data)
        return data

    def readinto(self, b) -> int:  # noqa: ANN001
        d = self.read(len(b))
        b[:len(d)] = d
        return len(d)


class _Tail(io.RawIOBase):
    """A virtual file of `size` bytes of which only the last len(buf) are known (for footers)."""

    def __init__(self, buf: bytes, size: int):
        self.buf, self.size, self.pos = buf, size, 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, pos: int, whence: int = 0) -> int:
        self.pos = {0: pos, 1: self.pos + pos, 2: self.size + pos}[whence]
        return self.pos

    def read(self, n: int = -1) -> bytes:
        lo = self.size - len(self.buf)
        if self.pos < lo:
            raise OSError("parquet footer is larger than the read window")
        n = self.size - self.pos if n < 0 else min(n, self.size - self.pos)
        d = self.buf[self.pos - lo:self.pos - lo + n]
        self.pos += len(d)
        return d

    def readinto(self, b) -> int:  # noqa: ANN001
        d = self.read(len(b))
        b[:len(d)] = d
        return len(d)


def get_range(client: httpx.Client, url: str, a: int, b: int, tries: int = 8) -> bytes:
    for i in range(tries):
        try:
            r = client.get(url, headers={"Range": f"bytes={a}-{b}"})
            r.raise_for_status()
            if len(r.content) != b - a + 1:
                raise httpx.HTTPError("short read")
            return r.content
        except httpx.HTTPError:
            if i == tries - 1:
                raise
    raise AssertionError("unreachable")


def parquet_length(tail: bytes, end: int) -> int:
    """Length of the Parquet file whose last byte is at `end - 1`; `tail` = bytes before `end`."""
    md = pq.read_metadata(_Tail(tail, end))
    footer = struct.unpack("<I", tail[-8:-4])[0]
    chunk_end = 0
    for g in range(md.num_row_groups):
        for j in range(md.num_columns):
            col = md.row_group(g).column(j)
            first = (col.dictionary_page_offset
                     if col.has_dictionary_page and col.dictionary_page_offset else col.data_page_offset)
            chunk_end = max(chunk_end, first + col.total_compressed_size)
    return chunk_end + footer + 8


def walk_back(client: httpx.Client, url: str, after: Member | None = None):
    """Yield the tar's Parquet members from the last one backwards (or from the one before
    `after`). One ~1 MB range request per member."""
    if after is None:
        total = int(client.head(url).headers["content-length"])
        lo = max(0, total - WINDOW)
        buf = get_range(client, url, lo, total - 1)
    else:
        lo = max(0, after.offset - 512 - WINDOW)
        buf = get_range(client, url, lo, after.offset - 513)
    while True:
        p = buf.rfind(b"PAR1")
        if p < 0 or lo + p < 4:
            return
        end = lo + p + 4
        length = parquet_length(buf[:end - lo], end)
        start = end - length
        lo = max(0, start - WINDOW)
        buf = get_range(client, url, lo, start - 1)
        hdr = buf[-512:]
        name = hdr[:100].rstrip(b"\0").decode()
        size = int(hdr[124:136].rstrip(b"\0 ").decode(), 8)
        if hdr[257:262] != b"ustar" or size != length or not name.endswith(".parquet"):
            raise ValueError(f"unexpected tar layout before offset {start}: {name!r}")
        yield Member(name, start, size)
        buf = buf[:-512]


# ── sessions ───────────────────────────────────────────────────────────────────────────

class Sessions(NamedTuple):
    sid: np.ndarray  # session id per row (dense from 0), rows grouped by session in play order
    song: np.ndarray  # one row per distinct song per session
    user: np.ndarray  # per session
    start: np.ndarray  # per session: first listen (unix seconds)


def sessions(user: np.ndarray, ts: np.ndarray, song: np.ndarray,
             gap: int = SESSION_GAP, min_len: int = MIN_SESSION,
             max_len: int = MAX_SESSION) -> Sessions:
    """Split listens into sessions, one row per distinct song per session.

    A new session starts at a new user or after a gap > `gap` seconds. Repeats of a song within
    a session are collapsed (first play kept); sessions longer than `max_len` distinct songs are
    cut into chunks of `max_len`; (chunked) sessions with < `min_len` songs are dropped.
    """
    e = np.array([], dtype=np.int64)
    if len(user) == 0:
        return Sessions(e, e, e, e)
    order = np.lexsort((ts, user))
    user, ts, song = user[order], ts[order], song[order]
    new = np.ones(len(user), dtype=bool)
    new[1:] = (user[1:] != user[:-1]) | (ts[1:] - ts[:-1] > gap)
    sid = np.cumsum(new) - 1
    # collapse repeats: keep the first (session, song) play
    idx = np.lexsort((song, sid))  # stable: the earliest play of a (session, song) comes first
    s_sid, s_song = sid[idx], song[idx]
    dup = np.r_[False, (s_sid[1:] == s_sid[:-1]) & (s_song[1:] == s_song[:-1])]
    first = np.sort(idx[~dup])
    sid, song, user, ts = sid[first], song[first], user[first], ts[first]
    # cut long sessions into chunks of max_len
    starts = np.flatnonzero(np.r_[True, sid[1:] != sid[:-1]])
    pos = np.arange(len(sid)) - np.repeat(starts, np.diff(np.r_[starts, len(sid)]))
    chunk = np.cumsum(np.r_[True, (sid[1:] != sid[:-1]) | (pos[1:] % max_len == 0)]) - 1
    counts = np.bincount(chunk)
    keep = counts[chunk] >= min_len
    chunk, song, user, ts = chunk[keep], song[keep], user[keep], ts[keep]
    head = np.r_[True, chunk[1:] != chunk[:-1]][:len(chunk)]
    dense = np.cumsum(head) - 1
    return Sessions(dense.astype(np.int64), song, user[head].astype(np.int64),
                    ts[head].astype(np.int64))
