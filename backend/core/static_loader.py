"""
NHIT | Loader that builds a SNAPSHOT-shaped dict from the static JSON
files in downloads/json/ produced by scripts/build_json_export.py.

The legacy code path (analytics.py + chatbot context builder) expects:
    SNAPSHOT["data"][plaza_name][YYYY-MM] = {
        "categories": [{name, count, amount, share_count, share_amount,
                        avg_fare}],
        "total_count": int,
        "total_amount": float,
        "avg_per_txn": float,
        ...
    }
    SNAPSHOT["plazas"]            -> list[str]   (canonical names)
    SNAPSHOT["years"]             -> list[int]
    SNAPSHOT["months"]            -> list[{year, month, label}]
    SNAPSHOT["available_months"]  -> list[{num, name}]
    SNAPSHOT["monthly_totals"]    -> plaza -> list[{year, month, label, count, amount}]
    SNAPSHOT["taxonomy"]          -> {rows, spvs, rounds, unmatched}
    SNAPSHOT["plaza_aliases"]     -> {} (empty for new pipeline)
    SNAPSHOT["validation_warnings"] -> []
    SNAPSHOT["generated_at"]      -> ISO timestamp

We don't store everything in memory naively — there are 1199 plazas × 15
periods. We build the structure once at startup; total RAM is a few
hundred MB at most.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

# `downloads/json/` lives at the project root; resolve from this module up
# two levels (backend/core/ → backend/ → root).
DEFAULT_JSON_DIR = Path(__file__).resolve().parents[2] / "downloads" / "json"

MONTH_NAMES = {
    1: "January",   2: "February",  3: "March",     4: "April",
    5: "May",       6: "June",      7: "July",      8: "August",
    9: "September", 10: "October",  11: "November", 12: "December",
}


def _enrich_categories(vehicles: dict | None) -> tuple[list[dict], int, float]:
    """Convert the new monthly file's `vehicles` dict into the enriched
    `categories` array (count, amount, share_count, share_amount, avg_fare)
    that legacy callers expect."""
    if not vehicles:
        return [], 0, 0.0
    raw = [
        {"name": name, "count": int(v.get("count") or 0), "amount": float(v.get("amount") or 0.0)}
        for name, v in vehicles.items()
    ]
    raw.sort(key=lambda c: c["amount"], reverse=True)

    total_count = sum(c["count"] for c in raw)
    total_amount = round(sum(c["amount"] for c in raw), 2)
    enriched = []
    for c in raw:
        enriched.append({
            "name":   c["name"],
            "count":  c["count"],
            "amount": round(c["amount"], 2),
            "share_count":  round(c["count"]  / total_count  * 100, 2) if total_count  else 0.0,
            "share_amount": round(c["amount"] / total_amount * 100, 2) if total_amount else 0.0,
            "avg_fare":     round(c["amount"] / c["count"],          2) if c["count"]  else 0.0,
        })
    return enriched, total_count, total_amount


def load_snapshot(json_dir: Path = DEFAULT_JSON_DIR) -> dict:
    """Read downloads/json/* into a single SNAPSHOT-shaped dict."""
    json_dir = Path(json_dir)
    if not json_dir.exists():
        raise FileNotFoundError(
            f"{json_dir} does not exist. Run scripts/build_json_export.py first."
        )

    index_path = json_dir / "_index.json"
    if not index_path.exists():
        raise FileNotFoundError(f"Missing {index_path}")
    with index_path.open(encoding="utf-8") as f:
        index = json.load(f)

    tax_path = json_dir / "_taxonomy.json"
    taxonomy = {"rows": [], "spvs": [], "rounds": [], "unmatched": []}
    if tax_path.exists():
        with tax_path.open(encoding="utf-8") as f:
            taxonomy = json.load(f)

    plaza_names = [p["plaza_name"] for p in index["plazas"]]
    plaza_set = set(plaza_names)
    periods: list[str] = sorted(index.get("periods") or [])

    data: dict[str, dict] = {p: {} for p in plaza_names}
    monthly_totals: dict[str, list[dict]] = {p: [] for p in plaza_names}
    months_meta: list[dict] = []
    years_set: set[int] = set()
    months_seen: dict[int, str] = {}

    for period in periods:
        year, month = period.split("-")
        year_i, month_i = int(year), int(month)
        years_set.add(year_i)
        months_seen.setdefault(month_i, MONTH_NAMES[month_i])
        month_label = f"{MONTH_NAMES[month_i][:3]} {year_i}"

        path = json_dir / "monthly" / f"{period}.json"
        with path.open(encoding="utf-8") as f:
            doc = json.load(f)

        period_has_data = False
        for entry in doc.get("plazas", []):
            name = entry["plaza_name"]
            if name not in plaza_set:
                # Plaza unknown to index; still keep it so chatbot search works.
                plaza_set.add(name)
                plaza_names.append(name)
                data.setdefault(name, {})
                monthly_totals.setdefault(name, [])
            cats, tcount, tamount = _enrich_categories(entry.get("vehicles"))
            if not cats:
                continue
            avg_per_txn = round(tamount / tcount, 2) if tcount else 0.0
            data[name][period] = {
                "plaza":       name,
                "year":        year_i,
                "month":       month_i,
                "month_name":  MONTH_NAMES[month_i],
                "categories":  cats,
                "total_count":  tcount,
                "total_amount": tamount,
                "avg_per_txn":  avg_per_txn,
                "category_count": len(cats),
                "piu":         entry.get("piu") or "",
                "ro":          entry.get("ro") or "",
                "annual_pass": entry.get("annual_pass"),
            }
            monthly_totals[name].append({
                "year":   year_i,
                "month":  month_i,
                "label":  month_label,
                "count":  tcount,
                "amount": tamount,
            })
            period_has_data = True
        if period_has_data:
            months_meta.append({"year": year_i, "month": month_i, "label": month_label})

    # Sort the monthly_totals chronologically per plaza.
    for p in monthly_totals:
        monthly_totals[p].sort(key=lambda x: (x["year"], x["month"]))

    available_months = [
        {"num": n, "name": months_seen[n]}
        for n in sorted(months_seen)
    ]

    snapshot = {
        "generated_at": index.get("generated_at"),
        "plazas":   sorted(plaza_set),
        "years":    sorted(years_set),
        "months":   months_meta,
        "available_months": available_months,
        "data":     data,
        "monthly_totals": monthly_totals,
        "taxonomy": taxonomy,
        "plaza_aliases": {},
        "validation_warnings": [],
    }
    log.info(
        "static_loader: %d plazas, %d periods, taxonomy_rows=%d",
        len(snapshot["plazas"]), len(periods), len(taxonomy.get("rows", [])),
    )
    return snapshot
