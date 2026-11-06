"""make lb-install RUN=<name>: put a candidate index from `make lb-index` (data/lb/runs/<name>/)
into the app's ListenBrainz tables: neighbours_lb, lb_tracks, lb_holdout, lb_meta.

  1. load into *_new tables while the current ones keep serving the app
  2. index them, then swap in ONE transaction: current → *_old, *_new → current, all_tracks view
     recreated on the new lb_tracks (`lb_spotify`, keyed by song hash, carries over untouched)
  3. the replaced tables are KEPT as *_old, nothing is dropped:
       --rollback   swap *_old back in (the rejected index becomes *_old)
       --drop-old   drop the *_old tables once the new index is approved

lb_holdout = the run's time-split hold-out (runs/holdout.npz), mapped to the run's track ids.
"""

from __future__ import annotations

import argparse
import io
import json
import time

import numpy as np
import pandas as pd

from swave import lb
from swave.config import connect
from swave.pipeline import artist_index
from swave.pipeline.evaluate import git_hash
from swave.pipeline.lb_index import HOLDOUT, RUNS

# table → its indexes, named <table><tag>_<suffix> (tag: "" live, "_new", "_old", "_tmp")
TABLES = {"neighbours_lb": ["track"], "lb_tracks": ["search", "song", "pkey"],
          "lb_holdout": [], "lb_meta": []}
ROWS_PER_COPY = 2_000_000


def log(msg: str, t0: float) -> None:
    print(f"[{(time.time() - t0) / 60:5.1f} min] {msg}", flush=True)


def exists(conn, name: str) -> bool:  # noqa: ANN001
    return conn.execute("SELECT to_regclass(%s)", (name,)).fetchone()[0] is not None


def rename(conn, frm: str, to: str) -> None:  # noqa: ANN001
    """Rename every table (and its indexes) tagged `frm` to tag `to`; missing ones are skipped."""
    for t, idx in TABLES.items():
        if not exists(conn, t + frm):
            continue
        conn.execute(f"ALTER TABLE {t}{frm} RENAME TO {t}{to}")
        for i in idx:
            conn.execute(f"ALTER INDEX IF EXISTS {t}{frm}_{i} RENAME TO {t}{to}_{i}")


def normalise_pkey(conn) -> None:  # noqa: ANN001
    """lb-build leaves the live primary key named lb_tracks_new_pkey, which would clash with the
    new table's: give it the live name first."""
    row = conn.execute("""SELECT conname FROM pg_constraint
                          WHERE conrelid = to_regclass('lb_tracks') AND contype = 'p'""").fetchone()
    if row and row[0] != "lb_tracks_pkey":
        conn.execute(f"ALTER INDEX {row[0]} RENAME TO lb_tracks_pkey")


def holdout_rows(song: np.ndarray, tid: np.ndarray) -> list[tuple[int, int, int]]:
    """(sess, pos, track_id) of the shared time hold-out; songs outside the run's vocabulary dropped."""
    z = np.load(HOLDOUT)
    o = np.argsort(song)
    song, tid = song[o], tid[o]
    rows = []
    for s in range(len(z["offsets"]) - 1):
        songs = z["songs"][z["offsets"][s]:z["offsets"][s + 1]]
        pos = np.minimum(np.searchsorted(song, songs), len(song) - 1)
        ids = tid[pos][song[pos] == songs]
        rows += [(s, p, int(t)) for p, t in enumerate(ids)]
    return rows


