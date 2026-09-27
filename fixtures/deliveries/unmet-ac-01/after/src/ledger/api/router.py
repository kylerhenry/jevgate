"""API router: maps HTTP routes to service calls and service errors to JSON responses."""
from __future__ import annotations

from ledger.api.responses import Response, json_error
from ledger.errors import LedgerError, NotFoundError, ValidationError
from ledger.services import posting


def post_entry(request) -> Response:
    """POST /v1/entries: post one balanced entry for the current tenant."""
    try:
        entry_id = posting.post_entry(request.entry())
    except LedgerError as exc:
        return error_response(exc)
    return Response(201, {"id": entry_id})


def error_response(exc: LedgerError) -> Response:
    """Translate a service error into the JSON error response the API documents."""
    if isinstance(exc, NotFoundError):
        return json_error(404, exc.code, str(exc))
    if isinstance(exc, ValidationError):
        return json_error(422, exc.code, str(exc))
    return json_error(500, "internal", "internal error")
