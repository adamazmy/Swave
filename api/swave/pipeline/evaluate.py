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
"""

from __future__ import annotations

import argparse
import csv
import subprocess
import time
from datetime import datetime, timezone

import numpy as np

from swave import cooc, scorers
from swave import recommend as rk
from swave.config import EVAL_CSV, ROOT, connect

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
    ap.add_argument("--holdout", choices=("mpd", "lb"), default="mpd")
    args = ap.parse_args()
    configs = [rk.load_weights(p) for p in args.scorers or [None]]

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
            active = [(scorers.Cooccurrence(name, lookups[name]), w) for name, w in weights.items()]
            hits, recalls = [], []
            for seeds, hidden in split.values():
                comb = rk.combine(active, seeds, None, lambda t: pop[t])
                res = rk.penalise(comb, args.alpha, seeds, args.at)
                h, r = metrics(res.track, hidden)
                hits.append(h)
                recalls.append(r)
            report(args, weights, len(split), hits, recalls, prep + time.time() - t1)


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
