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
import os
import sqlite3
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


@app.route("/api/taxonomy")
def taxonomy():
    """Hierarchy used to drive the cascading SPV → Round → Project → Plaza
    dropdowns in the UI. Each row carries its canonical plaza name (or null
    if no PDF data is available for it)."""
    tx = SNAPSHOT.get("taxonomy", {})
    return jsonify({
        "rows":      tx.get("rows", []),
        "spvs":      tx.get("spvs", []),
        "rounds":    tx.get("rounds", []),
        "unmatched": tx.get("unmatched", []),
    })


# ── Aggregation helpers ─────────────────────────────────────────────────────
def _filter_taxonomy_rows(spv: str, round_: str, project: str, plaza: str):
    """Return all taxonomy rows whose canonical plaza has parsed data, after
    applying any non-empty filters. Filter values are case-insensitive
    exact matches on the corresponding column."""
    rows = SNAPSHOT.get("taxonomy", {}).get("rows", [])
    out = []
    for r in rows:
        if not r.get("canonical_plaza"):
            continue
        if spv     and r["spv"].lower()           != spv.lower():     continue
        if round_  and r["round"].lower()         != round_.lower():  continue
        if project and r["project"].lower()       != project.lower(): continue
        if plaza   and r["excel_plaza"].lower()   != plaza.lower():   continue
        out.append(r)
    return out


def _aggregate_plazas_for_month(plazas: list[str], year: int, month: int) -> dict | None:
    """Wrapper so the server always uses the shared analytics helpers."""
    return aggregate_plazas_for_month(SNAPSHOT, plazas, year, month)


def _aggregate_plazas_for_range(
    plazas: list[str],
    start_year: int,
    start_month: int,
    end_year: int,
    end_month: int,
) -> dict | None:
    return aggregate_plazas_for_range(
        SNAPSHOT,
        plazas,
        start_year,
        start_month,
        end_year,
        end_month,
    )


def _scope_label(spv, round_, project, plaza):
    """Human-readable description of which scope is being aggregated."""
    if plaza:   return plaza
    if project: return f"{project} ({round_ or 'All Rounds'}, {spv or 'All SPVs'})"
    if round_:  return f"{spv or 'All SPVs'} · {round_}"
    if spv:     return f"{spv} · All Rounds"
    return "All NHIT Plazas"


@app.route("/api/aggregate")
def aggregate():
    """Hierarchical aggregation. Any of `spv`, `round`, `project`, `plaza`
    may be empty — empty means "include everything below this level"."""
    spv     = request.args.get("spv",     "").strip()
    round_  = request.args.get("round",   "").strip()
    project = request.args.get("project", "").strip()
    plaza   = request.args.get("plaza",   "").strip()
    try:
        year  = int(request.args.get("year",  "") or 0)
        month = int(request.args.get("month", "") or 0)
    except ValueError:
        return jsonify({"error": "year and month must be integers"}), 400
    if not year or not month:
        return jsonify({"error": "year and month are required"}), 400
    if not (1 <= month <= 12):
        return jsonify({"error": "month must be between 1 and 12"}), 400

    rows = _filter_taxonomy_rows(spv, round_, project, plaza)
    if not rows:
        return jsonify({
            "error": "No plazas match the selected filters.",
            "filters": {"spv": spv, "round": round_, "project": project, "plaza": plaza},
        }), 404

    canon_plazas = sorted({r["canonical_plaza"] for r in rows})
    if len(canon_plazas) == 0 or (not spv and not round_ and not project and not plaza):
        canon_plazas = SNAPSHOT["plazas"]
    
    rec = _aggregate_plazas_for_month(canon_plazas, year, month)
    if not rec:
        return jsonify({
            "error": f"No data found for the selected filters in {year}-{month:02d}.",
            "filters": {"spv": spv, "round": round_, "project": project, "plaza": plaza},
            "candidate_plazas": canon_plazas,
        }), 404

    rec["scope"] = {
        "label":   _scope_label(spv, round_, project, plaza),
        "spv":     spv     or None,
        "round":   round_  or None,
        "project": project or None,
        "plaza":   plaza   or None,
        "plaza_count": len(canon_plazas),
    }
    return jsonify({"record": rec})


