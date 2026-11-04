"""make lb-index RUN=<name>: build a CANDIDATE ListenBrainz index into data/lb/runs/<name>/,
without touching the app's tables (`neighbours_lb` keeps serving until `make lb-install`).

Time-based split: every session that starts in the last --holdout-days (30) of the data is left
out of training entirely; the shared eval hold-out (`runs/holdout.npz`, made once) is drawn
from those sessions, so every run is measured on the same sessions and seeds.

Memory-safe build, for 100% of the sessions:
  * vocabulary = songs with ≥ --min-listeners distinct users in the TRAINING period
  * sessions: 30-min gap, repeats collapsed, cut into chunks of --max-len songs (50: bounds the
    pairs one long session creates), < 3 vocabulary songs dropped
  * session weights: per-user cap (a user with n > --user-cap sessions counts as --user-cap
    sessions: weight cap/n) × optional recency 0.5^(age_years / --half-life)
  * two passes over the per-user buckets (count, then fill pre-sized arrays): no concatenation
    spikes. Matrix = sessions × tracks CSR (binary) + tracks × sessions CSR (weights):
    ~16 bytes per entry, checked against --ram-gb before it is built
  * co-occurrence c_ij = Σ_s w_s, cosine c_ij / sqrt(n_i n_j) with n_i = Σ_s w_s, computed in
    chunks of target tracks (`cooc.chunks`), top-k streamed to raw files (never all in RAM)
Peak RAM is printed per step. Outputs (all local, gitignored under data/):
  tracks.parquet  song hash → track_id (MPD id, or a new id after MPD's), how, listeners, pop_est,
                  name/artist/mbid of new tracks
  nbr_{src,nbr,sim,co}.bin  neighbour lists sorted by src then sim desc (int32/int32/float32/int32)
  meta.json       config, stats, peak RAM, timings
"""

from __future__ import annotations

import argparse
import ctypes
import json
import shutil
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pandas as pd
import scipy.sparse as sp

from swave import cooc, lb
from swave.config import connect
from swave.pipeline.evaluate import git_hash
from swave.pipeline.lb_build import load_bucket, match, member_dirs, vocab_names
from swave.pipeline.lb_ingest import LB_DIR

RUNS = LB_DIR / "runs"
HOLDOUT = RUNS / "holdout.npz"
HOLDOUT_SESSIONS = 2000
HOLDOUT_MIN_LEN = 10
HOLDOUT_SEED_VOCAB = 20  # seeds come from songs with ≥ 20 training listeners: the same for every run
N_SEEDS = 5
YEAR = 365.25 * 86400
VOCAB_FLOOR = 5  # the vocabulary cache keeps every song with ≥ 5 listeners


# ── memory meter ──────────────────────────────────────────────────────────────────────────────

def rss() -> int:
    """Current working set of this process, bytes."""
    if sys.platform == "win32":
        class PMC(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong)] + [
                (n, ctypes.c_size_t) for n in (
                    "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                    "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                    "PagefileUsage", "PeakPagefileUsage")]
        c = PMC()
        c.cb = ctypes.sizeof(PMC)
        k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
        k32.GetCurrentProcess.restype = ctypes.c_void_p  # a 64-bit pseudo-handle (-1)
        psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(PMC), ctypes.c_ulong]
        psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb)
        return int(c.WorkingSetSize)
    with open("/proc/self/statm") as f:
        import os
        return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")


class Meter:
    """Samples RSS every 0.2 s; `step()` reports the peak of each step."""

    def __init__(self, t0: float):
        self.t0, self.peak, self.steps = t0, 0, {}
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        while True:
            self.peak = max(self.peak, rss())
            time.sleep(0.2)

    @contextmanager
    def step(self, name: str):
        self.peak = rss()
        t = time.time()
        log(f"{name}…", self.t0)
        yield
        gb = round(max(self.peak, rss()) / 1e9, 2)
        self.steps[name] = {"peak_gb": gb, "minutes": round((time.time() - t) / 60, 1)}
        log(f"{name}: done, peak RAM {gb} GB", self.t0)


def log(msg: str, t0: float) -> None:
    print(f"[{(time.time() - t0) / 60:6.1f} min] {msg}", flush=True)


# ── time split, vocabulary, hold-out ─────────────────────────────────────────────────────────

def time_split(dirs: list[Path], days: int) -> tuple[int, int]:
    """(cutoff, end): end = the newest listen; sessions starting at/after cutoff are held out."""
    end = int(np.load(dirs[-1] / "ts.npy", mmap_mode="r").max())
    return end - days * 86400, end


