"""
Tests for app/errors.py (error code library).

    cd script-server && venv/bin/python tests/test_errors.py      # no pytest needed
    cd script-server && venv/bin/python -m pytest tests/test_errors.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from app.errors import CATALOG, AppHTTPException, register_error_handlers  # noqa: E402


class _Uid(BaseModel):
    uid: str


def _client() -> TestClient:
    app = FastAPI()
    register_error_handlers(app)

    # The exact details app/main.py raises today.
    @app.get("/img")
    def img():
        raise HTTPException(status_code=404, detail="Image not found")

    @app.get("/badpath")
    def badpath():
        raise HTTPException(status_code=400, detail="Invalid path")

    @app.get("/skip")
    def skip():
        raise HTTPException(status_code=500, detail="Failed to skip detection")

    @app.get("/prom")
    def prom():
        raise HTTPException(status_code=502, detail="Prometheus error: ConnectError('[Errno 111] Connection refused')")

    @app.get("/worker")
    def worker():
        raise HTTPException(status_code=404, detail="Worker worker-9 not found")

    @app.get("/plain404")
    def plain404():
        raise HTTPException(status_code=404, detail="Exam 99 has no shifts")

    @app.get("/coded")
    def coded():
        raise AppHTTPException("DETECTION_FILE_NOT_FOUND")

    @app.post("/body")
    def body(payload: _Uid):
        return {"success": True, "uid": payload.uid}

    @app.get("/boom")
    def boom():
        raise RuntimeError("pymysql.err.OperationalError: (2003, \"Can't connect\")")

    @app.get("/ok")
    def ok():
        return {"success": True}

    return TestClient(app, raise_server_exceptions=False)


def _check(r, status, code, message=None):
    assert r.status_code == status, (r.status_code, r.text)
    b = r.json()
    assert b["success"] is False and b["code"] == code, b
    assert b["title"] == CATALOG[code]["title"] and b["action"] == CATALOG[code]["action"], b
    assert b["detail"] == b["message"], b
    assert b["requestId"] == r.headers.get("x-request-id"), (b, dict(r.headers))
    if message is not None:
        assert b["message"] == message, b
    return b


def test_catalog_entries_complete():
    for code, e in CATALOG.items():
        assert code.isupper() and isinstance(e["status"], int), code
        for k in ("title", "message", "action"):
            assert isinstance(e[k], str) and len(e[k]) > 3, (code, k)


def test_existing_details_get_codes():
    c = _client()
    _check(c.get("/img"), 404, "IMAGE_NOT_FOUND", CATALOG["IMAGE_NOT_FOUND"]["message"])
    _check(c.get("/badpath"), 400, "IMAGE_PATH_INVALID")
    _check(c.get("/skip"), 500, "DETECTION_UPDATE_FAILED")
    _check(c.get("/worker"), 404, "GPU_WORKER_NOT_FOUND")
    b = _check(c.get("/prom"), 502, "METRICS_ERROR")
    assert "Errno" not in b["message"], b            # raw error text never reaches the user


def test_unmapped_4xx_keeps_its_message():
    _check(_client().get("/plain404"), 404, "NOT_FOUND", "Exam 99 has no shifts")


def test_app_http_exception():
    _check(_client().get("/coded"), 404, "DETECTION_FILE_NOT_FOUND")


def test_unknown_route_and_method():
    c = _client()
    _check(c.get("/no-such-route"), 404, "ROUTE_NOT_FOUND")
    _check(c.delete("/ok"), 405, "METHOD_NOT_ALLOWED")


def test_validation_error_is_readable():
    b = _check(_client().post("/body", json={}), 422, "VALIDATION_FAILED")
    assert b["message"].startswith("uid:"), b
    assert b["details"][0]["loc"][-1] == "uid", b


def test_unhandled_exception_is_hidden():
    r = _client().get("/boom")
    assert r.status_code == 500, r.text
    b = r.json()
    assert b["code"] == "SERVER_ERROR" and "pymysql" not in r.text, b


def test_success_untouched_and_request_id_reused():
    c = _client()
    r = c.get("/ok", headers={"X-Request-Id": "abcdef12-3456"})
    assert r.json() == {"success": True}
    assert r.headers["x-request-id"] == "abcdef12-3456"
    r = c.get("/img", headers={"X-Request-Id": "<bad>"})
    assert r.json()["requestId"] != "<bad>"


if __name__ == "__main__":
    import logging
    logging.disable(logging.CRITICAL)            # keep the deliberate /boom traceback out of the output
    failed = 0
    for name, fn in sorted((n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)):
        try:
            fn()
            print(f"  ok    {name}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL  {name}: {exc}")
    print(f"\n{'FAILED' if failed else 'passed'}: {failed} failing")
    sys.exit(1 if failed else 0)
