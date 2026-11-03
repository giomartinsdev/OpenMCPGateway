"""Domain and application refusals, as HTTP — one table, one place.

A route that caught its own exceptions would drift: the same refusal would be a
403 in one endpoint and a 500 in another, and a client would need a decoder per
route. Registered once on the app, so a route body is a call and a return.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from gateway.application.principal import NotOnboarded
from gateway.application.services.roles import CannotGrant
from gateway.application.services.signin import SignInRefused
from gateway.configfile import ConfigFileError
from gateway.domain.errors import DomainRefusal, NotFound
from gateway.domain.filters import UnknownFilter
from gateway.domain.model import DomainError
from gateway.domain.paging import InvalidCursor
from gateway.domain.permissions import PermissionCatalogError
from gateway.infrastructure.eventstore.postgres import ConcurrencyError
from gateway.log import log

#: Refusal type -> status. Everything here is the caller's problem to fix, which
#: is why none of them is a 500. `HTTPException` is not in the table: FastAPI
#: already renders it, and overriding it would lose its headers.
_STATUS = {
    NotFound: 404,
    NotOnboarded: 403,
    SignInRefused: 503,
    ConcurrencyError: 409,
    DomainError: 422,
    DomainRefusal: 422,
    PermissionCatalogError: 422,
    ConfigFileError: 422,
    CannotGrant: 422,
    UnknownFilter: 422,
    InvalidCursor: 422,
}


def install(app: FastAPI) -> None:
    """Turn every refusal into its status, once, for the whole app."""

    async def refusal(request: Request, exc: Exception) -> JSONResponse:
        del request
        status = next(
            (code for kind, code in _STATUS.items() if isinstance(exc, kind)), 500
        )
        return JSONResponse({"detail": str(exc)}, status_code=status)

    for kind in _STATUS:
        app.add_exception_handler(kind, refusal)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        # The message is not the client's business; the middleware already wrote
        # it to the log with the path.
        log.error("http.unhandled", path=request.url.path, error=repr(exc))
        return JSONResponse({"detail": "internal error"}, status_code=500)


__all__ = ["install"]
