"""
NHIT | Build pre-computed analytics from the local PDF reports.

Reads every monthly VC-Wise PDF in downloads/vc_monthly/ and writes:

    data/snapshot.json   - in-memory-friendly analytics dump for the API
    data/etc.db          - normalised SQLite mirror for ad-hoc querying

Run once after dropping new PDFs into downloads/vc_monthly/:

    python build_data.py            # build (skip if files already match PDFs)
    python build_data.py --force    # rebuild from scratch

The server reads only the JSON snapshot at startup; the SQLite DB is
provided as a queryable artefact for analysts.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from constants import MONTH_MAP, MONTH_NAMES, NHIT_PLAZAS
from parser import parse_pdf
from taxonomy import load_taxonomy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("nhit.build")

DIR = Path(__file__).parent
DOWNLOADS_DIR = DIR / "downloads"
DATA_DIR = DIR / "data"
SNAPSHOT_PATH = DATA_DIR / "snapshot.json"
DB_PATH = DATA_DIR / "etc.db"

NHIT_LOWER = [p.lower().strip() for p in NHIT_PLAZAS]

_PDF_NAME_RE = re.compile(
    r"^(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[-_]?(20\d{2})",
    re.IGNORECASE,
)


# Words that occur in nearly every plaza name and would cause spurious
# substring/token matches if kept (e.g. "Bhadarabad TOLL PLAZA" must not
# match "3M Toll Plaza" just because they share the words "toll plaza").
_PLAZA_STOPWORDS = {"toll", "plaza", "fee", "ro"}


def _plaza_key(s: str) -> str:
    """Strip plaza/toll/fee stopwords and collapse to alphanumeric tokens."""
    tokens = [
        t for t in re.findall(r"[a-z0-9]+", s.lower())
        if t not in _PLAZA_STOPWORDS
    ]
    return " ".join(tokens)


# ── Plaza name reconciliation ───────────────────────────────────────────────
def match_nhit_plaza(raw_name: str) -> Optional[str]:
    """Map a raw PDF plaza string to its canonical NHIT entry, or None.

    Stopwords like "toll" and "plaza" are stripped first so that two plazas
    aren't matched to each other simply because both end in "TOLL PLAZA".
    """
    if raw_name is None:
        return None
    raw_key = _plaza_key(raw_name)
    if not raw_key:
        return None
    if raw_key in _CANON_KEYS:
        return _CANON_KEYS[raw_key]
    # Tight substring match on the cleaned keys (catches small typos like
    # "kalajhar"/"kalajhar toll plaza").
    for canon_key, canon_name in _CANON_KEYS.items():
        if canon_key == raw_key or canon_key in raw_key or raw_key in canon_key:
            # Avoid empty-side matches and very short keys (< 4 chars) that
            # could swallow many plazas.
            if min(len(canon_key), len(raw_key)) >= 4:
                return canon_name
    return raw_name


# Pre-compute canonical key -> canonical name once so match_nhit_plaza is O(1)
# in the common path. Built lazily to allow test-time monkey-patching of
# NHIT_PLAZAS if ever needed.
_CANON_KEYS: dict[str, str] = {_plaza_key(p): p for p in NHIT_PLAZAS}


# ── PDF discovery ───────────────────────────────────────────────────────────
def find_pdfs() -> list[dict]:
    """Discover monthly PDFs under DOWNLOADS_DIR, oldest first."""
    out: list[dict] = []
    for f in DOWNLOADS_DIR.rglob("*.pdf"):
        m = _PDF_NAME_RE.match(f.name.lower())
        if not m:
            continue
        month = MONTH_MAP.get(m.group(1).lower())
        year = int(m.group(2))
        if month and year:
            out.append({"year": year, "month": month, "path": f})
    out.sort(key=lambda x: (x["year"], x["month"]))
    return out


# ── Per-plaza record builder ────────────────────────────────────────────────
def _bucket_rows_by_canonical_plaza(rows: list[dict]) -> dict[str, dict]:
    """Pre-bucket every parsed row by its canonical NHIT plaza so that the
    per-plaza record builder is O(rows) instead of O(rows * plazas)."""
    cache: dict[str, str | None] = {}
    out: dict[str, dict[str, dict]] = {}
    for r in rows:
        raw = r["plaza_name"]
        canon = cache.get(raw)
        if canon is None and raw not in cache:
            canon = match_nhit_plaza(raw)
            cache[raw] = canon
        if not canon:
            continue
        plaza_bucket = out.setdefault(canon, {})
        d = plaza_bucket.setdefault(r["vehicle_category"], {"count": 0, "amount": 0.0})
        d["count"]  += r["count"]
        d["amount"] += r["amount"]
    return out


def _build_record_from_bucket(cat_map: dict[str, dict], plaza: str, year: int, month: int):
    """Turn a category->totals map into the dashboard record."""
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
            "name":   c["name"],
            "count":  c["count"],
            "amount": c["amount"],
            "share_count":  round(c["count"]  / total_count  * 100, 2) if total_count  else 0.0,
            "share_amount": round(c["amount"] / total_amount * 100, 2) if total_amount else 0.0,
            "avg_fare":     round(c["amount"] / c["count"], 2) if c["count"] else 0.0,
        })

    top_amt = max(enriched, key=lambda c: c["amount"])
    top_cnt = max(enriched, key=lambda c: c["count"])

    return {
        "plaza":         plaza,
        "year":          year,
        "month":         month,
        "month_name":    MONTH_NAMES[month],
        "categories":    enriched,
        "total_count":   total_count,
        "total_amount":  total_amount,
        "avg_per_txn":   round(total_amount / total_count, 2) if total_count else 0.0,
        "avg_count_per_day":   int(round(total_count / 30.0)) if total_count else 0,
        "avg_revenue_per_day": round(total_amount / 30.0, 2) if total_amount else 0.0,
        "category_count": len(enriched),
        "top_by_amount": {"name": top_amt["name"], "amount": top_amt["amount"]},
        "top_by_count":  {"name": top_cnt["name"], "count":  top_cnt["count"]},
    }


# ── Validation ───────────────────────────────────────────────────────────────
def _validate_against_totals(label: str, rows: list[dict], pdf_totals: dict) -> list[str]:
    """Sum parsed cnt/amt per plaza and compare to the PDF's TOTAL_* cells.
    Anything off by more than 1% (or > Rs. 100) is reported as a warning."""
    sums: dict[str, dict] = {}
    for r in rows:
        d = sums.setdefault(r["plaza_name"], {"cnt": 0, "amt": 0.0})
        d["cnt"] += r["count"]
        d["amt"] += r["amount"]

    warnings: list[str] = []
    for plaza_raw, tot in pdf_totals.items():
        s = sums.get(plaza_raw, {"cnt": 0, "amt": 0.0})
        cnt_pdf = tot["count"]
        amt_pdf = tot["amount"]
        if cnt_pdf and abs(s["cnt"] - cnt_pdf) / max(cnt_pdf, 1) > 0.01:
            warnings.append(
                f"{label} | {plaza_raw}: parsed count {s['cnt']:,} vs PDF total {cnt_pdf:,}"
            )
        if amt_pdf and abs(s["amt"] - amt_pdf) > max(100.0, amt_pdf * 0.01):
            warnings.append(
                f"{label} | {plaza_raw}: parsed amount {s['amt']:.2f} vs PDF total {amt_pdf:.2f}"
            )
    return warnings


# ── SQLite schema ───────────────────────────────────────────────────────────
SCHEMA = """
CREATE TABLE plazas (
    id    INTEGER PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE
);

