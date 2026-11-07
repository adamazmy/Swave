"""Spotify flow tests with a fake Spotify (httpx.MockTransport). No real tokens, synthetic tracks."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from swave import spotify
from swave.main import create_app
from tests.test_api import dsn, uri  # noqa: F401  (reuse the synthetic test database)


class FakeSpotify:
    """Records requests and answers like the Spotify accounts + Web API would."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.token_calls = 0
        self.fail_items = False
        self.forbid_read = False  # dev mode: playlist owned by someone else
        self.scope = "playlist-modify-private ugc-image-upload"
        self.fail_cover = False
        self.not_registered = False  # dev mode: this account isn't on the app's User Management list
        self.covers: dict[str, bytes] = {}
        # (uri, title, artist) as Spotify returns them today
        self.playlist_items = [
            (uri(1), "Paper Lanterns", "The Placeholders"),
            ("spotify:episode:fake000000000000000001", "Some Podcast", "Host"),
            (uri(2), "Static Bloom", "Mock Orchestra"),
            (uri(99), "Unknown Song", "Nobody"),
        ]

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        path = req.url.path
        if req.url.host == "accounts.spotify.com":
            form = parse_qs(req.content.decode())
            self.token_calls += 1
            if form["grant_type"] == ["authorization_code"]:
                assert form["code_verifier"][0] and form["client_id"] == ["test-client"]
                return httpx.Response(200, json={"access_token": "AT1", "refresh_token": "RT1",
                                                 "expires_in": 3600, "scope": self.scope})
            if form["refresh_token"] == ["revoked"]:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": "AT2", "expires_in": 3600})  # no new RT
        if self.not_registered and req.url.host == "api.spotify.com":
            return httpx.Response(403, json={"error": {"status": 403, "message":
                                  "User not registered in the Developer Dashboard"}})
        if path == "/v1/me":
            return httpx.Response(200, json={"id": "owner", "display_name": "Test Owner"})
        if path == "/v1/me/playlists" and req.method == "POST":
            return httpx.Response(201, json={"id": "PL123", "external_urls": {"spotify": "https://open.spotify.com/playlist/PL123"}})
        if req.method == "PUT" and path.endswith("/images"):
            if self.fail_cover:
                return httpx.Response(403, json={"error": {"status": 403, "message": "Insufficient client scope"}})
            assert req.headers["content-type"] == "image/jpeg"
            self.covers[path.split("/")[3]] = req.content
            return httpx.Response(202)
        if req.method == "GET" and path.count("/") == 3 and path.startswith("/v1/playlists/"):
            return httpx.Response(200, json={"name": "My Road Trip"})
        if req.method == "PUT" and path.startswith("/v1/playlists/"):
            if "missing" in path:
                return httpx.Response(404, json={"error": {"status": 404, "message": "Not found"}})
            return httpx.Response(200, json={"snapshot_id": "s"} if path.endswith("/items") else None)
        if path.endswith("/items") and req.method == "POST":
            if self.fail_items:
                return httpx.Response(403, json={"error": {"status": 403, "message": "Forbidden"}})
            return httpx.Response(201, json={"snapshot_id": "s"})
        if path.endswith("/items") and req.method == "GET":
            if self.forbid_read:
                return httpx.Response(403, json={"error": {"status": 403, "message": "Forbidden"}})
            off = int(req.url.params["offset"])
            page = self.playlist_items[off:off + 2]  # tiny pages to exercise pagination
            nxt = "next" if off + 2 < len(self.playlist_items) else None
            return httpx.Response(200, json={"items": [
                {"item": {"uri": u, "name": n, "artists": [{"name": a}]}} for u, n, a in page], "next": nxt})
        return httpx.Response(404, json={"error": {"status": 404, "message": "not found"}})


@pytest.fixture
def fake():
    return FakeSpotify()


@pytest.fixture
def client(dsn, fake, monkeypatch):  # noqa: F811
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "test-client")
    with psycopg.connect(dsn) as conn:
        conn.execute("DROP TABLE IF EXISTS spotify_auth; DROP TABLE IF EXISTS user_prefs")
    http = httpx.Client(transport=httpx.MockTransport(fake))
    with TestClient(create_app(dsn, http=http, weights={"cooccurrence_mpd": 1.0}),
                    follow_redirects=False) as c:
        yield c


