"""make preview-bench: coverage of the OLD (v1-v3) and NEW preview resolvers on the same tracks.

Sample: distinct tracks the app has actually shown (`shown` events), in a fixed md5 order, so
before/after are compared on identical, realistic input. Both resolvers query the live APIs now;
the cache (`preview_sources`) is not read or written (Spotify ISRC/duration lookups do fill
`track_meta`). Prints aggregates only (no track data).

  old: Deezer plain search "artist title", deezer.pick (as in v1-v3)
  new: previews chain: Deezer ISRC → Deezer search (loose names, ±3 s) → iTunes (queued)
"""

from __future__ import annotations

import argparse
import time

import httpx

from swave import deezer, previews
from swave.config import connect


def old(http: httpx.Client, m: previews.Meta) -> str | None:
    hit = deezer.search(http, m.artist, m.name)
    return "deezer" if hit and hit[1] else None


def new(http: httpx.Client, m: previews.Meta) -> str | None:
    for source in previews.SOURCES:
        hit = previews.RESOLVERS[source](http, m, 120.0)
        if hit and hit.preview:
            return source
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sample", type=int, default=300)
    ap.add_argument("--days", type=int, default=30, help="shown in the last N days")
    args = ap.parse_args()
    http = httpx.Client(timeout=15.0)
    with connect() as conn:
        uris = [r[0] for r in conn.execute(
            """SELECT track_uri FROM (SELECT DISTINCT track_uri FROM events
               WHERE action = 'shown' AND ts > now() - make_interval(days => %s)) t
               ORDER BY md5(track_uri) LIMIT %s""", (args.days, args.sample)).fetchall()]
        counts = {"old": {}, "new": {}}
        errors = {"old": 0, "new": 0}
        isrc = 0
        t0 = time.time()
        for i, u in enumerate(uris, 1):
            try:
                m = previews.meta(conn, http, u)
            except LookupError:
                continue
            conn.commit()
            isrc += m.isrc is not None
            for name, fn in (("old", old), ("new", new)):
                try:
                    src = fn(http, m) or "none"
                except (deezer.DeezerError, previews.SourceError):
                    errors[name] += 1
                    src = "error"
                counts[name][src] = counts[name].get(src, 0) + 1
            if i % 25 == 0:
                print(f"  {i}/{len(uris)}  ({time.time() - t0:.0f}s)", flush=True)
    n = len(uris)
    print(f"\nSample: {n} tracks shown in the last {args.days} days ({isrc} with an ISRC from Spotify)")
    for name in ("old", "new"):
        c = counts[name]
        ok = sum(v for k, v in c.items() if k not in ("none", "error"))
        detail = ", ".join(f"{k} {v}" for k, v in sorted(c.items()))
        print(f"  {name}: {ok}/{n} = {100 * ok / n:.1f}% have a preview   ({detail})")


if __name__ == "__main__":
    main()
