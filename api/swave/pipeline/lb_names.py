"""make lb-names: fold the per-file song names (data/lb/names/<n>.parquet) into one deduplicated
table (data/lb/names_all/s<shard>.parquet), one row per song.

lb-ingest writes the spellings of each dump file separately, so a song heard in 300 files is
stored 300 times (~90% of the rows are repeats). The build only needs, per song: the most played
spelling, total plays and a recording MBID. To keep later merges exact, each row also carries the
plays behind its spelling (`top`) and behind its MBID (`mbid_top`).

  pass 1  batches of files → per-batch reduce → data/lb/names_tmp/b<batch>_s<shard>.parquet
  pass 2  per shard: reduce the batch files (and an existing names_all shard) → names_all

Verification (--verify, run automatically): distinct songs and total plays of names_all equal
those of the inputs. --delete-inputs removes the merged per-file files, only after it passes.
Incremental: new per-file names (a later lb-ingest) are folded into the existing names_all.
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from swave import lb
from swave.pipeline.lb_ingest import LB_DIR

NAMES = LB_DIR / "names"
ALL = LB_DIR / "names_all"
TMP = LB_DIR / "names_tmp"
SHARDS = 32
BATCH = 10  # files per pass-1 batch (~7M rows, well under 1 GB in Arrow)
COLS = ["song", "artist", "title", "mbid", "top", "plays", "mbid_top"]


def log(msg: str, t0: float) -> None:
    print(f"[{(time.time() - t0) / 60:5.1f} min] {msg}", flush=True)


def per_file(t: pa.Table) -> pa.Table:
    """A per-file names table → the merge schema (spelling plays = the file's plays)."""
    plays = t["plays"].cast(pa.int64())
    has = pc.is_valid(t["mbid"])
    return pa.table({
        "song": t["song"].cast(pa.int64()), "artist": t["artist"].cast(pa.large_string()),
        "title": t["title"].cast(pa.large_string()), "mbid": t["mbid"].cast(pa.large_string()),
        "top": plays, "plays": plays,
        "mbid_top": pc.if_else(has, plays, pa.scalar(0, pa.int64()))})


def reduce(t: pa.Table) -> pa.Table:
    """Rows of the merge schema → one row per song: spelling with the highest `top`, MBID with
    the highest `mbid_top`, plays summed. Ties: the first row in input order (stable sort)."""
    if t.num_rows == 0:
        return t
    by_top = t.sort_by([("song", "ascending"), ("top", "descending")])
    total = t.group_by("song").aggregate([("plays", "sum")]).sort_by("song")
    first = _firsts(by_top["song"])
    best = by_top.take(first)
    mb = t.filter(pc.is_valid(t["mbid"])).sort_by([("song", "ascending"), ("mbid_top", "descending")])
    mb = mb.take(_firsts(mb["song"])) if mb.num_rows else mb
    # align the MBID rows to `best` (both sorted by song; MBID rows are a subset)
    pos = np.searchsorted(best["song"].to_numpy(), mb["song"].to_numpy())
    mbid = np.full(best.num_rows, None, dtype=object)
    mbid_top = np.zeros(best.num_rows, dtype=np.int64)
    mbid[pos] = mb["mbid"].to_numpy(zero_copy_only=False)
    mbid_top[pos] = mb["mbid_top"].to_numpy()
    assert np.array_equal(total["song"].to_numpy(), best["song"].to_numpy())
    return pa.table({
        "song": best["song"], "artist": best["artist"], "title": best["title"],
        "mbid": pa.array(mbid, pa.large_string()), "top": best["top"],
        "plays": total["plays_sum"], "mbid_top": pa.array(mbid_top)})


def _firsts(sorted_keys: pa.ChunkedArray) -> np.ndarray:
    k = sorted_keys.to_numpy()
    return np.flatnonzero(np.r_[True, k[1:] != k[:-1]]) if len(k) else np.array([], np.int64)


def shard_of(song: pa.ChunkedArray) -> np.ndarray:
    return (song.to_numpy().view(np.uint64) % SHARDS).astype(np.int64)


def inputs() -> list[Path]:
    return sorted(NAMES.glob("*.parquet"), key=lambda p: lb.file_key(p.stem)) if NAMES.exists() else []


def totals(files: list[Path], t0: float) -> tuple[int, int]:
    """(distinct songs, total plays) straight from the per-file inputs (+ existing names_all)."""
    old = sorted(ALL.glob("s*.parquet")) if ALL.exists() else []
    every = old + files
    songs, plays, pending = np.array([], np.int64), 0, []
    for i, f in enumerate(every):
        t = pq.read_table(f, columns=["song", "plays"])
        plays += int(pc.sum(t["plays"]).as_py() or 0)
        pending.append(t["song"].to_numpy())
        if len(pending) == 50 or i == len(every) - 1:
            songs = np.union1d(songs, np.unique(np.concatenate(pending)))
            pending = []
            log(f"  verify inputs: {i + 1}/{len(every)} files", t0)
    return len(songs), plays


def merge(t0: float) -> tuple[int, int]:
    files = inputs()
    if not files:
        raise SystemExit("no per-file names to merge")
    log(f"{len(files)} per-file names files, {sum(f.stat().st_size for f in files) / 1e9:.1f} GB", t0)
    shutil.rmtree(TMP, ignore_errors=True)
    TMP.mkdir(parents=True)
    batches = [files[i:i + BATCH] for i in range(0, len(files), BATCH)]
    for b, group in enumerate(batches):
        t = reduce(pa.concat_tables([per_file(pq.read_table(f)) for f in group]))
        s = shard_of(t["song"])
        for k in range(SHARDS):
            pq.write_table(t.filter(pa.array(s == k)), TMP / f"b{b}_s{k}.parquet", compression="zstd")
        log(f"  pass 1: batch {b + 1}/{len(batches)} ({t.num_rows:,} songs)", t0)
    out = LB_DIR / "names_all.new"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir()
    songs = plays = 0
    for k in range(SHARDS):
        parts = [pq.read_table(f) for f in sorted(TMP.glob(f"b*_s{k}.parquet"))]
        if (ALL / f"s{k}.parquet").exists():
            parts.append(pq.read_table(ALL / f"s{k}.parquet"))
        t = reduce(pa.concat_tables(parts).select(COLS))
        pq.write_table(t, out / f"s{k}.parquet", compression="zstd")
        songs += t.num_rows
        plays += int(pc.sum(t["plays"]).as_py() or 0)
        if k % 8 == 7:
            log(f"  pass 2: shard {k + 1}/{SHARDS}", t0)
    return songs, plays


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--delete-inputs", action="store_true",
                    help="after a verified merge, delete the per-file names it folded in")
    args = ap.parse_args()
    t0 = time.time()
    files = inputs()
    want = totals(files, t0)
    log(f"inputs: {want[0]:,} distinct songs, {want[1]:,} plays", t0)
    got = merge(t0)
    log(f"merged: {got[0]:,} songs, {got[1]:,} plays", t0)
    ok = got == want
    new = LB_DIR / "names_all.new"
    size = sum(f.stat().st_size for f in new.glob("*.parquet"))
    report = {"files": len(files), "songs": got[0], "plays": got[1], "expected_songs": want[0],
              "expected_plays": want[1], "ok": ok, "gb": round(size / 1e9, 2)}
    (new / "merge.json").write_text(json.dumps(report, indent=1))
    if not ok:
        raise SystemExit(f"verification FAILED, inputs kept, nothing replaced: {report}")
    shutil.rmtree(ALL, ignore_errors=True)
    new.rename(ALL)
    shutil.rmtree(TMP, ignore_errors=True)
    log(f"names_all: {got[0]:,} songs in {size / 1e9:.2f} GB (verified)", t0)
    if args.delete_inputs:
        for f in files:
            f.unlink()
        log(f"deleted {len(files)} per-file names files", t0)


if __name__ == "__main__":
    main()
