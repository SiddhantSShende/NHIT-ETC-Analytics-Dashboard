"""
NHIT | ETC Analytics API Server.

Loads the pre-built snapshot.json (and SQLite mirror) at startup and
serves every endpoint from in-memory dictionaries. No PDFs are touched
on the request path.

If snapshot.json is missing the server will trigger a build automatically
so that a fresh checkout works with `python server.py`.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path

from flask import Flask, g, jsonify, request, send_from_directory
from flask_cors import CORS
from werkzeug.exceptions import HTTPException

# ── Paths & logging ─────────────────────────────────────────────────────────
DIR = Path(__file__).parent
DATA_DIR = DIR / "data"
SNAPSHOT_PATH = DATA_DIR / "snapshot.json"
DB_PATH = DATA_DIR / "etc.db"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("nhit.server")

app = Flask(__name__, static_folder=str(DIR), static_url_path="")
CORS(app)

# In-memory snapshot loaded at startup.
SNAPSHOT: dict | None = None


# ── Snapshot loader ─────────────────────────────────────────────────────────
def _ensure_snapshot() -> None:
    """Load snapshot.json into the SNAPSHOT global. Build it first if missing."""
    global SNAPSHOT

    if not SNAPSHOT_PATH.exists():
        log.warning("%s missing — running build_data.py", SNAPSHOT_PATH)
        from build_data import main as build_main
        rc = build_main([])
        if rc != 0:
            raise RuntimeError("build_data.py failed; cannot start server")

    log.info("Loading snapshot from %s", SNAPSHOT_PATH)
    with SNAPSHOT_PATH.open(encoding="utf-8") as f:
        SNAPSHOT = json.load(f)

    log.info(
        "Snapshot loaded: %d plazas, %d months, %d validation warnings",
        len(SNAPSHOT["plazas"]),
        len(SNAPSHOT["months"]),
        len(SNAPSHOT.get("validation_warnings", [])),
    )


# ── Request lifecycle ───────────────────────────────────────────────────────
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
    return resp


@app.errorhandler(HTTPException)
def _on_http_error(exc):
    """Pass through Werkzeug HTTP errors (404, 405, …) with their original
    status — don't masquerade them as 500s."""
    return jsonify({
        "error":      exc.description,
        "status":     exc.code,
        "request_id": getattr(g, "req_id", None),
    }), exc.code


@app.errorhandler(Exception)
def _on_error(exc):
    log.exception("[%s] unhandled: %s", getattr(g, "req_id", "?"), exc)
    return jsonify({
        "error":      "Internal server error",
        "request_id": getattr(g, "req_id", None),
    }), 500


# ── Routes ──────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(str(DIR), "index.html")


@app.route("/favicon.ico")
def favicon():
    """Browsers always ask for /favicon.ico — return 204 so it doesn't show
    up as a 500 from the catch-all error handler."""
    return ("", 204)


@app.route("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "snapshot_generated_at": SNAPSHOT.get("generated_at"),
        "plazas":           len(SNAPSHOT["plazas"]),
        "months_available": len(SNAPSHOT["months"]),
        "validation_warnings": len(SNAPSHOT.get("validation_warnings", [])),
        "sqlite_present": DB_PATH.exists(),
    })


@app.route("/api/meta")
def meta():
    """Dropdown data: plaza list, available years, available months."""
    return jsonify({
        "plazas": SNAPSHOT["plazas"],
        "years":  SNAPSHOT["years"],
        "months": SNAPSHOT["available_months"],
    })


@app.route("/api/data")
def data():
    """Full analytics for one (plaza, year, month)."""
    plaza = request.args.get("plaza", "").strip()
    try:
        year = int(request.args.get("year", "") or 0)
        month = int(request.args.get("month", "") or 0)
    except ValueError:
        return jsonify({"error": "year and month must be integers"}), 400

    if not plaza or not year or not month:
        return jsonify({"error": "Missing parameters: plaza, year, month required."}), 400
    if not (1 <= month <= 12):
        return jsonify({"error": "month must be between 1 and 12"}), 400
    if plaza not in SNAPSHOT["data"]:
        return jsonify({"error": f"Unknown plaza: {plaza}"}), 404

    rec = SNAPSHOT["data"][plaza].get(f"{year}-{month:02d}")
    if not rec:
        return jsonify({
            "error": f"No data found for {plaza} in {year}-{month:02d}.",
        }), 404

    return jsonify({"record": rec})


@app.route("/api/trend")
def trend():
    """Monthly totals for one plaza across every available report."""
    plaza = request.args.get("plaza", "").strip()
    if not plaza:
        return jsonify({"error": "plaza parameter required"}), 400
    if plaza not in SNAPSHOT["monthly_totals"]:
        return jsonify({"error": f"Unknown plaza: {plaza}"}), 404
    return jsonify({"plaza": plaza, "trend": SNAPSHOT["monthly_totals"][plaza]})


@app.route("/api/validation")
def validation():
    """Surfaces parsing-vs-PDF-total discrepancies for transparency."""
    return jsonify({
        "warnings": SNAPSHOT.get("validation_warnings", []),
    })


@app.route("/api/raw")
def raw():
    """Direct SQLite query for a plaza — handy for analysts."""
    plaza = request.args.get("plaza", "").strip()
    if not DB_PATH.exists():
        return jsonify({"error": "etc.db not built yet — run build_data.py"}), 503
    if not plaza:
        return jsonify({"error": "plaza parameter required"}), 400
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT r.year, r.month, t.vehicle_category, t.count, t.amount
        FROM transactions t
        JOIN plazas  p ON p.id = t.plaza_id
        JOIN reports r ON r.id = t.report_id
        WHERE p.name = ?
        ORDER BY r.year, r.month, t.vehicle_category
        """,
        (plaza,),
    ).fetchall()
    conn.close()
    return jsonify({"plaza": plaza, "rows": [dict(r) for r in rows]})


# Load snapshot at import time so this works under any WSGI server too.
_ensure_snapshot()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=False, threaded=True)
