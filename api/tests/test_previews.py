"""Preview resolver chain, cache and attempt log. Synthetic tracks; every provider is faked."""

from __future__ import annotations

import psycopg
import pytest

from swave import deezer, itunes, previews
from swave.previews import Candidate as C, Meta
from tests.test_api import client, dsn, uri  # noqa: F401  (fixtures: swave_test DB)


# ── matching ────────────────────────────────────────────────────────────────────────────────

def test_loose_names():
    assert previews.loose_title("Señorita (feat. Someone) - Remastered 2011") == "senorita"
    assert previews.loose_title("Static Bloom [Live] feat. X") == "static bloom"
    assert previews.loose_artist("Beyoncé feat. Jay-Z") == "beyonce"
    assert previews.loose_artist("Mock Orchestra, Dummy Duo") == "mock orchestra"


def test_choose_checks_artist_title_and_duration():
    live = C("1", "Static Bloom - Live", "Mock Orchestra", 300_000, "u1")
    studio = C("2", "Static Bloom", "Mock Orchestra", 201_000, "u2")
    cover = C("3", "Static Bloom", "Cover Band", 200_000, "u3")
    assert previews.choose([live, studio, cover], "Mock Orchestra", "Static Bloom", 200_000) is studio
    # duration rules out the live version even though its loose title matches
    assert previews.choose([live, cover], "Mock Orchestra", "Static Bloom", 200_000) is None
    # duration unknown: the loose title match is accepted
    assert previews.choose([live], "Mock Orchestra", "Static Bloom", None) is live


def test_choose_fuzzy_title_needs_duration():
    c = C("1", "Wahy Zekrayat", "Fake Singer", 198_000, "u")
    assert previews.choose([c], "Fake Singer", "Wahi Zekrayat", 200_000) is c
    assert previews.choose([c], "Fake Singer", "Wahi Zekrayat", None) is None


def test_choose_prefers_a_version_with_preview():
    silent = C("1", "Quiet Engine", "Mock Orchestra", None, None)
    loud = C("2", "Quiet Engine", "Mock Orchestra", None, "u")
    assert previews.choose([silent, loud], "Mock Orchestra", "Quiet Engine", None) is loud


def dz(id_, title, artist, secs, preview):
    return {"id": id_, "title": title, "artist": {"name": artist}, "duration": secs, "preview": preview}


def test_deezer_isrc_then_search(monkeypatch):
    m = Meta("Static Bloom", "Mock Orchestra", "XX0000000001", 200_000)
    monkeypatch.setattr(deezer, "by_isrc", lambda h, i: dz(5, "Static Bloom", "Mock Orchestra", 200, "p5"))
    monkeypatch.setattr(deezer, "search_raw", lambda h, q: pytest.fail("no search needed"))
    assert previews.resolve_deezer(None, m, 1).id == "5"
    # ISRC version has no preview: another version found by search wins
    monkeypatch.setattr(deezer, "by_isrc", lambda h, i: dz(5, "Static Bloom", "Mock Orchestra", 200, ""))
    monkeypatch.setattr(deezer, "search_raw", lambda h, q: [dz(6, "Static Bloom", "Mock Orchestra", 201, "p6")])
    assert previews.resolve_deezer(None, m, 1).id == "6"
    # …and when search finds nothing better, the ISRC match is kept (matched, no preview)
    monkeypatch.setattr(deezer, "search_raw", lambda h, q: [])
    hit = previews.resolve_deezer(None, m, 1)
    assert hit.id == "5" and hit.preview is None


def test_deezer_isrc_trusts_title_when_artist_credit_differs(monkeypatch):
    m = Meta("Static Bloom", "Soundtrack Cast", "XX0000000001", 200_000)
    monkeypatch.setattr(deezer, "by_isrc", lambda h, i: dz(5, "Static Bloom", "Mock Orchestra", 200, "p"))
    assert previews.resolve_deezer(None, m, 1).id == "5"
    monkeypatch.setattr(deezer, "by_isrc", lambda h, i: dz(5, "Other Song", "Mock Orchestra", 200, "p"))
    monkeypatch.setattr(deezer, "search_raw", lambda h, q: [])
    assert previews.resolve_deezer(None, m, 1) is None


