"""Related-artists scorer: Deezer (mocked) → artist_tracks → candidates (SYNTHETIC data, throwaway DB)."""

from __future__ import annotations

import httpx
import pytest
import psycopg
from fastapi.testclient import TestClient

from swave import related
from swave.main import create_app
from swave.pipeline import artist_index
from tests.test_api import dsn, uri  # noqa: F401 (pytest fixture)


class Deezer:
    """Knows "The Placeholders" (id 1): related = Mock Orchestra, then Dummy Duo."""

    def __init__(self):
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.deezer.com"
        self.calls += 1
        if request.url.path == "/search/artist":
            q = request.url.params["q"]
            return httpx.Response(200, json={"data": [{"id": 1, "name": q}] if q == "The Placeholders" else []})
        if request.url.path == "/artist/1/related":
            return httpx.Response(200, json={"data": [{"id": 2, "name": "Mock Orchestra"},
                                                      {"id": 3, "name": "Dummy Duo"}]})
        return httpx.Response(404)


def test_related_artists_scorer_ranks_related_songs_and_caches_deezer(dsn):  # noqa: F811
    d = Deezer()
    app = create_app(dsn, http=httpx.Client(transport=httpx.MockTransport(d)),
                     weights={"related_artists": 1.0})
    with TestClient(app) as c:  # startup creates the LB tables and deezer_related
        with psycopg.connect(dsn) as conn:
            conn.execute("DELETE FROM deezer_related")
            artist_index.build(conn)
        got = c.post("/recommend", json={"seeds": [uri(1)], "max_per_artist": 0}).json()["results"]
        names = [(r["name"], r["reason"]) for r in got]
        # weight(artist) × (0.5 + 0.5 × pop / the artist's best): Mock Orchestra (rank 0, weight 1):
        # Static Bloom 1.0, Quiet Engine 0.55, Live 0.5375; Dummy Duo (rank 1, 1/1.2): 0.833;
        # the seed's own artist (0.5): Neon Harbour 0.5
        assert [n for n, _ in names] == ["Static Bloom", "Paper Planes Again", "Quiet Engine",
                                         "Static Bloom - Live", "Neon Harbour"]
        assert dict(names)["Neon Harbour"] == "by your seed's artist"
        assert dict(names)["Quiet Engine"] == "by an artist similar to your seeds'"
        calls = d.calls
        assert calls == 2  # one artist search + one related lookup
        c.post("/recommend", json={"seeds": [uri(1)]})
        assert d.calls == calls  # cached in deezer_related
        # an artist Deezer doesn't know: cached as a miss, the scorer just adds nothing
        assert c.post("/recommend", json={"seeds": [uri(3)]}).status_code == 200
        with psycopg.connect(dsn) as conn:
            assert conn.execute("SELECT deezer_id FROM deezer_related WHERE artist_key = 'dummy duo'"
                                ).fetchone() == (None,)


def test_fetch_keeps_deezer_order_and_drops_the_artist_itself():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search/artist":
            return httpx.Response(200, json={"data": [{"id": 9, "name": "Nova Fake"}]})
        return httpx.Response(200, json={"data": [{"id": 1, "name": "B Band"}, {"id": 9, "name": "Nova Fake"},
                                                  {"id": 2, "name": "A Act feat. Someone"}]})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    assert related.fetch_deezer(http, "nova fake", "Nova Fake") == (True, 9, ["b band", "a act"])
    quota = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"error": {"code": 100, "message": "boom"}})))
    assert related.fetch_deezer(quota, "nova fake", "Nova Fake")[0] is False  # error: not cached


def test_fetch_lb_resolves_the_mbid_then_ranks_similar_artists_by_score(monkeypatch):
    monkeypatch.setattr(related, "_mb_wait", lambda: None)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        assert request.headers["user-agent"].startswith("Swave/")  # MusicBrainz requires one
        if request.url.host == "musicbrainz.org":
            return httpx.Response(200, json={"artists": [{"id": "mbid-other", "name": "Nova Fakes"},
                                                         {"id": "mbid-nova", "name": "Nova Fake"}]})
        assert request.url.params["artist_mbids"] == "mbid-nova"
        return httpx.Response(200, json=[{"name": "Low", "score": 5}, {"name": "High", "score": 90},
                                         {"name": "Nova Fake", "score": 99}])

    http = httpx.Client(transport=httpx.MockTransport(handler))
    assert related.fetch_lb(http, "nova fake", "Nova Fake") == (True, "mbid-nova", ["high", "low"])
    assert seen == ["musicbrainz.org", "labs.api.listenbrainz.org"]
    down = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    assert related.fetch_lb(down, "nova fake", "Nova Fake")[0] is False  # not cached


def test_style_boost_scores_only_given_tracks_by_style_overlap():
    import numpy as np

    from swave import recommend as rk
    from swave import scorers, styles

    artists = {1: ("seed act", "Seed Act"), 2: ("same style", "Same Style"),
               3: ("other style", "Other Style"), 4: ("no data", "No Data")}
    prof = {"seed act": {"Techno": 3, "House": 1}, "same style": {"Techno": 2},
            "other style": {"Chanson": 5}}
    boost = styles.StyleBoost(lambda ids: {int(i): artists[int(i)] for i in ids if int(i) in artists},
                              lambda keys: {k: prof[k] for k in keys if k in prof})
    c = boost.score_tracks(np.array([1]), np.array([2, 3, 4]))
    assert c.track.tolist() == [2]  # chanson shares nothing, no data scores nothing
    assert c.raw[0] == pytest.approx(3 / np.sqrt(10))  # cos((3,1),(2,0)) with Techno, House
    assert boost.candidates(np.array([1]), None).track.size == 0  # adds no candidates itself
    # in the ranker: the boost lifts the on-style candidate over a slightly stronger off-style one
    co = scorers.Cooccurrence("cooccurrence_lb", lambda s: (np.array([1, 1]), np.array([2, 3]),
                                                            np.array([0.5, 0.6]), np.array([5, 6])))
    comb = rk.combine([(co, 1.0), (boost, 0.5)], np.array([1]), None, lambda t: np.ones(len(t)))
    ranked = rk.penalise(comb, 0.0, np.array([1]), 2)
    assert ranked.track.tolist() == [2, 3]
    plain = rk.penalise(rk.combine([(co, 1.0)], np.array([1]), None, lambda t: np.ones(len(t))),
                        0.0, np.array([1]), 2)
    assert plain.track.tolist() == [3, 2]
