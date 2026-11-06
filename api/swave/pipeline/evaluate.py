"""make eval: hit-rate@N and recall@N on 1,000 held-out playlists (or ListenBrainz sessions).

--holdout mpd: the MPD playlists in `eval_holdout`; --holdout lb: the recent listening sessions in
`lb_holdout` (made by lb-build, excluded from `neighbours_lb`), which also measures post-2017 music.
Seeds: 5 tracks per playlist/session, drawn with a fixed RNG seeded by its id. Hidden: the rest.
The MPD scorer's neighbours are computed on the fly (below); the ListenBrainz scorer reads
`neighbours_lb`. Several --scorers configs can be evaluated in one run (one row each).
Neighbours are computed on the fly from the training matrix with exactly the same function as
build-index (incl. top-k truncation), so eval needs no index build and can compare methods/k/alpha.
Scoring goes through the same ranker as the API (`recommend.combine` / `penalise`) with the scorer
weights from `--scorers` (default: scorers.json), which are logged in the `scorers` column.
Appends one metrics-only row to eval/results.csv (no track data).

--holdout time --lb-run <name>: the time-split hold-out of `make lb-index` (sessions of the last
30 days, never trained on; data/lb/runs/holdout.npz) scored with that run's candidate index
(data/lb/runs/<name>/) instead of `neighbours_lb`. Songs are stored as hashes and mapped through
the run's own vocabulary, so every run sees the same sessions and seeds; recall counts every
hidden song, so a song outside a run's vocabulary is a miss. --lb-k truncates the lists (k=100
from a k=200 run). The `holdout` column reads time/<run>.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import time
from datetime import datetime, timezone

import httpx
import numpy as np
import pandas as pd

from swave import cooc, related, scorers, styles
from swave import recommend as rk
from swave.config import EVAL_CSV, ROOT, connect

LB_RUNS = ROOT / "data" / "lb" / "runs"

CSV_FIELDS = ["date", "git", "method", "k", "min_co", "alpha", "n_eval", "seeds", "at",
              "hit_rate", "recall", "seconds", "scorers", "holdout"]


def git_hash() -> str:
    def run(*cmd: str) -> str:
        return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True).stdout.strip()

    h = run("git", "rev-parse", "--short", "HEAD") or "nogit"
    # results.csv itself changes on every eval, so it doesn't count as uncommitted code.
    dirty = run("git", "status", "--porcelain", "--untracked-files=no", "--", ".",
                ":(exclude)eval/results.csv")
    return h + ("-dirty" if dirty else "")


def metrics(recs: np.ndarray, hidden: np.ndarray) -> tuple[float, float]:
    """(hit, recall) for one playlist: hit = any hidden track recommended."""
    found = np.isin(hidden, recs).sum()
    return float(found > 0), found / len(hidden)


def holdout_sets(conn, which: str, n_seeds: int) -> dict[int, tuple[np.ndarray, np.ndarray]]:  # noqa: ANN001
    if which == "mpd":
        rows = conn.execute("""
            SELECT h.pid, t.track_id FROM eval_holdout h
            JOIN playlist_tracks pt USING (pid) JOIN tracks t USING (track_uri)
            ORDER BY h.pid, t.track_id""").fetchall()
    else:
        rows = conn.execute("SELECT sess, track_id FROM lb_holdout ORDER BY sess, track_id").fetchall()
    by_id: dict[int, list[int]] = {}
    for i, tid in rows:
        by_id.setdefault(i, []).append(tid)
    split = {}
    for i, tids in by_id.items():
        tids_arr = np.unique(np.array(tids))
        seeds = np.random.default_rng(i).choice(tids_arr, n_seeds, replace=False)
        split[i] = (seeds, np.setdiff1d(tids_arr, seeds))
    return split


class Run:
    """A candidate index from `make lb-index`: song → track id, popularity, neighbour lists."""

    def __init__(self, name: str, k: int | None):
        d = LB_RUNS / name
        t = pd.read_parquet(d / "tracks.parquet", columns=["song", "track_id", "pop_est"])
        self.song, self.tid = t["song"].to_numpy(), t["track_id"].to_numpy()
        o = np.argsort(self.song)
        self.song, self.tid, pop = self.song[o], self.tid[o], t["pop_est"].to_numpy()[o]
        self.new = {int(i): int(p) for i, p in zip(self.tid, pop) if p > 0}
        cols = [np.fromfile(d / f"nbr_{c}.bin", dtype=dt) for c, dt in
                (("src", np.int32), ("nbr", np.int32), ("sim", np.float32), ("co", np.int32))]
        if k:
            src = cols[0]
            starts = np.flatnonzero(np.r_[True, src[1:] != src[:-1]])
            rank = np.arange(len(src)) - np.repeat(starts, np.diff(np.r_[starts, len(src)]))
            cols = [c[rank < k] for c in cols]
        self.src, self.nbr, self.sim, self.co = cols
        assert np.all(self.src[1:] >= self.src[:-1]), "neighbour lists must be sorted by src"
        # one entry per source track: lookups search this small array, not the 100M-row one
        # (searchsorted of int64 seeds in the int32 column would copy it on every call)
        self.keys, self.starts = np.unique(self.src, return_index=True)
        self.ends = np.r_[self.starts[1:], len(self.src)]

    def ids(self, songs: np.ndarray) -> np.ndarray:
        """Track id per song hash, -1 if the song is not in this run's vocabulary."""
        pos = np.minimum(np.searchsorted(self.song, songs), len(self.song) - 1)
        return np.where(self.song[pos] == songs, self.tid[pos], -1)

    def lookup(self, seeds: np.ndarray):
        seeds = np.asarray(seeds, dtype=self.keys.dtype)
        pos = np.minimum(np.searchsorted(self.keys, seeds), len(self.keys) - 1)
        hit = pos[self.keys[pos] == seeds]
        idx = (np.concatenate([np.arange(self.starts[i], self.ends[i]) for i in hit])
               if len(hit) else np.array([], np.int64))
        return (self.src[idx].astype(np.int64), self.nbr[idx].astype(np.int64),
                self.sim[idx].astype(np.float64), self.co[idx].astype(np.float64))


