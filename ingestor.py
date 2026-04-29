"""
NHIT | Ingestor helpers.

Normalizes plaza names and builds stable aliases so slight variations
across PDFs resolve to a single canonical plaza name.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import re
from typing import Iterable

_PLAZA_STOPWORDS = {"toll", "plaza", "fee", "ro"}


def normalize_plaza_name(name: str) -> str:
    """Normalize plaza text into a stable key for matching."""
    if not name:
        return ""
    tokens = [
        t for t in re.findall(r"[a-z0-9]+", str(name).lower())
        if t not in _PLAZA_STOPWORDS
    ]
    return " ".join(tokens)


def build_plaza_alias_map(
    rows: Iterable[dict],
    preferred_names: Iterable[str] | None = None,
) -> dict[str, str]:
    """Build raw plaza name -> canonical plaza name mapping.

    If a normalized key matches a preferred name, that preferred name is
    used as the canonical display name. Otherwise the most frequent raw
    name for that key is used.
    """
    key_counts: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        raw = str(r.get("plaza_name") or "").strip()
        if not raw:
            continue
        key = normalize_plaza_name(raw)
        if not key:
            continue
        key_counts[key][raw] += 1

    preferred_names = list(preferred_names or [])
    preferred_map = {
        normalize_plaza_name(p): p
        for p in preferred_names
        if normalize_plaza_name(p)
    }

    alias_map: dict[str, str] = {}
    for key, counter in key_counts.items():
        if key in preferred_map:
            canonical = preferred_map[key]
        else:
            canonical = sorted(
                counter.items(),
                key=lambda x: (-x[1], -len(x[0]), x[0].lower()),
            )[0][0]
        for raw in counter.keys():
            alias_map[raw] = canonical
    return alias_map


def bucket_rows_by_canonical_plaza(
    rows: Iterable[dict],
    alias_map: dict[str, str],
) -> dict[str, dict[str, dict]]:
    """Aggregate parsed rows into canonical plaza buckets."""
    out: dict[str, dict[str, dict]] = {}
    for r in rows:
        raw = r.get("plaza_name")
        if not raw:
            continue
        canon = alias_map.get(raw)
        if not canon:
            canon = str(raw).strip()
            if not canon:
                continue
        cat = r.get("vehicle_category") or ""
        if not cat:
            continue
        plaza_bucket = out.setdefault(canon, {})
        d = plaza_bucket.setdefault(cat, {"count": 0, "amount": 0.0})
        d["count"] += int(r.get("count") or 0)
        d["amount"] += float(r.get("amount") or 0.0)
    return out
