"""lb-install: candidate run → app tables, old ones kept, rollback (SYNTHETIC data, throwaway DB)."""

from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd
import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from swave import lb
from swave.pipeline import lb_install

TEST_DB = "swave_test_lb_install"
N_MPD = 3  # MPD track ids 0..2; LB-only ids start at 3


@pytest.fixture()
def conn():
    base = os.environ.get("DATABASE_URL")
    if not base:
        pytest.skip("DATABASE_URL not set")
    try:
        admin = psycopg.connect(base, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("Postgres not reachable (run `make db`)")
    with admin:
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(TEST_DB)))
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TEST_DB)))
    with psycopg.connect(make_conninfo(base, dbname=TEST_DB)) as c:
        # the live index as lb-build leaves it (primary key still named lb_tracks_new_pkey)
        c.execute("""
            CREATE EXTENSION IF NOT EXISTS pg_trgm;
            CREATE TABLE tracks (track_uri text PRIMARY KEY, track_id int UNIQUE, name text,
                                 artist text, album text, playlist_count int);
            INSERT INTO tracks VALUES ('spotify:track:fake0', 0, 'A', 'X', 'Al', 9),
                                      ('spotify:track:fake1', 1, 'B', 'X', 'Al', 5),
                                      ('spotify:track:fake2', 2, 'C', 'Y', 'Al', 3);
            CREATE TABLE lb_tracks (track_id int NOT NULL, song bigint NOT NULL, name text NOT NULL,
                artist text NOT NULL, recording_mbid text, listeners int NOT NULL, pop_est int NOT NULL,
                CONSTRAINT lb_tracks_new_pkey PRIMARY KEY (track_id));
            CREATE UNIQUE INDEX lb_tracks_song ON lb_tracks (song);
            CREATE INDEX lb_tracks_search ON lb_tracks USING gin (lower(name || ' ' || artist) gin_trgm_ops);
            CREATE TABLE neighbours_lb (track_id int, neighbour_id int, sim real, co_count int);
            CREATE INDEX neighbours_lb_track ON neighbours_lb (track_id);
            CREATE TABLE lb_holdout (sess int, pos int, track_id int);
            CREATE TABLE lb_meta (built_at timestamptz, git text, stats jsonb);
            INSERT INTO lb_tracks VALUES (3, 111, 'Old Song', 'Old Artist', NULL, 30, 4);
            INSERT INTO neighbours_lb VALUES (0, 3, 0.5, 7);
            INSERT INTO lb_meta VALUES (now(), 'old', '{"run": "old"}');
        """)
        c.execute(lb.SCHEMA)
        c.execute("INSERT INTO lb_spotify VALUES (222, 'spotify:track:fakelb', now())")
        c.execute(lb.VIEW)
        c.commit()
        yield c


@pytest.fixture()
def run(tmp_path, monkeypatch):
    """A candidate run: songs 10, 20 match MPD 0, 1; songs 222, 333 are new (ids 3, 4)."""
    d = tmp_path / "cand"
    d.mkdir()
    pd.DataFrame({"song": [10, 20, 222, 333], "track_id": [0, 1, 3, 4], "how": [1, 1, 0, 0],
                  "listeners": [50, 40, 12, 11], "pop_est": [0, 0, 6, 5],
                  "name": [None, None, "New Song", "Tab\tName"],
                  "artist": [None, None, "Nova Fake", "Ghost"],
                  "mbid": [None, None, "00000000-0000-0000-0000-00000000000a", None]}
                 ).to_parquet(d / "tracks.parquet")
    src, nbr = np.array([0, 0, 3], np.int32), np.array([3, 1, 0], np.int32)
    sim, co = np.array([0.123456789, 0.25, 0.123456789], np.float32), np.array([9, 4, 9], np.int32)
    for name, a in (("src", src), ("nbr", nbr), ("sim", sim), ("co", co)):
        a.tofile(d / f"nbr_{name}.bin")
    (d / "meta.json").write_text(json.dumps({
        "git": "abc", "vocab": 4, "new": 2, "neighbour_rows": 3, "steps": {},
        "config": {"k": 100, "min_co": 2, "min_listeners": 10, "half_life": 2.0}}))
    # hold-out: session 0 = songs 10, 222, 999 (999 unknown to the run)
    np.savez(tmp_path / "holdout.npz", offsets=np.array([0, 3]), songs=np.array([10, 222, 999]),
             seeds=np.array([[10]]))
    monkeypatch.setattr(lb_install, "RUNS", tmp_path)
    monkeypatch.setattr(lb_install, "HOLDOUT", tmp_path / "holdout.npz")
    return "cand"