def time_sets(run: Run) -> list[tuple[np.ndarray, np.ndarray, int]]:
    """(seed ids, hidden ids with -1 for unknown songs, number of hidden songs) per session."""
    z = np.load(LB_RUNS / "holdout.npz")
    out = []
    for i in range(len(z["seeds"])):
        songs = z["songs"][z["offsets"][i]:z["offsets"][i + 1]]
        seed_songs = z["seeds"][i]
        hidden = np.setdiff1d(songs, seed_songs)
        seeds = np.unique(run.ids(seed_songs))
        out.append((seeds[seeds >= 0], run.ids(hidden), len(hidden)))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--method", choices=cooc.METHODS, default="cosine")
    ap.add_argument("--k", type=int, default=200)
    ap.add_argument("--min-co", type=int, default=2)
    ap.add_argument("--alpha", type=float, default=0.0)
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--at", type=int, default=50)
    ap.add_argument("--scorers", nargs="*", default=[None],
                    help="weights config(s) (default: scorers.json); one result row each")
    ap.add_argument("--holdout", choices=("mpd", "lb", "time"), default="mpd")
    ap.add_argument("--lb-run", help="--holdout time: candidate index in data/lb/runs/")
    ap.add_argument("--lb-k", type=int, help="truncate the run's lists to this k")
    args = ap.parse_args()
    configs = [rk.load_weights(p) for p in args.scorers or [None]]
    if args.holdout == "time":
        return eval_run(args, configs)

    with connect() as conn:
        t0 = time.time()
        pop = np.array([r[0] for r in conn.execute(
            "SELECT playlist_count FROM tracks ORDER BY track_id")], dtype=np.float64)
        n_mpd = len(pop)
        extra = conn.execute("SELECT track_id, pop_est FROM lb_tracks").fetchall()
        if extra:
            ext = np.zeros(max(t for t, _ in extra) + 1)
            ext[:n_mpd] = pop
            for t, p in extra:
                ext[t] = p
            pop = ext
        split = holdout_sets(conn, args.holdout, args.seeds)
        http = httpx.Client(timeout=15.0)
        all_seeds = np.unique(np.concatenate([s for s, _ in split.values()]))
        for name, source in related.SCORERS.items():
            if any(name in w for w in configs):
                prewarm_related(conn, http, all_seeds, source)

        # MPD neighbour lists for every distinct (MPD) seed, same truncation as the production index.
        nb: dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
        if any("cooccurrence_mpd" in w for w in configs):
            c = cooc.load_training(conn)
            all_seeds = np.unique(np.concatenate([s for s, _ in split.values()]))
            for part in cooc.chunks(c, all_seeds[all_seeds < n_mpd]):
                src, nbr, sim, co = cooc.neighbours(c, part, args.method, args.k, args.min_co)
                bounds = (np.searchsorted(src, part, side="left"),
                          np.searchsorted(src, part, side="right"))
                # src follows `part` order, which is sorted, so searchsorted is valid.
                for s, a, b in zip(part, *bounds):
                    nb[int(s)] = (nbr[a:b], sim[a:b], co[a:b])
        prep = time.time() - t0

        for weights in configs:
            t1 = time.time()
            lookups = {"cooccurrence_mpd": scorers.from_dict(nb),
                       "cooccurrence_lb": scorers.from_db(conn, "neighbours_lb")}
            active = [(related.db_lookups(conn, http, name, related.SCORERS[name]) if name in related.SCORERS
                       else styles.db_lookups(conn) if name == "discogs_style"
                       else scorers.Cooccurrence(name, lookups[name]), w) for name, w in weights.items()]
            hits, recalls = [], []
            for seeds, hidden in split.values():
                comb = rk.combine(active, seeds, None, lambda t: pop[t])
                res = rk.penalise(comb, args.alpha, seeds, args.at)
                h, r = metrics(res.track, hidden)
                hits.append(h)
                recalls.append(r)
            report(args, weights, len(split), hits, recalls, prep + time.time() - t1)