def training_vocab(dirs: list[Path], cutoff: int, t0: float) -> tuple[np.ndarray, np.ndarray]:
    """(song, listeners) for songs with ≥ VOCAB_FLOOR distinct users before `cutoff` (cached)."""
    path = RUNS / f"vocab_{cutoff}.npz"
    if path.exists():
        z = np.load(path)
        return z["song"], z["listeners"]
    songs, counts = np.array([], np.int64), np.array([], np.int64)
    for b in range(lb.BUCKETS):
        d = load_bucket(dirs, b)
        keep = d["ts"] < cutoff
        so, uo = d["song"][keep], d["user"][keep]
        o = np.lexsort((uo, so))
        so, uo = so[o], uo[o]
        first = np.r_[True, (so[1:] != so[:-1]) | (uo[1:] != uo[:-1])]
        s, c = np.unique(so[first], return_counts=True)
        u, inv = np.unique(np.concatenate([songs, s]), return_inverse=True)
        songs, counts = u, np.bincount(inv, weights=np.concatenate([counts, c])).astype(np.int64)
        if b % 16 == 15:
            log(f"  vocabulary: bucket {b + 1}/{lb.BUCKETS}, {len(songs):,} songs", t0)
    keep = counts >= VOCAB_FLOOR
    RUNS.mkdir(parents=True, exist_ok=True)
    np.savez(path, song=songs[keep], listeners=counts[keep])
    return songs[keep], counts[keep]


def make_holdout(dirs: list[Path], cutoff: int, seedable: np.ndarray, t0: float) -> None:
    """HOLDOUT_SESSIONS sessions starting after `cutoff`, ≥ HOLDOUT_MIN_LEN distinct songs of
    which ≥ N_SEEDS are seedable. Stored as raw song hashes (each run maps them to its own ids)."""
    cand, total = [], 0  # (hash order, songs, seedable songs)
    for b in range(lb.BUCKETS):
        d = load_bucket(dirs, b)
        keep = d["ts"] >= cutoff
        s = lb.sessions(d["user"][keep], d["ts"][keep], d["song"][keep], min_len=HOLDOUT_MIN_LEN)
        if len(s.user) == 0:
            continue
        ok = np.isin(s.song, seedable)
        valid = np.flatnonzero(np.bincount(s.sid, weights=ok.astype(np.float64), minlength=len(s.user)) >= N_SEEDS)
        total += len(valid)
        h = (s.user[valid].astype(np.int64) * 2654435761 + s.start[valid]) % 2**32
        best = valid[np.argsort(h, kind="stable")[:HOLDOUT_SESSIONS]]  # global top ⊆ union of these
        starts = np.searchsorted(s.sid, best)
        ends = np.searchsorted(s.sid, best, side="right")
        for i, a, z in zip(best, starts, ends):
            hh = int((int(s.user[i]) * 2654435761 + int(s.start[i])) % 2**32)
            cand.append((hh, s.song[a:z], s.song[a:z][ok[a:z]]))
        if b % 16 == 15:
            log(f"  hold-out: bucket {b + 1}/{lb.BUCKETS}, {total:,} candidate sessions", t0)
    cand.sort(key=lambda c: c[0])
    chosen = cand[:HOLDOUT_SESSIONS]
    seeds = [np.random.default_rng(h).choice(np.unique(ok), N_SEEDS, replace=False)
             for h, _, ok in chosen]
    songs = [np.unique(s) for _, s, _ in chosen]
    RUNS.mkdir(parents=True, exist_ok=True)
    np.savez(HOLDOUT, offsets=np.r_[0, np.cumsum([len(s) for s in songs])],
             songs=np.concatenate(songs), seeds=np.stack(seeds), cutoff=cutoff, candidates=total)


# ── training sessions → matrix ────────────────────────────────────────────────────────────────

def bucket_rows(dirs: list[Path], b: int, cutoff: int, vocab: np.ndarray, tid: np.ndarray,
                args: argparse.Namespace) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """One bucket's training sessions → (row length per session, track ids, weight per session).
    Rows are grouped by session; track ids are unique within a session."""
    d = load_bucket(dirs, b)
    keep = d["ts"] < cutoff
    s = lb.sessions(d["user"][keep], d["ts"][keep], d["song"][keep], min_len=1,
                    max_len=args.max_len)
    # sessions that START after the cutoff are already gone (ts < cutoff); a session crossing
    # the cutoff keeps its earlier part only
    pos = np.searchsorted(vocab, s.song)
    pos[pos == len(vocab)] = 0
    inv = vocab[pos] == s.song
    sid, t = s.sid[inv], tid[pos[inv]]
    # minimum length counts vocabulary songs (as lb-build did), before tracks are deduplicated
    ok = np.bincount(sid, minlength=len(s.user)) >= lb.MIN_SESSION
    # two songs can map to one MPD track: unique (session, track)
    o = np.lexsort((t, sid))
    sid, t = sid[o], t[o]
    first = np.r_[True, (sid[1:] != sid[:-1]) | (t[1:] != t[:-1])]
    sid, t = sid[first], t[first]
    n = np.bincount(sid, minlength=len(s.user))
    if args.sample < 1:  # whole sessions by hash (the old build's --max-nnz sampling)
        g = (s.user.astype(np.int64) * 2654435761 + s.start) % 10**6
        ok &= g < args.sample * 10**6
    keep_rows = ok[sid]
    sid, t = sid[keep_rows], t[keep_rows]
    sess = np.flatnonzero(ok)
    w = np.ones(len(sess), np.float32)
    if args.user_cap:
        u, inv_u, cnt = np.unique(s.user[sess], return_inverse=True, return_counts=True)
        w *= np.minimum(1.0, args.user_cap / cnt[inv_u]).astype(np.float32)
    if args.half_life:
        age = np.maximum(0, cutoff - s.start[sess]) / YEAR
        w *= (0.5 ** (age / args.half_life)).astype(np.float32)
    return n[sess].astype(np.int64), t.astype(np.int32), w


