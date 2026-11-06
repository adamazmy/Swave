"""make artist-index: `artist_tracks(artist_key, track_id, pop)`, the most popular songs per
primary artist in the catalogue (MPD `tracks` + ListenBrainz `lb_tracks`), for the
related-artists scorer. `artist_key` = `previews.loose_artist(artist)` ("X feat. Y" → "x").
Rebuild after ingest or lb-install (track ids change); takes ~1 min.
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd

from swave import previews
from swave.config import connect

PER_ARTIST = 30  # the scorer uses the top 20; a few spare for songs without a Spotify match


def build(conn) -> tuple[int, int]:  # noqa: ANN001
    """Rebuild `artist_tracks` from tracks + lb_tracks; returns (artists, rows)."""
    rows = conn.execute("""SELECT track_id, artist, playlist_count FROM tracks
                           UNION ALL SELECT track_id, artist, pop_est FROM lb_tracks""").fetchall()
    df = pd.DataFrame(rows, columns=["track_id", "artist", "pop"])
    keys = {a: previews.loose_artist(a) for a in df["artist"].unique()}
    df["artist_key"] = df["artist"].map(keys)
    df = df[df["artist_key"] != ""]
    df = df.sort_values(["artist_key", "pop", "track_id"], ascending=[True, False, True])
    df = df.groupby("artist_key", sort=False).head(PER_ARTIST)
    conn.execute("""DROP TABLE IF EXISTS artist_tracks_new;
        CREATE TABLE artist_tracks_new (artist_key text NOT NULL, track_id int NOT NULL,
                                        pop int NOT NULL)""")
    with conn.cursor().copy("COPY artist_tracks_new FROM STDIN") as copy:
        for k, t, p in zip(df["artist_key"], df["track_id"], df["pop"]):
            copy.write_row((k, int(t), int(np.int64(p))))
    conn.execute("""CREATE INDEX artist_tracks_new_key ON artist_tracks_new (artist_key);
        DROP TABLE IF EXISTS artist_tracks;
        ALTER TABLE artist_tracks_new RENAME TO artist_tracks;
        ALTER INDEX artist_tracks_new_key RENAME TO artist_tracks_key;
        ANALYZE artist_tracks;""")
    conn.commit()
    return int(df["artist_key"].nunique()), len(df)


def main() -> None:
    t0 = time.time()
    with connect() as conn:
        artists, rows = build(conn)
    print(f"artist-index: {artists:,} artists, {rows:,} rows in {time.time() - t0:.0f}s", flush=True)

if __name__ == "__main__":
    main()
