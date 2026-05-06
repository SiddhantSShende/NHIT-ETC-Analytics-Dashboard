"""
NHIT | Analytics helpers for aggregation.
"""
from __future__ import annotations

from typing import Iterable, List, Tuple

from .constants import MONTH_NAMES


def _iter_months(start_year: int, start_month: int, end_year: int, end_month: int):
    y, m = start_year, start_month
    while (y, m) <= (end_year, end_month):
        yield y, m
        m += 1
        if m > 12:
            y += 1
            m = 1


def aggregate_plazas_for_month(
    snapshot: dict,
    plazas: Iterable[str],
    year: int,
    month: int,
) -> dict | None:
    """Sum per-category totals across plazas for a single month."""
    key = f"{year}-{month:02d}"
    cat_map: dict[str, dict] = {}
    plazas_included: List[str] = []

    for plaza in plazas:
        rec = snapshot["data"].get(plaza, {}).get(key)
        if not rec:
            continue
        plazas_included.append(plaza)
        for c in rec.get("categories", []):
            d = cat_map.setdefault(c["name"], {"count": 0, "amount": 0.0})
            d["count"] += c["count"]
            d["amount"] += c["amount"]

    if not cat_map:
        return None

    cats = [
        {"name": k, "count": v["count"], "amount": round(v["amount"], 2)}
        for k, v in cat_map.items()
    ]
    cats.sort(key=lambda c: c["amount"], reverse=True)

    total_count = sum(c["count"] for c in cats)
    total_amount = round(sum(c["amount"] for c in cats), 2)

    enriched = []
    for c in cats:
        enriched.append({
            "name": c["name"],
            "count": c["count"],
            "amount": c["amount"],
            "share_count": round(c["count"] / total_count * 100, 2) if total_count else 0.0,
            "share_amount": round(c["amount"] / total_amount * 100, 2) if total_amount else 0.0,
            "avg_fare": round(c["amount"] / c["count"], 2) if c["count"] else 0.0,
        })

    top_amt = max(enriched, key=lambda c: c["amount"])
    top_cnt = max(enriched, key=lambda c: c["count"])

    return {
        "year": year,
        "month": month,
        "month_name": MONTH_NAMES[month],
        "categories": enriched,
        "total_count": total_count,
        "total_amount": total_amount,
        "avg_per_txn": round(total_amount / total_count, 2) if total_count else 0.0,
        "avg_count_per_day": int(round(total_count / 30.0)) if total_count else 0,
        "avg_revenue_per_day": round(total_amount / 30.0, 2) if total_amount else 0.0,
        "category_count": len(enriched),
        "top_by_amount": {"name": top_amt["name"], "amount": top_amt["amount"]},
        "top_by_count": {"name": top_cnt["name"], "count": top_cnt["count"]},
        "plazas_included": plazas_included,
    }


def aggregate_plazas_for_range(
    snapshot: dict,
    plazas: Iterable[str],
    start_year: int,
    start_month: int,
    end_year: int,
    end_month: int,
) -> dict | None:
    """Sum totals across plazas for a month range (inclusive)."""
    if (start_year, start_month) > (end_year, end_month):
        return None

    cat_map: dict[str, dict] = {}
    plazas_set = set()
    months_included: List[dict] = []

    for y, m in _iter_months(start_year, start_month, end_year, end_month):
        key = f"{y}-{m:02d}"
        month_has_data = False
        for plaza in plazas:
            rec = snapshot["data"].get(plaza, {}).get(key)
            if not rec:
                continue
            plazas_set.add(plaza)
            month_has_data = True
            for c in rec.get("categories", []):
                d = cat_map.setdefault(c["name"], {"count": 0, "amount": 0.0})
                d["count"] += c["count"]
                d["amount"] += c["amount"]
        if month_has_data:
            months_included.append({
                "year": y,
                "month": m,
                "label": f"{MONTH_NAMES[m][:3]} {y}",
            })

    if not cat_map:
        return None

    cats = [
        {"name": k, "count": v["count"], "amount": round(v["amount"], 2)}
        for k, v in cat_map.items()
    ]
    cats.sort(key=lambda c: c["amount"], reverse=True)

    total_count = sum(c["count"] for c in cats)
    total_amount = round(sum(c["amount"] for c in cats), 2)

    enriched = []
    for c in cats:
        enriched.append({
            "name": c["name"],
            "count": c["count"],
            "amount": c["amount"],
            "share_count": round(c["count"] / total_count * 100, 2) if total_count else 0.0,
            "share_amount": round(c["amount"] / total_amount * 100, 2) if total_amount else 0.0,
            "avg_fare": round(c["amount"] / c["count"], 2) if c["count"] else 0.0,
        })

    top_amt = max(enriched, key=lambda c: c["amount"])
    top_cnt = max(enriched, key=lambda c: c["count"])

    months_with_data = max(len(months_included), 1)
    days = months_with_data * 30.0

    plazas_included = [p for p in plazas if p in plazas_set]

    return {
        "period": {
            "start": {"year": start_year, "month": start_month},
            "end": {"year": end_year, "month": end_month},
        },
        "months_included": months_included,
        "categories": enriched,
        "total_count": total_count,
        "total_amount": total_amount,
        "avg_per_txn": round(total_amount / total_count, 2) if total_count else 0.0,
        "avg_count_per_day": int(round(total_count / days)) if total_count else 0,
        "avg_revenue_per_day": round(total_amount / days, 2) if total_amount else 0.0,
        "category_count": len(enriched),
        "top_by_amount": {"name": top_amt["name"], "amount": top_amt["amount"]},
        "top_by_count": {"name": top_cnt["name"], "count": top_cnt["count"]},
        "plazas_included": plazas_included,
    }
