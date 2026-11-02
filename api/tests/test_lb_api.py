"""ListenBrainz scorer + lazy Spotify resolution against a throwaway DB (SYNTHETIC data only)."""

from __future__ import annotations

import os

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo

from swave import matching, resolve, spotify
from swave.main import create_app
from swave.matching import song_hash

TEST_DB = "swave_test_lb"
BOTH = {"cooccurrence_mpd": 1.0, "cooccurrence_lb": 1.0}


def uri(i: int) -> str:
    return f"spotify:track:fake{i:018d}"


LB_URI = "spotify:track:lbfake0000000000000001"  # what the (mocked) labs service returns
# MPD tracks: (track_id, name, artist, playlist_count)
TRACKS = [(0, "Neon Harbour", "The Placeholders", 900), (1, "Paper Lanterns", "The Placeholders", 300),
          (2, "Static Bloom", "Mock Orchestra", 200)]
NEIGHBOURS = [(1, 0, 0.30, 120), (1, 2, 0.50, 80)]
# ListenBrainz-only tracks: (track_id, name, artist, mbid, listeners, pop_est)
LB_TRACKS = [(100, "Midnight Static", "Nova Fake", "00000000-0000-0000-0000-00000000000a", 50, 40),
             (101, "Unfindable Song", "Ghost Artist", None, 20, 10)]
NEIGHBOURS_LB = [(1, 100, 0.9, 30), (1, 101, 0.8, 20), (1, 2, 0.3, 10), (100, 1, 0.9, 30),
                 (100, 2, 0.2, 5)]


@pytest.fixture(scope="module")
def dsn() -> str:
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
    test_dsn = make_conninfo(base, dbname=TEST_DB)
    with psycopg.connect(test_dsn) as conn:
        conn.execute("""
            CREATE EXTENSION IF NOT EXISTS pg_trgm;
            CREATE TABLE tracks (track_uri text PRIMARY KEY, track_id int UNIQUE, name text,
                                 artist text, album text, playlist_count int);
            CREATE TABLE neighbours (track_id int, neighbour_id int, sim real, co_count int);
            CREATE TABLE lb_tracks (track_id int PRIMARY KEY, song bigint NOT NULL UNIQUE,
                name text NOT NULL, artist text NOT NULL, recording_mbid text,
                listeners int NOT NULL, pop_est int NOT NULL);
            CREATE TABLE neighbours_lb (track_id int, neighbour_id int, sim real, co_count int);
        """)
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO tracks VALUES (%s, %s, %s, %s, 'Fake Album', %s)",
                            [(uri(t), t, n, a, pc) for t, n, a, pc in TRACKS])
            cur.executemany("INSERT INTO neighbours VALUES (%s, %s, %s, %s)", NEIGHBOURS)
            cur.executemany("INSERT INTO lb_tracks VALUES (%s, %s, %s, %s, %s, %s, %s)",
                            [(t, song_hash(n, a), n, a, m, li, p) for t, n, a, m, li, p in LB_TRACKS])
            cur.executemany("INSERT INTO neighbours_lb VALUES (%s, %s, %s, %s)", NEIGHBOURS_LB)
    return test_dsn


class Labs:
    """Mocked labs.api.listenbrainz.org: knows one MBID; can be switched to failing."""

    def __init__(self):
        self.calls, self.fail = 0, False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == "labs.api.listenbrainz.org"
        self.calls += 1
        if self.fail:
            return httpx.Response(500)
        body = [{"recording_mbid": m, "spotify_track_ids":
                 [LB_URI.split(":")[-1]] if m.endswith("a") else []}
                for m in (x["recording_mbid"] for x in __import__("json").loads(request.content))]
        return httpx.Response(200, json=body)


@pytest.fixture
def labs(dsn, monkeypatch):
    lab = Labs()
    searches: list[tuple[str, str]] = []

    def fake_search(conn, http, title, artist):
        searches.append((title, artist))
        return None  # Spotify knows none of the made-up songs

    monkeypatch.setattr(spotify, "search_track", fake_search)
    lab.searches = searches
    with psycopg.connect(dsn) as conn:
        conn.execute("DROP TABLE IF EXISTS lb_spotify CASCADE")  # the app recreates it + view
    return lab


@pytest.fixture
def client(dsn, labs):
    with TestClient(create_app(dsn, httpx.Client(transport=httpx.MockTransport(labs)),
                               weights=BOTH)) as c:
        yield c


