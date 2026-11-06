"""Who is calling: the Cloudflare Access identity of the private deployment.

Behind Cloudflare Access, every request that reaches the origin carries a signed JWT in the
`Cf-Access-Jwt-Assertion` header. When CF_ACCESS_AUD is set, the API verifies it (RS256,
Cloudflare's public keys from https://<team>/cdn-cgi/access/certs, audience = the Access
application's AUD tag, expiry) on every request except /health, and rejects the request (403)
without a valid token. The verified email is the user id: events, Spotify login and
"delete my data" are per user.

Without CF_ACCESS_AUD (local development) every request is the single user LOCAL_USER.
SWAVE_REQUIRE_ACCESS=1 (set by the private deployment) makes a missing configuration a startup
error, so the public-facing instance can never run unauthenticated by accident.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

import jwt

HEADER = "Cf-Access-Jwt-Assertion"
LOCAL_USER = "local"
OPEN_PATHS = {"/health"}  # container health check; returns no user data


class AccessError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    team_domain: str  # e.g. "yourteam.cloudflareaccess.com"
    aud: str  # the Access application's Application Audience (AUD) tag

    @property
    def issuer(self) -> str:
        return f"https://{self.team_domain}"

    @property
    def certs_url(self) -> str:
        return f"https://{self.team_domain}/cdn-cgi/access/certs"


def config_from_env() -> Config | None:
    team = os.environ.get("CF_ACCESS_TEAM_DOMAIN", "").strip().removeprefix("https://").rstrip("/")
    aud = os.environ.get("CF_ACCESS_AUD", "").strip()
    required = os.environ.get("SWAVE_REQUIRE_ACCESS", "").strip() in ("1", "true", "yes")
    if team and aud:
        return Config(team, aud)
    if required or team or aud:
        raise AccessError("Cloudflare Access is required: set both CF_ACCESS_TEAM_DOMAIN and CF_ACCESS_AUD")
    return None


class Verifier:
    """Verifies Access tokens; `signing_key(token)` returns the public key (injectable in tests)."""

    def __init__(self, cfg: Config, signing_key: Callable[[str], object] | None = None):
        self.cfg = cfg
        if signing_key is None:
            jwks = jwt.PyJWKClient(cfg.certs_url, cache_keys=True, lifespan=3600)
            signing_key = lambda token: jwks.get_signing_key_from_jwt(token).key  # noqa: E731
        self.signing_key = signing_key

    def email(self, token: str | None) -> str:
        """The verified email in `token`, or AccessError."""
        if not token:
            raise AccessError("Missing Cloudflare Access token")
        try:
            claims = jwt.decode(token, self.signing_key(token), algorithms=["RS256"],
                                audience=self.cfg.aud, issuer=self.cfg.issuer,
                                options={"require": ["exp", "iat", "aud", "iss"]})
        except (jwt.PyJWTError, ValueError) as e:
            raise AccessError(f"Invalid Cloudflare Access token: {type(e).__name__}") from None
        email = str(claims.get("email") or "").strip().lower()
        if not email:
            raise AccessError("Cloudflare Access token has no email")
        return email