def test_deezer_falls_back_to_plain_search(monkeypatch):
    queries = []
    monkeypatch.setattr(deezer, "search_raw", lambda h, q: queries.append(q) or (
        [] if q.startswith("artist:") else [dz(7, "Quiet Engine", "Mock Orchestra", 100, "p")]))
    assert previews.resolve_deezer(None, Meta("Quiet Engine", "Mock Orchestra", None, None), 1).id == "7"
    assert queries == ['artist:"mock orchestra" track:"quiet engine"', "mock orchestra quiet engine"]


def test_rate_limiter_spaces_calls_and_refuses_long_waits():
    now = [100.0]
    slept = []
    lim = itunes.RateLimiter(per_minute=20, clock=lambda: now[0], sleep=slept.append)
    lim.acquire(timeout=10)
    lim.acquire(timeout=10)
    lim.acquire(timeout=10)
    assert slept == [3.0, 6.0]  # 3 s apart (20/min), measured from the same instant
    with pytest.raises(itunes.ItunesError):
        lim.acquire(timeout=5)  # next slot is 9 s away


# ── chain + cache through the API ───────────────────────────────────────────────────────────

class Fake:
    """Programmable providers: per source, a Candidate (or None, or an exception) per track title,
    and the fresh URL each provider id returns now."""

    def __init__(self, monkeypatch):
        self.found: dict[str, dict[str, object]] = {"deezer": {}, "itunes": {}}
        self.urls: dict[tuple[str, str], str | None] = {}
        self.calls: list[tuple] = []
        for s in previews.SOURCES:
            monkeypatch.setitem(previews.RESOLVERS, s, self._resolver(s))
        monkeypatch.setattr(previews, "fresh_url", self._fresh)

    def _resolver(self, source):
        def run(http, m, wait):
            self.calls.append(("resolve", source, m.name))
            r = self.found[source].get(m.name)
            if isinstance(r, Exception):
                raise r
            return r
        return run

    def _fresh(self, http, source, pid, wait):
        self.calls.append(("fresh", source, pid))
        return self.urls.get((source, pid))


@pytest.fixture
def fake(monkeypatch, client, dsn):  # noqa: F811  (client first: it creates the tables)
    with psycopg.connect(dsn) as conn:
        conn.execute("TRUNCATE preview_sources, preview_attempts, track_meta")
    return Fake(monkeypatch)


def get(client, i, *exclude):  # noqa: F811
    return client.get("/preview", params={"track_uri": uri(i), "exclude": list(exclude)}).json()


# TRACKS in test_api: 2 = "Static Bloom", 4 = "Quiet Engine", 3 = "Paper Planes Again"

def test_first_play_resolves_then_cache_gives_fresh_urls(client, fake):  # noqa: F811
    fake.found["deezer"]["Static Bloom"] = C("42", "Static Bloom", "Mock Orchestra", None, "https://dz/search")
    fake.urls[("deezer", "42")] = "https://dz/fresh"
    first, second = get(client, 2), get(client, 2)
    assert (first["preview_url"], first["source"], first["outcome"]) == ("https://dz/search", "deezer", "ok")
    assert second["preview_url"] == "https://dz/fresh"
    assert first["open_url"] == "https://www.deezer.com/track/42"
    assert fake.calls == [("resolve", "deezer", "Static Bloom"), ("fresh", "deezer", "42")]


def test_falls_back_to_itunes_and_links_to_apple(client, fake):  # noqa: F811
    fake.found["deezer"]["Static Bloom"] = None
    fake.found["itunes"]["Static Bloom"] = C("777", "Static Bloom", "Mock Orchestra", None, "https://it/p")
    r = get(client, 2)
    assert (r["source"], r["preview_url"]) == ("itunes", "https://it/p")
    assert r["open_url"] == "https://music.apple.com/song/777"


