"""
HTTP routes: static index, /api/health, /api/chat (OpenRouter proxy).

`register_routes(app, snapshot_provider)` wires everything onto a Flask
app. `snapshot_provider` is a zero-arg callable that returns the live
SNAPSHOT dict — passing it as a callable instead of the dict itself
keeps server.py and routes.py from racing on import order.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Callable

from flask import Flask, g, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from ..chatbot.context import build_data_context
from ..chatbot.engine import answer as engine_answer
from .security import apply_security_headers

log = logging.getLogger("nhit.api")

# OpenRouter configuration sourced from env (or .env via dotenv loader).
_SERVER_OR_KEY: str = os.getenv(
    "OPENROUTER_API_KEY",
    "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",
)
_OR_MODEL: str = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.2-3b-instruct:free")
_OR_MAX_TOKENS: int = int(os.getenv("OPENROUTER_MAX_TOKENS", "800"))
_OR_TEMPERATURE: float = float(os.getenv("OPENROUTER_TEMPERATURE", "0.2"))

_FALLBACK_MODELS = [
    _OR_MODEL,
    "google/gemma-3-4b-it:free",
    "liquid/lfm-2.5-1.2b-instruct:free",
    "nvidia/nemotron-nano-9b-v2:free",
    "qwen/qwen3-coder:free",
]

_SYSTEM_PROMPT_HEADER = (
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
)


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

    # ── Chat ──────────────────────────────────────────────────────────────
    @app.route("/api/chat", methods=["POST"])
    def chat():
        api_key = request.headers.get("X-OR-Key", "").strip() or _SERVER_OR_KEY
        if not api_key:
            return jsonify({
                "error": "Missing OpenRouter API key — set OPENROUTER_API_KEY in .env or pass X-OR-Key header.",
            }), 401

        body = request.get_json(force=True, silent=True) or {}
        user_message = (body.get("message") or "").strip()
        if not user_message:
            return jsonify({"error": "Empty message."}), 400

        history = body.get("history", [])
        snap = snapshot_provider()

        # Deterministic engine first; falls through to LLM only if the
        # engine cannot answer.
        try:
            engine_result = engine_answer(snap, user_message)
            if engine_result and engine_result.get("matched") and engine_result.get("reply"):
                log.info(
                    "[%s] engine answered intent=%s",
                    getattr(g, "req_id", "?"), engine_result.get("intent"),
                )
                return jsonify({
                    "reply": engine_result["reply"],
                    "source": "engine",
                    "intent": engine_result.get("intent"),
                })
        except Exception as exc:
            log.exception("Engine failed, falling back to LLM: %s", exc)

        data_ctx = build_data_context(snap, user_message)
        system_prompt = _SYSTEM_PROMPT_HEADER + data_ctx

        messages = [{"role": "system", "content": system_prompt}]
        for h in history[-10:]:
            if h.get("role") in ("user", "assistant") and h.get("content"):
                messages.append({"role": h["role"], "content": h["content"]})
        messages.append({"role": "user", "content": user_message})

        # De-duplicate model list while preserving order.
        models_to_try: list[str] = []
        for m in _FALLBACK_MODELS:
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
                reply = (content or "").strip() or (
                    "I'm sorry, I couldn't generate a response (the model returned an empty reply)."
                )
                return jsonify({"reply": reply})
            except urllib.error.HTTPError as e:
                last_error_code = e.code
                last_error_body = e.read().decode(errors="replace")
                if e.code in (429, 502, 503, 400, 404):
                    log.warning(
                        "OpenRouter %s failed on model %s, trying next…",
                        e.code, model_id,
                    )
                    continue
                log.error("OpenRouter HTTP %d: %s", e.code, last_error_body)
                return jsonify({
                    "error": f"OpenRouter error {e.code}: {last_error_body[:200]}",
                }), 502
            except Exception as exc:
                log.exception("Chat proxy error: %s", exc)
                return jsonify({"error": str(exc)}), 500

        return jsonify({
            "error": f"All free models are currently overloaded. Last error {last_error_code}: {last_error_body[:200]}",
        }), 502
