"""Provider adapters (Spotify, Apple Music links), open-in redirects, pasted song lists.
Synthetic tracks (test_api's swave_test DB); the iTunes API is always faked."""

from __future__ import annotations

import psycopg
import pytest

from swave import itunes, paste, providers
from swave.previews import Candidate
from tests.test_api import client, dsn, uri  # noqa: F401  (fixtures)

APPLE = providers.PROVIDERS["apple"]


def test_capabilities():
    caps = {p["name"]: p for p in providers.describe()}
    assert (caps["apple"]["can_search"], caps["apple"]["can_read"], caps["apple"]["can_write"]) == (True, False, False)
    assert (caps["spotify"]["can_search"], caps["spotify"]["can_read"], caps["spotify"]["can_write"]) == (True, True, True)
    assert caps["apple"]["configured"] is True
    with pytest.raises(providers.Unsupported):
        APPLE.read_playlist(None, None, "https://music.apple.com/us/playlist/x/pl.u-123")
    with pytest.raises(providers.Unsupported):
        APPLE.write_playlist(None, None, "n", "d", [uri(1)])


def test_providers_endpoint(client):  # noqa: F811
    names = [p["name"] for p in client.get("/providers").json()]
    assert names == ["spotify", "apple"]


@pytest.fixture
def apple(monkeypatch, client, dsn):  # noqa: F811
    with psycopg.connect(dsn) as conn:
        conn.execute("TRUNCATE provider_tracks, preview_sources, track_meta")
    calls = []
    found: dict[str, object] = {}

    def match(http, m, timeout):
        calls.append(m.name)
        r = found.get(m.name)
        if isinstance(r, Exception):
            raise r
        return r
    monkeypatch.setattr(APPLE, "match", match)
    return found, calls


def open_(client, name, i):  # noqa: F811
    return client.get(f"/providers/{name}/open", params={"track_uri": uri(i)}, follow_redirects=False)


def test_open_in_spotify(client):  # noqa: F811
    r = open_(client, "spotify", 2)
    assert r.status_code == 302 and r.headers["location"] == f"https://open.spotify.com/track/{uri(2).split(':')[2]}"


def test_open_in_apple_matches_once_then_caches(client, apple):  # noqa: F811
    found, calls = apple
    found["Static Bloom"] = Candidate("555", "Static Bloom", "Mock Orchestra", None, None)
    assert open_(client, "apple", 2).headers["location"] == "https://music.apple.com/song/555"
    assert open_(client, "apple", 2).headers["location"] == "https://music.apple.com/song/555"
    assert calls == ["Static Bloom"]


def test_open_in_apple_falls_back_to_search(client, apple):  # noqa: F811
    found, calls = apple
    loc = open_(client, "apple", 4).headers["location"]  # no match: cached miss → search page
    assert loc == "https://music.apple.com/search?term=Mock%20Orchestra%20Quiet%20Engine"
    open_(client, "apple", 4)
    assert calls == ["Quiet Engine"]
    found["Paper Planes Again"] = itunes.ItunesError("rate limited")  # transient: not cached
    assert "search?term=" in open_(client, "apple", 3).headers["location"]
    found["Paper Planes Again"] = Candidate("9", "Paper Planes Again", "Dummy Duo", None, None)
    assert open_(client, "apple", 3).headers["location"] == "https://music.apple.com/song/9"


def test_open_in_apple_reuses_itunes_preview_match(client, apple, dsn):  # noqa: F811
    _, calls = apple
    with psycopg.connect(dsn) as conn:
        conn.execute("INSERT INTO preview_sources VALUES (%s, 'itunes', '777', true, now())", (uri(1),))
    assert open_(client, "apple", 1).headers["location"] == "https://music.apple.com/song/777"
    assert calls == []


def test_open_errors(client, apple):  # noqa: F811
    assert open_(client, "tidal", 1).status_code == 404
    assert client.get("/providers/apple/open", params={"track_uri": uri(77)},
                      follow_redirects=False).status_code == 404


def test_import_rejects_apple_playlist_links(client):  # noqa: F811
    r = client.post("/import", json={"url": "https://music.apple.com/us/playlist/chill/pl.u-abc123"})
    assert r.status_code == 422 and "paste" in r.json()["detail"]


# ── pasted song lists ───────────────────────────────────────────────────────────────────────

def test_readings():
    assert paste.readings("Static Bloom - Mock Orchestra")[:2] == [
        ("Static Bloom", "Mock Orchestra"), ("Mock Orchestra", "Static Bloom")]
    # Apple Music clipboard: Name, Time, Artist, Album (the time column is dropped)
    assert paste.readings("Quiet Engine\t3:45\tMock Orchestra\tFake Album")[0] == ("Quiet Engine", "Mock Orchestra")
    assert paste.readings("3. Static Bloom by Mock Orchestra")[0] == ("Static Bloom", "Mock Orchestra")
    assert paste.readings("just one field") == []
    assert paste.lines(" a \n\n b\r\nc ") == ["a", "b", "c"]


def test_import_text(client):  # noqa: F811
    text = "\n".join([
        "Static Bloom - Mock Orchestra",                 # title - artist
        "Dummy Duo – Paper Planes Again",                # artist – title (en dash)
        "Quiet Engine\t3:45\tMock Orchestra\tFake Album",  # Apple Music copy
        "Static Bloom - Mock Orchestra",                 # duplicate: listed once
        "Made Up Song - Nobody",                         # not in the catalogue
        "no separator here",
    ])
    body = client.post("/import/text", json={"text": text}).json()
    assert body["playlist"] == {"id": "", "name": "your pasted list", "provider": "paste"}
    assert [t["track_uri"] for t in body["tracks"]] == [uri(2), uri(3), uri(4)]
    assert body["total"] == 6 and body["missing"] == 2
    assert body["unmatched"] == ["Made Up Song - Nobody", "no separator here"]
    assert client.post("/import/text", json={"text": ""}).status_code == 422