def install(conn, run):
    lb_install.normalise_pkey(conn)
    conn.commit()
    lb_install.load(conn, run, 0.0)
    lb_install.swap_in(conn)


def test_install_swaps_in_the_run_and_keeps_the_old_index(conn, run):
    install(conn, run)
    assert conn.execute("SELECT track_id, name FROM lb_tracks ORDER BY 1").fetchall() == [
        (3, "New Song"), (4, "Tab\tName")]
    rows = conn.execute("SELECT track_id, neighbour_id, sim, co_count FROM neighbours_lb ORDER BY 1, 2").fetchall()
    assert rows == [(0, 1, 0.25, 4), (0, 3, pytest.approx(0.123456789, rel=1e-7), 9),
                    (3, 0, pytest.approx(0.123456789, rel=1e-7), 9)]
    # float32 round-trips exactly into `real`
    assert np.float32(rows[1][2]) == np.float32(0.123456789)
    assert conn.execute("SELECT sess, pos, track_id FROM lb_holdout ORDER BY pos").fetchall() == [
        (0, 0, 0), (0, 1, 3)]
    assert conn.execute("SELECT stats->>'run', stats->>'half_life' FROM lb_meta").fetchone() == ("cand", "2.0")
    # old index kept, with its indexes renamed out of the way
    assert conn.execute("SELECT name FROM lb_tracks_old").fetchall() == [("Old Song",)]
    assert conn.execute("SELECT count(*) FROM neighbours_lb_old").fetchone()[0] == 1
    idx = {r[0] for r in conn.execute("SELECT indexname FROM pg_indexes WHERE tablename LIKE 'lb_tracks%'")}
    assert idx == {"lb_tracks_pkey", "lb_tracks_song", "lb_tracks_search",
                   "lb_tracks_old_pkey", "lb_tracks_old_song", "lb_tracks_old_search"}
    # the view follows the new lb_tracks; lb_spotify (keyed by song) carries over
    assert conn.execute("SELECT track_id FROM all_tracks WHERE track_uri = 'spotify:track:fakelb'"
                        ).fetchone()[0] == 3


def test_rollback_restores_the_old_index_and_keeps_the_new_one(conn, run):
    install(conn, run)
    lb_install.rollback(conn)
    assert conn.execute("SELECT name FROM lb_tracks").fetchall() == [("Old Song",)]
    assert conn.execute("SELECT stats->>'run' FROM lb_meta").fetchone()[0] == "old"
    assert conn.execute("SELECT count(*) FROM lb_tracks_old").fetchone()[0] == 2
    # song 222 is not in the old index, so the view no longer resolves it
    assert conn.execute("SELECT count(*) FROM all_tracks WHERE track_uri = 'spotify:track:fakelb'"
                        ).fetchone()[0] == 0
    lb_install.rollback(conn)  # and forward again
    assert conn.execute("SELECT count(*) FROM lb_tracks").fetchone()[0] == 2


def test_drop_old_removes_only_the_old_tables(conn, run):
    install(conn, run)
    lb_install.drop_old(conn)
    assert not lb_install.exists(conn, "lb_tracks_old")
    assert conn.execute("SELECT count(*) FROM neighbours_lb").fetchone()[0] == 3


def test_load_refuses_a_run_built_against_other_mpd_ids(conn, run):
    conn.execute("INSERT INTO tracks VALUES ('spotify:track:fake3', 3, 'D', 'Z', 'Al', 2)")
    conn.commit()
    with pytest.raises(SystemExit, match="MPD tracks changed"):
        lb_install.load(conn, run, 0.0)
