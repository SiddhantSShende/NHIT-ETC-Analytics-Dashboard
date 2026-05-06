"""
NHIT chatbot context-builder + scope detector.

Pure data functions that turn a free-text user message into:
  - the resolved plaza set (and a human-readable scope label),
  - an "AUTHORITATIVE_ANSWER" sentence the LLM can echo verbatim,
  - a compact context block summarising the relevant numbers.

Kept separate from the LLM proxy (api/routes.py) so the engine and the
prompt-builder can be unit-tested without spinning up Flask.
"""
from __future__ import annotations

import re

from ..core.analytics import aggregate_plazas_for_month, aggregate_plazas_for_range
from ..core.constants import MONTH_MAP, MONTH_NAMES


# ── Formatting (Indian convention) ─────────────────────────────────────────
def fmt_inr(amount: float) -> str:
    a = float(amount or 0)
    if a >= 1e7:
        return f"₹{a / 1e7:.2f} Cr"
    if a >= 1e5:
        return f"₹{a / 1e5:.2f} L"
    return "₹{:,.2f}".format(a)


def fmt_int(n: int) -> str:
    n = int(n or 0)
    if n >= 1e7:
        return f"{n / 1e7:.2f} Cr"
    if n >= 1e5:
        return f"{n / 1e5:.2f} L"
    return f"{n:,}"


# ── Scope detection ────────────────────────────────────────────────────────
def detect_scope(snapshot: dict, message: str) -> tuple[list[str], str, dict]:
    """Resolve a user message to a concrete plaza set + human-readable scope.

    Detection order (most specific wins):
      1. Single plaza name → that plaza only.
      2. SPV (NSPPL/NEPPL/NWPPL) and/or Round (R1..R5) → canonical_plaza
         set from taxonomy filtered by that SPV/Round.
      3. None of the above → the full network (all plazas).
    """
    msg_lower = message.lower()
    tax_rows = snapshot.get("taxonomy", {}).get("rows", [])
    spvs = snapshot.get("taxonomy", {}).get("spvs", [])
    rounds = snapshot.get("taxonomy", {}).get("rounds", [])
    all_plazas = list(snapshot["data"].keys())

    # 1. Plaza name as substring (longest first to prefer the most specific match).
    matched_plaza = None
    for p in sorted(all_plazas, key=lambda x: -len(x)):
        if p.lower() in msg_lower:
            matched_plaza = p
            break

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
                "spv": matched_spv,
                "round": matched_round,
                "plaza_count": len(plazas),
            }
        # SPV+Round combo had no plazas; if the SPV alone has data, fall back to that.
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
                    "spv": matched_spv,
                    "round": None,
                    "plaza_count": len(spv_plazas),
                    "note": f"requested Round {matched_round} not present",
                }
        return [], (
            f"No plazas match the requested filter "
            f"({(matched_spv or '')} {(matched_round or '')}".strip() + ")"
        ), {"spv": matched_spv, "round": matched_round, "plaza_count": 0}

    return all_plazas, "All NHIT Plazas (whole network)", {"plaza_count": len(all_plazas)}


