"""make lb-report: match stats of the last lb-build + how many new tracks are post-2017.

The MPD is a 2017/18 snapshot, so ListenBrainz songs it lacks are expected to be mostly newer.
Release years come from the MusicBrainz API (`first-release-date` of the recording) for a random
sample of new tracks that have a recording MBID: 1 request/second (MusicBrainz's rate limit), with
an identifying User-Agent as MusicBrainz asks. Prints aggregate numbers only.
"""

from __future__ import annotations

import argparse
import math
import time

import httpx

from swave.config import connect

MB_URL = "https://musicbrainz.org/ws/2/recording/{}"
USER_AGENT = "Swave/0.2 ( https://github.com/adamazmy/Swave )"


def release_year(client: httpx.Client, mbid: str) -> int | None:
    for _ in range(3):
        r = client.get(MB_URL.format(mbid), params={"fmt": "json"})
        if r.status_code == 503:  # rate limited: back off
            time.sleep(2)
            continue
        if r.status_code != 200:
            return None
        date = r.json().get("first-release-date") or ""
        return int(date[:4]) if date[:4].isdigit() else None
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=1000)
    args = ap.parse_args()
    with connect() as conn:
        stats = conn.execute("SELECT stats FROM lb_meta").fetchone()[0]
        print("lb-build:", ", ".join(f"{k}={v:,}" if isinstance(v, int) else f"{k}={v}"
                                     for k, v in stats.items()))
        v = stats["vocab"]
        print(f"match rate: {(stats['exact'] + stats['loose']) / v:.1%} "
              f"({stats['exact'] / v:.1%} exact, {stats['loose'] / v:.1%} loose); "
              f"{stats['new']:,} new tracks")
        n_mbid, n_new = conn.execute(
            "SELECT count(recording_mbid), count(*) FROM lb_tracks").fetchone()
        sample = [r[0] for r in conn.execute(
            """SELECT recording_mbid FROM lb_tracks WHERE recording_mbid IS NOT NULL
               ORDER BY md5(recording_mbid) LIMIT %s""", (args.sample,))]

    years: list[int] = []
    with httpx.Client(timeout=15, headers={"User-Agent": USER_AGENT}) as client:
        for i, mbid in enumerate(sample, 1):
            t = time.time()
            y = release_year(client, mbid)
            if y:
                years.append(y)
            if i % 100 == 0:
                print(f"  {i}/{len(sample)} looked up", flush=True)
            time.sleep(max(0.0, 1.05 - (time.time() - t)))
    post = sum(y >= 2018 for y in years)
    p = post / len(years) if years else 0.0
    ci = 1.96 * math.sqrt(p * (1 - p) / len(years)) if years else 0.0
    print(f"release year known for {len(years)}/{len(sample)} sampled new tracks "
          f"({n_mbid:,} of {n_new:,} new tracks have an MBID)")
    print(f"post-2017 (first released 2018+): {p:.1%} ± {ci:.1%} → ≈ {round(p * n_new):,} "
          f"of {n_new:,} new tracks")
    by = {}
    for y in years:
        k = "≤2017" if y <= 2017 else str(y)
        by[k] = by.get(k, 0) + 1
    print("by first-release year:", ", ".join(f"{k}: {v}" for k, v in sorted(by.items())))


if __name__ == "__main__":
    main()
