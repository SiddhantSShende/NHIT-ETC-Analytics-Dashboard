"""
NHIT | Analytics helpers for aggregation.
"""
from __future__ import annotations

from typing import Iterable, List, Tuple

from .constants import MONTH_NAMES, CATEGORY_ORDER, days_in_month


def _iter_months(start_year: int, start_month: int, end_year: int, end_month: int):
    y, m = start_year, start_month
    while (y, m) <= (end_year, end_month):
        yield y, m
        m += 1
        if m > 12:
            y += 1
            m = 1


def _category_sort_key(name: str) -> int:
    """Canonical display order index; unknown labels go last."""
    try:
        return CATEGORY_ORDER.index(name)
    except ValueError:
        return 999


def aggregate_plazas_for_month(
    snapshot: dict,
    plazas: Iterable[str],
    year: int,
    month: int,
) -> dict | None:
    """Sum per-category totals across plazas for a single month.

    For FY-summary periods (2023-04..2024-03) plazas carry only totals,
    no per-class breakdown; we still include them in total_count /
    total_amount via the extra_* accumulators so KPI answers
    return real figures for those months.
    """
    key = f"{year}-{month:02d}"
    cat_map: dict[str, dict] = {}
    plazas_included: List[str] = []
    extra_count = 0
    extra_amount = 0.0

    for plaza in plazas:
        rec = snapshot["data"].get(plaza, {}).get(key)
        if not rec:
            continue
        plazas_included.append(plaza)
        cats_for_plaza = rec.get("categories", [])
        if cats_for_plaza:
            for c in cats_for_plaza:
                d = cat_map.setdefault(c["name"], {"count": 0, "amount": 0.0})
                d["count"] += c["count"]
                d["amount"] += c["amount"]
        else:
            extra_count += int(rec.get("total_count") or 0)
            extra_amount += float(rec.get("total_amount") or 0.0)

    if not cat_map and extra_count == 0 and extra_amount == 0.0:
        return None

    cats = [
        {"name": k, "count": v["count"], "amount": round(v["amount"], 2)}
        for k, v in cat_map.items()
    ]
    cats.sort(key=lambda c: _category_sort_key(c["name"]))

    total_count = sum(c["count"] for c in cats) + extra_count
    total_amount = round(sum(c["amount"] for c in cats) + extra_amount, 2)

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

    top_amt = max(enriched, key=lambda c: c["amount"]) if enriched else {"name": "", "amount": 0}
    top_cnt = max(enriched, key=lambda c: c["count"])  if enriched else {"name": "", "count":  0}

    days = days_in_month(year, month)

    return {
        "year": year,
        "month": month,
        "month_name": MONTH_NAMES[month],
        "categories": enriched,
        "total_count": total_count,
        "total_amount": total_amount,
        "avg_per_txn": round(total_amount / total_count, 2) if total_count else 0.0,
        "avg_count_per_day": int(round(total_count / days)) if total_count else 0,
        "avg_revenue_per_day": round(total_amount / days, 2) if total_amount else 0.0,
        "days_in_period": days,
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
    extra_count = 0
    extra_amount = 0.0

    for y, m in _iter_months(start_year, start_month, end_year, end_month):
        key = f"{y}-{m:02d}"
        month_has_data = False
        for plaza in plazas:
            rec = snapshot["data"].get(plaza, {}).get(key)
            if not rec:
                continue
            plazas_set.add(plaza)
            month_has_data = True
            cats_for_plaza = rec.get("categories", [])
            if cats_for_plaza:
                for c in cats_for_plaza:
                    d = cat_map.setdefault(c["name"], {"count": 0, "amount": 0.0})
                    d["count"] += c["count"]
                    d["amount"] += c["amount"]
            else:
                extra_count += int(rec.get("total_count") or 0)
                extra_amount += float(rec.get("total_amount") or 0.0)
        if month_has_data:
            months_included.append({
                "year": y,
                "month": m,
                "label": f"{MONTH_NAMES[m][:3]} {y}",
            })

    if not cat_map and extra_count == 0 and extra_amount == 0.0:
        return None

    cats = [
        {"name": k, "count": v["count"], "amount": round(v["amount"], 2)}
        for k, v in cat_map.items()
    ]
    cats.sort(key=lambda c: _category_sort_key(c["name"]))

    total_count = sum(c["count"] for c in cats) + extra_count
    total_amount = round(sum(c["amount"] for c in cats) + extra_amount, 2)

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

    top_amt = max(enriched, key=lambda c: c["amount"]) if enriched else {"name": "", "amount": 0}
    top_cnt = max(enriched, key=lambda c: c["count"])  if enriched else {"name": "", "count":  0}

    days = max(
        sum(days_in_month(mi["year"], mi["month"]) for mi in months_included),
        1,
    )

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
        "days_in_period": days,
        "category_count": len(enriched),
        "top_by_amount": {"name": top_amt["name"], "amount": top_amt["amount"]},
        "top_by_count": {"name": top_cnt["name"], "count": top_cnt["count"]},
        "plazas_included": plazas_included,
    }
