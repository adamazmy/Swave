"""Production entry point: the API served under a path prefix on the web app's own domain.

The private deployment routes https://<domain>/api/* to this container (Cloudflare Tunnel
ingress), without stripping the prefix, so the app strips it here: /api/recommend → /recommend,
with root_path = /api so generated URLs keep the prefix. Paths outside the prefix pass through
unchanged (the container health check calls /health directly).

    uvicorn swave.serve:create --factory --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import os

from swave.main import create_app


def strip_prefix(app, prefix: str):  # noqa: ANN001, ANN201 (ASGI app)
    prefix = "/" + prefix.strip("/")

    async def asgi(scope, receive, send):  # noqa: ANN001
        if scope["type"] in ("http", "websocket"):
            path = scope["path"]
            if path == prefix or path.startswith(prefix + "/"):
                scope = dict(scope, path=path[len(prefix):] or "/",
                             raw_path=(path[len(prefix):] or "/").encode(),
                             root_path=scope.get("root_path", "") + prefix)
        await app(scope, receive, send)

    return asgi


def create():  # noqa: ANN201
    return strip_prefix(create_app(), os.environ.get("SWAVE_PATH_PREFIX", "/api"))