def build_matrix(dirs, cutoff, vocab, tid, n_tracks, args, meter, stats) -> cooc.Cooc:
    with meter.step("count pass"):
        n_sess, nnz = [], []
        for b in range(lb.BUCKETS):
            lens, _, _ = bucket_rows(dirs, b, cutoff, vocab, tid, args)
            n_sess.append(len(lens))
            nnz.append(int(lens.sum()))
    S, N = sum(n_sess), sum(nnz)
    need = (N * 16 + S * 16 + n_tracks * 24) / 1e9 + 2.5  # X + Xt + indptrs + chunk working set
    stats.update(sessions=S, entries=N, est_ram_gb=round(need, 1))
    log(f"{S:,} training sessions, {N:,} entries; matrix needs ~{need:.1f} GB "
        f"(budget {args.ram_gb} GB)", meter.t0)
    if need > args.ram_gb:
        raise SystemExit(f"over the RAM budget ({need:.1f} > {args.ram_gb} GB): nothing built")
    with meter.step("fill pass"):
        # int32 index arrays while they fit: scipy upcasts BOTH to int64 if either is (2× RAM)
        idx_t = np.int32 if N < 2**31 - 1 else np.int64
        indptr = np.zeros(S + 1, idx_t)
        indices = np.empty(N, np.int32)
        weight = np.empty(S, np.float32)
        r = e = 0
        for b in range(lb.BUCKETS):
            lens, t, w = bucket_rows(dirs, b, cutoff, vocab, tid, args)
            indptr[r + 1:r + 1 + len(lens)] = e + np.cumsum(lens)
            indices[e:e + len(t)] = t
            weight[r:r + len(w)] = w
            r, e = r + len(lens), e + len(t)
            if b % 16 == 15:
                log(f"  fill: bucket {b + 1}/{lb.BUCKETS}", meter.t0)
        assert (r, e) == (S, N)
    with meter.step("transpose"):
        # Xt carries the session weights; X stays binary (c_ij = Σ_s w_s x_si x_sj)
        X = sp.csr_matrix((np.repeat(weight, np.diff(indptr)), indices, indptr),
                          shape=(S, n_tracks))
        del indices
        Xt = X.T.tocsr()
        X.data[:] = 1.0
        n = np.asarray(Xt.sum(axis=1)).ravel().astype(np.float64)
        stats["weight_share"] = round(float(weight.sum() / S), 4)
    return cooc.Cooc(X=X, Xt=Xt, n=n, P=S)


def chunks(c: cooc.Cooc, rows: np.ndarray, budget: int) -> list[np.ndarray]:
    """Like `cooc.chunks`, but the work bound counts entries, not weights: work_i = Σ over the
    sessions containing i of the session length (Xt holds weights, so Xt @ len would undercount).
    Computed over row blocks so no nnz-sized float64 array is allocated."""
    lens = np.diff(c.X.indptr).astype(np.float64)
    work = np.zeros(c.X.shape[1])
    step = 2_000_000
    for a in range(0, c.X.shape[0], step):
        p0, p1 = c.X.indptr[a], c.X.indptr[min(a + step, c.X.shape[0])]
        rl = np.repeat(lens[a:a + step], np.diff(c.X.indptr[a:a + step + 1]))
        work += np.bincount(c.X.indices[p0:p1], weights=rl, minlength=len(work))
    out, start, acc = [], 0, 0.0
    w = work[rows]
    for i, x in enumerate(w):
        if acc + x > budget and i > start:
            out.append(rows[start:i])
            start, acc = i, 0.0
        acc += x
    if start < len(rows):
        out.append(rows[start:])
    return out


