"""Feedback report on SYNTHETIC events in the throwaway test DB."""

from __future__ import annotations

import psycopg
import pytest

from swave.main import APP_SCHEMA
from swave.pipeline import feedback
from tests.test_api import dsn, uri  # noqa: F401  (reuse the synthetic test database)


@pytest.fixture
def conn(dsn):  # noqa: F811
    with psycopg.connect(dsn) as c:
        c.execute(APP_SCHEMA)
        c.execute("DELETE FROM events")
        yield c
        c.rollback()


def add(conn, action, track, session="s1", days_ago=0):
    conn.execute(
        "INSERT INTO events (ts, session_id, action, track_uri) VALUES (now() - make_interval(days => %s), %s, %s, %s)",
        (days_ago, session, action, uri(track) if track is not None else None),
    )


def this_week(conn) -> feedback.Week:
    rows = feedback.weekly(conn, 1)
    assert len(rows) == 1
    return rows[0]


def test_shown_counted_once_per_session(conn):
    for _ in range(3):  # three re-ranks show the same two songs again
        add(conn, "shown", 1)
        add(conn, "shown", 2)
    add(conn, "kept", 1)
    w = this_week(conn)
    assert (w.shown, w.kept, w.keep_rate) == (2, 1, 0.5)


def test_more_like_this_counts_as_kept_once(conn):
    for t in (1, 2, 3, 4):
        add(conn, "shown", t)
    add(conn, "more_like_this", 1)
    add(conn, "kept", 1)  # same song: still one keep
    add(conn, "more_like_this", 2)
    w = this_week(conn)
    assert (w.kept, w.keep_rate) == (2, 0.5)


def test_sessions_and_saves(conn):
    add(conn, "shown", 1, session="a")
    add(conn, "saved", 1, session="a")
    add(conn, "saved", 2, session="a")
    add(conn, "shown", 1, session="b")
    w = this_week(conn)
    assert (w.sessions, w.shown, w.saved) == (2, 2, 1)


def test_old_events_outside_window_are_ignored(conn):
    add(conn, "shown", 1, days_ago=60)
    assert feedback.weekly(conn, 4) == []
    assert "No Swave activity" in feedback.report(conn, 4, 5)


def test_most_removed_tracks_and_artists(conn):
    # Synthetic catalogue from test_api: 2 = Static Bloom / Mock Orchestra, 4 = Quiet Engine / Mock Orchestra
    for s in ("a", "b", "c"):
        add(conn, "removed", 2, session=s)
    add(conn, "removed", 2, session="a")  # same session twice: counted once
    add(conn, "removed", 4, session="a")
    add(conn, "removed", 1, session="b")
    tracks = feedback.most_removed_tracks(conn, 4, 10)
    assert tracks[0] == ("Static Bloom · Mock Orchestra", 3)
    artists = dict(feedback.most_removed_artists(conn, 4, 10))
    assert artists["Mock Orchestra"] == 4 and artists["The Placeholders"] == 1


def test_report_renders(conn):
    add(conn, "shown", 1)
    add(conn, "shown", 2)
    add(conn, "kept", 2)
    add(conn, "removed", 1)
    text = feedback.report(conn, 8, 5)
    assert "keep rate" in text and "50.0%" in text
    assert "Paper Lanterns · The Placeholders" in text
