"""
Security middleware: CORS allowlist + response security headers.

Both Vercel deployments and self-hosted instances are first-class — the
allowlist contains the production Vercel URL plus any localhost ports,
and additional origins can be added via the NHIT_CORS_ORIGINS env var
(comma-separated). Set NHIT_CORS_ALLOW_ALL=1 to allow any origin (only
useful for local dev or trusted intranet hosting).
"""
from __future__ import annotations

import os

from flask import Flask
from flask_cors import CORS


# Origins that are always allowed. The Vercel preview URL plus the
# usual local-dev ports.
DEFAULT_ALLOWED_ORIGINS: list[str] = [
    "https://nhit-etc-analytics-dashboard.vercel.app",
    "http://localhost:5050",
    "http://localhost:5051",
    "http://127.0.0.1:5050",
    "http://127.0.0.1:5051",
]


def _resolve_cors_origins() -> list[str] | str:
    """Build the effective CORS origin list.

    Reads:
      NHIT_CORS_ALLOW_ALL  → if "1"/"true", returns "*" (open CORS).
      NHIT_CORS_ORIGINS    → comma-separated list of extra origins.
    """
    if os.getenv("NHIT_CORS_ALLOW_ALL", "").lower() in ("1", "true", "yes"):
        return "*"
    extra = os.getenv("NHIT_CORS_ORIGINS", "")
    extras = [o.strip() for o in extra.split(",") if o.strip()]
    return DEFAULT_ALLOWED_ORIGINS + extras


def configure_cors(app: Flask) -> None:
    """Enable CORS on /api/* using the resolved origin allowlist."""
    origins = _resolve_cors_origins()
    CORS(app, resources={r"/api/*": {"origins": origins}})


def _build_csp() -> str:
    """Build the Content-Security-Policy.

    `connect-src 'self'` is sufficient because all fetches are
    same-origin. External CDNs are only needed for the
    chart library and Google Fonts.
    """
    parts = [
        "default-src 'self'",
        "script-src 'self' https://cdn.jsdelivr.net",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com",
        "img-src 'self' data:",
        "connect-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "object-src 'none'",
    ]
    return "; ".join(parts)


_CSP = _build_csp()


def apply_security_headers(resp):
    """Inject the standard set of hardening headers on every response."""
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    resp.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    resp.headers["Content-Security-Policy"] = _CSP
    return resp
