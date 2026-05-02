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
from chatbot_engine import answer as engine_answer
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
def _detect_scope(message: str) -> tuple[list[str], str, dict]:
    """Resolve a user message to a concrete plaza set + human-readable scope.

    Detection order (most specific wins):
      1. Single plaza name → that plaza only.
      2. SPV (NSPPL/NEPPL/NWPPL) and/or Round (R1..R5) → canonical_plaza
         set from taxonomy filtered by that SPV/Round.
      3. None of the above → the full network (all plazas).

    Returns (plazas, label, meta) where meta carries spv/round/plaza filters
    used so the chatbot can echo the scope verbatim.
    """
    import re
    msg_lower = message.lower()
    tax_rows = SNAPSHOT.get("taxonomy", {}).get("rows", [])
    spvs = SNAPSHOT.get("taxonomy", {}).get("spvs", [])
    rounds = SNAPSHOT.get("taxonomy", {}).get("rounds", [])
    all_plazas = list(SNAPSHOT["data"].keys())

    # 1. Try to spot any plaza name as a substring (longest first to prefer
    #    "Bhadarabad TOLL PLAZA" over a possible substring of another name).
    matched_plaza = None
    for p in sorted(all_plazas, key=lambda x: -len(x)):
        if p.lower() in msg_lower:
            matched_plaza = p
            break

    # 2. Detect SPV codes (NSPPL etc.) and Round labels (R1, R2 …) via word
    #    boundaries so "NSPPL" doesn't accidentally match inside another word.
    matched_spv = None
    for s in spvs:
        if re.search(rf"\b{re.escape(s)}\b", message, flags=re.IGNORECASE):
            matched_spv = s
            break
    matched_round = None
    for r in rounds:
        if re.search(rf"\b{re.escape(r)}\b", message, flags=re.IGNORECASE):
            matched_round = r
            break

    if matched_plaza:
        return [matched_plaza], matched_plaza, {"plaza": matched_plaza}

    if matched_spv or matched_round:
        rows = [
            r for r in tax_rows
            if r.get("canonical_plaza")
            and (not matched_spv   or r.get("spv")   == matched_spv)
            and (not matched_round or r.get("round") == matched_round)
        ]
        plazas = sorted({r["canonical_plaza"] for r in rows})
        if plazas:
            label_bits = []
            if matched_spv:   label_bits.append(f"SPV {matched_spv}")
            if matched_round: label_bits.append(f"Round {matched_round}")
            return plazas, " · ".join(label_bits), {
                "spv": matched_spv, "round": matched_round, "plaza_count": len(plazas),
            }
        # Fall back to SPV-only when SPV+Round combo is empty but the SPV
        # itself has data (e.g. user asked for "NEPPL R1" but NEPPL only
        # has Round R3 in the portfolio).
        if matched_spv and matched_round:
            spv_rows = [
                r for r in tax_rows
                if r.get("canonical_plaza") and r.get("spv") == matched_spv
            ]
            spv_plazas = sorted({r["canonical_plaza"] for r in spv_rows})
            if spv_plazas:
                actual_rounds = sorted({r["round"] for r in spv_rows})
                label = (
                    f"SPV {matched_spv} (NOTE: {matched_spv} has no Round "
                    f"{matched_round} in this portfolio — only Round(s) "
                    f"{', '.join(actual_rounds)})"
                )
                return spv_plazas, label, {
                    "spv": matched_spv, "round": None,
                    "plaza_count": len(spv_plazas),
                    "note": f"requested Round {matched_round} not present",
                }
        # SPV/Round was specified but matches no plazas at all.
        return [], (
            f"No plazas match the requested filter "
            f"({(matched_spv or '')} {(matched_round or '')}".strip() + ")"
        ), {"spv": matched_spv, "round": matched_round, "plaza_count": 0}

    # 3. Default: whole network.
    return all_plazas, "All NHIT Plazas (whole network)", {"plaza_count": len(all_plazas)}


def _fmt_inr(amount: float) -> str:
    """Indian convention: ₹X.XX Cr / ₹X.XX L / ₹X,XX,XXX."""
    a = float(amount or 0)
    if a >= 1e7:  return f"₹{a / 1e7:.2f} Cr"
    if a >= 1e5:  return f"₹{a / 1e5:.2f} L"
    return "₹{:,.2f}".format(a).replace(",", ",")  # default Indian-ish grouping


def _fmt_int(n: int) -> str:
    n = int(n or 0)
    if n >= 1e7: return f"{n / 1e7:.2f} Cr"
    if n >= 1e5: return f"{n / 1e5:.2f} L"
    return f"{n:,}"