def login(client) -> None:
    r = client.get("/auth/login", params={"return_to": "http://localhost:3000/results?seeds=x"})
    q = parse_qs(urlsplit(r.headers["location"]).query)
    cb = client.get("/auth/callback", params={"state": q["state"][0], "code": "CODE"})
    assert cb.status_code == 302 and "spotify=connected" in cb.headers["location"]


def test_login_redirects_with_pkce(client):
    r = client.get("/auth/login")
    assert r.status_code == 302
    loc = urlsplit(r.headers["location"])
    q = parse_qs(loc.query)
    assert loc.netloc == "accounts.spotify.com" and loc.path == "/authorize"
    assert q["code_challenge_method"] == ["S256"] and len(q["code_challenge"][0]) == 43
    assert q["redirect_uri"] == ["http://127.0.0.1:8000/auth/callback"]
    assert "playlist-modify-private" in q["scope"][0]


def test_callback_connects_and_returns_to_app(client, fake):
    login(client)
    s = client.get("/auth/status").json()
    assert s == {"configured": True, "connected": True, "display_name": "Test Owner",
                 "can_upload_cover": True}


def test_old_login_without_cover_scope_is_reported(client, fake):
    fake.scope = "playlist-modify-private"
    login(client)
    assert client.get("/auth/status").json()["can_upload_cover"] is False


def test_return_to_must_be_the_web_app(client):
    r = client.get("/auth/login", params={"return_to": "https://evil.example/steal"})
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    cb = client.get("/auth/callback", params={"state": state, "error": "access_denied"})
    assert cb.headers["location"].startswith("http://localhost:3000/")
    assert "spotify_error=access_denied" in cb.headers["location"]


def test_unknown_state_is_rejected(client):
    cb = client.get("/auth/callback", params={"state": "forged", "code": "x"})
    assert "spotify_error" in cb.headers["location"]
    assert client.get("/auth/status").json()["connected"] is False


def test_save_requires_login(client):
    r = client.post("/save", json={"name": "x", "track_uris": [uri(1)]})
    assert r.status_code == 401


def test_save_creates_private_playlist_in_batches(client, fake):
    login(client)
    uris = [f"spotify:track:{i:022d}" for i in range(150)] + [f"spotify:track:{0:022d}"]  # one dup
    r = client.post("/save", json={"name": "My vibe", "track_uris": uris})
    assert r.status_code == 201
    assert r.json() == {"id": "PL123", "url": "https://open.spotify.com/playlist/PL123", "added": 150,
                        "cover_error": None}
    create = next(q for q in fake.requests if q.url.path == "/v1/me/playlists")
    assert json.loads(create.content)["public"] is False
    adds = [json.loads(q.content)["uris"] for q in fake.requests if q.url.path == "/v1/playlists/PL123/items"]
    assert [len(a) for a in adds] == [100, 50]
    assert create.headers["authorization"] == "Bearer AT1"


def test_save_with_playlist_id_updates_instead_of_creating(client, fake):
    login(client)
    pid = "A" * 22
    uris = [f"spotify:track:{i:022d}" for i in range(130)]
    r = client.post("/save", json={"name": "Renamed", "track_uris": uris, "playlist_id": pid})
    assert r.status_code == 201 and r.json()["id"] == pid and r.json()["added"] == 130
    assert not any(q.url.path == "/v1/me/playlists" for q in fake.requests)  # no new playlist
    calls = [(q.method, q.url.path, json.loads(q.content)) for q in fake.requests if pid in q.url.path]
    assert calls[0] == ("PUT", f"/v1/playlists/{pid}", {"name": "Renamed", "description": "Built with Swave", "public": False})
    assert calls[1][:2] == ("PUT", f"/v1/playlists/{pid}/items") and len(calls[1][2]["uris"]) == 100
    assert calls[2][:2] == ("POST", f"/v1/playlists/{pid}/items") and len(calls[2][2]["uris"]) == 30


def test_update_of_a_vanished_playlist_says_so(client):
    login(client)
    r = client.post("/save", json={"name": "x", "track_uris": [uri(1)], "playlist_id": "missing" + "A" * 15})
    assert r.status_code == 404 and "save it as new" in r.json()["detail"]


def test_save_reports_spotify_errors(client, fake):
    login(client)
    fake.fail_items = True
    r = client.post("/save", json={"name": "x", "track_uris": [uri(1)]})
    assert r.status_code == 502 and "User Management" in r.json()["detail"]


