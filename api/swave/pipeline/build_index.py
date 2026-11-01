"""make build-index: compute top-k neighbours for every track into the `neighbours` table."""

from __future__ import annotations

import argparse
import io
import time

import pandas as pd

from swave import cooc
from swave.config import connect
from swave.pipeline.evaluate import git_hash


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--method", choices=cooc.METHODS, default="cosine")
    ap.add_argument("--k", type=int, default=200)
    ap.add_argument("--min-co", type=int, default=2)
    args = ap.parse_args()
    t0 = time.time()

    with connect() as conn:
        c = cooc.load_training(conn)
        parts = cooc.chunks(c)
        print(f"{c.Xt.shape[0]:,} tracks in {len(parts)} chunks "
              f"(method={args.method}, k={args.k}, min_co={args.min_co})", flush=True)

        conn.execute("SET synchronous_commit = off")
        # Created and filled in one transaction, so with wal_level=minimal the COPY skips the WAL.
        conn.execute("""
            DROP TABLE IF EXISTS neighbours_new;
            CREATE TABLE neighbours_new (
                track_id int NOT NULL, neighbour_id int NOT NULL, sim real NOT NULL, co_count int NOT NULL
            )""")
        rows = 0
        with conn.cursor().copy(
            "COPY neighbours_new (track_id, neighbour_id, sim, co_count) FROM STDIN"
        ) as copy:
            for i, part in enumerate(parts, 1):
                src, nbr, sim, co = cooc.neighbours(c, part, args.method, args.k, args.min_co)
                buf = io.StringIO()
                pd.DataFrame({"s": src, "n": nbr, "sim": sim, "co": co}).to_csv(
                    buf, sep="\t", header=False, index=False, float_format="%.6g"
                )
                copy.write(buf.getvalue())
                rows += len(src)
                if i % 20 == 0 or i == len(parts):
                    elapsed = time.time() - t0
                    eta = elapsed / i * (len(parts) - i)
                    print(f"  chunk {i}/{len(parts)}: {rows:,} rows, {elapsed / 60:.1f} min, "
                          f"ETA {eta / 60:.0f} min", flush=True)

        print("indexing…", flush=True)
        conn.execute("""
            CREATE INDEX neighbours_new_track ON neighbours_new (track_id);
            DROP TABLE IF EXISTS neighbours, index_meta;
            ALTER TABLE neighbours_new RENAME TO neighbours;
            ALTER INDEX neighbours_new_track RENAME TO neighbours_track;
            CREATE TABLE index_meta (method text, k int, min_co int, built_at timestamptz, git text);
        """)
        conn.execute(
            "INSERT INTO index_meta VALUES (%s, %s, %s, now(), %s)",
            (args.method, args.k, args.min_co, git_hash()),
        )
        conn.commit()
        conn.execute("ANALYZE neighbours")

    print(f"build-index done: {rows:,} neighbour rows in {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()