CREATE TABLE reports (
    id          INTEGER PRIMARY KEY,
    year        INTEGER NOT NULL,
    month       INTEGER NOT NULL,
    source_pdf  TEXT    NOT NULL,
    parsed_at   TEXT    NOT NULL,
    UNIQUE(year, month)
);

CREATE TABLE transactions (
    plaza_id          INTEGER NOT NULL REFERENCES plazas(id),
    report_id         INTEGER NOT NULL REFERENCES reports(id),
    vehicle_category  TEXT    NOT NULL,
    count             INTEGER NOT NULL,
    amount            REAL    NOT NULL,
    PRIMARY KEY(plaza_id, report_id, vehicle_category)
);

CREATE INDEX idx_tx_plaza  ON transactions(plaza_id);
CREATE INDEX idx_tx_report ON transactions(report_id);
"""


# Top-level worker so it can be pickled by ProcessPoolExecutor.
def _parse_one(entry: dict):
    """Parse one PDF in a child process. Returns the entry plus the parsed data."""
    rows, pdf_totals = parse_pdf(entry["path"])
    return {**entry, "rows": rows, "pdf_totals": pdf_totals}


# ── Main build pipeline ─────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="Rebuild even if snapshot.json is fresh.")
    ap.add_argument("--workers", type=int,
                    default=min(os.cpu_count() or 4, 8),
                    help="Parallel PDF-parsing workers (default: min(8, cpu_count)).")
    args = ap.parse_args(argv)

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    pdfs = find_pdfs()
    # Deduplicate PDFs by (year, month). If multiple exist, take the first found.
    unique_pdfs = {}
    for entry in pdfs:
        key = (entry["year"], entry["month"])
        if key not in unique_pdfs:
            unique_pdfs[key] = entry
    pdfs = list(unique_pdfs.values())
    pdfs.sort(key=lambda x: (x["year"], x["month"]))

    if not pdfs:
        log.error("No PDF reports found in %s", DOWNLOADS_DIR)
        return 1

    if SNAPSHOT_PATH.exists() and not args.force:
        snap_mtime = SNAPSHOT_PATH.stat().st_mtime
        latest_pdf = max(p["path"].stat().st_mtime for p in pdfs)
        if snap_mtime > latest_pdf:
            log.info("snapshot.json is up-to-date, skipping (use --force to rebuild)")
            return 0

    log.info("Building from %d PDFs (%d workers) in %s",
             len(pdfs), args.workers, DOWNLOADS_DIR)

    # Parse every PDF in parallel — pdfplumber is CPU bound, so a process pool
    # gives a near-linear speedup on multi-core machines. We collect parsed
    # results and process them in chronological order afterwards.
    parsed: list[dict] = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_parse_one, e): e for e in pdfs}
        for fut in as_completed(futures):
            entry = futures[fut]
            label = f"{MONTH_NAMES[entry['month']]} {entry['year']}"
            try:
                parsed.append(fut.result())
                log.info("  parsed %s (%s)", label, entry["path"].name)
            except Exception as e:
                log.error("  FAILED %s: %s", label, e)
    parsed.sort(key=lambda x: (x["year"], x["month"]))

    all_plazas = set(NHIT_PLAZAS)
    for entry in parsed:
        for r in entry.get("rows", []):
            canon = match_nhit_plaza(r["plaza_name"])
            if canon:
                all_plazas.add(canon)
    all_plazas_list = sorted(list(all_plazas))

    # Reset SQLite.
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    plaza_ids: dict[str, int] = {}
    for p in all_plazas_list:
        cur = conn.execute("INSERT INTO plazas(name) VALUES(?)", (p,))
        plaza_ids[p] = cur.lastrowid

    # Load the SPV/Round/Project/Plaza taxonomy from data/Project details.xlsx
    # so cascading dropdowns + aggregations stay driven by the analyst's source
    # spreadsheet rather than a hardcoded table.
    try:
        taxonomy = load_taxonomy(canonical_plazas=all_plazas_list)
        log.info(
            "Taxonomy: %d rows, SPVs=%s, rounds=%s, unmatched=%s",
            len(taxonomy["rows"]), taxonomy["spvs"],
            taxonomy["rounds"], taxonomy["unmatched"],
        )
    except FileNotFoundError as e:
        log.warning("Taxonomy unavailable (%s) — proceeding without it.", e)
        taxonomy = {"rows": [], "spvs": [], "rounds": [], "unmatched": []}

    known_canon_in_tax = {r["canonical_plaza"] for r in taxonomy["rows"] if r.get("canonical_plaza")}
    extra_plazas = [p for p in all_plazas_list if p not in known_canon_in_tax]
    if extra_plazas:
        for p in extra_plazas:
            taxonomy["rows"].append({
                "s_no": 9999,
                "spv": "Unknown SPV",
                "round": "Unknown Round",
                "project": "Unknown Project",
                "excel_plaza": p,
                "canonical_plaza": p,
            })
        if "Unknown SPV" not in taxonomy["spvs"]:
            taxonomy["spvs"].append("Unknown SPV")
            taxonomy["spvs"].sort()
        if "Unknown Round" not in taxonomy["rounds"]:
            taxonomy["rounds"].append("Unknown Round")
            taxonomy["rounds"].sort()


    snapshot = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "plazas": all_plazas_list,
        "months": [],                       # all (year, month) pairs available
        "available_months": [],             # unique month names for the dropdown
        "years": [],
        "data": {p: {} for p in all_plazas_list},
        "monthly_totals": {p: [] for p in all_plazas_list},
        "validation_warnings": [],
        "taxonomy": taxonomy,
    }

    for entry in parsed:
        y, m, path = entry["year"], entry["month"], entry["path"]
        label = f"{MONTH_NAMES[m]} {y}"
        rows, pdf_totals = entry["rows"], entry["pdf_totals"]
        warnings = _validate_against_totals(label, rows, pdf_totals)
        if warnings:
            for w in warnings[:5]:
                log.warning(w)
            if len(warnings) > 5:
                log.warning("  …and %d more", len(warnings) - 5)
            snapshot["validation_warnings"].extend(warnings)

        snapshot["months"].append({
            "year": y, "month": m, "name": MONTH_NAMES[m],
            "label": f"{MONTH_NAMES[m][:3]} {y}",
        })

        # Insert into reports - ignore if it already exists
        cur = conn.execute(
            "INSERT OR IGNORE INTO reports(year,month,source_pdf,parsed_at) VALUES(?,?,?,?)",
            (y, m, path.name, datetime.now(timezone.utc).isoformat()),
        )
        cur = conn.execute("SELECT id FROM reports WHERE year=? AND month=?", (y, m))
        report_id = cur.fetchone()[0]

        plaza_buckets = _bucket_rows_by_canonical_plaza(rows)
        for plaza in NHIT_PLAZAS:
            cat_map = plaza_buckets.get(plaza)
            rec = _build_record_from_bucket(cat_map or {}, plaza, y, m)
            if not rec:
                continue
            key = f"{y}-{m:02d}"
            snapshot["data"][plaza][key] = rec
            snapshot["monthly_totals"][plaza].append({
                "year": y, "month": m,
                "month_name": MONTH_NAMES[m],
                "label": f"{MONTH_NAMES[m][:3]} {y}",
                "count":  rec["total_count"],
                "amount": rec["total_amount"],
            })
            for c in rec["categories"]:
                try:
                    conn.execute(
                        "INSERT INTO transactions(plaza_id, report_id, vehicle_category, count, amount) "
                        "VALUES(?,?,?,?,?)",
                        (plaza_ids[plaza], report_id, c["name"], c["count"], c["amount"]),
                    )
                except sqlite3.IntegrityError:
                    pass

    # Sort everything for deterministic output.
    snapshot["months"].sort(key=lambda x: (x["year"], x["month"]), reverse=True)
    snapshot["years"] = sorted({m["year"] for m in snapshot["months"]}, reverse=True)
    mset = sorted({(m["month"], m["name"]) for m in snapshot["months"]})
    snapshot["available_months"] = [{"num": x[0], "name": x[1]} for x in mset]
    for plaza, lst in snapshot["monthly_totals"].items():
        lst.sort(key=lambda x: (x["year"], x["month"]))

    conn.commit()
    conn.close()

    SNAPSHOT_PATH.write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    populated = sum(1 for p, months in snapshot["data"].items() if months)
    log.info(
        "Done. plazas_with_data=%d  months=%d  warnings=%d  snapshot=%.1f KB  db=%.1f KB",
        populated, len(snapshot["months"]), len(snapshot["validation_warnings"]),
        SNAPSHOT_PATH.stat().st_size / 1024, DB_PATH.stat().st_size / 1024,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
