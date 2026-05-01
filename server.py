"""
NHIT | ETC Analytics API Server.

The frontend now reads `downloads/json/` directly via data-layer.js, so
this server exposes only:
  - GET  /api/health   liveness + diagnostics
  - POST /api/chat     OpenRouter proxy with snapshot-derived data context
  - static file serving (index.html, app.js, data-layer.js, etc.)

The aggregation/meta/taxonomy endpoints have been retired; they were
replaced by the static JSON files in downloads/json/. This file still
loads the same data into memory because the chatbot's `_build_data_context`
needs to fuzzy-match plazas/months and surface concrete numbers.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
import uuid
from pathlib import Path

# Load .env if python-dotenv is installed (graceful fallback otherwise)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

# Server-side OpenRouter configurations (from .env or system environment)
_SERVER_OR_KEY: str = os.getenv("OPENROUTER_API_KEY", "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")
_OR_MODEL: str = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.2-3b-instruct:free")
_OR_MAX_TOKENS: int = int(os.getenv("OPENROUTER_MAX_TOKENS", "800"))
_OR_TEMPERATURE: float = float(os.getenv("OPENROUTER_TEMPERATURE", "0.2"))

from flask import Flask, g, jsonify, request, send_from_directory
from flask_cors import CORS
from werkzeug.exceptions import HTTPException
from analytics import aggregate_plazas_for_month, aggregate_plazas_for_range
from static_loader import load_snapshot, DEFAULT_JSON_DIR

# ── Paths & logging ─────────────────────────────────────────────────────────
DIR = Path(__file__).parent
JSON_DIR = DEFAULT_JSON_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("nhit.server")

app = Flask(__name__, static_folder=str(DIR), static_url_path="")
# 1MB limit for incoming payloads to prevent DoS attacks
app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024

# Strict CORS: Only allow production Vercel URL and local development
CORS(app, resources={r"/api/*": {
    "origins": [
        "https://nhit-etc-analytics-dashboard.vercel.app",
        "http://localhost:5050",
        "http://localhost:5051",
        "http://127.0.0.1:5050",
        "http://127.0.0.1:5051"
    ]
}})

# In-memory snapshot loaded at startup.
SNAPSHOT: dict | None = None


# ── Snapshot loader ─────────────────────────────────────────────────────────
def _ensure_snapshot() -> None:
    """Load downloads/json/* into the SNAPSHOT global for the chatbot."""
    global SNAPSHOT
    if not (JSON_DIR / "_index.json").exists():
        raise RuntimeError(
            f"{JSON_DIR}/_index.json missing — run scripts/build_json_export.py first"
        )
    SNAPSHOT = load_snapshot(JSON_DIR)
    log.info(
        "Snapshot loaded: %d plazas, %d months",
        len(SNAPSHOT["plazas"]),
        len(SNAPSHOT["months"]),
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
    
    # Inject Enterprise Security Headers
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['X-Frame-Options'] = 'DENY'
    resp.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    resp.headers['Content-Security-Policy'] = "default-src 'self' https://openrouter.ai https://cdn.jsdelivr.net https://fonts.googleapis.com https://fonts.gstatic.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self' https://nhit-etc-analytics-dashboard.vercel.app;"
    
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
        "details":    str(exc),
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
        "data_source": str(JSON_DIR.relative_to(DIR)),
    })


# ── Chat / AI endpoint ──────────────────────────────────────────────────────
def _build_data_context(message: str) -> str:
    """Extract a compact, relevant slice of snapshot data to include as
    AI context.  We fuzzy-match plaza names and months from the user
    message so the model always has concrete numbers to cite."""
    msg_lower = message.lower()
    from constants import MONTH_MAP, MONTH_NAMES

    import re

    month_tokens = sorted(MONTH_MAP.keys(), key=len, reverse=True)
    month_pattern = r"\b(" + "|".join(re.escape(t) for t in month_tokens) + r")\b"
    months_found: list[int] = []
    for m in re.finditer(month_pattern, msg_lower, flags=re.IGNORECASE):
        token = m.group(1).lower()
        month_num = MONTH_MAP.get(token)
        if month_num and (not months_found or months_found[-1] != month_num):
            months_found.append(month_num)

    years_in_msg = [int(y) for y in re.findall(r"\b(20\d{2})\b", message)]
    detected_year: int | None = years_in_msg[0] if years_in_msg else None
    detected_month: int | None = months_found[0] if months_found else None

    range_start = None
    range_end = None
    if len(months_found) >= 2:
        start_month = months_found[0]
        end_month = months_found[-1]
        if years_in_msg:
            start_year = years_in_msg[0]
            end_year = years_in_msg[-1] if len(years_in_msg) > 1 else start_year
            range_start = (start_year, start_month)
            range_end = (end_year, end_month)

    # ── Detect plaza names (case-insensitive substring match + aliases) ─────
    all_plazas = list(SNAPSHOT["data"].keys())
    alias_map = SNAPSHOT.get("plaza_aliases", {})
    alias_lookup = {a.lower(): c for a, c in alias_map.items()}

    matched_set: set[str] = set()
    for p in all_plazas:
        pl = p.lower()
        if pl in msg_lower or any(word in msg_lower for word in pl.split() if len(word) > 3):
            matched_set.add(p)
    for alias, canon in alias_lookup.items():
        if alias in msg_lower:
            matched_set.add(canon)

    matched_plazas = sorted(matched_set) if matched_set else all_plazas

    lines: list[str] = []

    # ── Aggregate summary (single month or range) ───────────────────────────
    if range_start and range_end:
        start_year, start_month = range_start
        end_year, end_month = range_end
        rec = aggregate_plazas_for_range(
            SNAPSHOT,
            matched_plazas,
            start_year,
            start_month,
            end_year,
            end_month,
        )
        if rec:
            range_label = (
                f"{MONTH_NAMES[start_month]} {start_year} to "
                f"{MONTH_NAMES[end_month]} {end_year}"
            )
            lines.append(
                f"AGGREGATE | {range_label} | Plazas: {len(rec['plazas_included'])} | "
                f"Total txns: {rec['total_count']} | Total revenue: ₹{rec['total_amount']:,.0f}"
            )
    elif detected_year and detected_month:
        rec = aggregate_plazas_for_month(SNAPSHOT, matched_plazas, detected_year, detected_month)
        if rec:
            month_label = f"{MONTH_NAMES[detected_month]} {detected_year}"
            lines.append(
                f"AGGREGATE | {month_label} | Plazas: {len(rec['plazas_included'])} | "
                f"Total txns: {rec['total_count']} | Total revenue: ₹{rec['total_amount']:,.0f}"
            )

    # ── Per-plaza monthly data ─────────────────────────────────────────────
    plaza_sample = matched_plazas
    if detected_year and detected_month:
        key = f"{detected_year}-{detected_month:02d}"
        plaza_sample = sorted(
            matched_plazas,
            key=lambda p: SNAPSHOT["data"].get(p, {}).get(key, {}).get("total_amount", 0),
            reverse=True,
        )

    for plaza in plaza_sample[:8]:
        plaza_data = SNAPSHOT["data"].get(plaza, {})
        for key, rec in plaza_data.items():
            yr, mo = map(int, key.split("-"))
            if detected_year and yr != detected_year:
                continue
            if detected_month and mo != detected_month:
                continue
            cats_summary = ", ".join(
                f"{c['name']}: {c['count']} txns / ₹{c['amount']:,.0f}"
                for c in rec.get("categories", [])[:6]
            )
            lines.append(
                f"Plaza: {plaza} | {MONTH_NAMES.get(mo, mo)} {yr} | "
                f"Total txns: {rec['total_count']} | "
                f"Total revenue: ₹{rec['total_amount']:,.0f} | "
                f"Avg fare: ₹{rec.get('avg_per_txn', 0):,.2f} | "
                f"Categories → {cats_summary}"
            )

    # ── Monthly totals overview (trend) ─────────────────────────────────────
    for plaza in matched_plazas[:4]:
        trend = SNAPSHOT.get("monthly_totals", {}).get(plaza, [])
        if trend:
            trend_str = " | ".join(
                f"{t['label']}: {t['count']} txns ₹{t['amount']:,.0f}"
                for t in trend[-6:]   # last 6 months
            )
            lines.append(f"Trend for {plaza}: {trend_str}")

    # ── Portfolio summary ────────────────────────────────────────────────────
    total_plazas = len(SNAPSHOT["plazas"])
    available_months = SNAPSHOT.get("available_months", [])
    lines.insert(0, (
        f"Portfolio: {total_plazas} NHIT toll plazas · "
        f"{len(available_months)} months of data · "
        f"Plazas: {', '.join(all_plazas[:20])}"
    ))

    return "\n".join(lines) if lines else "No matching data found in snapshot."


@app.route("/api/chat", methods=["POST"])
def chat():
    """Proxy chat to OpenRouter. Expects JSON body with `message` and
    optionally `history` (list of {role, content} dicts). The client
    may pass its OpenRouter API key in the X-OR-Key header; if absent
    the server falls back to the OPENROUTER_API_KEY env variable."""
    api_key = request.headers.get("X-OR-Key", "").strip() or _SERVER_OR_KEY
    if not api_key:
        return jsonify({"error": "Missing OpenRouter API key — set OPENROUTER_API_KEY in .env or pass X-OR-Key header."}), 401

    body = request.get_json(force=True, silent=True) or {}
    user_message = (body.get("message") or "").strip()
    if not user_message:
        return jsonify({"error": "Empty message."}), 400

    history = body.get("history", [])  # [{role, content}, ...]

    # Build data context
    data_ctx = _build_data_context(user_message)

    system_prompt = (
        "You are NHIT Analytics Assistant — a professional, data-driven assistant "
        "for the National Highways Infra Trust ETC Analytics Dashboard.\n"
        "Rules:\n"
        "- Provide comprehensive and insightful answers based on the data.\n"
        "- Use bullet points for readability when comparing multiple plazas or metrics.\n"
        "- Always cite actual numbers (transactions, revenue in ₹) from the data.\n"
        "- Format currency as ₹X.XX Cr / ₹X.XX L / ₹X,XX,XXX as appropriate.\n"
        "- If data for the exact query is missing, say so clearly and suggest "
        "  what data IS available.\n"
        "- Do NOT make up numbers. Only use the provided data.\n\n"
        "DATA CONTEXT (current snapshot):\n"
        + data_ctx
    )

    messages = [{"role": "system", "content": system_prompt}]
    for h in history[-10:]:   # last 10 turns for context
        if h.get("role") in ("user", "assistant") and h.get("content"):
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": user_message})

    fallback_models = [
        _OR_MODEL,
        "google/gemma-3-4b-it:free",
        "liquid/lfm-2.5-1.2b-instruct:free",
        "nvidia/nemotron-nano-9b-v2:free",
        "qwen/qwen3-coder:free"
    ]
    
    # De-duplicate models but preserve order
    models_to_try = []
    for m in fallback_models:
        if m not in models_to_try:
            models_to_try.append(m)

    last_error_body = ""
    last_error_code = 502

    for model_id in models_to_try:
        payload = json.dumps({
            "model": model_id,
            "messages": messages,
            "max_tokens": _OR_MAX_TOKENS,
            "temperature": _OR_TEMPERATURE,
        }).encode()

        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://nhit-dashboard",
                "X-Title": "NHIT ETC Analytics Chatbot",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode())
            content = result.get("choices", [{}])[0].get("message", {}).get("content")
            reply = (content or "").strip()
            if not reply:
                reply = "I'm sorry, I couldn't generate a response (the model returned an empty reply)."
            return jsonify({"reply": reply})
        except urllib.error.HTTPError as e:
            last_error_code = e.code
            last_error_body = e.read().decode(errors="replace")
            # Only retry if it's a 429 rate limit or 502/503 upstream error
            if e.code in (429, 502, 503, 400, 404):
                log.warning("OpenRouter %s failed on model %s, trying next...", e.code, model_id)
                continue
            else:
                log.error("OpenRouter HTTP %d: %s", e.code, last_error_body)
                return jsonify({"error": f"OpenRouter error {e.code}: {last_error_body[:200]}"}), 502
        except Exception as exc:
            log.exception("Chat proxy error: %s", exc)
            return jsonify({"error": str(exc)}), 500

    # If all models failed
    return jsonify({"error": f"All free models are currently overloaded. Last error {last_error_code}: {last_error_body[:200]}"}), 502


# Load snapshot at import time so this works under any WSGI server too.
_ensure_snapshot()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5051, debug=False, threaded=True)
