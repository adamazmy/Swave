"""make feedback: weekly keep rate and the most-removed tracks/artists, printed to the terminal.

Counting rules (see CLAUDE.md):
- Every event is counted once per (session, track): a re-rank shows the same song again and
  logs `shown` again, which would otherwise deflate the keep rate.
- Kept = ✓ keep or ＋ "more like this" (the UI keeps a song you ask for more of).
- Keep rate = kept ÷ shown. Weeks start Monday (UTC).
Reads the events table only; nothing is written or exported.
"""

from __future__ import annotations

import argparse
from typing import NamedTuple

import psycopg

from swave.config import connect


class Week(NamedTuple):
    week: str  # Monday, YYYY-MM-DD
    sessions: int
    shown: int
    kept: int
    removed: int
    saved: int  # sessions that saved a playlist

    @property
    def keep_rate(self) -> float | None:
        return self.kept / self.shown if self.shown else None


WEEKLY_SQL = """
WITH ev AS (
    SELECT DISTINCT date_trunc('week', ts AT TIME ZONE 'UTC')::date AS wk, session_id,
           CASE WHEN action = 'more_like_this' THEN 'kept' ELSE action END AS action, track_uri
    FROM events
    WHERE ts >= date_trunc('week', now() AT TIME ZONE 'UTC') - make_interval(weeks => %(weeks)s - 1)
)
SELECT wk::text,
       count(DISTINCT session_id),
       count(*) FILTER (WHERE action = 'shown'),
       count(*) FILTER (WHERE action = 'kept'),
       count(*) FILTER (WHERE action = 'removed'),
       count(DISTINCT session_id) FILTER (WHERE action = 'saved')
FROM ev GROUP BY wk ORDER BY wk
"""

# Removal counted once per session; names from the tracks table (left join: survives re-ingest).
REMOVED_SQL = """
WITH r AS (
    SELECT DISTINCT session_id, track_uri FROM events
    WHERE action = 'removed' AND ts >= now() - make_interval(weeks => %(weeks)s)
)
SELECT {key}, count(*) AS n
FROM r LEFT JOIN tracks t USING (track_uri)
GROUP BY {key} ORDER BY n DESC, 1 LIMIT %(top)s
"""


def weekly(conn: psycopg.Connection, weeks: int) -> list[Week]:
    return [Week(*row) for row in conn.execute(WEEKLY_SQL, {"weeks": weeks}).fetchall()]


def most_removed_tracks(conn: psycopg.Connection, weeks: int, top: int) -> list[tuple[str, int]]:
    key = "coalesce(t.name || ' · ' || t.artist, r.track_uri)"
    return [(r[0], r[1]) for r in
            conn.execute(REMOVED_SQL.format(key=key), {"weeks": weeks, "top": top}).fetchall()]


def most_removed_artists(conn: psycopg.Connection, weeks: int, top: int) -> list[tuple[str, int]]:
    key = "coalesce(t.artist, '(unknown artist)')"
    return [(r[0], r[1]) for r in
            conn.execute(REMOVED_SQL.format(key=key), {"weeks": weeks, "top": top}).fetchall()]


def pct(x: float | None) -> str:
    return "   -" if x is None else f"{x:5.1%}"


def report(conn: psycopg.Connection, weeks: int, top: int) -> str:
    rows = weekly(conn, weeks)
    if not rows:
        return f"No Swave activity in the last {weeks} weeks. Build a vibe and come back!"

    out = [f"Swave feedback · last {weeks} weeks (UTC, weeks start Monday)", ""]
    out.append(f"{'week of':<11} {'sessions':>8} {'shown':>6} {'kept':>5} {'keep rate':>9} "
               f"{'removed':>7} {'saved':>5}")
    for w in rows:
        out.append(f"{w.week:<11} {w.sessions:>8} {w.shown:>6} {w.kept:>5} {pct(w.keep_rate):>9} "
                   f"{w.removed:>7} {w.saved:>5}")
    shown, kept = sum(w.shown for w in rows), sum(w.kept for w in rows)
    out.append(f"{'total':<11} {sum(w.sessions for w in rows):>8} {shown:>6} {kept:>5} "
               f"{pct(kept / shown if shown else None):>9} {sum(w.removed for w in rows):>7} "
               f"{sum(w.saved for w in rows):>5}")

    for title, items in (("Most-removed tracks", most_removed_tracks(conn, weeks, top)),
                         ("Most-removed artists", most_removed_artists(conn, weeks, top))):
        out += ["", title]
        out += [f"  {n:>3}×  {label}" for label, n in items] or ["  (nothing removed yet)"]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--weeks", type=int, default=8)
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()
    with connect() as conn:
        print(report(conn, args.weeks, args.top))


if __name__ == "__main__":
    main()