def test_expired_token_is_refreshed_and_old_refresh_token_kept(client, fake, dsn):
    login(client)
    with psycopg.connect(dsn) as conn:
        conn.execute("UPDATE spotify_auth SET expires_at = now() - interval '1 minute'")
    assert client.post("/save", json={"name": "x", "track_uris": [uri(1)]}).status_code == 201
    with psycopg.connect(dsn) as conn:
        at, rt = conn.execute("SELECT access_token, refresh_token FROM spotify_auth").fetchone()
    assert (at, rt) == ("AT2", "RT1")


def test_revoked_refresh_token_disconnects(client, dsn):
    login(client)
    with psycopg.connect(dsn) as conn:
        conn.execute("UPDATE spotify_auth SET expires_at = now() - interval '1 minute', refresh_token = 'revoked'")
    assert client.post("/save", json={"name": "x", "track_uris": [uri(1)]}).status_code == 401
    assert client.get("/auth/status").json()["connected"] is False


def test_import_maps_playlist_to_index(client):
    login(client)
    r = client.post("/import", json={"url": "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc"})
    body = r.json()
    assert [t["track_uri"] for t in body["tracks"]] == [uri(1), uri(2)]  # episode skipped, 99 unknown
    assert body["playlist"] == {"id": "37i9dQZF1DXcBWIGoYBM5M", "name": "My Road Trip", "provider": "spotify"}
    assert body["total"] == 3 and body["missing"] == 1


def test_import_of_someone_elses_playlist_explains_dev_mode(client, fake):
    login(client)
    fake.forbid_read = True
    r = client.post("/import", json={"url": "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"})
    assert r.status_code == 403 and "own or collaborate" in r.json()["detail"]


def test_import_rejects_non_playlist_links(client):
    login(client)
    assert client.post("/import", json={"url": "https://open.spotify.com/track/abc"}).status_code == 422


def test_parse_playlist_id():
    pid = "37i9dQZF1DXcBWIGoYBM5M"
    assert spotify.parse_playlist_id(f"https://open.spotify.com/playlist/{pid}?si=x") == pid
    assert spotify.parse_playlist_id(f"spotify:playlist:{pid}") == pid
    assert spotify.parse_playlist_id("https://example.com") is None


COVER = "A" * 200  # synthetic base64 payload


def test_save_with_cover_and_public_flag(client, fake):
    login(client)
    r = client.post("/save", json={"name": "x", "track_uris": [uri(1)], "public": True, "cover_jpeg": COVER})
    assert r.status_code == 201 and r.json()["cover_error"] is None
    create = next(q for q in fake.requests if q.url.path == "/v1/me/playlists")
    assert json.loads(create.content)["public"] is True
    assert fake.covers["PL123"] == COVER.encode()


def test_cover_failure_keeps_the_playlist(client, fake):
    login(client)
    fake.fail_cover = True
    r = client.post("/save", json={"name": "x", "track_uris": [uri(1)], "cover_jpeg": COVER})
    assert r.status_code == 201 and r.json()["id"] == "PL123"
    assert "Reconnect Spotify" in r.json()["cover_error"]


def test_cover_must_be_base64_within_256kb(client):
    login(client)
    too_big = "A" * (256 * 1024 + 1)
    assert client.post("/save", json={"name": "x", "track_uris": [uri(1)], "cover_jpeg": too_big}).status_code == 422
    assert client.post("/save", json={"name": "x", "track_uris": [uri(1)], "cover_jpeg": "<svg>" * 50}).status_code == 422


def test_add_appends_only_new_songs(client, fake):
    login(client)
    pid = "37i9dQZF1DXcBWIGoYBM5M"  # fake playlist contains uri(1), uri(2), uri(99) + an episode
    r = client.post(f"/playlists/{pid}/add", json={"track_uris": [uri(1), uri(3), uri(4), uri(3)]})
    assert r.status_code == 201 and r.json()["added"] == 2 and r.json()["skipped"] == 1
    adds = [json.loads(q.content)["uris"] for q in fake.requests
            if q.method == "POST" and q.url.path == f"/v1/playlists/{pid}/items"]
    assert adds == [[uri(3), uri(4)]]  # nothing existing touched, no PUT/replace
    assert not any(q.method == "PUT" for q in fake.requests)


def test_add_to_someone_elses_playlist(client, fake):
    login(client)
    fake.forbid_read = True
    r = client.post("/playlists/37i9dQZF1DXcBWIGoYBM5M/add", json={"track_uris": [uri(1)]})
    assert r.status_code == 403 and "own or collaborate" in r.json()["detail"]


def other_id(i: int) -> str:
    """A Spotify id the dataset doesn't have: same song, newer release."""
    return f"spotify:track:new{i:019d}"