# ── Context builder ───────────────────────────────────────────────────────
def build_data_context(snapshot: dict, message: str) -> str:
    """Compose a scope-aware context block for the LLM.

    Detects SPV/Round/Plaza/Month/Year from the message and produces a single
    AUTHORITATIVE_ANSWER sentence the model can echo verbatim. All numbers
    are computed from the snapshot — no LLM math.
    """
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
    detected_year = years_in_msg[0] if years_in_msg else None
    detected_month = months_found[0] if months_found else None
    range_start = range_end = None
    if len(months_found) >= 2 and years_in_msg:
        start_month, end_month = months_found[0], months_found[-1]
        start_year = years_in_msg[0]
        end_year = years_in_msg[-1] if len(years_in_msg) > 1 else start_year
        range_start = (start_year, start_month)
        range_end = (end_year, end_month)

    scope_plazas, scope_label, scope_meta = detect_scope(snapshot, message)
    is_single_plaza = "plaza" in scope_meta

    period_phrase = ""
    rec = None
    if range_start and range_end:
        sy, sm = range_start
        ey, em = range_end
        rec = aggregate_plazas_for_range(snapshot, scope_plazas, sy, sm, ey, em)
        period_phrase = f"the period {MONTH_NAMES[sm]} {sy} to {MONTH_NAMES[em]} {ey}"
    elif detected_year and detected_month:
        rec = aggregate_plazas_for_month(snapshot, scope_plazas, detected_year, detected_month)
        period_phrase = f"{MONTH_NAMES[detected_month]} {detected_year}"

    auth_sentence = ""
    no_data_sentence = ""
    period_requested = bool(period_phrase)
    if rec:
        auth_sentence = (
            f"For {scope_label} ({len(rec['plazas_included'])} plazas with data) "
            f"in {period_phrase}: total revenue = {fmt_inr(rec['total_amount'])} "
            f"(₹{rec['total_amount']:,.0f}); total transactions = "
            f"{fmt_int(rec['total_count'])} ({rec['total_count']:,}); "
            f"average fare per transaction = ₹{rec['avg_per_txn']:,.2f}; "
            f"top category by revenue = {rec['top_by_amount']['name']} "
            f"({fmt_inr(rec['top_by_amount']['amount'])}); top category by volume = "
            f"{rec['top_by_count']['name']} ({fmt_int(rec['top_by_count']['count'])} txns)."
        )
    elif period_requested:
        avail_months = ", ".join(
            f"{MONTH_NAMES[m['month']][:3]} {m['year']}"
            for m in snapshot.get("months", [])
        )
        no_data_sentence = (
            f"For {scope_label} in {period_phrase}, NO DATA is available in "
            f"the dataset. Do NOT estimate, extrapolate, or substitute another "
            f"period. Available months on record: {avail_months}."
        )

    lines: list[str] = []
    if auth_sentence:
        lines.append("=" * 8 + " AUTHORITATIVE_ANSWER (use this sentence verbatim, do NOT recompute) " + "=" * 8)
        lines.append(auth_sentence)
        lines.append("=" * 8 + " END AUTHORITATIVE_ANSWER " + "=" * 8)
        lines.append("")
    elif no_data_sentence:
        lines.append("=" * 8 + " AUTHORITATIVE_ANSWER (use this sentence verbatim, do NOT recompute) " + "=" * 8)
        lines.append(no_data_sentence)
        lines.append("=" * 8 + " END AUTHORITATIVE_ANSWER " + "=" * 8)
        lines.append("")

    available_months = snapshot.get("available_months", [])
    spvs = snapshot.get("taxonomy", {}).get("spvs", [])
    rounds = snapshot.get("taxonomy", {}).get("rounds", [])
    lines.append(
        f"PORTFOLIO: {len(snapshot['plazas'])} plazas · "
        f"{len(available_months)} months of data · "
        f"SPVs={','.join(spvs)} · Rounds={','.join(rounds)}"
    )
    lines.append(f"SCOPE: {scope_label} (covers {len(scope_plazas)} plaza(s))")

    if rec:
        cats_str = "; ".join(
            f"{c['name']}: {fmt_int(c['count'])} txns / {fmt_inr(c['amount'])}"
            for c in rec["categories"]
        )
        lines.append(f"CATEGORY_BREAKDOWN: {cats_str}")
        if not range_start:
            lines.append(f"AVG_REVENUE_PER_DAY: {fmt_inr(rec.get('avg_revenue_per_day', 0))}")

    if is_single_plaza:
        plaza = scope_plazas[0]
        # Suppress trend block when user asked about a period with no data —
        # otherwise the LLM may pluck a number from a different month and
        # mislabel it.
        suppress_trend = period_requested and not rec
        if not suppress_trend:
            trend = snapshot.get("monthly_totals", {}).get(plaza, [])
            if trend:
                trend_str = " | ".join(
                    f"{t['label']}: {fmt_int(t['count'])} txns / {fmt_inr(t['amount'])}"
                    for t in trend
                )
                lines.append(f"TREND_FOR_{plaza}: {trend_str}")
    elif rec and detected_year and detected_month:
        key = f"{detected_year}-{detected_month:02d}"
        ranked = sorted(
            scope_plazas,
            key=lambda p: snapshot["data"].get(p, {}).get(key, {}).get("total_amount", 0),
            reverse=True,
        )
        top_lines = []
        for plaza in ranked[:10]:
            r = snapshot["data"].get(plaza, {}).get(key)
            if not r:
                continue
            top_lines.append(
                f"  {plaza}: txns={fmt_int(r['total_count'])} revenue={fmt_inr(r['total_amount'])}"
            )
        if top_lines:
            lines.append(f"TOP_PLAZAS_IN_SCOPE ({MONTH_NAMES[detected_month]} {detected_year}):")
            lines.extend(top_lines)

    if not rec and (detected_year or detected_month):
        avail = ", ".join(
            f"{MONTH_NAMES[m['month']][:3]} {m['year']}" for m in snapshot.get("months", [])
        )
        lines.append(f"NO_DATA_FOR_REQUESTED_PERIOD. Available months: {avail}")

    return "\n".join(lines) if lines else "No matching data found in JSON files."