# ── main ───────────────────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--min-listeners", type=int, default=20)
    ap.add_argument("--k", type=int, default=200)
    ap.add_argument("--min-co", type=float, default=2)
    ap.add_argument("--max-len", type=int, default=50, help="sessions cut into chunks of this many songs")
    ap.add_argument("--user-cap", type=int, default=2000, help="sessions per user at full weight (0 = off)")
    ap.add_argument("--half-life", type=float, default=0, help="recency half-life in years (0 = off)")
    ap.add_argument("--sample", type=float, default=1.0, help="fraction of sessions kept (by hash)")
    ap.add_argument("--holdout-days", type=int, default=30)
    ap.add_argument("--ram-gb", type=float, default=11.0, help="peak RAM budget (~70%% of total)")
    ap.add_argument("--chunk", type=int, default=40_000_000, help="co-occurrence entries per chunk")
    args = ap.parse_args()
    t0 = time.time()
    meter = Meter(t0)
    out = RUNS / args.run
    tmp = RUNS / f"{args.run}.partial"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    dirs = member_dirs()
    cutoff, end = time_split(dirs, args.holdout_days)
    stats: dict = {"config": vars(args), "git": git_hash(), "files": len(dirs),
                   "cutoff": cutoff, "end": end}
    log(f"{len(dirs)} ingested files; training < {time.strftime('%Y-%m-%d', time.gmtime(cutoff))}, "
        f"hold-out after", t0)

    with meter.step("vocabulary"):
        all_songs, all_listeners = training_vocab(dirs, cutoff, t0)
        if not HOLDOUT.exists():
            make_holdout(dirs, cutoff, all_songs[all_listeners >= HOLDOUT_SEED_VOCAB], t0)
        keep = all_listeners >= args.min_listeners
        vocab, listeners = all_songs[keep], all_listeners[keep]
    log(f"vocabulary: {len(vocab):,} songs with ≥ {args.min_listeners} training listeners", t0)

    with meter.step("names + matching"):
        names = vocab_names(vocab, t0, RUNS / f"names_{cutoff}_{args.min_listeners}.parquet")
        with connect() as conn:
            n_mpd = conn.execute("SELECT max(track_id) + 1 FROM tracks").fetchone()[0]
            tid, how = match(conn, names)
            pcs = np.array([r[0] for r in conn.execute(
                "SELECT playlist_count FROM tracks ORDER BY track_id")], dtype=np.float64)
        new = np.flatnonzero(tid < 0)
        tid[new] = n_mpd + np.arange(len(new))
        m = how > 0
        slope, icpt = np.polyfit(np.log(listeners[m]), np.log(pcs[tid[m]]), 1)
        pop_est = np.zeros(len(vocab), np.int64)
        pop_est[new] = np.maximum(1, np.round(np.exp(icpt + slope * np.log(listeners[new]))))
        pd.DataFrame({"song": vocab, "track_id": tid, "how": how, "listeners": listeners,
                      "pop_est": pop_est,
                      "name": names["title"].where(tid >= n_mpd), "artist": names["artist"].where(tid >= n_mpd),
                      "mbid": names["mbid"].where(tid >= n_mpd)}).to_parquet(tmp / "tracks.parquet")
        stats.update(vocab=len(vocab), exact=int((how == 1).sum()), loose=int((how == 2).sum()),
                     new=len(new), pop_fit=[round(icpt, 3), round(slope, 3)],
                     matched_listener_share=round(float(listeners[m].sum() / listeners.sum()), 4))

    c = build_matrix(dirs, cutoff, vocab, tid.astype(np.int32), n_mpd + len(new), args, meter, stats)

    with meter.step("neighbours"):
        targets = np.flatnonzero(c.n > 0)
        parts = chunks(c, targets, args.chunk)
        log(f"  {len(targets):,} tracks in {len(parts)} chunks", t0)
        files = {k: open(tmp / f"nbr_{k}.bin", "wb") for k in ("src", "nbr", "sim", "co")}
        rows = 0
        for i, part in enumerate(parts, 1):
            got = cooc.neighbours(c, part, "cosine", args.k, args.min_co)
            for f, a in zip(files.values(), got):
                a.tofile(f)
            rows += len(got[0])
            if i % 25 == 0 or i == len(parts):
                log(f"  chunk {i}/{len(parts)}: {rows:,} rows", t0)
        for f in files.values():
            f.close()
        stats["neighbour_rows"] = rows

    stats.update(steps=meter.steps, minutes=round((time.time() - t0) / 60, 1),
                 disk_gb=round(sum(f.stat().st_size for f in tmp.iterdir()) / 1e9, 2))
    (tmp / "meta.json").write_text(json.dumps(stats, indent=1, default=str))
    shutil.rmtree(out, ignore_errors=True)
    tmp.rename(out)
    log(f"run {args.run} done: {json.dumps({k: v for k, v in stats.items() if k != 'config'}, default=str)}", t0)


if __name__ == "__main__":
    main()