def load(conn, run: str, t0: float) -> dict:  # noqa: ANN001
    d = RUNS / run
    meta = json.loads((d / "meta.json").read_text())
    t = pd.read_parquet(d / "tracks.parquet")
    n_mpd = conn.execute("SELECT max(track_id) + 1 FROM tracks").fetchone()[0]
    new = t[t["track_id"] >= n_mpd]
    if new["name"].isna().any() or len(new) != meta["new"]:
        raise SystemExit(f"{run}: new-track ids don't start at {n_mpd}; the MPD tracks changed since "
                         "the run was built, rebuild it")
    conn.execute("SET synchronous_commit = off")
    conn.execute("""
        DROP TABLE IF EXISTS neighbours_lb_new, lb_tracks_new, lb_holdout_new, lb_meta_new;
        CREATE TABLE neighbours_lb_new (track_id int NOT NULL, neighbour_id int NOT NULL,
                                        sim real NOT NULL, co_count int NOT NULL);
        CREATE TABLE lb_tracks_new (track_id int NOT NULL, song bigint NOT NULL,
            name text NOT NULL, artist text NOT NULL, recording_mbid text,
            listeners int NOT NULL, pop_est int NOT NULL);
        CREATE TABLE lb_holdout_new (sess int NOT NULL, pos int NOT NULL, track_id int NOT NULL);
        CREATE TABLE lb_meta_new (built_at timestamptz, git text, stats jsonb);
    """)
    with conn.cursor().copy("COPY lb_tracks_new FROM STDIN") as copy:
        for r in new.itertuples(index=False):
            copy.write_row((int(r.track_id), int(r.song), str(r.name), str(r.artist),
                            r.mbid if isinstance(r.mbid, str) else None, int(r.listeners),
                            int(r.pop_est)))
    log(f"lb_tracks_new: {len(new):,} rows", t0)
    hold = holdout_rows(t["song"].to_numpy(), t["track_id"].to_numpy())
    with conn.cursor().copy("COPY lb_holdout_new FROM STDIN") as copy:
        for row in hold:
            copy.write_row(row)
    log(f"lb_holdout_new: {len(hold):,} rows", t0)

    cols = [np.memmap(d / f"nbr_{c}.bin", dtype=dt, mode="r") for c, dt in
            (("src", np.int32), ("nbr", np.int32), ("sim", np.float32), ("co", np.int32))]
    n = len(cols[0])
    assert n == meta["neighbour_rows"] and all(len(c) == n for c in cols)
    with conn.cursor().copy("COPY neighbours_lb_new FROM STDIN") as copy:
        for a in range(0, n, ROWS_PER_COPY):
            s, b, sim, co = (c[a:a + ROWS_PER_COPY] for c in cols)
            buf = io.StringIO()
            # %.9g: float32 round-trips exactly into Postgres `real`
            pd.DataFrame({"s": s, "n": b, "sim": sim, "co": co}).to_csv(
                buf, sep="\t", header=False, index=False, float_format="%.9g")
            copy.write(buf.getvalue())
            if (a // ROWS_PER_COPY) % 10 == 9 or a + ROWS_PER_COPY >= n:
                log(f"  neighbours_lb_new: {min(a + ROWS_PER_COPY, n):,}/{n:,} rows", t0)
    log("indexing…", t0)
    conn.execute("""
        ALTER TABLE lb_tracks_new ADD CONSTRAINT lb_tracks_new_pkey PRIMARY KEY (track_id);
        CREATE UNIQUE INDEX lb_tracks_new_song ON lb_tracks_new (song);
        CREATE INDEX lb_tracks_new_search ON lb_tracks_new
            USING gin (lower(name || ' ' || artist) gin_trgm_ops);
        CREATE INDEX neighbours_lb_new_track ON neighbours_lb_new (track_id);
    """)
    stats = {k: v for k, v in meta.items() if k not in ("steps", "config")}
    stats.update(run=run, k=meta["config"]["k"], min_co=meta["config"]["min_co"],
                 min_listeners=meta["config"]["min_listeners"],
                 half_life=meta["config"]["half_life"], built_git=meta["git"])
    conn.execute("INSERT INTO lb_meta_new VALUES (now(), %s, %s)", (git_hash(), json.dumps(stats)))
    conn.commit()
    return stats


def swap_in(conn) -> None:  # noqa: ANN001
    """One transaction: live → *_old, *_new → live, view on the new lb_tracks."""
    rename(conn, "", "_old")
    rename(conn, "_new", "")
    conn.execute(lb.SCHEMA)  # lb_spotify etc. if this DB never ran the API
    conn.execute(lb.VIEW)
    conn.commit()


def rollback(conn) -> None:  # noqa: ANN001
    if not exists(conn, "neighbours_lb_old"):
        raise SystemExit("no *_old tables to roll back to")
    rename(conn, "", "_tmp")
    rename(conn, "_old", "")
    rename(conn, "_tmp", "_old")
    conn.execute(lb.VIEW)
    conn.commit()


def drop_old(conn) -> None:  # noqa: ANN001
    conn.execute("DROP TABLE IF EXISTS " + ", ".join(t + "_old" for t in TABLES))
    conn.commit()


def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--run", help="candidate index in data/lb/runs/ to install")
    g.add_argument("--rollback", action="store_true", help="swap the *_old tables back in")
    g.add_argument("--drop-old", action="store_true", help="drop the *_old tables")
    args = ap.parse_args()
    t0 = time.time()
    with connect() as conn:
        if args.rollback:
            rollback(conn)
            log("rolled back: the previous index is live again, the replaced one is *_old", t0)
            return
        if args.drop_old:
            drop_old(conn)
            log("dropped the *_old tables", t0)
            return
        if exists(conn, "neighbours_lb_old"):
            raise SystemExit("*_old tables exist: --drop-old (or --rollback) before installing another run")
        if not (RUNS / args.run / "meta.json").exists():
            raise SystemExit(f"no finished run {args.run} in {RUNS}")
        normalise_pkey(conn)
        conn.commit()
        stats = load(conn, args.run, t0)
        swap_in(conn)
        conn.execute("ANALYZE neighbours_lb")
        conn.execute("ANALYZE lb_tracks")
        conn.commit()
        artist_index.build(conn)  # LB track ids changed: the related-artists lookup follows
    log(f"lb-install done: {args.run} is live, previous index kept as *_old ({stats['neighbour_rows']:,} "
        f"neighbour rows, {stats['vocab']:,} songs)", t0)


if __name__ == "__main__":
    main()