def _build_data_context(message: str) -> str:
    """Build a compact, scope-aware context block for the LLM. Detects SPV/
    Round/Plaza/Month/Year from the message and produces a single
    AUTHORITATIVE_ANSWER sentence the model can echo verbatim. All numbers
    are computed from downloads/json/* via static_loader."""
    import re
    from constants import MONTH_MAP, MONTH_NAMES

    msg_lower = message.lower()

    month_tokens = sorted(MONTH_MAP.keys(), key=len, reverse=True)
    month_pattern = r"\b(" + "|".join(re.escape(t) for t in month_tokens) + r")\b"
    months_found: list[int] = []
    for m in re.finditer(month_pattern, msg_lower, flags=re.IGNORECASE):
        token = m.group(1).lower()
        mn = MONTH_MAP.get(token)
        if mn and (not months_found or months_found[-1] != mn):
            months_found.append(mn)

    years_in_msg = [int(y) for y in re.findall(r"\b(20\d{2})\b", message)]
    detected_year  = years_in_msg[0]  if years_in_msg  else None
    detected_month = months_found[0] if months_found else None
    range_start = range_end = None
    if len(months_found) >= 2 and years_in_msg:
        start_month, end_month = months_found[0], months_found[-1]
        start_year = years_in_msg[0]
        end_year = years_in_msg[-1] if len(years_in_msg) > 1 else start_year
        range_start = (start_year, start_month)
        range_end   = (end_year,   end_month)

    scope_plazas, scope_label, scope_meta = _detect_scope(message)
    is_single_plaza = "plaza" in scope_meta

    # Compute the authoritative numbers up front. We will paste them at the
    # very top of the context as a complete sentence the LLM should quote.
    period_phrase = ""
    rec = None
    if range_start and range_end:
        sy, sm = range_start; ey, em = range_end
        rec = aggregate_plazas_for_range(SNAPSHOT, scope_plazas, sy, sm, ey, em)
        period_phrase = f"the period {MONTH_NAMES[sm]} {sy} to {MONTH_NAMES[em]} {ey}"
    elif detected_year and detected_month:
        rec = aggregate_plazas_for_month(SNAPSHOT, scope_plazas, detected_year, detected_month)
        period_phrase = f"{MONTH_NAMES[detected_month]} {detected_year}"

    auth_sentence = ""
    no_data_sentence = ""
    period_requested = bool(period_phrase)
    if rec:
        auth_sentence = (
            f"For {scope_label} ({len(rec['plazas_included'])} plazas with data) "
            f"in {period_phrase}: total revenue = {_fmt_inr(rec['total_amount'])} "
            f"(₹{rec['total_amount']:,.0f}); total transactions = "
            f"{_fmt_int(rec['total_count'])} ({rec['total_count']:,}); "
            f"average fare per transaction = ₹{rec['avg_per_txn']:,.2f}; "
            f"top category by revenue = {rec['top_by_amount']['name']} "
            f"({_fmt_inr(rec['top_by_amount']['amount'])}); top category by volume = "
            f"{rec['top_by_count']['name']} ({_fmt_int(rec['top_by_count']['count'])} txns)."
        )
    elif period_requested:
        # User asked for a specific period that has no data. Build a
        # complete, deterministic "no data" sentence so the LLM cannot
        # silently lift a number from the trend block.
        avail_months = ", ".join(
            f"{MONTH_NAMES[m['month']][:3]} {m['year']}"
            for m in SNAPSHOT.get("months", [])
        )
        no_data_sentence = (
            f"For {scope_label} in {period_phrase}, NO DATA is available in "
            f"the dataset. Do NOT estimate, extrapolate, or substitute another "
            f"period. Available months on record: {avail_months}."
        )

    lines: list[str] = []
    if auth_sentence:
        # Repeat for emphasis; small models tend to anchor on what's at the top.
        lines.append("=" * 8 + " AUTHORITATIVE_ANSWER (use this sentence verbatim, do NOT recompute) " + "=" * 8)
        lines.append(auth_sentence)
        lines.append("=" * 8 + " END AUTHORITATIVE_ANSWER " + "=" * 8)
        lines.append("")
    elif no_data_sentence:
        lines.append("=" * 8 + " AUTHORITATIVE_ANSWER (use this sentence verbatim, do NOT recompute) " + "=" * 8)
        lines.append(no_data_sentence)
        lines.append("=" * 8 + " END AUTHORITATIVE_ANSWER " + "=" * 8)
        lines.append("")

    available_months = SNAPSHOT.get("available_months", [])
    spvs = SNAPSHOT.get("taxonomy", {}).get("spvs", [])
    rounds = SNAPSHOT.get("taxonomy", {}).get("rounds", [])
    lines.append(
        f"PORTFOLIO: {len(SNAPSHOT['plazas'])} plazas · "
        f"{len(available_months)} months of data · "
        f"SPVs={','.join(spvs)} · Rounds={','.join(rounds)}"
    )
    lines.append(f"SCOPE: {scope_label} (covers {len(scope_plazas)} plaza(s))")

    if rec:
        cats_str = "; ".join(
            f"{c['name']}: {_fmt_int(c['count'])} txns / {_fmt_inr(c['amount'])}"
            for c in rec["categories"]
        )
        lines.append(f"CATEGORY_BREAKDOWN: {cats_str}")
        if not range_start:
            lines.append(f"AVG_REVENUE_PER_DAY: {_fmt_inr(rec.get('avg_revenue_per_day', 0))}")

    if is_single_plaza:
        plaza = scope_plazas[0]
        # Suppress the trend block when the user asked about a specific period
        # that has NO data — otherwise the LLM may pluck a number from a
        # different month and label it with the requested period.
        suppress_trend = period_requested and not rec
        if not suppress_trend:
            trend = SNAPSHOT.get("monthly_totals", {}).get(plaza, [])
            if trend:
                trend_str = " | ".join(
                    f"{t['label']}: {_fmt_int(t['count'])} txns / {_fmt_inr(t['amount'])}"
                    for t in trend
                )
                lines.append(f"TREND_FOR_{plaza}: {trend_str}")
    elif rec and detected_year and detected_month:
        # Rank top plazas in the chosen scope so the model can answer
        # follow-up questions like "which plaza is highest in NSPPL".
        key = f"{detected_year}-{detected_month:02d}"
        ranked = sorted(
            scope_plazas,
            key=lambda p: SNAPSHOT["data"].get(p, {}).get(key, {}).get("total_amount", 0),
            reverse=True,
        )
        top_lines = []
        for plaza in ranked[:10]:
            r = SNAPSHOT["data"].get(plaza, {}).get(key)
            if not r:
                continue
            top_lines.append(
                f"  {plaza}: txns={_fmt_int(r['total_count'])} revenue={_fmt_inr(r['total_amount'])}"
            )
        if top_lines:
            lines.append(f"TOP_PLAZAS_IN_SCOPE ({MONTH_NAMES[detected_month]} {detected_year}):")
            lines.extend(top_lines)

    if not rec and (detected_year or detected_month):
        # Period mentioned but no data — surface what IS available.
        avail = ", ".join(
            f"{MONTH_NAMES[m['month']][:3]} {m['year']}" for m in SNAPSHOT.get("months", [])
        )
        lines.append(f"NO_DATA_FOR_REQUESTED_PERIOD. Available months: {avail}")

    return "\n".join(lines) if lines else "No matching data found in JSON files."


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

    # ── Deterministic engine first ──────────────────────────────────────────
    # The engine reads numbers directly from the loaded JSON snapshot, so its
    # output never hallucinates. We only fall through to the LLM if the engine
    # crashes or explicitly chooses not to handle the question.
    try:
        engine_result = engine_answer(SNAPSHOT, user_message)
        if engine_result and engine_result.get("matched") and engine_result.get("reply"):
            log.info("[%s] engine answered intent=%s",
                     getattr(g, "req_id", "?"), engine_result.get("intent"))
            return jsonify({
                "reply": engine_result["reply"],
                "source": "engine",
                "intent": engine_result.get("intent"),
            })
    except Exception as exc:
        log.exception("Engine failed, falling back to LLM: %s", exc)

    # ── LLM fallback (only when engine cannot answer) ───────────────────────
    data_ctx = _build_data_context(user_message)

    system_prompt = (
        "You are NHIT Analytics Assistant for the National Highways Infra Trust "
        "ETC Analytics Dashboard.\n\n"
        "STRICT RULES:\n"
        "1. The DATA CONTEXT below contains a SCOPE line and an ANSWER line that "
        "have already been computed from the source-of-truth JSON files. You MUST "
        "cite numbers from the ANSWER line VERBATIM — do not recompute, sum, "
        "estimate, scale, or 'extrapolate' anything yourself.\n"
        "2. Format revenue using Indian conventions: ₹X.XX Cr (≥1,00,00,000), "
        "₹X.XX L (≥1,00,000), or ₹X,XX,XXX otherwise. Do not invent figures.\n"
        "3. The SCOPE line tells you which plazas the ANSWER covers. If the user "
        "asked about a different scope than what SCOPE shows, say so explicitly "
        "and answer for the scope shown.\n"
        "4. If no ANSWER line is present (data missing for the requested period), "
        "say so plainly and list which months ARE available.\n"
        "5. NEVER fabricate plaza names, revenue figures, or transaction counts.\n\n"
        "DATA CONTEXT:\n"
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
