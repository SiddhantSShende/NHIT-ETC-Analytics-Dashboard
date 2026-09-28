"""
NHIT | Per-month + per-plaza JSON exporter.

Reads IHMCL PDFs in `downloads/{vc_monthly,ETC_Monthly_Data,Monthly_Annual_Pass_Report,MLFF_Plaza_Data}/`
and emits structured JSON files into `downloads/json/`:

    downloads/json/
      monthly/<YYYY-MM>.json   one file per period, all plazas
            mlff/<YYYY-MM>.json      separate MLFF report data per period
      plazas/<slug>.json       one file per plaza, all periods
      _index.json              periods, plazas, sources, unmapped names

vc_monthly and ETC_Monthly_Data per-month PDFs share the same 17-column
VC-wise layout. Annual-Pass PDFs and MLFF PDFs use distinct layouts and are
parsed separately. MLFF output stays separate from ETC dashboard totals.

Incremental by default: a period that already has monthly/<YYYY-MM>.json is
NOT re-parsed — its record is rehydrated from that file instead. Parsing a
PDF costs ~2 minutes (pdfplumber table extraction), so a full re-parse of
every period grew to ~105 min against the orchestrator's 120 min
BUILD_TIMEOUT. Only new periods cost anything now; the index and per-plaza
files are re-derived from the monthly JSON, which is cheap.

Pass --full to force a re-parse of everything (e.g. after changing a parser
or to correct a period whose PDF was re-published). --full is authoritative:
its output is what incremental builds must reproduce.

Run:  python scripts/build_json_export.py [--full]
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.core.parser import to_number, CATEGORY_LABEL, _detect_columns, _SKIP_PLAZA_VALUES  # noqa: E402
from backend.core.ingestor import normalize_plaza_name  # noqa: E402
from backend.core.constants import MONTH_MAP, MONTH_NAMES, NHIT_PLAZAS  # noqa: E402
from backend.core.taxonomy import load_taxonomy, EXCEL_PATH_DEFAULT  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("build_json_export")

DOWNLOADS = ROOT / "downloads"
OUT_DIR = DOWNLOADS / "json"
MONTHLY_DIR = OUT_DIR / "monthly"
PLAZAS_DIR = OUT_DIR / "plazas"
MLFF_DIR = OUT_DIR / "mlff"
# Per-period validation_warnings, kept beside the exports. Warnings are
# produced at parse time, so a rehydrated period cannot recompute its own —
# without this cache, _index.json's validation_warnings would silently drop
# to "warnings from PDFs parsed this run" and the orchestrator's sanity gate
# would compare against a moving baseline. Absent file == all zeros, which
# matches the committed index.
BUILD_CACHE = OUT_DIR / "_build_cache.json"

CATEGORY_ORDER = [
    CATEGORY_LABEL["CAR_JEEP"],
    CATEGORY_LABEL["LCV"],
    CATEGORY_LABEL["BUS_TRUCK"],
    CATEGORY_LABEL["3_AXLE"],
    CATEGORY_LABEL["4_6_AXLE"],
    CATEGORY_LABEL["OSV"],
]

VALIDATION_TOLERANCE = 0.005  # 0.5%


# ---------------------------------------------------------------------------
# Period parsing
# ---------------------------------------------------------------------------

_PERIOD_FROM_NAME_RE = re.compile(
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*[-_\s]*(\d{4}|\d{2})",
    re.IGNORECASE,
)

_FY_FROM_NAME_RE = re.compile(r"FY[-_\s]*(\d{2})[-_\s]*(\d{2})", re.IGNORECASE)


def period_from_filename(name: str) -> str | None:
    """Extract YYYY-MM period from a filename like 'Sept-2025-ETC-Data.pdf'."""
    m = _PERIOD_FROM_NAME_RE.search(name)
    if not m:
        return None
    mon = MONTH_MAP.get(m.group(1).lower())
    yr = int(m.group(2))
    if yr < 100:
        yr += 2000
    if not mon:
        return None
    return f"{yr:04d}-{mon:02d}"


def fy_periods_from_filename(name: str) -> list[str] | None:
    """Given 'Monthly-ETC-Data-FY-23-24-2.pdf', return Apr-Mar periods:
    ['2023-04','2023-05',...,'2024-03']. Returns None if FY token absent."""
    m = _FY_FROM_NAME_RE.search(name)
    if not m:
        return None
    fy_a = int(m.group(1))
    fy_b = int(m.group(2))
    if (fy_a + 1) % 100 != fy_b:
        return None
    start_year = 2000 + fy_a
    periods: list[str] = []
    for i in range(12):
        month = 4 + i
        year = start_year if month <= 12 else start_year + 1
        month = month if month <= 12 else month - 12
        periods.append(f"{year:04d}-{month:02d}")
    return periods


# ---------------------------------------------------------------------------
# Plaza canonicalization
# ---------------------------------------------------------------------------

def _slug(canonical: str) -> str:
    key = normalize_plaza_name(canonical)
    return key.replace(" ", "-") if key else "unknown"


class PlazaResolver:
    """Resolves raw plaza strings to a stable canonical name + slug.

    Uses the same normalize_plaza_name() as ingestor.py: tokens minus stopwords.
    Preferred-name registry (NHIT_PLAZAS) wins; otherwise the longest seen
    raw name for that key is treated as canonical.
    """

    def __init__(self) -> None:
        self._key_to_canonical: dict[str, str] = {
            normalize_plaza_name(p): p for p in NHIT_PLAZAS
        }
        self._raw_to_canonical: dict[str, str] = {}
        self.unmapped_to_nhit: set[str] = set()

    def seed(self, canonical: str, raws: list[str]) -> None:
        """Replay a decision this resolver made on an earlier run.

        resolve() is order-dependent: the first raw name seen for a key
        becomes that key's canonical. An incremental build skips most PDFs,
        so those raw names are never seen and a new period's plaza could be
        canonicalized differently than it was historically. Seeding from the
        already-emitted monthly JSON restores the same state a full parse
        would have reached by this point.

        setdefault throughout: NHIT_PLAZAS preferred names are installed in
        __init__ and must keep winning over anything learned from a PDF.
        """
        for raw in raws:
            self._raw_to_canonical.setdefault(raw, canonical)
            raw_key = normalize_plaza_name(raw)
            if raw_key:
                self._key_to_canonical.setdefault(raw_key, canonical)
        key = normalize_plaza_name(canonical)
        if key:
            self._key_to_canonical.setdefault(key, canonical)

    def resolve(self, raw: str) -> tuple[str, str] | None:
        raw = (raw or "").strip()
        if not raw:
            return None
        if raw in self._raw_to_canonical:
            canon = self._raw_to_canonical[raw]
            return canon, _slug(canon)
        key = normalize_plaza_name(raw)
        if not key:
            return None
        canonical = self._key_to_canonical.get(key)
        if canonical is None:
            canonical = raw
            self._key_to_canonical[key] = canonical
            self.unmapped_to_nhit.add(raw)
        else:
            existing_key = normalize_plaza_name(canonical)
            # Prefer a longer raw name when canonical was just a short fallback.
            if (
                canonical not in NHIT_PLAZAS
                and existing_key == key
                and len(raw) > len(canonical)
            ):
                canonical = raw
                self._key_to_canonical[key] = canonical
        self._raw_to_canonical[raw] = canonical
        return canonical, _slug(canonical)


# ---------------------------------------------------------------------------
# vc_monthly / ETC_Monthly_Data per-month parser (delegates to parser.py)
# ---------------------------------------------------------------------------

def parse_vc_wise(pdf_path: Path) -> tuple[dict[str, dict], dict[str, dict]]:
    """Parse a VC-wise monthly PDF in a single pdfplumber pass.

    Replicates parser.parse_pdf logic but also captures PIU/RO and
    aggregates per-plaza in one go (parser.py drops PIU/RO and reopening
    the PDF for them ~doubles runtime on 100-row × 10-page PDFs).

    Returns:
        per_plaza: raw_plaza -> {piu, ro, vehicles, total_extracted}
        pdf_totals: raw_plaza -> {count, amount} from TOTAL_CNT/TOTAL_AMT cells
    """
    per_plaza: dict[str, dict] = {}
    pdf_totals: dict[str, dict] = {}

    headers: list[str] | None = None
    plaza_idx = 0
    piu_idx: int | None = None
    ro_idx: int | None = None
    cat_pairs: list[tuple[str, int, int]] = []
    total_cnt_idx: int | None = None
    total_amt_idx: int | None = None

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                if not table:
                    continue

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
                        piu_idx = next(
                            (j for j, h in enumerate(headers) if h.upper().strip() == "PIU"),
                            None,
                        )
                        ro_idx = next(
                            (j for j, h in enumerate(headers) if h.upper().strip() == "RO"),
                            None,
                        )
                        break

                if not headers or not cat_pairs:
                    continue

                start = local_header_idx + 1 if local_header_idx >= 0 else 0
                for r in table[start:]:
                    if not r or len(r) <= plaza_idx:
                        continue
                    name = str(r[plaza_idx] or "").strip()
                    if not name:
                        continue
                    # pdfplumber occasionally merges two adjacent plaza rows
                    # into one cell; the resulting name contains a newline
                    # and the numeric columns are digit-soup. Same defensive
                    # filter as parse_fy_summary().
                    if "\n" in name:
                        log.warning(
                            "[%s] dropping merged row: %r",
                            pdf_path.name, name[:80],
                        )
                        continue
                    pl = name.lower()
                    if pl in _SKIP_PLAZA_VALUES or "vc wise" in pl:
                        continue

                    piu = (
                        str(r[piu_idx] or "").strip()
                        if piu_idx is not None and piu_idx < len(r)
                        else ""
                    )
                    ro = (
                        str(r[ro_idx] or "").strip()
                        if ro_idx is not None and ro_idx < len(r)
                        else ""
                    )

                    bucket = per_plaza.setdefault(name, {
                        "piu": piu,
                        "ro": ro,
                        "vehicles": {},
                        "total_extracted": {"count": 0, "amount": 0.0},
                    })
                    if not bucket["piu"] and piu:
                        bucket["piu"] = piu
                    if not bucket["ro"] and ro:
                        bucket["ro"] = ro

                    if total_cnt_idx is not None and total_cnt_idx < len(r):
                        tot = pdf_totals.setdefault(name, {"count": 0, "amount": 0.0})
                        tot["count"] += int(to_number(r[total_cnt_idx]))
                        if total_amt_idx is not None and total_amt_idx < len(r):
                            tot["amount"] = round(
                                tot["amount"] + to_number(r[total_amt_idx]), 2
                            )

                    for label, ci, ai in cat_pairs:
                        cnt = int(to_number(r[ci] if ci < len(r) else 0))
                        amt = to_number(r[ai] if ai < len(r) else 0)
                        if cnt == 0 and amt == 0:
                            continue
                        v = bucket["vehicles"].setdefault(label, {"count": 0, "amount": 0.0})
                        v["count"] += cnt
                        v["amount"] = round(v["amount"] + amt, 2)
                        bucket["total_extracted"]["count"] += cnt
                        bucket["total_extracted"]["amount"] = round(
                            bucket["total_extracted"]["amount"] + amt, 2
                        )

    return per_plaza, pdf_totals


# ---------------------------------------------------------------------------
# Annual Pass parser (5-column layout)
# ---------------------------------------------------------------------------

def parse_annual_pass(pdf_path: Path) -> dict[str, dict]:
    """Annual-Pass monthly PDF -> raw_plaza -> {piu, ro, transaction_count}.

    Supports two header layouts seen in IHMCL reports:
      - 5-col (2025): "... | Plaza Name | PIU | RO | Total number of Annual Pass
        Transaction Count, including single journey, return journey etc."
      - 7-col (2026): "... | Plaza Name | PIU | RO | Sum of QUALIFIED_SJ_TXN
        | Sum of QUALIFIED_RJ_TXN | Sum of TOTAL_QUALIFIED_TXN"
    For the 7-col layout we use TOTAL_QUALIFIED_TXN (single + return journeys).
    """
    out: dict[str, dict] = {}
    with pdfplumber.open(pdf_path) as pdf:
        plaza_idx = piu_idx = ro_idx = count_idx = None
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                if not table:
                    continue
                # Detect header (only on first table seen per PDF).
                if plaza_idx is None:
                    for r in table[:3]:
                        rl = [str(c or "").strip() for c in r]
                        # Collapse whitespace so split words like
                        # "QUALIFIED\n_SJ_TXN" still match TOTAL_QUALIFIED_TXN.
                        joined = re.sub(r"\s+", "", " | ".join(rl).upper())
                        if "PLAZANAME" in joined and "PIU" in joined and (
                            "ANNUALPASS" in joined
                            or "TRANSACTIONCOUNT" in joined
                            or "TOTAL_QUALIFIED_TXN" in joined
                            or "QUALIFIED_TXN" in joined
                        ):
                            for j, h in enumerate(rl):
                                hu = h.upper()
                                hu_compact = re.sub(r"\s+", "", hu)
                                if "PLAZA" in hu and plaza_idx is None:
                                    plaza_idx = j
                                elif hu.strip() == "PIU":
                                    piu_idx = j
                                elif hu.strip() == "RO":
                                    ro_idx = j
                                elif "TOTAL_QUALIFIED_TXN" in hu_compact:
                                    count_idx = j  # prefer TOTAL over SJ/RJ
                                elif count_idx is None and (
                                    "ANNUAL PASS" in hu or "TRANSACTION COUNT" in hu
                                ):
                                    count_idx = j
                            break
                if plaza_idx is None or count_idx is None:
                    continue

                for r in table:
                    if not r or len(r) <= plaza_idx:
                        continue
                    name = str(r[plaza_idx] or "").strip()
                    if not name:
                        continue
                    nlow = name.lower()
                    if nlow in {"plaza name", "total", "grand total"} or "month" in nlow.split():
                        continue
                    # Skip rows whose count cell is non-numeric (header repeats).
                    raw_count = r[count_idx] if count_idx < len(r) else None
                    cnt_val = to_number(raw_count)
                    if raw_count is None or (isinstance(raw_count, str) and not re.search(r"\d", raw_count)):
                        continue

                    piu = str(r[piu_idx] or "").strip() if piu_idx is not None and piu_idx < len(r) else ""
                    ro = str(r[ro_idx] or "").strip() if ro_idx is not None and ro_idx < len(r) else ""
                    out[name] = {
                        "piu": piu,
                        "ro": ro,
                        "transaction_count": int(cnt_val),
                    }
    return out


def _mlff_metric_key(header: str, category: str, offset: int, width: int) -> str:
    compact = re.sub(r"[^A-Z]", "", header.upper())
    if "ANNUALPASS" in compact:
        return "annual_pass_count"
    if "ENOTICE" in compact and "AMOUNT" in compact:
        return "e_notice_amount_received"
    if "ENOTICE" in compact:
        return "e_notice_count"
    if "ETCTXNCOUNT" in compact:
        return "transaction_count"
    if "ETCTXNAMOUNT" in compact:
        return "transaction_amount"

    if category.strip().lower() == "total":
        return (
            "transaction_count",
            "transaction_amount",
            "e_notice_count",
            "e_notice_amount_received",
        )[offset]
    if offset == 0:
        return "transaction_count"
    if offset == 1:
        return "transaction_amount"
    if width == 4 and offset == 2:
        return "annual_pass_count"
    return "e_notice_count"


def parse_mlff_table(table: list[list]) -> list[dict]:
    """Parse one IHMCL MLFF monthly table without merging it into ETC data."""
    header_idx = next(
        (
            index
            for index, row in enumerate(table[:4])
            if "PLAZA NAME" in " ".join(str(cell or "") for cell in row).upper()
            and "MONTH-YEAR" in " ".join(str(cell or "") for cell in row).upper()
        ),
        None,
    )
    if header_idx is None or header_idx == 0 or header_idx + 1 >= len(table):
        return []

    current_header = table[header_idx]
    category_terms = ("CAR", "JEEP", "VAN", "LCV", "BUS", "AXLE", "MAV", "OSV", "TOTAL")
    has_group_headers = any(
        any(term in str(cell or "").upper() for term in category_terms)
        for cell in current_header[4:]
    )
    group_row = current_header if has_group_headers else table[header_idx - 1]
    metric_row = table[header_idx + 1] if has_group_headers else current_header
    data_start = header_idx + 2 if has_group_headers else header_idx + 1
    starts = [
        index for index, value in enumerate(group_row)
        if index >= 4 and str(value or "").strip()
    ]
    if not starts:
        return []

    rows = []
    for row in table[data_start:]:
        if len(row) < 4:
            continue
        plaza_name = str(row[1] or "").strip()
        if not plaza_name or plaza_name.lower() in {"plaza name", "total", "grand total"}:
            continue
        vehicles = {}
        total = {}
        for group_index, start in enumerate(starts):
            end = starts[group_index + 1] if group_index + 1 < len(starts) else len(group_row)
            category = str(group_row[start] or "").strip()
            values = {}
            for column in range(start, end):
                if column >= len(row):
                    continue
                metric = _mlff_metric_key(
                    str(metric_row[column] or ""), category, column - start, end - start
                )
                number = to_number(row[column])
                values[metric] = int(number) if metric.endswith("count") else round(number, 2)
            if category.lower() == "total":
                total = values
            elif any(values.values()):
                vehicles[category] = values
        rows.append({
            "reporting_period_raw": str(row[0] or "").strip(),
            "plaza_name": plaza_name,
            "piu": str(row[2] or "").strip(),
            "ro": str(row[3] or "").strip(),
            "vehicles": vehicles,
            "total": total,
        })
    return rows


def parse_mlff_pdf(pdf_path: Path) -> list[dict]:
    """Extract plaza rows from an IHMCL MLFF monthly report PDF."""
    rows = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                rows.extend(parse_mlff_table(table))
    return rows


# ---------------------------------------------------------------------------
# FY-summary parser (12-month wide layout)
# ---------------------------------------------------------------------------

def parse_fy_summary(pdf_path: Path) -> dict[str, dict[str, dict]]:
    """Parse a FY-summary PDF (rows: plaza | state | 12 × (Count, Amount)).

    Returns: period -> raw_plaza -> {"total": {"count","amount"}}.
    Per-vehicle-class breakdown is NOT available in FY summaries, so the
    caller emits records with vehicles=null and only totals populated.

    Header row appears only on page 1 of the PDF; pdfplumber treats the
    first row of pages 2+ as the table header, so we accept any row whose
    cell 0 is a non-skippable plaza name.
    """
    periods = fy_periods_from_filename(pdf_path.name)
    if not periods or len(periods) != 12:
        log.warning("Cannot infer FY periods from %s", pdf_path.name)
        return {}

    out: dict[str, dict[str, dict]] = {p: {} for p in periods}

    def _is_header_row(cells: list) -> bool:
        first = str(cells[0] or "").strip().lower()
        if first in {"fee plaza name", "plaza name"}:
            return True
        # On pages 2+, pdfplumber may promote the first data row to header,
        # so we only flag literal header markers here.
        return False

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                if not table:
                    continue
                for r in table:
                    if not r or len(r) < 26:
                        continue
                    if _is_header_row(r):
                        continue
                    name = str(r[0] or "").strip()
                    if not name:
                        continue
                    # pdfplumber occasionally merges two adjacent plaza rows
                    # into one cell; the resulting name contains a newline
                    # and the numeric columns are concatenated digit-soup.
                    # Drop these — they corrupt downstream aggregations.
                    if "\n" in name:
                        log.warning("[%s] dropping merged row: %r", pdf_path.name, name[:80])
                        continue
                    nl = name.lower()
                    if nl in _SKIP_PLAZA_VALUES or nl in {"total", "grand total"}:
                        continue
                    if "fee plaza" in nl or "plaza name" in nl:
                        continue

                    for i in range(12):
                        ci = 2 + i * 2
                        ai = ci + 1
                        if ci >= len(r) or ai >= len(r):
                            break
                        cnt_raw = r[ci]
                        amt_raw = r[ai]
                        cnt = int(to_number(cnt_raw))
                        amt = to_number(amt_raw)
                        if cnt == 0 and amt == 0:
                            continue
                        bucket = out[periods[i]].setdefault(name, {
                            "total": {"count": 0, "amount": 0.0},
                        })
                        bucket["total"]["count"] += cnt
                        bucket["total"]["amount"] = round(
                            bucket["total"]["amount"] + amt, 2
                        )

    return out


def discover_fy_pdfs() -> list[Path]:
    """FY-tagged summary PDFs from ETC_Monthly_Data/<year>/."""
    out: list[Path] = []
    etc_dir = DOWNLOADS / "ETC_Monthly_Data"
    if not etc_dir.exists():
        return out
    for year_dir in sorted(etc_dir.iterdir()):
        if not year_dir.is_dir():
            continue
        for p in sorted(year_dir.glob("*.pdf")):
            if _FY_FROM_NAME_RE.search(p.name):
                out.append(p)
    return out


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_totals(label: str, per_plaza: dict, pdf_totals: dict) -> int:
    """Compare extracted category sums against PDF TOTAL cells. Returns warning count."""
    warnings = 0
    for name, bucket in per_plaza.items():
        if name not in pdf_totals:
            continue
        ex = bucket["total_extracted"]
        pt = pdf_totals[name]
        for field in ("count", "amount"):
            a = float(ex[field])
            b = float(pt[field])
            if b == 0 and a == 0:
                continue
            denom = max(abs(a), abs(b), 1.0)
            if abs(a - b) / denom > VALIDATION_TOLERANCE:
                log.warning(
                    "[%s] %s %s mismatch: extracted=%s pdf_total=%s",
                    label, name, field, a, b,
                )
                warnings += 1
    return warnings


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_vc_pdfs() -> list[tuple[str, Path]]:
    """Period-tagged VC-wise PDFs from vc_monthly/ + ETC_Monthly_Data/<year>/."""
    found: dict[str, Path] = {}

    def add(p: Path) -> None:
        period = period_from_filename(p.name)
        if not period:
            log.warning("Cannot infer period from filename: %s", p)
            return
        # vc_monthly takes priority over ETC_Monthly_Data when same period.
        if period not in found:
            found[period] = p

    for p in sorted((DOWNLOADS / "vc_monthly").glob("*.pdf")):
        add(p)
    etc_dir = DOWNLOADS / "ETC_Monthly_Data"
    if etc_dir.exists():
        for year_dir in sorted(etc_dir.iterdir()):
            if not year_dir.is_dir():
                continue
            for p in sorted(year_dir.glob("*.pdf")):
                if "FY" in p.name.upper():
                    continue
                add(p)
    return sorted(found.items())


def discover_annual_pass_pdfs() -> list[tuple[str, Path]]:
    """Period-tagged Annual-Pass PDFs from Monthly_Annual_Pass_Report/."""
    base = DOWNLOADS / "Monthly_Annual_Pass_Report"
    out: dict[str, Path] = {}
    if not base.exists():
        return []
    for year_dir in sorted(base.iterdir()):
        if not year_dir.is_dir():
            continue
        for p in sorted(year_dir.glob("*.pdf")):
            if "FAQ" in p.name.upper():
                continue
            period = period_from_filename(p.name)
            if not period:
                continue
            if period not in out:
                out[period] = p
    return sorted(out.items())


def discover_mlff_pdfs() -> list[tuple[str, Path]]:
    """Find MLFF monthly reports in the archive and prior unclassified downloads."""
    out: dict[str, Path] = {}
    for source_dir in (DOWNLOADS / "MLFF_Plaza_Data", DOWNLOADS / "unclassified"):
        if not source_dir.exists():
            continue
        for path in sorted(source_dir.rglob("*.pdf")):
            name = path.name
            if not (
                name.upper().startswith("MLFF")
                or re.match(r"^VC[_\s-]*Wise[_\s-]*Monthly[_\s-]*Data_", name, re.I)
            ):
                continue
            period = period_from_filename(name)
            if period:
                out.setdefault(period, path)
            else:
                log.warning("Cannot infer period from MLFF report filename: %s", path)
    return sorted(out.items())


# ---------------------------------------------------------------------------
# Incremental rehydration
# ---------------------------------------------------------------------------

def existing_periods() -> set[str]:
    """Periods that already have a monthly export and so need no re-parse."""
    if not MONTHLY_DIR.exists():
        return set()
    return {
        p.stem for p in MONTHLY_DIR.glob("*.json")
        if re.fullmatch(r"\d{4}-\d{2}", p.stem)
    }


def load_prev_unmapped() -> set[str]:
    """The previous build's unmapped_to_nhit set.

    resolve() records a raw name here the first time it invents a canonical
    for an unknown key. On an incremental build those first sightings happened
    on an earlier run and never recur — resolve() short-circuits on the
    memoized raw — so the set has to be carried forward or _index.json would
    report only the raw names a single new period happened to introduce.
    """
    index_path = OUT_DIR / "_index.json"
    if not index_path.exists():
        return set()
    try:
        doc = json.loads(index_path.read_text(encoding="utf-8"))
        return set(doc.get("unmapped_to_nhit_canonical", []))
    except Exception as exc:
        log.warning("Ignoring unreadable _index.json: %s", exc)
        return set()


def load_warnings_cache() -> dict[str, int]:
    if not BUILD_CACHE.exists():
        return {}
    try:
        doc = json.loads(BUILD_CACHE.read_text(encoding="utf-8"))
        return {str(k): int(v) for k, v in doc.get("warnings_by_period", {}).items()}
    except Exception as exc:  # corrupt cache must never break a build
        log.warning("Ignoring unreadable %s: %s", BUILD_CACHE.name, exc)
        return {}


def hydrate_period(
    period: str,
    monthly: dict[str, dict[str, dict]],
    sources_by_period: dict[str, list[str]],
    resolver: PlazaResolver,
) -> bool:
    """Rebuild a period's in-memory state from its monthly JSON.

    Must round-trip exactly: the emit step downstream rewrites every period,
    so any field lost here shows up immediately as a diff on a file that
    should not have changed. That is the intended check — see the git diff
    step in the plan.
    """
    path = MONTHLY_DIR / f"{period}.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("Cannot reuse %s (%s) — will re-parse from PDF", path.name, exc)
        return False

    sources_by_period[period] = list(doc.get("sources", []))
    for rec in doc.get("plazas", []):
        canon = rec["plaza_name"]
        raws = rec.get("source_raw_names", [])
        monthly[period][canon] = {
            "plaza_name": canon,
            "plaza_slug": rec["plaza_slug"],
            "piu": rec["piu"],
            "ro": rec["ro"],
            "vehicles": rec["vehicles"],
            "total": rec["total"],
            "annual_pass": rec["annual_pass"],
            "raw_names": set(raws),
        }
        resolver.seed(canon, raws)
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export IHMCL report PDFs to JSON under downloads/json/."
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="re-parse every PDF instead of reusing existing monthly JSON "
             "(slow: ~105 min; needed after a parser change)",
    )
    args = parser.parse_args(argv)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    MONTHLY_DIR.mkdir(parents=True, exist_ok=True)
    PLAZAS_DIR.mkdir(parents=True, exist_ok=True)
    MLFF_DIR.mkdir(parents=True, exist_ok=True)

    resolver = PlazaResolver()

    # period -> canonical_name -> plaza record
    monthly: dict[str, dict[str, dict]] = defaultdict(dict)
    # period -> [pdf_relpaths]
    sources_by_period: dict[str, list[str]] = defaultdict(list)

    # ----- Reuse periods already exported -----
    # Rehydrate before any parsing so the resolver has seen every historical
    # raw name by the time a new period resolves its plazas.
    reused: set[str] = set()
    if not args.full:
        for period in sorted(existing_periods()):
            if hydrate_period(period, monthly, sources_by_period, resolver):
                reused.add(period)
        if reused:
            resolver.unmapped_to_nhit |= load_prev_unmapped()
            log.info(
                "Reusing %d already-exported period(s): %s .. %s",
                len(reused), min(reused), max(reused),
            )
    else:
        log.info("--full: re-parsing every PDF from scratch")

    warnings_cache = {} if args.full else load_warnings_cache()
    warnings_by_period: dict[str, int] = {
        p: warnings_cache.get(p, 0) for p in reused
    }

    # ----- VC-wise PDFs -----
    vc_pdfs = discover_vc_pdfs()
    log.info("Found %d VC-wise PDFs (%d to parse)",
             len(vc_pdfs), sum(1 for p, _ in vc_pdfs if p not in reused))
    for period, pdf_path in vc_pdfs:
        if period in reused:
            continue
        log.info("Parsing VC-wise %s -> %s", pdf_path.name, period)
        per_plaza, pdf_totals = parse_vc_wise(pdf_path)
        warnings_by_period[period] = warnings_by_period.get(period, 0) + validate_totals(
            pdf_path.name, per_plaza, pdf_totals
        )
        sources_by_period[period].append(str(pdf_path.relative_to(ROOT)).replace("\\", "/"))

        for raw, bucket in per_plaza.items():
            resolved = resolver.resolve(raw)
            if not resolved:
                continue
            canon, _ = resolved
            target = monthly[period].get(canon)
            if target is None:
                target = monthly[period][canon] = {
                    "plaza_name": canon,
                    "plaza_slug": _slug(canon),
                    "piu": bucket["piu"],
                    "ro": bucket["ro"],
                    "vehicles": {cat: {"count": 0, "amount": 0.0} for cat in CATEGORY_ORDER},
                    "total": {"count": 0, "amount": 0.0},
                    "annual_pass": None,
                    "raw_names": set(),
                }
            else:
                if not target["piu"] and bucket["piu"]:
                    target["piu"] = bucket["piu"]
                if not target["ro"] and bucket["ro"]:
                    target["ro"] = bucket["ro"]
            target["raw_names"].add(raw)
            for cat, v in bucket["vehicles"].items():
                slot = target["vehicles"].setdefault(cat, {"count": 0, "amount": 0.0})
                slot["count"] += int(v["count"])
                slot["amount"] = round(slot["amount"] + float(v["amount"]), 2)
            target["total"]["count"] += int(bucket["total_extracted"]["count"])
            target["total"]["amount"] = round(
                target["total"]["amount"] + float(bucket["total_extracted"]["amount"]), 2
            )

    # ----- FY-summary PDFs (12 months of totals, no per-class breakdown) -----
    # Processed AFTER per-month VC-wise PDFs so that any overlapping period
    # (none today, but defensive) keeps the richer per-class data and only
    # back-fills missing plazas with totals-only records.
    fy_pdfs = discover_fy_pdfs()
    log.info("Found %d FY-summary PDFs", len(fy_pdfs))
    for pdf_path in fy_pdfs:
        # An FY summary spans 12 periods. Once all of them are exported it can
        # contribute nothing (the per-period guard below drops it anyway), so
        # skip the parse rather than pay for it and throw the result away.
        fy_periods = fy_periods_from_filename(pdf_path.name) or []
        if fy_periods and all(p in reused for p in fy_periods):
            continue
        log.info("Parsing FY-summary %s", pdf_path.name)
        fy_data = parse_fy_summary(pdf_path)
        rel = str(pdf_path.relative_to(ROOT)).replace("\\", "/")
        for period, plazas in fy_data.items():
            if not plazas:
                continue
            # Skip periods already populated by per-month VC-wise PDFs.
            # FY summaries only have totals, no per-class breakdown, and
            # canonicalization between sources is imperfect — merging them
            # would create duplicate plaza entries for the same period.
            if monthly.get(period):
                log.info(
                    "  skipping FY period %s (already covered by VC-wise data)",
                    period,
                )
                continue
            sources_by_period[period].append(rel)
            for raw, info in plazas.items():
                resolved = resolver.resolve(raw)
                if not resolved:
                    continue
                canon, _ = resolved
                target = monthly[period].get(canon)
                if target is None:
                    target = monthly[period][canon] = {
                        "plaza_name": canon,
                        "plaza_slug": _slug(canon),
                        "piu": "",
                        "ro": "",
                        "vehicles": None,
                        "total": {"count": 0, "amount": 0.0},
                        "annual_pass": None,
                        "raw_names": set(),
                    }
                target["raw_names"].add(raw)
                # Only back-fill totals when this plaza-period has no per-class
                # data; otherwise the VC-wise extraction wins.
                if target["vehicles"] is None:
                    if target["total"] is None:
                        target["total"] = {"count": 0, "amount": 0.0}
                    target["total"]["count"] += int(info["total"]["count"])
                    target["total"]["amount"] = round(
                        target["total"]["amount"] + float(info["total"]["amount"]), 2
                    )

    # ----- Annual-Pass PDFs -----
    ap_pdfs = discover_annual_pass_pdfs()
    log.info("Found %d Annual-Pass PDFs (%d to parse)",
             len(ap_pdfs), sum(1 for p, _ in ap_pdfs if p not in reused))
    for period, pdf_path in ap_pdfs:
        if period in reused:
            continue
        log.info("Parsing Annual-Pass %s -> %s", pdf_path.name, period)
        ap_rows = parse_annual_pass(pdf_path)
        sources_by_period[period].append(str(pdf_path.relative_to(ROOT)).replace("\\", "/"))

        for raw, info in ap_rows.items():
            resolved = resolver.resolve(raw)
            if not resolved:
                continue
            canon, _ = resolved
            target = monthly[period].get(canon)
            if target is None:
                target = monthly[period][canon] = {
                    "plaza_name": canon,
                    "plaza_slug": _slug(canon),
                    "piu": info["piu"],
                    "ro": info["ro"],
                    "vehicles": None,
                    "total": None,
                    "annual_pass": None,
                    "raw_names": set(),
                }
            target["raw_names"].add(raw)
            if not target["piu"] and info["piu"]:
                target["piu"] = info["piu"]
            if not target["ro"] and info["ro"]:
                target["ro"] = info["ro"]
            ap = target["annual_pass"] or {"transaction_count": 0}
            ap["transaction_count"] += int(info["transaction_count"])
            target["annual_pass"] = ap

    # ----- Separate MLFF monthly JSON (not part of the ETC dashboard totals) -----
    mlff_reports = []
    for period, pdf_path in discover_mlff_pdfs():
        log.info("Parsing MLFF report %s -> %s", pdf_path.name, period)
        rows = parse_mlff_pdf(pdf_path)
        if not rows:
            log.warning("No plaza rows parsed from MLFF report %s", pdf_path.name)
        relative_path = str(pdf_path.relative_to(ROOT)).replace("\\", "/")
        doc = {
            "period": period,
            "year": int(period[:4]),
            "month": MONTH_NAMES[int(period[5:])],
            "source": relative_path,
            "report_type": "mlff_monthly",
            "plaza_count": len(rows),
            "plazas": rows,
        }
        (MLFF_DIR / f"{period}.json").write_text(
            json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        mlff_reports.append({
            "period": period,
            "source": relative_path,
            "path": f"mlff/{period}.json",
            "plaza_count": len(rows),
        })
    known_mlff_periods = {report["period"] for report in mlff_reports}
    for stale in MLFF_DIR.glob("*.json"):
        if stale.stem not in known_mlff_periods:
            stale.unlink()
            log.info("Pruned stale MLFF JSON %s", stale.name)

    # ----- Emit per-month files -----
    for period in sorted(monthly):
        year, mon = period.split("-")
        year_i, mon_i = int(year), int(mon)
        plazas_list = []
        for canon in sorted(monthly[period]):
            rec = monthly[period][canon]
            rec_out = {
                "plaza_name": rec["plaza_name"],
                "plaza_slug": rec["plaza_slug"],
                "piu": rec["piu"],
                "ro": rec["ro"],
                "vehicles": rec["vehicles"],
                "total": rec["total"],
                "annual_pass": rec["annual_pass"],
                "source_raw_names": sorted(rec["raw_names"]),
            }
            plazas_list.append(rec_out)
        doc = {
            "period": period,
            "year": year_i,
            "month": MONTH_NAMES[mon_i],
            "month_number": mon_i,
            "sources": sorted(set(sources_by_period.get(period, []))),
            "plaza_count": len(plazas_list),
            "plazas": plazas_list,
        }
        path = MONTHLY_DIR / f"{period}.json"
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Wrote %d monthly files -> %s", len(monthly), MONTHLY_DIR)

    # ----- Emit per-plaza files -----
    plaza_index: dict[str, dict] = {}
    for period in sorted(monthly):
        # Sorted, not insertion order: the slug de-duplication below hands out
        # "-2"/"-3" suffixes by first-seen order, so iteration order decides
        # which of two colliding plazas keeps the bare slug. Insertion order is
        # PDF row order when parsed but sorted order when rehydrated, which
        # would make incremental and --full disagree. Sorting pins it for both.
        for canon in sorted(monthly[period]):
            rec = monthly[period][canon]
            slot = plaza_index.setdefault(canon, {
                "plaza_name": canon,
                "plaza_slug": rec["plaza_slug"],
                "piu": rec["piu"],
                "ro": rec["ro"],
                "months": {},
                "raw_names": set(),
            })
            if not slot["piu"] and rec["piu"]:
                slot["piu"] = rec["piu"]
            if not slot["ro"] and rec["ro"]:
                slot["ro"] = rec["ro"]
            slot["raw_names"].update(rec["raw_names"])
            slot["months"][period] = {
                "vehicles": rec["vehicles"],
                "total": rec["total"],
                "annual_pass": rec["annual_pass"],
                "sources": sorted(set(sources_by_period.get(period, []))),
            }

    used_slugs: set[str] = set()
    for canon, slot in plaza_index.items():
        slug = slot["plaza_slug"] or "unknown"
        base = slug
        n = 2
        while slug in used_slugs:
            slug = f"{base}-{n}"
            n += 1
        used_slugs.add(slug)
        slot["plaza_slug"] = slug

        ordered_months = {p: slot["months"][p] for p in sorted(slot["months"])}
        doc = {
            "plaza_name": slot["plaza_name"],
            "plaza_slug": slug,
            "piu": slot["piu"],
            "ro": slot["ro"],
            "source_raw_names": sorted(slot["raw_names"]),
            "period_count": len(ordered_months),
            "months": ordered_months,
        }
        path = PLAZAS_DIR / f"{slug}.json"
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Wrote %d plaza files -> %s", len(plaza_index), PLAZAS_DIR)

    # plaza_index is rebuilt from every period each run, so any file not named
    # above belongs to a slug that no longer exists in the source data.
    stale = [p for p in PLAZAS_DIR.glob("*.json") if p.stem not in used_slugs]
    for p in stale:
        p.unlink()
    if stale:
        log.info("Pruned %d stale plaza file(s)", len(stale))

    # ----- Taxonomy (SPV / Round / Project / Plaza) -----
    canonical_plazas_list = sorted(plaza_index.keys())
    canonical_to_slug = {p: plaza_index[p]["plaza_slug"] for p in canonical_plazas_list}
    taxonomy_doc: dict = {"rows": [], "spvs": [], "rounds": [], "unmatched": []}
    if EXCEL_PATH_DEFAULT.exists():
        try:
            tx = load_taxonomy(EXCEL_PATH_DEFAULT, canonical_plazas_list)
            for r in tx["rows"]:
                r["plaza_slug"] = canonical_to_slug.get(r.get("canonical_plaza")) if r.get("canonical_plaza") else None
            taxonomy_doc = tx
            log.info(
                "Taxonomy: %d rows, %d SPVs, %d rounds, %d unmatched",
                len(tx["rows"]), len(tx["spvs"]), len(tx["rounds"]), len(tx["unmatched"]),
            )
        except Exception as exc:
            log.warning("Taxonomy load failed: %s", exc)
    else:
        log.warning("Taxonomy Excel not found at %s — skipping", EXCEL_PATH_DEFAULT)

    (OUT_DIR / "_taxonomy.json").write_text(
        json.dumps(taxonomy_doc, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # ----- Index -----
    # Rehydrated periods carry their warning count forward from the cache;
    # only periods parsed this run can contribute a new one.
    total_warnings = sum(warnings_by_period.values())
    BUILD_CACHE.write_text(
        json.dumps(
            {"warnings_by_period": {p: warnings_by_period.get(p, 0) for p in sorted(monthly)}},
            indent=2,
        ),
        encoding="utf-8",
    )

    index_doc = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "periods": sorted(monthly),
        "mlff_reports": mlff_reports,
        "plaza_count": len(plaza_index),
        "plazas": [
            {
                "plaza_name": slot["plaza_name"],
                "plaza_slug": slot["plaza_slug"],
                "piu": slot["piu"],
                "ro": slot["ro"],
                "periods": sorted(slot["months"].keys()),
            }
            for slot in sorted(plaza_index.values(), key=lambda s: s["plaza_name"].lower())
        ],
        "sources_by_period": {p: sorted(set(s)) for p, s in sources_by_period.items()},
        "validation_warnings": total_warnings,
        "unmapped_to_nhit_canonical": sorted(resolver.unmapped_to_nhit),
    }
    (OUT_DIR / "_index.json").write_text(
        json.dumps(index_doc, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    log.info(
        "Done. periods=%d (%d reused, %d parsed) plazas=%d validation_warnings=%d",
        len(monthly), len(reused), len(monthly) - len(reused),
        len(plaza_index), total_warnings,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
