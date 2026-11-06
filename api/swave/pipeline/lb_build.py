"""make lb-build: data/lb/ listens → sessions → vocabulary → matching → `neighbours_lb`.

Steps (each cached under data/lb/build/, delete that folder to redo):
  1. vocabulary: songs with ≥ --min-listeners distinct users (default 20: 1.5M songs, 90% of
     listening; ≥ 10 gave 2.7M, too big for top-k in RAM/Postgres; owner's call 2026-11-02).
     Buckets have disjoint users, so per-bucket counts just add
  2. sessions per bucket (`lb.sessions`: 30-min gap, repeats collapsed, < 3 songs dropped),
     restricted to vocabulary songs
  3. names + matching: each vocabulary song → an MPD track (exact `song_key`, then same artist +
     title prefix ≥ 60%) or a NEW track in `lb_tracks` (ids continue after MPD's)
  4. LB eval hold-out: --holdout sessions from the last 30 days with ≥ 10 songs → `lb_holdout`,
     excluded from the matrix
  5. session × track matrix → `cooc.neighbours` (cosine, top-k) → `neighbours_lb`
"""

from __future__ import annotations

import argparse
import io
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import scipy.sparse as sp

from swave import cooc, lb
from swave.config import connect
from swave.deezer import norm
from swave.matching import PREFIX_MIN_RATIO
from swave.pipeline.evaluate import git_hash
from swave.pipeline.lb_ingest import LB_DIR

BUILD = LB_DIR / "build"
HOLDOUT_DAYS = 30
HOLDOUT_MIN_LEN = 10


def log(msg: str, t0: float) -> None:
    print(f"[{(time.time() - t0) / 60:5.1f} min] {msg}", flush=True)


def member_dirs() -> list[Path]:
    dirs = [d for d in (LB_DIR / "listens").iterdir() if (d / "done").exists()]
    return sorted(dirs, key=lambda d: lb.file_key(d.name))


def load_bucket(dirs: list[Path], b: int, cols=("user", "ts", "song")) -> dict[str, np.ndarray]:
    out: dict[str, list[np.ndarray]] = {c: [] for c in cols}
    for d in dirs:
        lo, hi = np.load(d / "bounds.npy")[[b, b + 1]]
        if hi > lo:
            for c in cols:
                out[c].append(np.load(d / f"{c}.npy", mmap_mode="r")[lo:hi])
    return {c: (np.concatenate(v) if v else np.array([], np.int64)) for c, v in out.items()}