@app.route("/api/aggregate-trend")
def aggregate_trend():
    """Monthly count + revenue series across every available report, summed
    across all plazas matching the SPV/Round/Project/Plaza filters."""
    spv     = request.args.get("spv",     "").strip()
    round_  = request.args.get("round",   "").strip()
    project = request.args.get("project", "").strip()
    plaza   = request.args.get("plaza",   "").strip()

    rows = _filter_taxonomy_rows(spv, round_, project, plaza)
    if not rows:
        return jsonify({"trend": [], "scope": _scope_label(spv, round_, project, plaza)})

    canon_plazas = sorted({r["canonical_plaza"] for r in rows})
    if len(canon_plazas) == 0 or (not spv and not round_ and not project and not plaza):
        canon_plazas = SNAPSHOT["plazas"]
        
    trend = []
    for m in SNAPSHOT["months"]:
        rec = _aggregate_plazas_for_month(canon_plazas, m["year"], m["month"])
        if rec:
            trend.append({
                "year":       m["year"],
                "month":      m["month"],
                "month_name": rec["month_name"],
                "label":      m["label"],
                "count":      rec["total_count"],
                "amount":     rec["total_amount"],
            })
    trend.sort(key=lambda x: (x["year"], x["month"]))
    return jsonify({
        "scope": _scope_label(spv, round_, project, plaza),
        "trend": trend,
    })


@app.route("/api/aggregate-range")
def aggregate_range():
    """Sum totals across a month range for the given filters."""
    spv     = request.args.get("spv",     "").strip()
    round_  = request.args.get("round",   "").strip()
    project = request.args.get("project", "").strip()
    plaza   = request.args.get("plaza",   "").strip()
    try:
        start_year  = int(request.args.get("start_year",  "") or 0)
        start_month = int(request.args.get("start_month", "") or 0)
        end_year    = int(request.args.get("end_year",    "") or 0)
        end_month   = int(request.args.get("end_month",   "") or 0)
    except ValueError:
        return jsonify({"error": "year and month must be integers"}), 400
    if not (start_year and start_month and end_year and end_month):
        return jsonify({"error": "start/end year and month are required"}), 400
    if not (1 <= start_month <= 12 and 1 <= end_month <= 12):
        return jsonify({"error": "month must be between 1 and 12"}), 400
    if (start_year, start_month) > (end_year, end_month):
        return jsonify({"error": "start date must be before end date"}), 400

    rows = _filter_taxonomy_rows(spv, round_, project, plaza)
    if not rows:
        return jsonify({
            "error": "No plazas match the selected filters.",
            "filters": {"spv": spv, "round": round_, "project": project, "plaza": plaza},
        }), 404

    canon_plazas = sorted({r["canonical_plaza"] for r in rows})
    if len(canon_plazas) == 0 or (not spv and not round_ and not project and not plaza):
        canon_plazas = SNAPSHOT["plazas"]

    rec = _aggregate_plazas_for_range(
        canon_plazas,
        start_year,
        start_month,
        end_year,
        end_month,
    )
    if not rec:
        return jsonify({
            "error": "No data found for the selected filters in the range.",
            "filters": {"spv": spv, "round": round_, "project": project, "plaza": plaza},
            "candidate_plazas": canon_plazas,
        }), 404

    rec["scope"] = {
        "label":   _scope_label(spv, round_, project, plaza),
        "spv":     spv     or None,
        "round":   round_  or None,
        "project": project or None,
        "plaza":   plaza   or None,
        "plaza_count": len(canon_plazas),
    }
    return jsonify({"record": rec})


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
