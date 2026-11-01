"""Spotify Web API in development mode (rules since Feb 2026), single user.

- Auth: Authorization Code + PKCE (no client secret). Tokens live in `spotify_auth` (one row).
- Save: POST /me/playlists, then POST /playlists/{id}/items in batches of 100.
- Read a pasted playlist: GET /playlists/{id}/items (owned or collaborative playlists only),
  track under items[].item (items[].track is deprecated).
Never log tokens.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import secrets
from urllib.parse import urlencode

import httpx
import psycopg

ACCOUNTS = "https://accounts.spotify.com"
API = "https://api.spotify.com/v1"
SCOPES = ("playlist-modify-private playlist-modify-public "
          "playlist-read-private playlist-read-collaborative")
BATCH = 100  # max items per add-items request
PLAYLIST_ID = re.compile(r"playlist[/:]([A-Za-z0-9]{22})")

SCHEMA = """
CREATE TABLE IF NOT EXISTS spotify_auth (
    id            int PRIMARY KEY CHECK (id = 1),
    access_token  text NOT NULL,
    refresh_token text NOT NULL,
    expires_at    timestamptz NOT NULL,
    display_name  text
);
"""


class SpotifyError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class NotConnected(Exception):
    """No (valid) Spotify login: the user has to connect again."""


def client_id() -> str:
    cid = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
    if not cid:
        raise SpotifyError(503, "SPOTIFY_CLIENT_ID is not set in .env (restart the API after adding it)")
    return cid


def redirect_uri() -> str:
    return os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8000/auth/callback")


def pkce_pair() -> tuple[str, str]:
    """(code_verifier, code_challenge). Verifier: 86 URL-safe chars (spec: 43–128)."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def authorize_url(state: str, challenge: str) -> str:
    return f"{ACCOUNTS}/authorize?" + urlencode({
        "client_id": client_id(),
        "response_type": "code",
        "redirect_uri": redirect_uri(),
        "state": state,
        "scope": SCOPES,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
    })


def _message(r: httpx.Response) -> str:
    try:
        body = r.json()
    except ValueError:
        return r.text[:200] or f"HTTP {r.status_code}"
    err = body.get("error")
    if isinstance(err, dict):
        return err.get("message") or f"HTTP {r.status_code}"
    return body.get("error_description") or str(err or f"HTTP {r.status_code}")


def _token(http: httpx.Client, data: dict) -> dict:
    try:
        r = http.post(f"{ACCOUNTS}/api/token", data=data)
    except httpx.HTTPError as e:
        raise SpotifyError(502, f"Can't reach Spotify: {e}") from e
    if r.status_code != 200:
        raise SpotifyError(r.status_code, _message(r))
    return r.json()


def _store(conn: psycopg.Connection, tok: dict, display_name: str | None = None) -> None:
    """Upsert tokens. Spotify may omit refresh_token on refresh: keep the old one then."""
    conn.execute(
        """INSERT INTO spotify_auth (id, access_token, refresh_token, expires_at, display_name)
           VALUES (1, %(a)s, %(r)s, now() + make_interval(secs => %(e)s), %(d)s)
           ON CONFLICT (id) DO UPDATE SET
               access_token  = EXCLUDED.access_token,
               refresh_token = COALESCE(NULLIF(%(r)s, ''), spotify_auth.refresh_token),
               expires_at    = EXCLUDED.expires_at,
               display_name  = COALESCE(EXCLUDED.display_name, spotify_auth.display_name)""",
        {"a": tok["access_token"], "r": tok.get("refresh_token") or "",
         "e": max(0, int(tok.get("expires_in", 3600)) - 60), "d": display_name},
    )
    # Commit now: a later error must not roll back a (possibly rotated) refresh token.
    conn.commit()


def connect(conn: psycopg.Connection, http: httpx.Client, code: str, verifier: str) -> str:
    """Exchange the authorization code, store tokens, return the account's display name."""
    tok = _token(http, {
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(),
        "client_id": client_id(), "code_verifier": verifier,
    })
    if not tok.get("refresh_token"):
        raise SpotifyError(502, "Spotify returned no refresh token")
    _store(conn, tok)
    me = call(conn, http, "GET", "/me").json()
    name = me.get("display_name") or me.get("id") or "Spotify user"
    conn.execute("UPDATE spotify_auth SET display_name = %s WHERE id = 1", (name,))
    return name


def status(conn: psycopg.Connection) -> str | None:
    row = conn.execute("SELECT display_name FROM spotify_auth WHERE id = 1").fetchone()
    return (row[0] or "Spotify user") if row else None


def disconnect(conn: psycopg.Connection) -> None:
    conn.execute("DELETE FROM spotify_auth")
    conn.commit()  # persists even when the caller then raises


def access_token(conn: psycopg.Connection, http: httpx.Client) -> str:
    row = conn.execute(
        "SELECT access_token, refresh_token, expires_at > now() FROM spotify_auth WHERE id = 1"
    ).fetchone()
    if not row:
        raise NotConnected
    access, refresh, valid = row
    if valid:
        return access
    try:
        tok = _token(http, {"grant_type": "refresh_token", "refresh_token": refresh,
                            "client_id": client_id()})
    except SpotifyError as e:
        if e.status in (400, 401):  # revoked / invalid_grant: log in again
            disconnect(conn)
            raise NotConnected from e
        raise
    _store(conn, tok)
    return tok["access_token"]


def call(conn: psycopg.Connection, http: httpx.Client, method: str, path: str, **kw) -> httpx.Response:
    token = access_token(conn, http)
    try:
        r = http.request(method, f"{API}{path}", headers={"Authorization": f"Bearer {token}"}, **kw)
    except httpx.HTTPError as e:
        raise SpotifyError(502, f"Can't reach Spotify: {e}") from e
    if r.status_code == 401:
        disconnect(conn)
        raise NotConnected
    if r.status_code >= 400:
        raise SpotifyError(r.status_code, _message(r))
    return r


def create_playlist(conn: psycopg.Connection, http: httpx.Client, name: str, description: str,
                    uris: list[str]) -> dict:
    """Private playlist with `uris` in order. Returns {id, url, added}."""
    pl = call(conn, http, "POST", "/me/playlists",
              json={"name": name, "description": description, "public": False}).json()
    for i in range(0, len(uris), BATCH):
        call(conn, http, "POST", f"/playlists/{pl['id']}/items", json={"uris": uris[i:i + BATCH]})
    return {"id": pl["id"], "url": pl.get("external_urls", {}).get("spotify"), "added": len(uris)}


def parse_playlist_id(text: str) -> str | None:
    """Playlist id from an open.spotify.com link or a spotify:playlist: URI."""
    m = PLAYLIST_ID.search(text.strip())
    return m.group(1) if m else None


def playlist_track_uris(conn: psycopg.Connection, http: httpx.Client, playlist_id: str,
                        cap: int = 500) -> list[str]:
    """Track URIs of one of the user's own/collaborative playlists, in order (max `cap`)."""
    uris: list[str] = []
    offset = 0
    while len(uris) < cap:
        page = call(conn, http, "GET", f"/playlists/{playlist_id}/items",
                    params={"limit": 50, "offset": offset, "additional_types": "track",
                            "fields": "items(item(uri)),next"}).json()
        items = page.get("items", [])
        for it in items:
            uri = (it.get("item") or it.get("track") or {}).get("uri") or ""
            if uri.startswith("spotify:track:"):
                uris.append(uri)
        if not page.get("next") or not items:
            break
        offset += len(items)  # advance by what we actually got, not the requested limit
    return uris[:cap]
