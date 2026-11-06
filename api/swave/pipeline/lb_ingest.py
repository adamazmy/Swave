"""make lb-ingest: ListenBrainz listens of the last N years → compact per-file arrays in data/lb/.

Nothing raw is kept: each Parquet file of the remote Spark dump is range-read (5 columns, one row
group at a time) and reduced to, per listen, (user int32, ts int32, song int64), where song =
`lb.song_hash(title, artist)`. Rows are partitioned into `lb.BUCKETS` buckets by user so the
session step can load one bucket at a time. Per file, the distinct songs with their most common
spelling + MusicBrainz recording id go to data/lb/names/. Resumable: finished files are skipped.

    data/lb/members.jsonl        tar members (newest first), listed once by walking the tar backwards
    data/lb/listens/<n>/*.npy    user, ts, song (sorted by bucket) + bounds[BUCKETS + 1]
    data/lb/names/<n>.parquet    song, title, artist, mbid, plays
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from swave import lb
from swave.config import ROOT

LB_DIR = ROOT / "data" / "lb"
PROBE_EVERY = 10  # while listing, check listened_at every N members to know when to stop
DEFAULT_SINCE = "2023-11-01"


def unix(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def ts_range(client: httpx.Client, url: str, m: lb.Member) -> tuple[int, int]:
    """(min, max) listened_at of a file, reading only that column."""
    t = pq.ParquetFile(lb.Remote(client, url, m.offset, m.size)).read(columns=["listened_at"])
    mm = pc.min_max(t["listened_at"].cast(pa.int64()))
    return mm["min"].as_py() // 1_000_000_000, mm["max"].as_py() // 1_000_000_000


def max_ts(client: httpx.Client, url: str, m: lb.Member) -> int:
    return ts_range(client, url, m)[1]


def older_than(members: list[lb.Member], until: int, min_ts) -> list[lb.Member]:
    """The members holding listens before `until`. The dump's files are time-ordered and `members`
    runs newest → oldest, so binary-search the first file whose earliest listen is before `until`
    (~log2(n) column reads) instead of opening every newer file. This dump's Parquet files carry
    no row-group statistics, so a per-group skip is not possible."""
    lo, hi = 0, len(members)
    while lo < hi:
        mid = (lo + hi) // 2
        if min_ts(members[mid]) < until:
            hi = mid
        else:
            lo = mid + 1
    return members[lo:]


def list_members(url: str, since: int) -> list[lb.Member]:
    """Members newest → oldest, down to the first one that ends before `since` (cached)."""
    path = LB_DIR / f"members_{lb.dump_no(url)}.jsonl"  # one listing per dump
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    members = [lb.Member(r["name"], r["offset"], r["size"]) for r in rows if "name" in r]
    if rows and rows[-1].get("end"):
        listed_to = rows[-1].get("since", unix(DEFAULT_SINCE))  # `"end": true` = an old listing
        if listed_to <= since:
            return members
        # going further back: drop the end marker and keep walking from the oldest listed file
        path.write_text("".join(json.dumps(r) + "\n" for r in rows[:-1]))
    with httpx.Client(timeout=120) as client, open(path, "a") as f:
        for i, m in enumerate(lb.walk_back(client, url, members[-1] if members else None)):
            members.append(m)
            f.write(json.dumps(m._asdict()) + "\n")
            f.flush()
            if (len(members) % PROBE_EVERY == 0) and max_ts(client, url, m) < since:
                f.write(json.dumps({"end": True, "since": since}) + "\n")
                break
            if len(members) % 50 == 0:
                print(f"  listed {len(members)} files", flush=True)
    return members


def member_id(m: lb.Member) -> str:
    """'1511' for the original dump's files, '<dump>-<n>' for any other dump's (numbers collide)."""
    stem = Path(m.name).stem
    dump = lb.dump_no(m.name) if "spark-dump-" in m.name else lb.LEGACY_DUMP
    return stem if dump == lb.LEGACY_DUMP else f"{dump}-{stem}"


def group_range(pf: pq.ParquetFile, g: int) -> tuple[int, int] | None:
    """(min, max) listened_at of row group `g` in unix seconds from the footer statistics, or None."""
    try:
        i = pf.schema_arrow.get_field_index("listened_at")
        typ = pf.schema_arrow.field(i).type
        st = pf.metadata.row_group(g).column(i).statistics
        if st is None or not st.has_min_max:
            return None
        per_s = {"s": 1, "ms": 10**3, "us": 10**6, "ns": 10**9}[typ.unit]
        sec = [pa.scalar(v, type=typ).cast(pa.int64()).as_py() // per_s for v in (st.min, st.max)]
        return sec[0], sec[1]
    except Exception:  # unexpected statistics: read the group and filter instead
        return None


def ingest_member(url: str, m: lb.Member, since: int, min_free_gb: float = 0,
                  until: int | None = None) -> tuple[str, int]:
    """Reduce one Parquet file to listens in [since, until) + names. Row groups entirely outside
    the range are skipped from their footer statistics (not downloaded); a file with nothing in
    range writes nothing. Returns (id, listens kept; -1 = already done, -2 = skipped: free disk
    below `min_free_gb`)."""
    out = LB_DIR / "listens" / member_id(m)
    if (out / "done").exists():
        return member_id(m), -1
    if min_free_gb and shutil.disk_usage(LB_DIR).free / 1e9 < min_free_gb:
        return member_id(m), -2
    users, tss, songs, names = [], [], [], []
    with httpx.Client(timeout=120) as client:
        src = lb.Remote(client, url, m.offset, m.size)
        pf = pq.ParquetFile(src)
        for g in range(pf.metadata.num_row_groups):
            r = group_range(pf, g)
            if r and (r[1] < since or (until is not None and r[0] >= until)):
                continue
            t = pf.read_row_group(g, columns=lb.COLUMNS)
            ts = pc.divide(t["listened_at"].cast(pa.int64()), 1_000_000_000)
            keep = pc.greater_equal(ts, since)
            if until is not None:
                keep = pc.and_(keep, pc.less(ts, until))
            t = t.filter(keep)
            if t.num_rows == 0:
                continue
            raw = pc.binary_join_element_wise(t["artist_name"], t["recording_name"], "\x1f")
            enc = raw.combine_chunks().dictionary_encode()
            hashes = np.array([lb.song_hash(*reversed(s.split("\x1f", 1)))
                               for s in enc.dictionary.to_pylist()], dtype=np.int64)
            idx = enc.indices.to_numpy()
            users.append(t["user_id"].to_numpy().astype(np.int32))
            tss.append((t["listened_at"].cast(pa.int64()).to_numpy() // 1_000_000_000)
                       .astype(np.int32))
            songs.append(hashes[idx])
            # per distinct spelling: plays and one recording mbid
            df = pd.DataFrame({"i": idx, "mbid": t["recording_mbid"].to_pandas()})
            agg = df.groupby("i").agg(plays=("i", "size"), mbid=("mbid", "first"))
            pairs = [s.split("\x1f", 1) for s in enc.dictionary.to_pylist()]
            names.append(pd.DataFrame({
                "song": hashes[agg.index], "artist": [pairs[i][0] for i in agg.index],
                "title": [pairs[i][1] for i in agg.index], "mbid": agg["mbid"].to_numpy(),
                "plays": agg["plays"].to_numpy()}))
    user = np.concatenate(users) if users else np.array([], np.int32)
    ts_ = np.concatenate(tss) if tss else np.array([], np.int32)
    song = np.concatenate(songs) if songs else np.array([], np.int64)
    if until is not None and len(user) == 0:
        return member_id(m), 0  # entirely after `until` (already covered by another dump)
    bucket = user % lb.BUCKETS
    order = np.argsort(bucket, kind="stable")
    bounds = np.searchsorted(bucket[order], np.arange(lb.BUCKETS + 1))
    out.mkdir(parents=True, exist_ok=True)
    for name, arr in (("user", user[order]), ("ts", ts_[order]), ("song", song[order]),
                      ("bounds", bounds)):
        np.save(out / f"{name}.npy", arr)
    if names:
        n = pd.concat(names)
        plays = n.groupby("song")["plays"].sum()
        best = n.sort_values("plays", ascending=False).drop_duplicates("song").set_index("song")
        best["plays"] = plays
        (LB_DIR / "names").mkdir(exist_ok=True)
        best.reset_index().to_parquet(LB_DIR / "names" / f"{member_id(m)}.parquet", index=False,
                                      compression="zstd")
    (out / "done").touch()
    assert len(user) == 0 or user.max() < 2**31
    return member_id(m), len(user)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=DEFAULT_SINCE, help="first day of listens kept (UTC)")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--url", default=lb.DUMP_URL)
    ap.add_argument("--min-free-gb", type=float, default=0,
                    help="stop taking new files once free disk drops below this (newest go first, "
                         "so the ingested range stays contiguous; rerun later to resume)")
    ap.add_argument("--until", help="first day NOT kept (UTC): take only older history from a newer "
                                    "dump whose recent listens are already ingested")
    args = ap.parse_args()
    since = unix(args.since)
    until = unix(args.until) if args.until else None
    t0 = time.time()
    members = list_members(args.url, since)
    print(f"{len(members)} files in the tar down to {args.since} ({time.time() - t0:.0f}s)", flush=True)
    if until is not None:
        with httpx.Client(timeout=120) as client:
            members = older_than(members, until, lambda m: ts_range(client, args.url, m)[0])
        print(f"{len(members)} files hold listens before {args.until} "
              f"(newest: {Path(members[0].name).stem if members else '-'}, {time.time() - t0:.0f}s)",
              flush=True)
    total, done, skipped = 0, 0, 0
    with ProcessPoolExecutor(args.workers) as pool:
        futs = [pool.submit(ingest_member, args.url, m, since, args.min_free_gb, until)
                for m in members]
        for fut in as_completed(futs):
            _, n = fut.result()
            done += 1
            total += max(n, 0)
            skipped += n == -2
            if done % 10 == 0 or done == len(members):
                el = time.time() - t0
                print(f"  {done}/{len(members)} files, {total:,} listens this run, {el / 60:.0f} min",
                      flush=True)
    if skipped:
        print(f"{skipped} files skipped: free disk below {args.min_free_gb} GB")
    print(f"lb-ingest done in {(time.time() - t0) / 60:.0f} min")


if __name__ == "__main__":
    main()