def test_import_matches_songs_whose_spotify_id_changed(client, fake):
    login(client)
    fake.playlist_items = [
        (other_id(1), "Static Bloom - 2011 Remaster", "Mock Orchestra"),   # title match -> uri(2)
        (other_id(2), "Paper Lanterns (Live)", "The Placeholders"),         # title match -> uri(1)
        (other_id(3), "Quiet Engines", "Mock Orchestra"),                   # near-identical -> uri(4)
        (other_id(4), "Quiet Engine Part Two Reprise", "Mock Orchestra"),  # prefix too short: no match
        (other_id(5), "Static Bloom", "Cover Band"),                        # wrong artist: no match
    ]
    body = client.post("/import", json={"url": "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"}).json()
    assert [t["track_uri"] for t in body["tracks"]] == [uri(2), uri(1), uri(4)]
    assert (body["total"], body["missing"], body["loose"]) == (5, 2, 3)
    assert body["unmatched"] == ["Quiet Engine Part Two Reprise · Mock Orchestra", "Static Bloom · Cover Band"]


def test_two_versions_of_one_song_appear_once(client, fake):
    login(client)
    fake.playlist_items = [(uri(2), "Static Bloom", "Mock Orchestra"),
                           (other_id(1), "Static Bloom - Remastered", "Mock Orchestra")]
    body = client.post("/import", json={"url": "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"}).json()
    assert [t["track_uri"] for t in body["tracks"]] == [uri(2)]


def test_add_skips_a_song_already_there_under_another_id(client, fake):
    login(client)
    fake.playlist_items = [(other_id(1), "Static Bloom - 2011 Remaster", "Mock Orchestra")]
    pid = "37i9dQZF1DXcBWIGoYBM5M"
    r = client.post(f"/playlists/{pid}/add", json={"track_uris": [uri(2), uri(3)]})
    assert r.json()["added"] == 1 and r.json()["skipped"] == 1
    adds = [json.loads(q.content)["uris"] for q in fake.requests
            if q.method == "POST" and q.url.path == f"/v1/playlists/{pid}/items"]
    assert adds == [[uri(3)]]


# ── "I listen on": per-user service, allowlist refusals, silent reconnect ──────────────────────

def test_service_choice_is_saved_per_user(client):
    s = client.get("/me/service").json()
    assert s["service"] is None and s["spotify"]["connected"] is False  # first visit: picker shows
    assert client.put("/me/service", json={"service": "spotify"}).json()["service"] == "spotify"
    assert client.get("/me/service").json()["service"] == "spotify"
    assert client.put("/me/service", json={"service": "other"}).json()["service"] == "other"
    assert client.put("/me/service", json={"service": "apple"}).status_code == 422  # coming soon
    client.delete("/me/data")
    assert client.get("/me/service").json()["service"] is None  # delete my data covers it


def test_account_not_on_the_allowlist_is_refused_cleanly(client, fake, dsn):  # noqa: F811
    fake.not_registered = True
    r = client.get("/auth/login", params={"return_to": "http://localhost:3000/results?seeds=x"})
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    cb = client.get("/auth/callback", params={"state": state, "code": "CODE"})
    loc = cb.headers["location"]
    assert cb.status_code == 302 and loc.startswith("http://localhost:3000/results?seeds=x")
    assert "spotify_error=not_allowlisted" in loc  # a code for the page, not a raw error
    s = client.get("/me/service").json()["spotify"]
    assert s["connected"] is False and s["blocked"] is True  # tokens dropped, saving falls back
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT count(*) FROM spotify_auth").fetchone()[0] == 0
    fake.not_registered = False  # added to the allowlist: logging in again clears the flag
    login(client)
    s = client.get("/me/service").json()["spotify"]
    assert s["connected"] is True and s["blocked"] is False


def test_save_refused_for_allowlist_flags_the_user(client, fake):
    login(client)
    fake.not_registered = True  # removed from the allowlist after logging in
    r = client.post("/save", json={"name": "x", "track_uris": [uri(1)]})
    assert r.status_code == 403 and r.headers["x-swave-error"] == "not_allowlisted"
    assert "Copy for Spotify" in r.json()["detail"]
    assert client.get("/me/service").json()["spotify"]["blocked"] is True


def test_save_without_a_login_asks_the_page_to_reconnect(client):
    r = client.post("/save", json={"name": "x", "track_uris": [uri(1)]})
    assert r.status_code == 401 and r.headers["x-swave-error"] == "reconnect"
