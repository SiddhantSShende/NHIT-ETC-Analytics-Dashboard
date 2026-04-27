"""
NHIT | PDF parser for IHMCL VC-Wise monthly reports.

Pure functions — no Flask, no global state. Parses one PDF and emits
per-plaza category rows plus the per-plaza TOTAL_CNT/TOTAL_AMT cells
so the build pipeline can validate that our parsing matches the
official totals.

PDF column layout (consistent across all months we have):
    PLAZA_NAME | PIU | RO
    | CAR_JEEP_CNT  | CAR_JEEP_AMT
    | LCV_CNT       | LCV_AMT
    | BUS_TRUCK_CNT | BUS_TRUCK_AMT
    | 3_AXLE_CNT    | 3_AXLE_AMT
    | 4_6_AXLE_CNT  | 4_6_AXLE_AMT
    | OSV_CNT       | OSV_AMT
    | TOTAL_CNT     | TOTAL_AMT

Numeric cells contain awkward whitespace and Indian-grouping commas:
"1 5,353" => 15353. We strip everything except digits/dot/minus.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, List, Tuple

import pdfplumber

log = logging.getLogger(__name__)

CATEGORY_LABEL: Dict[str, str] = {
    "CAR_JEEP":  "Car / Jeep / Van (VC4)",
    "LCV":       "Light Commercial Vehicle (VC5)",
    "BUS_TRUCK": "Bus / Truck - 2 Axle (VC6)",
    "3_AXLE":    "3-Axle Vehicle (VC7)",
    "4_6_AXLE":  "4-6 Axle (VC8/9/10)",
    "OSV":       "Oversized Vehicle (VC11+)",
}

_NUMERIC_RE = re.compile(r"[^\d.\-]")
_SKIP_PLAZA_VALUES = {"total", "grand total", "sub total", "plaza_name"}


def to_number(cell) -> float:
    """Parse a PDF cell into a float. Tolerant of spaces, commas, dashes, None."""
    if cell is None:
        return 0.0
    cleaned = _NUMERIC_RE.sub("", str(cell))
    if not cleaned or cleaned in ("-", "."):
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _detect_columns(headers: List[str]):
    """Given a header row, locate plaza column, per-category cnt/amt pairs,
    and the trailing TOTAL_CNT / TOTAL_AMT indices."""
    plaza_idx = next(
        (i for i, h in enumerate(headers) if "PLAZA" in h.upper()),
        0,
    )
    cat_pairs: List[Tuple[str, int, int]] = []
    total_cnt_idx = None
    total_amt_idx = None

    for j, h in enumerate(headers):
        hu = h.upper().strip()
        if hu == "TOTAL_CNT":
            total_cnt_idx = j
        elif hu == "TOTAL_AMT":
            total_amt_idx = j
        elif hu.endswith("_CNT") and not hu.startswith("TOTAL"):
            base = hu[:-4]
            amt_target = base + "_AMT"
            amt_j = next(
                (k for k, hh in enumerate(headers) if hh.upper().strip() == amt_target),
                None,
            )
            if amt_j is not None:
                label = CATEGORY_LABEL.get(base, base.replace("_", " ").title())
                cat_pairs.append((label, j, amt_j))

    return plaza_idx, cat_pairs, total_cnt_idx, total_amt_idx


def parse_pdf(pdf_path: Path):
    """Parse one VC-Wise monthly PDF.

    Returns:
        rows:       list of {plaza_name, vehicle_category, count, amount}
        pdf_totals: dict[plaza_name] -> {"count": int, "amount": float} from
                    the TOTAL_CNT / TOTAL_AMT cells on each PDF row, used by
                    build_data.py to verify parsing accuracy.
    """
    rows: List[dict] = []
    pdf_totals: Dict[str, dict] = {}

    headers: List[str] | None = None
    plaza_idx = 0
    cat_pairs: List[Tuple[str, int, int]] = []
    total_cnt_idx: int | None = None
    total_amt_idx: int | None = None

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                if not table:
                    continue

                # Look for the header row in the first 3 rows of this table.
                local_header_idx = -1
                for i, r in enumerate(table[:3]):
                    rl = [str(c or "").strip() for c in r]
                    upper = " ".join(rl).upper()
                    if "PLAZA_NAME" in upper and "_CNT" in upper and "_AMT" in upper:
                        headers = rl
                        local_header_idx = i
                        plaza_idx, cat_pairs, total_cnt_idx, total_amt_idx = (
                            _detect_columns(headers)
                        )
                        break

                if not headers or not cat_pairs:
                    continue

                start = local_header_idx + 1 if local_header_idx >= 0 else 0
                for r in table[start:]:
                    if not r or len(r) <= plaza_idx:
                        continue
                    plaza_raw = str(r[plaza_idx] or "").strip()
                    if not plaza_raw:
                        continue
                    pl = plaza_raw.lower()
                    if pl in _SKIP_PLAZA_VALUES or "vc wise" in pl:
                        continue

                    if total_cnt_idx is not None and total_cnt_idx < len(r):
                        # A plaza name may legitimately appear multiple times
                        # (one row per PIU). Accumulate so validation can sum
                        # all per-plaza occurrences before comparing.
                        bucket = pdf_totals.setdefault(plaza_raw, {"count": 0, "amount": 0.0})
                        bucket["count"]  += int(to_number(r[total_cnt_idx]))
                        if total_amt_idx is not None and total_amt_idx < len(r):
                            bucket["amount"] = round(
                                bucket["amount"] + to_number(r[total_amt_idx]), 2
                            )

                    for label, ci, ai in cat_pairs:
                        cnt = int(to_number(r[ci] if ci < len(r) else 0))
                        amt = to_number(r[ai] if ai < len(r) else 0)
                        if cnt > 0 or amt > 0:
                            rows.append({
                                "plaza_name": plaza_raw,
                                "vehicle_category": label,
                                "count": cnt,
                                "amount": round(amt, 2),
                            })

    return rows, pdf_totals
