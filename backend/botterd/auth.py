"""Local bearer-token authentication."""

from __future__ import annotations

import hmac
from collections.abc import Callable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from .errors import error_response


class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        *,
        token: str | None = None,
        token_provider: Callable[[], str] | None = None,
        public_paths: frozenset[str] | None = None,
    ):
        super().__init__(app)
        self.token = token
        self.token_provider = token_provider
        self.public_paths = public_paths or frozenset({"/v1/health"})

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path in self.public_paths or not request.url.path.startswith("/v1/"):
            return await call_next(request)
        expected = self.token or (self.token_provider() if self.token_provider else "")
        if self.token is None and expected:
            self.token = expected
        authorization = request.headers.get("authorization", "")
        scheme, separator, candidate = authorization.partition(" ")
        valid = bool(expected and separator and scheme.lower() == "bearer") and hmac.compare_digest(
            candidate.encode("utf-8"), expected.encode("utf-8")
        )
        if not valid:
            return error_response(
                401,
                "unauthorized",
                "A valid bearer token is required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)
