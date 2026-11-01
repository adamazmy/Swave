"""make eval: hit-rate@N and recall@N on the 1,000 held-out playlists.

Seeds: 5 tracks per playlist, drawn with a fixed RNG seeded by pid. Hidden: the rest.
Neighbours are computed on the fly from the training matrix with exactly the same function as
build-index (incl. top-k truncation), so eval needs no index build and can compare methods/k/alpha.
Appends one metrics-only row to eval/results.csv (no track data).
"""

from __future__ import annotations

import argparse
import csv
import subprocess
import time
from datetime import datetime, timezone

import numpy as np

from swave import cooc
from swave.config import EVAL_CSV, ROOT, connect
from swave.recommend import score

CSV_FIELDS = ["date", "git", "method", "k", "min_co", "alpha", "n_eval", "seeds", "at",
              "hit_rate", "recall", "seconds"]


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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--method", choices=cooc.METHODS, default="cosine")
    ap.add_argument("--k", type=int, default=200)
    ap.add_argument("--min-co", type=int, default=2)
    ap.add_argument("--alpha", type=float, default=0.0)
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--at", type=int, default=50)
    args = ap.parse_args()
    t0 = time.time()

    with connect() as conn:
        pop = np.array(
            [r[0] for r in conn.execute("SELECT playlist_count FROM tracks ORDER BY track_id")],
            dtype=np.float64,
        )
        rows = conn.execute("""
            SELECT h.pid, t.track_id FROM eval_holdout h
            JOIN playlist_tracks pt USING (pid) JOIN tracks t USING (track_uri)
            ORDER BY h.pid, t.track_id""").fetchall()
        c = cooc.load_training(conn)

    by_pid: dict[int, list[int]] = {}
    for pid, tid in rows:
        by_pid.setdefault(pid, []).append(tid)

    split = {}
    for pid, tids in by_pid.items():
        tids_arr = np.array(tids)
        seeds = np.random.default_rng(pid).choice(tids_arr, args.seeds, replace=False)
        split[pid] = (seeds, np.setdiff1d(tids_arr, seeds))

    # Neighbour lists for every distinct seed, same truncation as the production index.
    all_seeds = np.unique(np.concatenate([s for s, _ in split.values()]))
    nb: dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    for part in cooc.chunks(c, all_seeds):
        src, nbr, sim, co = cooc.neighbours(c, part, args.method, args.k, args.min_co)
        bounds = np.searchsorted(src, part, side="left"), np.searchsorted(src, part, side="right")
        # src follows `part` order, which is sorted, so searchsorted is valid.
        for s, a, b in zip(part, *bounds):
            nb[int(s)] = (nbr[a:b], sim[a:b], co[a:b])

    hits, recalls = [], []
    for seeds, hidden in split.values():
        lists = [nb[int(s)] for s in seeds]
        nbr = np.concatenate([x[0] for x in lists])
        res = score(nbr, np.concatenate([x[1] for x in lists]), np.concatenate([x[2] for x in lists]),
                    pop[nbr], args.alpha, seeds, args.at)
        h, r = metrics(res.track, hidden)
        hits.append(h)
        recalls.append(r)

    row = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "git": git_hash(), "method": args.method, "k": args.k, "min_co": args.min_co,
        "alpha": args.alpha, "n_eval": len(split), "seeds": args.seeds, "at": args.at,
        "hit_rate": f"{np.mean(hits):.4f}", "recall": f"{np.mean(recalls):.4f}",
        "seconds": f"{time.time() - t0:.0f}",
    }
    print(f"eval {row['git']} {args.method} k={args.k} min_co={args.min_co} alpha={args.alpha} "
          f"n={len(split)}: hit-rate@{args.at}={row['hit_rate']} recall@{args.at}={row['recall']} "
          f"({row['seconds']}s)")

    EVAL_CSV.parent.mkdir(parents=True, exist_ok=True)
    new = not EVAL_CSV.exists() or EVAL_CSV.stat().st_size == 0
    with open(EVAL_CSV, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)


if __name__ == "__main__":
    main()