def test_recommend_mixes_scorers_and_resolves_lb_tracks(client, labs, dsn):
    body = client.post("/recommend", json={"seeds": [uri(1)]}).json()
    got = {r["track_uri"]: r for r in body["results"]}
    assert LB_URI in got  # resolved through the labs service
    assert got[LB_URI]["name"] == "Midnight Static" and got[LB_URI]["album"] == ""
    assert got[LB_URI]["reason"] == "appears with your seed in 30 listening sessions"
    # track 2: MPD gives 1.0 (its top), LB 0.3/0.9 = 0.33 < half of that → MPD reason only
    assert got[uri(2)]["reason"] == "appears with your seed in 80 shared playlists"
    assert len(got) == 3  # track 101 has no Spotify match → dropped
    assert labs.calls == 1 and labs.searches == [("Unfindable Song", "Ghost Artist")]
    with psycopg.connect(dsn) as conn:
        cache = dict(conn.execute("SELECT song, track_uri FROM lb_spotify").fetchall())
    assert cache == {song_hash("Midnight Static", "Nova Fake"): LB_URI,
                     song_hash("Unfindable Song", "Ghost Artist"): None}
    # second call: everything cached, no network
    client.post("/recommend", json={"seeds": [uri(1)]})
    assert labs.calls == 1 and len(labs.searches) == 1


def test_resolved_lb_track_works_as_seed(client):
    client.post("/recommend", json={"seeds": [uri(1)]})  # resolves track 100
    body = client.post("/recommend", json={"seeds": [LB_URI]}).json()
    assert body["seeds"][0]["name"] == "Midnight Static"
    assert [r["track_uri"] for r in body["results"]][0] == uri(1)


def test_search_includes_lb_tracks(client, labs):
    found = client.get("/search", params={"q": "midnight"}).json()
    assert [t["track_uri"] for t in found] == [LB_URI]
    assert client.get("/search", params={"q": "unfindable"}).json() == []  # no Spotify match
    assert labs.calls == 1


def test_labs_failure_is_not_cached(client, labs, dsn):
    labs.fail = True
    body = client.post("/recommend", json={"seeds": [uri(1)]}).json()
    assert LB_URI not in {r["track_uri"] for r in body["results"]}
    with psycopg.connect(dsn) as conn:
        cached = conn.execute("SELECT count(*) FROM lb_spotify WHERE track_uri IS NOT NULL").fetchone()[0]
    assert cached == 0
    labs.fail = False
    body = client.post("/recommend", json={"seeds": [uri(1)]}).json()
    assert LB_URI in {r["track_uri"] for r in body["results"]}


def test_spotify_not_connected_is_not_cached_as_miss(dsn, labs, monkeypatch):
    def offline(*_):
        raise spotify.NotConnected

    monkeypatch.setattr(spotify, "search_track", offline)
    http = httpx.Client(transport=httpx.MockTransport(labs))
    with TestClient(create_app(dsn, http, weights=BOTH)):  # creates lb_spotify
        pass
    with psycopg.connect(dsn) as conn:
        assert resolve.resolve(conn, http, [101]) == {}
        assert conn.execute("SELECT count(*) FROM lb_spotify").fetchone()[0] == 0


def test_import_matches_lb_only_songs_and_remembers_their_uri(client, dsn):
    item = matching.Item("spotify:track:userversion00000000001", "Midnight Static (Radio Edit)",
                         ["Nova Fake"])
    with psycopg.connect(dsn) as conn:
        [m] = matching.map_items(conn, [item])
        assert m.how == "lb" and m.row[0] == item.uri and m.row[1] == "Midnight Static"
        conn.commit()
        assert conn.execute("SELECT track_uri FROM lb_spotify WHERE song = %s",
                            (song_hash("Midnight Static", "Nova Fake"),)).fetchone()[0] == item.uri


def test_build_install_swaps_tables_and_keeps_the_view(client, dsn):
    """lb-build's final swap must work while the API's all_tracks view exists."""
    from swave.pipeline import lb_build

    client.post("/recommend", json={"seeds": [uri(1)]})  # resolves track 100 into lb_spotify
    with psycopg.connect(dsn) as conn:
        conn.execute("""
            CREATE TABLE neighbours_lb_new (LIKE neighbours_lb);
            CREATE TABLE lb_tracks_new (LIKE lb_tracks);
            CREATE TABLE lb_holdout_new (sess int, pos int, track_id int);
            INSERT INTO lb_tracks_new SELECT * FROM lb_tracks;
            INSERT INTO neighbours_lb_new SELECT * FROM neighbours_lb;
        """)
        lb_build.install(conn, {"vocab": 2})
        assert conn.execute("SELECT stats->>'vocab' FROM lb_meta").fetchone()[0] == "2"
        # the view is back and still sees the resolved ListenBrainz track
        assert conn.execute("SELECT track_id FROM all_tracks WHERE track_uri = %s",
                            (LB_URI,)).fetchone()[0] == 100
