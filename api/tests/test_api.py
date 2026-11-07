"""API tests against a throwaway `swave_test` database with SYNTHETIC tracks (never dataset data).

Skipped when Postgres isn't reachable (run `make db` first).
"""

from __future__ import annotations

import os

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo

from swave import deezer
from swave.main import create_app

TEST_DB = "swave_test"


def uri(i: int) -> str:
    return f"spotify:track:fake{i:018d}"  # 22-char made-up id


# (track_id, name, artist, playlist_count). Track 0 is the most popular.
TRACKS = [
    (0, "Neon Harbour", "The Placeholders", 900),
    (1, "Paper Lanterns", "The Placeholders", 300),
    (2, "Static Bloom", "Mock Orchestra", 200),
    (3, "Paper Planes Again", "Dummy Duo", 50),
    (4, "Quiet Engine", "Mock Orchestra", 20),
    (5, "100% Synthetic_Song", "Test Artist", 10),
    (6, "Static Bloom - Live", "Mock Orchestra", 15),  # another version of track 2
]
# (track_id, neighbour_id, sim, co_count)
NEIGHBOURS = [
    (1, 0, 0.30, 120), (1, 2, 0.50, 80), (1, 3, 0.40, 30), (1, 4, 0.20, 10), (1, 6, 0.10, 5),
    (2, 0, 0.25, 90), (2, 1, 0.50, 80), (2, 4, 0.45, 15),
]


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
            CREATE TABLE tracks (track_uri text PRIMARY KEY, track_id int UNIQUE, name text,
                                 artist text, album text, playlist_count int);
            CREATE TABLE neighbours (track_id int, neighbour_id int, sim real, co_count int);
            CREATE TABLE index_meta (method text, k int, min_co int, built_at timestamptz, git text);
            INSERT INTO index_meta VALUES ('cosine', 200, 2, now(), 'test');
        """)
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO tracks VALUES (%s, %s, %s, %s, 'Fake Album', %s)",
                [(uri(t), t, name, artist, pc) for t, name, artist, pc in TRACKS],
            )
            cur.executemany("INSERT INTO neighbours VALUES (%s, %s, %s, %s)", NEIGHBOURS)
    return test_dsn


@pytest.fixture
def client(dsn):
    # explicit weights: tests must not depend on the owner's scorers.json
    with TestClient(create_app(dsn, weights={"cooccurrence_mpd": 0.5, "cooccurrence_lb": 0.5})) as c:
        yield c


def test_health(client):
    body = client.get("/health").json()
    assert body["ok"] and body["index"]["method"] == "cosine"
    assert body["version"] == "dev"  # production images bake in SWAVE_GIT


def test_search_matches_all_words_most_popular_first(client):
    names = [t["name"] for t in client.get("/search", params={"q": "paper"}).json()]
    assert names == ["Paper Lanterns", "Paper Planes Again"]
    assert [t["name"] for t in client.get("/search", params={"q": "mock quiet"}).json()] == ["Quiet Engine"]


def test_search_escapes_like_wildcards(client):
    assert [t["name"] for t in client.get("/search", params={"q": "100%"}).json()] == ["100% Synthetic_Song"]
    assert client.get("/search", params={"q": "_s"}).json()[0]["name"] == "100% Synthetic_Song"


def test_search_validates_query(client):
    assert client.get("/search", params={"q": "a"}).status_code == 422


def test_recommend_sums_seeds_and_excludes_them(client):
    body = client.post("/recommend", json={"seeds": [uri(1), uri(2)]}).json()
    got = [r["track_uri"] for r in body["results"]]
    assert uri(1) not in got and uri(2) not in got
    assert got[0] == uri(4) or got[0] == uri(0)
    top = {r["track_uri"]: r for r in body["results"]}
    assert top[uri(0)]["seed_hits"] == 2 and top[uri(0)]["co_count"] == 210
    assert top[uri(0)]["reason"] == "appears with 2 of your 2 seeds in 210 shared playlists"
    assert body["unknown_seeds"] == []


def test_recommend_alpha_penalises_popular(client):
    familiar = client.post("/recommend", json={"seeds": [uri(1)], "alpha": 0}).json()["results"]
    adventurous = client.post("/recommend", json={"seeds": [uri(1)], "alpha": 1}).json()["results"]
    assert familiar[0]["track_uri"] == uri(2)  # highest sim (0.50)
    assert adventurous[0]["track_uri"] == uri(4)  # 0.20/20 beats 0.40/50 and 0.50/200
    assert familiar[0]["reason"] == "appears with your seed in 80 shared playlists"


def test_recommend_exclude_gives_next_best(client):
    first = client.post("/recommend", json={"seeds": [uri(1)], "n": 1}).json()["results"][0]
    nxt = client.post("/recommend", json={"seeds": [uri(1)], "n": 1,
                                          "exclude": [first["track_uri"]]}).json()["results"][0]
    assert nxt["track_uri"] != first["track_uri"] and nxt["track_uri"] == uri(3)


def test_recommend_unknown_seeds(client):
    body = client.post("/recommend", json={"seeds": [uri(1), uri(99)]}).json()
    assert body["unknown_seeds"] == [uri(99)]
    assert [s["name"] for s in body["seeds"]] == ["Paper Lanterns"]
    assert client.post("/recommend", json={"seeds": [uri(99)]}).status_code == 404


def test_recommend_seed_without_neighbours_returns_empty(client):
    assert client.post("/recommend", json={"seeds": [uri(5)]}).json()["results"] == []


def test_recommend_validation(client):
    assert client.post("/recommend", json={"seeds": []}).status_code == 422
    assert client.post("/recommend", json={"seeds": [uri(1)], "alpha": 3}).status_code == 422
    assert client.post("/recommend", json={"seeds": [uri(1)], "adventure": 1.5}).status_code == 422


def test_recommend_adventure_picks_alpha(client):
    familiar = client.post("/recommend", json={"seeds": [uri(1)], "adventure": 0}).json()
    adventurous = client.post("/recommend", json={"seeds": [uri(1)], "adventure": 1}).json()
    assert familiar["alpha"] == 0 and familiar["results"][0]["track_uri"] == uri(2)
    assert adventurous["alpha"] > 0 and adventurous["results"][0]["track_uri"] == uri(4)


def test_events_logged(client, dsn):
    r = client.post("/events", json={"action": "kept", "track_uri": uri(3), "position": 4,
                                     "slider": 0.3, "session_id": "s1"})
    assert r.status_code == 201
    with psycopg.connect(dsn) as conn:
        row = conn.execute("SELECT action, track_uri, position, slider FROM events WHERE id = %s",
                           (r.json()["id"],)).fetchone()
    assert row == ("kept", uri(3), 4, pytest.approx(0.3))


def test_events_batch(client, dsn):
    evs = [{"action": "shown", "track_uri": uri(i), "position": i, "slider": 0.0, "session_id": "b1"}
           for i in range(3)]
    r = client.post("/events/batch", json=evs)
    assert r.status_code == 201 and r.json() == {"logged": 3}
    with psycopg.connect(dsn) as conn:
        n = conn.execute("SELECT count(*) FROM events WHERE session_id = 'b1' AND action = 'shown'").fetchone()[0]
    assert n == 3
    assert client.post("/events/batch", json=[]).status_code == 422


def test_events_validation(client):
    assert client.post("/events", json={"action": "liked"}).status_code == 422
    assert client.post("/events", json={"action": "kept", "track_uri": "nope"}).status_code == 422


# Preview resolver tests: test_previews.py


def test_preview_unknown_track(client):
    assert client.get("/preview", params={"track_uri": uri(77)}).status_code == 404


def test_deezer_pick_prefers_exact_title_and_same_artist():
    cands = [
        {"id": 1, "title": "Static Bloom (Live)", "artist": {"name": "Mock Orchestra"}},
        {"id": 2, "title": "Static Bloom", "artist": {"name": "Cover Band"}},
        {"id": 3, "title": "Static Bloom - 2011 Remaster", "artist": {"name": "Mock Orchestra"}},
    ]
    assert deezer.pick(cands, "Mock Orchestra", "Static Bloom")["id"] == 1  # (Live) normalises away
    studio = {"id": 4, "title": "Static Bloom", "artist": {"name": "Mock Orchestra"}}
    assert deezer.pick(cands + [studio], "Mock Orchestra", "Static Bloom")["id"] == 4  # exact wins
    assert deezer.pick(cands[1:2], "Mock Orchestra", "Static Bloom") is None  # wrong artist


def test_exclude_songs_hides_every_version_of_a_song(client):
    base = [r["track_uri"] for r in client.post(
        "/recommend", json={"seeds": [uri(1)], "max_per_artist": 0}).json()["results"]]
    assert uri(2) in base and uri(6) in base  # studio + live version are both candidates
    got = [r["track_uri"] for r in client.post(
        "/recommend", json={"seeds": [uri(1)], "exclude_songs": [uri(2)]}).json()["results"]]
    assert uri(2) not in got and uri(6) not in got  # "Static Bloom" in any version
    assert uri(3) in got and uri(0) in got  # other songs untouched


def test_max_per_artist_caps_each_artist(client):
    """Seed 1's candidates by score: 2, 3, 0, 4, 6; Mock Orchestra = 2, 4, 6."""
    got = [r["track_uri"] for r in client.post("/recommend", json={"seeds": [uri(1)]}).json()["results"]]
    assert got == [uri(2), uri(3), uri(0), uri(4), uri(6)]  # default cap 5: nobody is over it
    got = [r["track_uri"] for r in client.post(
        "/recommend", json={"seeds": [uri(1)], "max_per_artist": 2, "n": 4}).json()["results"]]
    assert got == [uri(2), uri(3), uri(0), uri(4)]  # cap 2: the 3rd Mock Orchestra is out
    got = [r["track_uri"] for r in client.post(
        "/recommend", json={"seeds": [uri(1)], "max_per_artist": 1, "n": 3}).json()["results"]]
    assert got == [uri(2), uri(3), uri(0)]
    # soft cap: not enough other artists, so the over-cap songs fill the end of the list
    got = [r["track_uri"] for r in client.post(
        "/recommend", json={"seeds": [uri(1)], "max_per_artist": 1}).json()["results"]]
    assert got == [uri(2), uri(3), uri(0), uri(4), uri(6)]
    got = [r["track_uri"] for r in client.post(
        "/recommend", json={"seeds": [uri(1)], "max_per_artist": 0}).json()["results"]]
    assert got == [uri(2), uri(3), uri(0), uri(4), uri(6)]  # 0 = no cap
    # A replacement: Mock Orchestra already has 2 songs on the list, so it is skipped.
    got = [r["track_uri"] for r in client.post("/recommend", json={
        "seeds": [uri(1)], "exclude": [uri(2), uri(4), uri(3)], "listed": [uri(2), uri(4)],
        "max_per_artist": 2,
        "n": 1}).json()["results"]]
    assert got == [uri(0)]


def test_new_artists_leaves_out_seed_artists(client):
    base = [r["track_uri"] for r in client.post("/recommend", json={"seeds": [uri(1)]}).json()["results"]]
    assert uri(0) in base  # same artist as the seed ("The Placeholders")
    got = [r["track_uri"] for r in client.post(
        "/recommend", json={"seeds": [uri(1)], "new_artists": True}).json()["results"]]
    assert uri(0) not in got and uri(2) in got and uri(3) in got
    # Artists of exclude_songs (an imported playlist) count as known too.
    got = [r["track_uri"] for r in client.post("/recommend", json={
        "seeds": [uri(1)], "new_artists": True, "exclude_songs": [uri(2)]}).json()["results"]]
    assert got == [uri(3)]  # Mock Orchestra (2, 4, 6) gone as well
