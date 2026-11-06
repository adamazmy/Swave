"""POST /covers: Spotify oEmbed (mocked), cached in `covers` (SYNTHETIC ids, throwaway DB)."""

from __future__ import annotations

import httpx
import psycopg
from fastapi.testclient import TestClient

from swave.main import create_app
from tests.test_api import dsn, uri  # noqa: F401 (pytest fixture)

IMG = "https://i.scdn.co/image/fakecover000000000000000000000001"


class OEmbed:
    """uri(1): a cover; uri(2): unknown (404); uri(3): rate limited (429); uri(4): odd host."""

    def __init__(self):
        self.calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == "open.spotify.com" and request.url.path == "/oembed"
        tid = request.url.params["url"].rsplit("/", 1)[1]
        self.calls.append(tid)
        if tid == uri(1).rsplit(":", 1)[1]:
            return httpx.Response(200, json={"thumbnail_url": IMG})
        if tid == uri(2).rsplit(":", 1)[1]:
            return httpx.Response(404)
        if tid == uri(3).rsplit(":", 1)[1]:
            return httpx.Response(429)
        return httpx.Response(200, json={"thumbnail_url": "https://evil.example/x.jpg"})


def test_covers_are_fetched_once_and_cached(dsn):  # noqa: F811
    o = OEmbed()
    with psycopg.connect(dsn) as conn:
        conn.execute("DROP TABLE IF EXISTS covers")
    app = create_app(dsn, http=httpx.Client(transport=httpx.MockTransport(o)),
                     weights={"cooccurrence_mpd": 1.0})
    with TestClient(app) as c:
        uris = [uri(1), uri(2), uri(3), uri(4)]
        got = c.post("/covers", json={"track_uris": uris}).json()
        assert got == {uri(1): IMG, uri(2): None, uri(3): None, uri(4): None}
        assert len(o.calls) == 4
        # second call: hits and definitive misses come from the cache; the 429 is retried
        got = c.post("/covers", json={"track_uris": uris}).json()
        assert got[uri(1)] == IMG and len(o.calls) == 5 and o.calls[-1] == uri(3).rsplit(":", 1)[1]
        assert c.post("/covers", json={"track_uris": ["not-a-uri"]}).status_code == 422
