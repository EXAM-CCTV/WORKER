"""
Error code library for the script-server (FastAPI).

Every error reply gets the same shape as the Node API:

    {"success": false, "message": ..., "code": ..., "title": ..., "action": ...,
     "requestId": ..., "detail": ...}

`detail` stays because FastAPI clients (and three frontend pages) read it; it now carries
the friendly message too. Keep codes in step with cctv_backend/src/errors/catalog.js and
cctv_frontend/src/errors/catalog.js.

Wire-up (app/main.py):  register_error_handlers(app)
Raise with a code:      raise AppHTTPException("DETECTION_UPDATE_FAILED")
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("app.errors")

CATALOG: dict[str, dict[str, Any]] = {
    # ── shared with the Node API ────────────────────────────────────────────
    "VALIDATION_FAILED": {"status": 422, "title": "Check the request",
                          "message": "Some of the information sent isn't valid.",
                          "action": "Check the values and try again."},
    "REQUEST_INVALID": {"status": 400, "title": "Invalid request",
                        "message": "Something in the request isn't valid.",
                        "action": "Check the values and try again."},
    "NOT_FOUND": {"status": 404, "title": "Not found",
                  "message": "The item you're looking for doesn't exist or was removed.",
                  "action": "Refresh the page to see the latest data."},
    "ROUTE_NOT_FOUND": {"status": 404, "title": "Not found",
                        "message": "This page or action doesn't exist.",
                        "action": "Refresh the page. If it keeps happening, the app may need an update."},
    "METHOD_NOT_ALLOWED": {"status": 405, "title": "Not allowed",
                           "message": "This action isn't supported here.",
                           "action": "Refresh the page and try again."},
    "AUTH_TOKEN_MISSING": {"status": 401, "title": "Not signed in",
                           "message": "You're not signed in.",
                           "action": "Please sign in to continue."},
    "AUTH_FORBIDDEN": {"status": 403, "title": "Not allowed",
                       "message": "You don't have permission to do this.",
                       "action": "Ask an administrator for access if you need it."},
    "CONFLICT": {"status": 409, "title": "Conflict",
                 "message": "This conflicts with data that already exists.",
                 "action": "Refresh the page and check the existing data."},
    "REQUEST_TOO_LARGE": {"status": 413, "title": "Too large",
                          "message": "What you sent is too large for the server to accept.",
                          "action": "Send less data at once and try again."},
    "UPSTREAM_UNAVAILABLE": {"status": 502, "title": "Service not responding",
                             "message": "A connected service isn't responding.",
                             "action": "Try again in a minute. If it keeps happening, contact support."},
    "UPSTREAM_TIMEOUT": {"status": 504, "title": "Service too slow",
                         "message": "A connected service took too long to respond.",
                         "action": "Try again in a minute."},
    "SERVER_ERROR": {"status": 500, "title": "Something went wrong",
                     "message": "Something went wrong on our side.",
                     "action": "Try again. If it keeps happening, share the reference code with support."},
    "SERVICE_UNAVAILABLE": {"status": 503, "title": "Temporarily unavailable",
                            "message": "This service is temporarily unavailable.",
                            "action": "Try again in a few minutes."},

    # ── script-server specific ──────────────────────────────────────────────
    "IMAGE_PATH_INVALID": {"status": 400, "title": "Invalid image link",
                           "message": "This image link isn't valid.",
                           "action": "Refresh the page to reload the images."},
    "IMAGE_NOT_FOUND": {"status": 404, "title": "Image unavailable",
                        "message": "This image is no longer available.",
                        "action": "It may have been cleaned up. Refresh the page."},
    "EXECUTION_LOGS_NOT_FOUND": {"status": 404, "title": "No logs",
                                 "message": "No logs were found for this run.",
                                 "action": "The run may not have started yet, or its logs were cleaned up."},
    "GPU_WORKER_NOT_FOUND": {"status": 404, "title": "GPU server not reporting",
                             "message": "This GPU server isn't reporting.",
                             "action": "Check that the worker is running, then refresh."},
    "METRICS_PATH_NOT_ALLOWED": {"status": 400, "title": "Request not allowed",
                                 "message": "This metrics request isn't allowed.",
                                 "action": "Refresh the GPU Monitor page."},
    "METRICS_UNAVAILABLE": {"status": 503, "title": "Metrics unavailable",
                            "message": "GPU metrics aren't available right now.",
                            "action": "The metrics service may be stopped. Try again later or contact support."},
    "METRICS_ERROR": {"status": 502, "title": "Metrics error",
                      "message": "The metrics service returned an error.",
                      "action": "Try again in a minute."},
    "DETECTION_REQUEST_INVALID": {"status": 400, "title": "Invalid detection",
                                  "message": "This detection request isn't valid.",
                                  "action": "Refresh the page and try again."},
    "DETECTION_FILE_NOT_FOUND": {"status": 404, "title": "Detection unavailable",
                                 "message": "This detection's file is no longer available.",
                                 "action": "Refresh the page to see the latest detections."},
    "DETECTION_UPDATE_FAILED": {"status": 500, "title": "Update failed",
                                "message": "This detection couldn't be updated.",
                                "action": "Try again. If it keeps happening, share the reference code with support."},
}

_CODE_FOR_STATUS = {
    400: "REQUEST_INVALID", 401: "AUTH_TOKEN_MISSING", 403: "AUTH_FORBIDDEN",
    404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 409: "CONFLICT", 413: "REQUEST_TOO_LARGE",
    422: "VALIDATION_FAILED", 502: "UPSTREAM_UNAVAILABLE", 503: "SERVICE_UNAVAILABLE",
    504: "UPSTREAM_TIMEOUT",
}

# HTTPException details already raised in app/main.py → code. Matched on (status, regex), so
# the raise sites keep working unchanged; new code should raise AppHTTPException instead.
_DETAIL_RULES: list[tuple[int, re.Pattern, str]] = [
    (400, re.compile(r"^Invalid path$"), "IMAGE_PATH_INVALID"),
    (404, re.compile(r"^Image not found$"), "IMAGE_NOT_FOUND"),
    (404, re.compile(r"^Logs not found"), "EXECUTION_LOGS_NOT_FOUND"),
    (404, re.compile(r"^Worker .+ not found$"), "GPU_WORKER_NOT_FOUND"),
    (400, re.compile(r"^Path not allowed"), "METRICS_PATH_NOT_ALLOWED"),
    (503, re.compile(r"^Prometheus not running"), "METRICS_UNAVAILABLE"),
    (502, re.compile(r"^Prometheus error"), "METRICS_ERROR"),
    (400, re.compile(r"^Invalid (relative path|source|detection type)$"), "DETECTION_REQUEST_INVALID"),
    (404, re.compile(r"^Source file not found"), "DETECTION_FILE_NOT_FOUND"),
    (500, re.compile(r"^Failed to (skip detection|undo skip)$"), "DETECTION_UPDATE_FAILED"),
]

_TECHNICAL_RE = re.compile(
    r"Traceback|Exception|Error:|errno|pymysql|OperationalError|ProgrammingError|SQL|"
    r"Connection refused|object has no attribute|NoneType|File \".+\.py\"", re.I)

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")


class AppHTTPException(StarletteHTTPException):
    """HTTPException carrying a catalog code: raise AppHTTPException("IMAGE_NOT_FOUND")."""

    def __init__(self, code: str, message: Optional[str] = None, status: Optional[int] = None,
                 headers: Optional[dict] = None):
        entry = CATALOG.get(code, CATALOG["SERVER_ERROR"])
        self.code = code if code in CATALOG else "SERVER_ERROR"
        super().__init__(status_code=status or entry["status"], detail=message or entry["message"],
                         headers=headers)


def looks_technical(text: Any) -> bool:
    return isinstance(text, str) and bool(_TECHNICAL_RE.search(text))


def code_for_status(status: int) -> str:
    return _CODE_FOR_STATUS.get(status) or ("SERVER_ERROR" if status >= 500 else "REQUEST_INVALID")


def classify_http_exception(status: int, detail: Any) -> tuple[str, Optional[str]]:
    """(code, message to show or None for the catalog wording) for an HTTPException."""
    text = detail if isinstance(detail, str) else None
    if text:
        for rule_status, pattern, code in _DETAIL_RULES:
            if status == rule_status and pattern.search(text):
                return code, None
    code = code_for_status(status)
    if status == 404 and text == "Not Found":          # Starlette's unknown-route reply
        return "ROUTE_NOT_FOUND", None
    if status == 405:
        return "METHOD_NOT_ALLOWED", None
    if text and status < 500 and not looks_technical(text):
        return code, text
    return code, None


def error_body(code: str, message: Optional[str] = None, request_id: Optional[str] = None,
               details: Any = None) -> dict:
    entry = CATALOG.get(code, CATALOG["SERVER_ERROR"])
    msg = message or entry["message"]
    body = {
        "success": False,
        "message": msg,
        "code": code if code in CATALOG else "SERVER_ERROR",
        "title": entry["title"],
        "action": entry["action"],
        "detail": msg,
    }
    if request_id:
        body["requestId"] = request_id
    if details is not None:
        body["details"] = details
    return body


def _request_id(request: Request) -> Optional[str]:
    return getattr(request.state, "request_id", None)


def _validation_message(errors: list) -> Optional[str]:
    """First field problem in plain words, e.g. "uid: Field required"."""
    if not errors:
        return None
    first = errors[0]
    loc = [str(p) for p in first.get("loc", []) if p not in ("body", "query", "path")]
    msg = str(first.get("msg", "")).strip()
    if not msg:
        return None
    return f"{'.'.join(loc)}: {msg}" if loc else msg


def register_error_handlers(app: FastAPI) -> None:
    @app.middleware("http")
    async def _request_id_middleware(request: Request, call_next):
        incoming = request.headers.get("x-request-id", "")
        request.state.request_id = incoming if _REQUEST_ID_RE.match(incoming) else str(uuid.uuid4())
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        return response

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(request: Request, exc: StarletteHTTPException):
        if isinstance(exc, AppHTTPException):
            code, message = exc.code, exc.detail
        else:
            code, message = classify_http_exception(exc.status_code, exc.detail)
        if exc.status_code >= 500:
            logger.error(f"[{_request_id(request)}] {request.method} {request.url.path} → "
                         f"{exc.status_code} {code}: {exc.detail}")
        return JSONResponse(status_code=exc.status_code, headers=getattr(exc, "headers", None),
                            content=error_body(code, message, _request_id(request)))

    @app.exception_handler(RequestValidationError)
    async def _validation_exception(request: Request, exc: RequestValidationError):
        errors = exc.errors()
        return JSONResponse(status_code=422, content=error_body(
            "VALIDATION_FAILED", _validation_message(errors), _request_id(request),
            details=[{"loc": e.get("loc"), "msg": e.get("msg")} for e in errors]))

    @app.exception_handler(Exception)
    async def _unhandled_exception(request: Request, exc: Exception):
        logger.exception(f"[{_request_id(request)}] Unhandled error on {request.method} {request.url.path}")
        return JSONResponse(status_code=500, content=error_body("SERVER_ERROR", None, _request_id(request)))