def test_exclude_skips_a_source_whose_url_failed_to_play(client, fake):  # noqa: F811
    fake.found["deezer"]["Static Bloom"] = C("42", "Static Bloom", "Mock Orchestra", None, "https://dz/1")
    fake.found["itunes"]["Static Bloom"] = C("777", "Static Bloom", "Mock Orchestra", None, "https://it/1")
    assert get(client, 2)["source"] == "deezer"
    assert get(client, 2, "deezer")["source"] == "itunes"


def test_no_match_is_cached(client, fake):  # noqa: F811
    assert get(client, 4)["outcome"] == "no_match"
    assert get(client, 4)["outcome"] == "no_match"
    assert [c for c in fake.calls if c[0] == "resolve"] == [
        ("resolve", "deezer", "Quiet Engine"), ("resolve", "itunes", "Quiet Engine")]


def test_matched_but_no_preview(client, fake):  # noqa: F811
    fake.found["deezer"]["Quiet Engine"] = C("9", "Quiet Engine", "Mock Orchestra", None, None)
    assert get(client, 4)["outcome"] == "matched_no_preview"
    # later the provider has a preview again: the cached id is re-checked, no new search
    fake.urls[("deezer", "9")] = "https://dz/9"
    assert get(client, 4)["preview_url"] == "https://dz/9"


def test_transient_errors_are_not_cached(client, fake):  # noqa: F811
    fake.found["deezer"]["Static Bloom"] = previews.SourceError("quota")
    fake.found["itunes"]["Static Bloom"] = previews.SourceError("rate limited")
    assert get(client, 2)["outcome"] == "source_error"
    fake.found["deezer"]["Static Bloom"] = C("42", "Static Bloom", "Mock Orchestra", None, "https://dz/1")
    assert get(client, 2)["outcome"] == "ok"


def test_unknown_track_404(client, fake):  # noqa: F811
    assert client.get("/preview", params={"track_uri": uri(77)}).status_code == 404


def test_resolve_reports_availability(client, fake):  # noqa: F811
    fake.found["deezer"]["Static Bloom"] = C("42", "Static Bloom", "Mock Orchestra", None, "https://dz/1")
    fake.found["itunes"]["Paper Planes Again"] = C("5", "Paper Planes Again", "Dummy Duo", None, "https://it/5")
    body = client.post("/previews/resolve", json={"track_uris": [uri(2), uri(4), uri(3), uri(77)]}).json()
    assert body[uri(2)]["status"] == "yes" and body[uri(2)]["source"] == "deezer"
    assert body[uri(3)] == {"status": "yes", "source": "itunes", "open_url": "https://music.apple.com/song/5"}
    assert body[uri(4)]["status"] == "no"
    assert body[uri(77)]["status"] == "unknown"  # not a known track
    # cached: a second call resolves nothing
    n = len(fake.calls)
    client.post("/previews/resolve", json={"track_uris": [uri(2), uri(4), uri(3)]})
    assert len(fake.calls) == n


def test_attempts_are_logged_and_counted(client, fake, dsn):  # noqa: F811
    for outcome, source, steps in [("ok", "deezer", "deezer:ok"),
                                   ("ok", "itunes", "deezer:playback_error>deezer:playback_error>itunes:ok"),
                                   ("no_match", None, None), ("expired_url", "deezer", None)]:
        r = client.post("/preview/attempts", json={"track_uri": uri(2), "outcome": outcome,
                                                   "source": source, "steps": steps})
        assert r.status_code == 201
    assert client.post("/preview/attempts", json={"track_uri": uri(2), "outcome": "great"}).status_code == 422
    with psycopg.connect(dsn) as conn:
        c = previews.coverage(conn, 7)
    assert c["attempts"] == 4 and c["ok"] == 2 and c["coverage"] == 0.5 and c["recovered"] == 1
    assert c["ok_by_source"] == {"deezer": 1, "itunes": 1}
    assert c["outcomes"]["expired_url"] == 1
