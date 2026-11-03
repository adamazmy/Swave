"""make preview-report: preview coverage for the last N days, printed to the terminal.

Coverage = preview attempts (clicks on a play button) that ended up playing ÷ all attempts, from
`preview_attempts` (one row per click, with its final outcome; the browser logs it). Also the
cache view: of the tracks resolved so far, how many have a preview at some source.
Reads only; nothing is written or exported.
"""

from __future__ import annotations

import argparse

from swave import previews
from swave.config import connect


def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=7)
    days = ap.parse_args().days
    with connect() as conn:
        c = previews.coverage(conn, days)
        cache = conn.execute(
            """SELECT count(*), count(*) FILTER (WHERE has_any)
               FROM (SELECT track_uri, bool_or(provider_id IS NOT NULL AND has_preview) AS has_any
                     FROM preview_sources GROUP BY 1) t""").fetchone()

    print(f"Preview attempts, last {days} days: {c['attempts']}")
    print(f"  coverage (played): {pct(c['coverage'])}  ({c['ok']} ok, {c['recovered']} after a retry/fallback)")
    for outcome in previews.OUTCOMES:
        n = c["outcomes"].get(outcome, 0)
        print(f"  {outcome:<20} {n:>5}  {pct(n / c['attempts'] if c['attempts'] else None)}")
    if c["ok_by_source"]:
        print("  played from: " + ", ".join(f"{s or '?'} {n}" for s, n in sorted(c["ok_by_source"].items())))
    total, has = cache
    print(f"Cache: {has} of {total} resolved tracks have a preview ({pct(has / total if total else None)})")


if __name__ == "__main__":
    main()