def merge_counts(keys: np.ndarray, counts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    u, inv = np.unique(keys, return_inverse=True)
    return u, np.bincount(inv, weights=counts).astype(np.int64)


# ── 1. vocabulary ──────────────────────────────────────────────────────────────────────

def vocabulary(dirs: list[Path], min_listeners: int, t0: float) -> tuple[np.ndarray, np.ndarray]:
    path = BUILD / "vocab.npz"
    if path.exists():
        z = np.load(path)
        if int(z["min_listeners"]) <= min_listeners:  # the cache holds every song above its own min
            keep = z["listeners"] >= min_listeners
            return z["song"][keep], z["listeners"][keep]
    songs, counts = np.array([], np.int64), np.array([], np.int64)
    n_listens = 0
    for b in range(lb.BUCKETS):
        d = load_bucket(dirs, b, ("user", "song"))
        n_listens += len(d["user"])
        o = np.lexsort((d["user"], d["song"]))
        so, uo = d["song"][o], d["user"][o]
        first = np.r_[True, (so[1:] != so[:-1]) | (uo[1:] != uo[:-1])]
        s, c = np.unique(so[first], return_counts=True)  # distinct listeners per song
        songs, counts = merge_counts(np.concatenate([songs, s]), np.concatenate([counts, c]))
        if b % 8 == 7:
            log(f"  vocabulary: bucket {b + 1}/{lb.BUCKETS}, {n_listens:,} listens, "
                f"{len(songs):,} distinct songs", t0)
    keep = counts >= min_listeners
    BUILD.mkdir(parents=True, exist_ok=True)
    np.savez(path, song=songs[keep], listeners=counts[keep], n_listens=n_listens,
             n_songs=len(songs), min_listeners=min_listeners)
    return songs[keep], counts[keep]


# ── 2. sessions ────────────────────────────────────────────────────────────────────────

def bucket_sessions(dirs: list[Path], vocab: np.ndarray, t0: float) -> None:
    """Per bucket → build/sessions_<b>.npz: vocab index per row, session id, session user/start."""
    for b in range(lb.BUCKETS):
        path = BUILD / f"sessions_{b}.npz"
        if path.exists():
            continue
        d = load_bucket(dirs, b)
        s = lb.sessions(d["user"], d["ts"], d["song"], min_len=1)
        pos = np.searchsorted(vocab, s.song)
        pos[pos == len(vocab)] = 0
        inv = vocab[pos] == s.song
        sid, idx = s.sid[inv], pos[inv]
        n = np.bincount(sid, minlength=len(s.user))
        keep = n[sid] >= lb.MIN_SESSION
        sid, idx = sid[keep], idx[keep]
        kept = np.flatnonzero(n >= lb.MIN_SESSION)
        _, sid = np.unique(sid, return_inverse=True)  # dense again
        np.savez(path, sid=sid.astype(np.int32), song=idx.astype(np.int32),
                 user=s.user[kept], start=s.start[kept])
        if b % 8 == 7:
            log(f"  sessions: bucket {b + 1}/{lb.BUCKETS}", t0)


def load_sessions(b: int) -> dict[str, np.ndarray]:
    z = np.load(BUILD / f"sessions_{b}.npz")
    return {k: z[k] for k in z.files}


# ── 3. names + matching ────────────────────────────────────────────────────────────────

def vocab_names(vocab: np.ndarray, t0: float, path: Path | None = None) -> pd.DataFrame:
    """Most played spelling (+ a recording MBID) of each vocabulary song, from the merged names
    table (`make lb-names`, data/lb/names_all/)."""
    from swave.pipeline import lb_names

    path = path or BUILD / "names.parquet"
    if path.exists():
        return pd.read_parquet(path)
    if lb_names.inputs():
        raise SystemExit("data/lb/names/ has unmerged per-file names: run `make lb-names` first")
    want = pa.array(vocab)
    parts = []
    for f in sorted(lb_names.ALL.glob("s*.parquet")):
        t = pq.read_table(f, columns=["song", "artist", "title", "mbid", "plays"])
        parts.append(t.filter(pc.is_in(t["song"], value_set=want)).to_pandas())
    log("  names: read from names_all", t0)
    names = pd.concat(parts).set_index("song").reindex(vocab).reset_index()
    path.parent.mkdir(parents=True, exist_ok=True)
    names.to_parquet(path, index=False)
    return names


def match(conn, names: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:  # noqa: ANN001
    """→ (MPD track_id or -1 per vocabulary song, how: 0 new, 1 exact, 2 loose)."""
    mpd = pd.DataFrame(conn.execute(
        "SELECT track_id, name, artist, playlist_count FROM tracks").fetchall(),
        columns=["track_id", "name", "artist", "pc"])
    mpd["song"] = [lb.song_hash(n, a) for n, a in zip(mpd["name"], mpd["artist"])]
    best = mpd.sort_values("pc", ascending=False).drop_duplicates("song").set_index("song")
    exact = best["track_id"].reindex(names["song"]).to_numpy()
    tid = np.where(np.isnan(exact), -1, exact).astype(np.int64)
    how = (tid >= 0).astype(np.int8)

    by_artist: dict[str, list[tuple[str, int, int]]] = {}
    for t, n, a, p in mpd[["track_id", "name", "artist", "pc"]].itertuples(index=False):
        by_artist.setdefault(norm(a), []).append((norm(n), t, p))
    for i in np.flatnonzero(tid < 0):
        title, artist = names.at[i, "title"], names.at[i, "artist"]
        if not isinstance(title, str) or not isinstance(artist, str):
            continue
        t = norm(title)
        cands = [(p, tr) for n, tr, p in by_artist.get(norm(artist), [])
                 if t and n and _prefix(t, n)]
        if cands:
            tid[i], how[i] = max(cands)[1], 2
    return tid, how


def _prefix(a: str, b: str) -> bool:
    short, long_ = sorted((a, b), key=len)
    return long_.startswith(short) and len(short) / len(long_) >= PREFIX_MIN_RATIO


SWAP = """
    CREATE INDEX neighbours_lb_new_track ON neighbours_lb_new (track_id);
    CREATE INDEX lb_tracks_new_search ON lb_tracks_new
        USING gin (lower(name || ' ' || artist) gin_trgm_ops);
    CREATE UNIQUE INDEX lb_tracks_new_song ON lb_tracks_new (song);
    -- CASCADE: the all_tracks view depends on lb_tracks; it is recreated right below
    DROP TABLE IF EXISTS neighbours_lb, lb_tracks, lb_holdout, lb_meta CASCADE;
    ALTER TABLE neighbours_lb_new RENAME TO neighbours_lb;
    ALTER INDEX neighbours_lb_new_track RENAME TO neighbours_lb_track;
    ALTER TABLE lb_tracks_new RENAME TO lb_tracks;
    ALTER INDEX lb_tracks_new_search RENAME TO lb_tracks_search;
    ALTER INDEX lb_tracks_new_song RENAME TO lb_tracks_song;
    ALTER TABLE lb_holdout_new RENAME TO lb_holdout;
    CREATE TABLE lb_meta (built_at timestamptz, git text, stats jsonb);
"""


def install(conn, stats: dict) -> None:  # noqa: ANN001
    """Swap the freshly built *_new tables in (one transaction) and recreate the all_tracks view."""
    conn.execute(SWAP)
    conn.execute(lb.SCHEMA)  # lb_spotify etc. if this DB never ran the API
    conn.execute(lb.VIEW)
    conn.execute("INSERT INTO lb_meta VALUES (now(), %s, %s)", (git_hash(), json.dumps(stats)))
    conn.commit()


def session_matrix(rows: np.ndarray, cols: np.ndarray, n_tracks: int) -> sp.csr_matrix:
    """Binary sessions × tracks CSR from row ids that are already ascending (no big sort)."""
    _, counts = np.unique(rows, return_counts=True)  # rows ascending: cheap run-length pass
    indptr = np.r_[0, np.cumsum(counts)].astype(np.int64)
    X = sp.csr_matrix((np.ones(len(cols), np.float32), cols, indptr),
                      shape=(len(counts), n_tracks))
    X.sum_duplicates()  # two LB songs matched to the same MPD track in one session
    X.data[:] = 1.0
    return X


# ── 4 + 5. hold-out, matrix, neighbours ────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-listeners", type=int, default=20)
    ap.add_argument("--k", type=int, default=100)
    ap.add_argument("--min-co", type=int, default=2)
    ap.add_argument("--holdout", type=int, default=1000)
    ap.add_argument("--max-nnz", type=int, default=200_000_000,
                    help="cap on matrix entries (RAM); above it sessions are sampled by hash")
    args = ap.parse_args()
    t0 = time.time()
    dirs = member_dirs()
    log(f"{len(dirs)} ingested files", t0)

    vocab, listeners = vocabulary(dirs, args.min_listeners, t0)
    z = np.load(BUILD / "vocab.npz")
    log(f"vocabulary: {len(vocab):,} songs with ≥ {args.min_listeners} listeners "
        f"(of {int(z['n_songs']):,} distinct, {int(z['n_listens']):,} listens)", t0)

    bucket_sessions(dirs, vocab, t0)
    names = vocab_names(vocab, t0)
    log("names ready", t0)

    with connect() as conn:
        n_mpd = conn.execute("SELECT max(track_id) + 1 FROM tracks").fetchone()[0]
        tid, how = match(conn, names)
        new = np.flatnonzero(tid < 0)
        tid[new] = n_mpd + np.arange(len(new))
        stats = {"vocab": len(vocab), "exact": int((how == 1).sum()), "loose": int((how == 2).sum()),
                 "new": len(new),
                 # weighted by listeners: how much of the listening lands on MPD tracks
                 "matched_listener_share": round(float(listeners[how > 0].sum() / listeners.sum()), 4)}
        log(f"matching: {stats['exact']:,} exact, {stats['loose']:,} loose, {stats['new']:,} new "
            f"({(stats['exact'] + stats['loose']) / len(vocab):.1%} of songs, "
            f"{stats['matched_listener_share']:.1%} weighted by listeners)", t0)

        # popularity estimate for new tracks: listeners → playlist_count scale (fit on matches)
        m = how > 0
        pcs = np.array([r[0] for r in conn.execute(
            "SELECT playlist_count FROM tracks ORDER BY track_id")], dtype=np.float64)
        slope, icpt = np.polyfit(np.log(listeners[m]), np.log(pcs[tid[m]]), 1)
        pop_est = np.maximum(1, np.round(np.exp(icpt + slope * np.log(listeners[new])))).astype(int)
        log(f"pop_est: log(playlist_count) = {icpt:.2f} + {slope:.2f}·log(listeners)", t0)

        # ── hold-out + training rows ──
        ends = [load_sessions(b)["start"].max() for b in range(lb.BUCKETS)]
        cutoff = max(ends) - HOLDOUT_DAYS * 86400
        cand = []
        for b in range(lb.BUCKETS):
            s = load_sessions(b)
            n = np.bincount(s["sid"], minlength=len(s["user"]))
            ok = np.flatnonzero((s["start"] >= cutoff) & (n >= HOLDOUT_MIN_LEN))
            h = (s["user"][ok] * 2654435761 + s["start"][ok]) % (2**32)  # deterministic order
            cand += [(int(x), b, int(i)) for x, i in zip(h, ok)]
        cand.sort()
        held = {(b, i) for _, b, i in cand[:args.holdout]}

        # pass 1: hold-out rows + sizes (no big arrays yet)
        hold_rows, n_users, n_sessions, total = [], 0, 0, 0
        for b in range(lb.BUCKETS):
            s = load_sessions(b)
            n_users += len(np.unique(s["user"]))
            hb = np.array(sorted(i for bb, i in held if bb == b), dtype=np.int64)
            lo, hi = np.searchsorted(s["sid"], hb), np.searchsorted(s["sid"], hb, side="right")
            hold_rows += [tid[s["song"][a:z]] for a, z in zip(lo, hi)]  # rows grouped by sid
            n_sessions += len(s["user"]) - len(hb)
            total += len(s["sid"]) - int((hi - lo).sum())
        log(f"{n_sessions:,} training sessions from {n_users:,} users, {total:,} entries; "
            f"{len(hold_rows)} held out", t0)
        # pass 2: collect, sampling whole sessions by hash per bucket when over --max-nnz (RAM)
        frac = min(1.0, args.max_nnz / total)
        rows, cols, off = [], [], 0
        for b in range(lb.BUCKETS):
            s = load_sessions(b)
            hb = np.array(sorted(i for bb, i in held if bb == b), dtype=np.int64)
            g = s["sid"].astype(np.int64) + off
            keep = ~np.isin(s["sid"], hb) & (((g * 2654435761) % 10**6) < frac * 10**6)
            rows.append(g[keep].astype(np.int32))
            cols.append(tid[s["song"][keep]].astype(np.int32))
            off += len(s["user"])
        rows, cols = np.concatenate(rows), np.concatenate(cols)
        if frac < 1:
            log(f"sampled {frac:.0%} of sessions to fit --max-nnz: {len(rows):,} entries", t0)

        X = session_matrix(rows, cols, n_mpd + len(new))
        del rows, cols
        c = cooc.from_matrix(X)
        del X
        targets = np.flatnonzero(c.n > 0)
        parts = cooc.chunks(c, targets)
        log(f"matrix {c.X.shape[0]:,} × {c.X.shape[1]:,}, {c.X.nnz:,} entries; "
            f"{len(targets):,} tracks in {len(parts)} chunks", t0)

        conn.execute("SET synchronous_commit = off")
        conn.execute("""
            DROP TABLE IF EXISTS neighbours_lb_new, lb_tracks_new, lb_holdout_new;
            CREATE TABLE neighbours_lb_new (track_id int NOT NULL, neighbour_id int NOT NULL,
                                            sim real NOT NULL, co_count int NOT NULL);
            CREATE TABLE lb_tracks_new (track_id int PRIMARY KEY, song bigint NOT NULL,
                name text NOT NULL, artist text NOT NULL, recording_mbid text,
                listeners int NOT NULL, pop_est int NOT NULL);
            CREATE TABLE lb_holdout_new (sess int NOT NULL, pos int NOT NULL, track_id int NOT NULL);
        """)
        with conn.cursor().copy("COPY lb_tracks_new FROM STDIN") as copy:
            for j, i in enumerate(new):
                mbid = names.at[i, "mbid"]
                copy.write_row((int(tid[i]), int(names.at[i, "song"]), str(names.at[i, "title"]),
                                str(names.at[i, "artist"]), mbid if isinstance(mbid, str) else None,
                                int(listeners[i]), int(pop_est[j])))
        with conn.cursor().copy("COPY lb_holdout_new FROM STDIN") as copy:
            for k, tr in enumerate(hold_rows):
                for p, t in enumerate(tr):
                    copy.write_row((k, p, int(t)))
        n_rows = 0
        with conn.cursor().copy("COPY neighbours_lb_new FROM STDIN") as copy:
            for i, part in enumerate(parts, 1):
                src, nbr, sim, co = cooc.neighbours(c, part, "cosine", args.k, args.min_co)
                buf = io.StringIO()
                pd.DataFrame({"s": src, "n": nbr, "sim": sim, "co": co}).to_csv(
                    buf, sep="\t", header=False, index=False, float_format="%.6g")
                copy.write(buf.getvalue())
                n_rows += len(src)
                if i % 20 == 0 or i == len(parts):
                    log(f"  neighbours chunk {i}/{len(parts)}: {n_rows:,} rows", t0)
        log("indexing…", t0)
        stats.update(listens=int(z["n_listens"]), sessions=n_sessions, users=n_users,
                     session_sample=round(frac, 4),
                     entries=int(c.X.nnz), neighbour_rows=n_rows, min_listeners=args.min_listeners,
                     k=args.k, min_co=args.min_co, pop_fit=[round(icpt, 3), round(slope, 3)])
        install(conn, stats)
        conn.execute("ANALYZE neighbours_lb")
        conn.execute("ANALYZE lb_tracks")
    log(f"lb-build done: {json.dumps(stats)}", t0)


if __name__ == "__main__":
    main()
