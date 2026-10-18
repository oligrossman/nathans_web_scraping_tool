"""Bearer-token ASGI middleware for the MCP HTTP transport."""

from __future__ import annotations

import hmac
import os
from typing import Iterable

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

ENV_TOKEN = "MCP_AUTH_TOKEN"
DEFAULT_PUBLIC_PATHS = frozenset({"/health"})


def load_auth_token() -> str:
    """Return ``MCP_AUTH_TOKEN`` or raise if it is unset/blank."""
    token = os.environ.get(ENV_TOKEN, "").strip()
    if not token:
        raise RuntimeError(
            f"{ENV_TOKEN} must be set to a non-empty bearer token before "
            "starting the MCP server."
        )
    return token


class BearerAuthMiddleware:
    """Reject requests without a matching ``Authorization: Bearer`` token.

    ``/health`` (and any other ``public_paths``) stay open for host probes.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        token: str | None = None,
        public_paths: Iterable[str] | None = None,
    ) -> None:
        self.app = app
        self.token = token if token is not None else load_auth_token()
        self.public_paths = frozenset(public_paths or DEFAULT_PUBLIC_PATHS)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "") or ""
        if path in self.public_paths:
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        auth_header = headers.get("authorization", "")
        scheme, _, presented = auth_header.partition(" ")
        if scheme.lower() != "bearer" or not presented:
            await JSONResponse({"detail": "Unauthorized"}, status_code=401)(
                scope, receive, send
            )
            return

        if not hmac.compare_digest(presented.strip(), self.token):
            await JSONResponse({"detail": "Unauthorized"}, status_code=401)(
                scope, receive, send
            )
            return

        await self.app(scope, receive, send)
