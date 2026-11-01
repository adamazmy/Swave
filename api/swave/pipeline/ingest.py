"""make ingest: stream the MPD slices (folder or zip) into Postgres, prune rare tracks.

Tables created: tracks, playlist_tracks, eval_holdout. Drops the derived neighbours table and
the training-matrix cache, because track ids change on every ingest.
"""

from __future__ import annotations

import json
import shutil
import time
import zipfile
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

from swave.config import CACHE_DIR, connect, mpd_path

MIN_PLAYLISTS = 5
HOLDOUT_SIZE = 1000
HOLDOUT_MIN_TRACKS = 10

Meta = dict[str, tuple[str, str, str]]


def iter_slices(path: Path) -> Iterator[dict]:
    """Yield one parsed slice at a time from a folder of mpd.slice.*.json files or the MPD zip."""
    if path.is_dir():
        zips = sorted(path.glob("*.zip"))
        files = sorted(path.rglob("mpd.slice.*.json"))
        if files:
            for f in files:
                with open(f, encoding="utf-8") as fh:
                    yield json.load(fh)
            return
        if not zips:
            raise SystemExit(f"No mpd.slice.*.json or .zip found in {path}")
        path = zips[0]
    with zipfile.ZipFile(path) as z:
        for name in sorted(n for n in z.namelist() if "mpd.slice." in n and n.endswith(".json")):
            with z.open(name) as fh:
                yield json.load(fh)


def slice_rows(data: dict, counts: Counter, meta: Meta) -> str:
    """Tab-separated (pid, track_uri) rows for one slice, de-duplicated within each playlist.

    Updates `counts` (playlists per track) and `meta` (name, artist, album) in place.
    """
    lines = []
    for p in data["playlists"]:
        pid = p["pid"]
        uris = set()
        for t in p["tracks"]:
            uri = t["track_uri"]
            uris.add(uri)
            if uri not in meta:
                meta[uri] = (t["track_name"], t["artist_name"], t["album_name"])
        counts.update(uris)
        lines.extend(f"{pid}\t{u}\n" for u in uris)
    return "".join(lines)


def kept_tracks(counts: Counter, min_playlists: int = MIN_PLAYLISTS) -> list[tuple[str, int]]:
    """Tracks in at least `min_playlists` playlists, most popular first (index = track_id)."""
    kept = [(u, c) for u, c in counts.items() if c >= min_playlists]
    kept.sort(key=lambda x: (-x[1], x[0]))
    return kept


def main() -> None:
    t0 = time.time()
    path = mpd_path()
    counts: Counter = Counter()
    meta: Meta = {}
    n_slices = n_playlists = 0

    with connect() as conn:
        conn.execute("SET synchronous_commit = off")
        conn.execute("""
            DROP TABLE IF EXISTS neighbours, index_meta, eval_holdout, playlist_tracks, tracks,
                                 staging_pt;
            CREATE TABLE staging_pt (pid int NOT NULL, track_uri text NOT NULL);
        """)
        with conn.cursor().copy("COPY staging_pt (pid, track_uri) FROM STDIN") as copy:
            for data in iter_slices(path):
                copy.write(slice_rows(data, counts, meta))
                n_slices += 1
                n_playlists += len(data["playlists"])
                if n_slices % 50 == 0:
                    print(f"  {n_slices} slices, {n_playlists:,} playlists, "
                          f"{len(counts):,} tracks ({time.time() - t0:.0f}s)", flush=True)

        kept = kept_tracks(counts)
        print(f"parsed {n_slices} slices / {n_playlists:,} playlists; tracks {len(counts):,} → "
              f"{len(kept):,} after pruning < {MIN_PLAYLISTS} playlists", flush=True)

        conn.execute("""
            CREATE TABLE tracks (
                track_uri      text PRIMARY KEY,
                track_id       int  NOT NULL UNIQUE,
                name           text NOT NULL,
                artist         text NOT NULL,
                album          text NOT NULL,
                playlist_count int  NOT NULL
            )""")
        with conn.cursor().copy(
            "COPY tracks (track_uri, track_id, name, artist, album, playlist_count) FROM STDIN"
        ) as copy:
            for tid, (uri, c) in enumerate(kept):
                name, artist, album = meta[uri]
                copy.write_row((uri, tid, name, artist, album, c))
        del meta, counts, kept

        print("filtering playlist_tracks…", flush=True)
        conn.execute("""
            CREATE TABLE playlist_tracks AS
                SELECT s.pid, s.track_uri FROM staging_pt s JOIN tracks t USING (track_uri);
            DROP TABLE staging_pt;
            CREATE INDEX playlist_tracks_pid ON playlist_tracks (pid);
            CREATE EXTENSION IF NOT EXISTS pg_trgm;
            CREATE INDEX tracks_search ON tracks USING gin (lower(name || ' ' || artist) gin_trgm_ops);
            CREATE INDEX tracks_artist ON tracks (lower(artist));  -- playlist import matching
        """)
        # Deterministic hold-out: 1,000 pseudo-random playlists with enough tracks to hide some.
        conn.execute(
            """CREATE TABLE eval_holdout AS
                   SELECT pid FROM playlist_tracks GROUP BY pid HAVING count(*) >= %s
                   ORDER BY md5(pid::text) LIMIT %s""",
            (HOLDOUT_MIN_TRACKS, HOLDOUT_SIZE),
        )
        conn.execute("ALTER TABLE eval_holdout ADD PRIMARY KEY (pid)")
        n_pt = conn.execute("SELECT count(*) FROM playlist_tracks").fetchone()[0]
        conn.commit()
        conn.execute("ANALYZE tracks; ANALYZE playlist_tracks;")

    shutil.rmtree(CACHE_DIR, ignore_errors=True)
    print(f"ingest done: {n_pt:,} playlist_tracks rows in {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()