def eval_run(args, configs) -> None:  # noqa: ANN001
    if not args.lb_run:
        raise SystemExit("--holdout time needs --lb-run <name>")
    t0 = time.time()
    run = Run(args.lb_run, args.lb_k)
    with connect() as conn:
        pop = np.array([r[0] for r in conn.execute(
            "SELECT playlist_count FROM tracks ORDER BY track_id")], dtype=np.float64)
        n_mpd = len(pop)
        if run.new:
            ext = np.zeros(max(run.new) + 1)
            ext[:n_mpd] = pop
            for t, p in run.new.items():
                ext[t] = p
            pop = ext
        sets = time_sets(run)
        nb: dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
        if any("cooccurrence_mpd" in w for w in configs):
            c = cooc.load_training(conn)
            all_seeds = np.unique(np.concatenate([s for s, _, _ in sets]))
            for part in cooc.chunks(c, all_seeds[all_seeds < n_mpd]):
                src, nbr, sim, co = cooc.neighbours(c, part, args.method, args.k, args.min_co)
                lo, hi = np.searchsorted(src, part), np.searchsorted(src, part, side="right")
                for s, a, b in zip(part, lo, hi):
                    nb[int(s)] = (nbr[a:b], sim[a:b], co[a:b])
    prep = time.time() - t0
    args.holdout = f"time/{args.lb_run}"
    meta = json.loads((LB_RUNS / args.lb_run / "meta.json").read_text())
    args.k = args.lb_k or meta["config"]["k"]  # the LB run's list length (MPD lists stay --k)
    conn = connect()  # the related-artists scorer reads the catalogue + its Deezer cache
    http = httpx.Client(timeout=15.0)
    all_seeds = np.unique(np.concatenate([s for s, _, _ in sets if len(s)]))
    for name, source in related.SCORERS.items():
        if any(name in w for w in configs):
            prewarm_related(conn, http, all_seeds, source)
    for weights in configs:
        t1 = time.time()
        lookups = {"cooccurrence_mpd": scorers.from_dict(nb), "cooccurrence_lb": run.lookup}
        active = [(related.db_lookups(conn, http, name, related.SCORERS[name]) if name in related.SCORERS
                   else styles.db_lookups(conn) if name == "discogs_style"
                   else scorers.Cooccurrence(name, lookups[name]), w) for name, w in weights.items()]
        hits, recalls = [], []
        for seeds, hidden, n_hidden in sets:
            if len(seeds) == 0:
                hits.append(0.0)
                recalls.append(0.0)
                continue
            comb = rk.combine(active, seeds, None, lambda t: pop[t])
            res = rk.penalise(comb, args.alpha, seeds, args.at)
            found = int(np.isin(hidden[hidden >= 0], res.track).sum())
            hits.append(float(found > 0))
            recalls.append(found / n_hidden)
        report(args, weights, len(sets), hits, recalls, prep + time.time() - t1)


def prewarm_related(conn, http, seeds: np.ndarray, source: str = "deezer") -> None:  # noqa: ANN001
    """Fetch Deezer's related artists for every seed artist once (cached in deezer_related),
    so the eval loop itself makes no network calls. ~2 calls per new artist, ~10/s."""
    scorer = related.db_lookups(conn, http)
    names = {k: n for k, n in scorer.artists_of(seeds).values() if k}
    keys = list(names)
    t0 = time.time()
    for a in range(0, len(keys), 200):
        related.related(conn, http, {k: names[k] for k in keys[a:a + 200]}, source)
        print(f"  similar artists ({source}): {min(a + 200, len(keys)):,}/{len(keys):,} seed artists "
              f"({time.time() - t0:.0f}s)", flush=True)


def report(args, weights: dict[str, float], n: int, hits: list, recalls: list,  # noqa: ANN001
           seconds: float) -> None:
    row = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "git": git_hash(), "method": args.method, "k": args.k, "min_co": args.min_co,
        "alpha": args.alpha, "n_eval": n, "seeds": args.seeds, "at": args.at,
        "hit_rate": f"{np.mean(hits):.4f}", "recall": f"{np.mean(recalls):.4f}",
        "seconds": f"{seconds:.0f}", "scorers": rk.weights_label(weights),
        "holdout": args.holdout,
    }
    print(f"eval {row['git']} {args.holdout} holdout, {args.method} k={args.k} "
          f"min_co={args.min_co} alpha={args.alpha} [{row['scorers']}] n={n}: "
          f"hit-rate@{args.at}={row['hit_rate']} recall@{args.at}={row['recall']} "
          f"({row['seconds']}s)", flush=True)

    EVAL_CSV.parent.mkdir(parents=True, exist_ok=True)
    new = not EVAL_CSV.exists() or EVAL_CSV.stat().st_size == 0
    with open(EVAL_CSV, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)


if __name__ == "__main__":
    main()
