"""Cloudflare Access gate + per-user data (SYNTHETIC tokens signed with a throwaway test key)."""

from __future__ import annotations

import time

import jwt
import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from swave import access
from swave.main import create_app
from tests.test_api import dsn  # noqa: F401 (pytest fixture)

CFG = access.Config("swave-test.cloudflareaccess.com", "aud-test-123")
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
WEIGHTS = {"cooccurrence_mpd": 1.0}


def token(email: str = "friend@example.com", aud: str = CFG.aud, exp: float | None = None, key=KEY) -> str:
    now = int(time.time())
    return jwt.encode({"email": email, "aud": [aud], "iss": CFG.issuer, "iat": now,
                       "exp": int(exp if exp is not None else now + 600), "sub": "x"}, key, algorithm="RS256")


def h(tok: str) -> dict:
    return {access.HEADER: tok}


@pytest.fixture
def gated(dsn):  # noqa: F811
    verifier = access.Verifier(CFG, signing_key=lambda _t: KEY.public_key())
    with TestClient(create_app(dsn, weights=WEIGHTS, verifier=verifier)) as c:
        with psycopg.connect(dsn) as conn:
            conn.execute("DELETE FROM events WHERE user_id LIKE '%%@example.com'")
            conn.execute("DELETE FROM preview_attempts WHERE user_id LIKE '%%@example.com'")
        yield c


def test_requests_without_a_valid_access_token_are_rejected(gated):
    assert gated.get("/providers").status_code == 403
    assert gated.get("/providers", headers=h("not-a-jwt")).status_code == 403
    assert gated.get("/providers", headers=h(token(aud="someone-else"))).status_code == 403
    assert gated.get("/providers", headers=h(token(exp=time.time() - 60))).status_code == 403
    assert gated.get("/providers", headers=h(token(key=OTHER_KEY))).status_code == 403  # forged
    assert gated.get("/providers", headers=h(token())).status_code == 200
    assert gated.get("/health").status_code == 200  # container health check, no user data
    assert gated.get("/me", headers=h(token("Friend@Example.com"))).json()["user"] == "friend@example.com"


def test_events_are_per_user_and_delete_removes_only_yours(gated):
    a, b = h(token("a@example.com")), h(token("b@example.com"))
    ev = {"action": "shown", "track_uri": "spotify:track:fake000000000000000001", "position": 0}
    assert gated.post("/events", json=ev, headers=a).status_code == 201
    assert gated.post("/events/batch", json=[ev, ev], headers=b).status_code == 201
    assert gated.post("/preview/attempts", headers=a, json={
        "track_uri": "spotify:track:fake000000000000000001", "outcome": "ok"}).status_code == 201
    me_a = gated.get("/me", headers=a).json()
    assert (me_a["events"], me_a["preview_attempts"], me_a["spotify_connected"]) == (1, 1, False)
    assert gated.get("/me", headers=b).json()["events"] == 2
    gone = gated.delete("/me/data", headers=a).json()["deleted"]
    assert gone == {"events": 1, "preview_attempts": 1, "spotify_login": 0}
    assert gated.get("/me", headers=a).json()["events"] == 0
    assert gated.get("/me", headers=b).json()["events"] == 2  # someone else's data is untouched
    assert gated.delete("/me/data").status_code == 403  # no token: nothing deleted


def test_local_mode_is_the_single_local_user(dsn, monkeypatch):  # noqa: F811
    for k in ("CF_ACCESS_TEAM_DOMAIN", "CF_ACCESS_AUD", "SWAVE_REQUIRE_ACCESS"):
        monkeypatch.delenv(k, raising=False)
    with TestClient(create_app(dsn, weights=WEIGHTS)) as c:
        assert c.get("/me").json()["user"] == access.LOCAL_USER


def test_private_deployment_refuses_to_start_without_access_config(monkeypatch):
    monkeypatch.setenv("SWAVE_REQUIRE_ACCESS", "1")
    monkeypatch.delenv("CF_ACCESS_TEAM_DOMAIN", raising=False)
    monkeypatch.delenv("CF_ACCESS_AUD", raising=False)
    with pytest.raises(access.AccessError, match="CF_ACCESS_AUD"):
        create_app("postgresql://unused", weights=WEIGHTS)
    monkeypatch.delenv("SWAVE_REQUIRE_ACCESS")
    monkeypatch.setenv("CF_ACCESS_AUD", "half-configured")
    with pytest.raises(access.AccessError):
        create_app("postgresql://unused", weights=WEIGHTS)


def test_old_single_row_spotify_login_is_rekeyed_to_the_local_user(dsn):  # noqa: F811
    with psycopg.connect(dsn) as conn:
        conn.execute("""DROP TABLE IF EXISTS spotify_auth;
            CREATE TABLE spotify_auth (id int PRIMARY KEY CHECK (id = 1), access_token text NOT NULL,
                refresh_token text NOT NULL, expires_at timestamptz NOT NULL, display_name text);
            INSERT INTO spotify_auth VALUES (1, 'AT', 'RT', now(), 'Owner')""")
    with TestClient(create_app(dsn, weights=WEIGHTS)) as c:
        assert c.get("/auth/status").json()["display_name"] == "Owner"
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT user_id FROM spotify_auth").fetchall() == [("local",)]
