"""
NHIT | ETC Analytics — Flask entry point.

This file is intentionally thin. Real implementation lives in the
`backend/` package:
    backend.api.security  — CORS + security headers
    backend.api.routes    — HTTP handlers (/, /api/health)
    backend.core          — analytics, parser, snapshot loader, …

Frontend assets are served from the project root (index.html) plus
the `frontend/` directory (CSS/JS modules).

Vercel: this module is the @vercel/python entry point declared in
vercel.json.
Self-hosted: `python server.py` runs the dev server on port 5051.
"""
from __future__ import annotations

import logging
from pathlib import Path

# Optionally load .env for local development.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

from flask import Flask

from backend.api.routes import register_routes
from backend.api.security import configure_cors
from backend.core.static_loader import DEFAULT_JSON_DIR, load_snapshot

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("nhit.server")

# ── Paths ─────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent
JSON_DIR = DEFAULT_JSON_DIR

# ── Flask app ─────────────────────────────────────────────────────────────
# `static_folder` is the project root so /styles.css, /frontend/js/app.js,
# /downloads/json/* are all reachable as same-origin static assets.
app = Flask(__name__, static_folder=str(ROOT), static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024  # 1 MiB cap on requests

configure_cors(app)

# ── Snapshot ───────────────────────────────────────────────────────────────
SNAPSHOT: dict | None = None


def _ensure_snapshot() -> dict:
    """Load downloads/json/* into the SNAPSHOT global. Cached after first call."""
    global SNAPSHOT
    if SNAPSHOT is not None:
        return SNAPSHOT
    if not (JSON_DIR / "_index.json").exists():
        raise RuntimeError(
            f"{JSON_DIR}/_index.json missing — run scripts/build_json_export.py first",
        )
    SNAPSHOT = load_snapshot(JSON_DIR)
    log.info(
        "Snapshot loaded: %d plazas, %d months",
        len(SNAPSHOT["plazas"]),
        len(SNAPSHOT["months"]),
    )
    return SNAPSHOT


# Load eagerly at import time so any deployment (gunicorn, vercel, dev) is ready
# to serve as soon as the worker is up.
_ensure_snapshot()

register_routes(app, snapshot_provider=_ensure_snapshot, static_dir=ROOT)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5051, debug=False, threaded=True)
