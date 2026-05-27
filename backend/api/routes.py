"""
HTTP routes: static index and /api/health.

`register_routes(app, snapshot_provider)` wires everything onto a Flask
app. `snapshot_provider` is a zero-arg callable that returns the live
SNAPSHOT dict — passing it as a callable instead of the dict itself
keeps server.py and routes.py from racing on import order.
"""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Callable

from flask import Flask, g, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from .security import apply_security_headers

log = logging.getLogger("nhit.api")


def register_routes(
    app: Flask,
    snapshot_provider: Callable[[], dict],
    static_dir: Path,
) -> None:
    """Attach all HTTP routes + lifecycle hooks to the Flask app."""

    @app.before_request
    def _start_timer() -> None:
        g.req_id = uuid.uuid4().hex[:8]
        g.t0 = time.perf_counter()

    @app.after_request
    def _log_request(resp):
        dur_ms = (time.perf_counter() - g.t0) * 1000
        log.info(
            "%s %s %s -> %d  %.1fms",
            g.req_id, request.method, request.full_path.rstrip("?"),
            resp.status_code, dur_ms,
        )
        resp.headers["X-Request-ID"] = g.req_id
        apply_security_headers(resp)
        return resp

    @app.errorhandler(HTTPException)
    def _on_http_error(exc):
        return jsonify({
            "error": exc.description,
            "status": exc.code,
            "request_id": getattr(g, "req_id", None),
        }), exc.code

    @app.errorhandler(Exception)
    def _on_error(exc):
        log.exception("[%s] unhandled: %s", getattr(g, "req_id", "?"), exc)
        return jsonify({
            "error": "Internal server error",
            "request_id": getattr(g, "req_id", None),
        }), 500

    # ── Static / health ────────────────────────────────────────────────────
    @app.route("/")
    def index():
        return send_from_directory(str(static_dir), "index.html")

    @app.route("/favicon.ico")
    def favicon():
        return ("", 204)

    @app.route("/api/health")
    def health():
        snap = snapshot_provider()
        return jsonify({
            "status": "ok",
            "snapshot_generated_at": snap.get("generated_at"),
            "plazas":           len(snap["plazas"]),
            "months_available": len(snap["months"]),
        })
