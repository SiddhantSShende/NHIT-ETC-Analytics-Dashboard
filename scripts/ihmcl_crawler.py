"""
NHIT | IHMCL ETC-transaction-reports crawler.

Scans https://ihmcl.co.in/etc-transaction-reports/ for monthly report PDFs and
downloads the ones whose period is not yet present locally:

    VC-wise monthly ETC   -> downloads/vc_monthly/
    Monthly Annual Pass   -> downloads/Monthly_Annual_Pass_Report/<year>/
    MLFF plaza data       -> downloads/MLFF_Plaza_Data/   (archive only,
                             not consumed by build_json_export.py)
    unrecognized monthly  -> downloads/unclassified/      (archive only, and
                             logged at WARNING — see classify())

Comparison is by period (YYYY-MM) per family, mirroring the discovery
semantics of scripts/build_json_export.py, so IHMCL filename churn
("Aug-2025-ETC-Data.pdf", "...-2.pdf" re-uploads) never creates duplicates.
Archive-only families diff by filename instead, having no period semantics.

Run:  python scripts/ihmcl_crawler.py [--dry-run] [--verbose]
Exit: 0 ok (possibly nothing new), 1 fatal (page unreachable / layout
      tripwire), 2 partial (some downloads failed).
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter, Retry

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from build_json_export import period_from_filename  # noqa: E402

log = logging.getLogger("ihmcl_crawler")

REPORTS_URL = "https://ihmcl.co.in/etc-transaction-reports/"
# The site 403s non-browser user agents; a plain Chrome UA gets through.
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

DOWNLOADS = ROOT / "downloads"
VC_MONTHLY_DIR = DOWNLOADS / "vc_monthly"
ETC_MONTHLY_DIR = DOWNLOADS / "ETC_Monthly_Data"
ANNUAL_PASS_DIR = DOWNLOADS / "Monthly_Annual_Pass_Report"
MLFF_DIR = DOWNLOADS / "MLFF_Plaza_Data"
# Monthly-looking PDFs we have no parser for. Kept out of PDF_DATA_DIRS in
# update_and_publish.py, so these are archived locally and never committed.
UNCLASSIFIED_DIR = DOWNLOADS / "unclassified"

# Non-data documents that also live on the page.
EXCLUDE_RE = re.compile(r"ANNUAL-RETURN|ANNUAL-REPORT|FAQ", re.I)
# Monthly reports are the only files named <month-token><sep><4-digit-year>...
MONTH_START_RE = re.compile(
    r"^(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*[-_\s]*\d{4}",
    re.I,
)
ANNUAL_PASS_RE = re.compile(r"annual[-_\s]*pass", re.I)
HREF_PDF_RE = re.compile(r'href=["\']([^"\']+\.pdf)["\']', re.I)

MIN_PDF_BYTES = 10_240
# The page always lists a year-plus of monthly links; fewer classified means
# the layout changed and "nothing new" cannot be trusted.
MIN_EXPECTED_MONTHLY = 5


@dataclass
class RemoteReport:
    url: str
    filename: str
    family: str  # "etc" | "annual_pass" | "mlff"
    period: str | None  # YYYY-MM for monthly families, None for mlff


@dataclass
class LocalState:
    etc: dict[str, str]  # period -> filename already on disk
    ap: dict[str, str]
    mlff_names: set[str]
    unclassified_names: set[str]


@dataclass
class CrawlResult:
    planned: list[RemoteReport] = field(default_factory=list)
    downloaded: list[tuple[RemoteReport, Path]] = field(default_factory=list)
    variants: list[RemoteReport] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    fatal: str | None = None


def dest_dir(report: RemoteReport) -> Path:
    if report.family == "unclassified":
        return UNCLASSIFIED_DIR
    if report.family == "etc":
        return VC_MONTHLY_DIR
    if report.family == "annual_pass":
        return ANNUAL_PASS_DIR / report.period[:4]
    return MLFF_DIR


def build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update(
        {
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/pdf,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    return session


def fetch_reports_page(session: requests.Session) -> str:
    resp = session.get(REPORTS_URL, timeout=(10, 30))
    resp.raise_for_status()
    return resp.text


def extract_pdf_links(html: str) -> list[str]:
    seen: set[str] = set()
    links: list[str] = []
    for href in HREF_PDF_RE.findall(html):
        url = urljoin(REPORTS_URL, href)
        if url not in seen:
            seen.add(url)
            links.append(url)
    return links


def classify(url: str) -> RemoteReport | None:
    name = unquote(urlparse(url).path.rsplit("/", 1)[-1])
    if EXCLUDE_RE.search(name):
        return None
    if name.upper().startswith("MLFF"):
        return RemoteReport(url, name, "mlff", None)
    period = period_from_filename(name)
    if not MONTH_START_RE.match(name):
        # MONTH_START_RE is anchored, so a report only counts as monthly when
        # its name *starts* with a month. If IHMCL renamed the main report to
        # something like VC_Wise_Monthly_Data_June_2026.pdf, every month would
        # fail that test, the tripwire below would still be satisfied by the
        # older links left on the page, and the crawler would report "0 new"
        # and exit 0 forever while the dashboard quietly froze. A name that
        # carries a month+year but fails the anchor is exactly that case, so
        # archive it and say so loudly rather than dropping it at DEBUG.
        if period:
            return RemoteReport(url, name, "unclassified", period)
        log.debug("Ignoring non-monthly PDF: %s", name)
        return None
    if not period:
        log.warning("Monthly-looking file without parseable period: %s", name)
        return None
    family = "annual_pass" if ANNUAL_PASS_RE.search(name) else "etc"
    return RemoteReport(url, name, family, period)


def local_state() -> LocalState:
    etc: dict[str, str] = {}
    # vc_monthly scanned first: it wins period collisions, mirroring
    # discover_vc_pdfs() in build_json_export.py.
    for p in sorted(VC_MONTHLY_DIR.glob("*.pdf")):
        period = period_from_filename(p.name)
        if period:
            etc.setdefault(period, p.name)
    if ETC_MONTHLY_DIR.exists():
        for year_dir in sorted(ETC_MONTHLY_DIR.iterdir()):
            if not year_dir.is_dir():
                continue
            for p in sorted(year_dir.glob("*.pdf")):
                if "FY" in p.name.upper():
                    continue  # 12-month FY summaries, not period files
                period = period_from_filename(p.name)
                if period:
                    etc.setdefault(period, p.name)

    ap: dict[str, str] = {}
    if ANNUAL_PASS_DIR.exists():
        for year_dir in sorted(ANNUAL_PASS_DIR.iterdir()):
            if not year_dir.is_dir():
                continue
            for p in sorted(year_dir.glob("*.pdf")):
                if "FAQ" in p.name.upper():
                    continue
                period = period_from_filename(p.name)
                if period:
                    ap.setdefault(period, p.name)

    mlff = {p.name for p in MLFF_DIR.glob("*.pdf")} if MLFF_DIR.exists() else set()
    unclassified = (
        {p.name for p in UNCLASSIFIED_DIR.glob("*.pdf")}
        if UNCLASSIFIED_DIR.exists() else set()
    )
    return LocalState(etc, ap, mlff, unclassified)


def plan_downloads(
    reports: list[RemoteReport], local: LocalState
) -> tuple[list[RemoteReport], list[RemoteReport]]:
    """Split remote reports into (to_download, variants).

    A variant is a remote file whose period is already covered locally under a
    *different* filename (IHMCL re-upload like "-2.pdf") — surfaced as a WARN
    for a human, never auto-downloaded. Mutates `local` so duplicate links on
    one page are only planned once.
    """
    to_download: list[RemoteReport] = []
    variants: list[RemoteReport] = []
    for r in reports:
        if r.family in ("mlff", "unclassified"):
            # Archive-only families diff by filename: nothing parses them, so
            # there is no period to reason about and a re-upload under a new
            # name is simply another file to keep.
            seen = local.mlff_names if r.family == "mlff" else local.unclassified_names
            if r.filename not in seen:
                to_download.append(r)
                seen.add(r.filename)
            continue
        have = local.etc if r.family == "etc" else local.ap
        existing = have.get(r.period)
        if existing is None:
            to_download.append(r)
            have[r.period] = r.filename
        elif existing != r.filename:
            variants.append(r)
    return to_download, variants


def _replace_with_retry(src: Path, dst: Path, attempts: int = 3, delay: float = 2.0) -> None:
    # OneDrive's sync client can hold transient locks on freshly written files.
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(delay)


def download_one(session: requests.Session, report: RemoteReport) -> Path:
    dest = dest_dir(report)
    dest.mkdir(parents=True, exist_ok=True)
    final = dest / report.filename
    if final.exists():
        log.info("Already on disk, skipping: %s", final)
        return final
    part = dest / f".{report.filename}.part"
    try:
        with session.get(report.url, stream=True, timeout=(10, 120)) as resp:
            resp.raise_for_status()
            with open(part, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    fh.write(chunk)
                fh.flush()
                os.fsync(fh.fileno())
        size = part.stat().st_size
        with open(part, "rb") as fh:
            magic = fh.read(5)
        if magic != b"%PDF-":
            raise ValueError(
                f"not a PDF (starts with {magic!r}) — likely an HTML error page"
            )
        if size < MIN_PDF_BYTES:
            raise ValueError(f"suspiciously small PDF ({size} bytes)")
        _replace_with_retry(part, final)
        log.info("Downloaded %s (%d bytes) -> %s", report.filename, size, final)
        return final
    except Exception:
        part.unlink(missing_ok=True)
        raise


def crawl(dry_run: bool = False) -> CrawlResult:
    result = CrawlResult()
    session = build_session()
    try:
        html = fetch_reports_page(session)
        links = extract_pdf_links(html)
        reports = [r for r in (classify(u) for u in links) if r]
    except Exception as exc:
        result.fatal = f"Failed to fetch/parse reports page: {exc}"
        log.error(result.fatal)
        return result

    monthly_count = sum(1 for r in reports if r.family in ("etc", "annual_pass"))
    if monthly_count < MIN_EXPECTED_MONTHLY:
        result.fatal = (
            f"Layout tripwire: only {monthly_count} monthly report links classified "
            f"(expected >= {MIN_EXPECTED_MONTHLY}). Page structure may have changed; "
            "refusing to report 'up to date'."
        )
        log.error(result.fatal)
        return result

    for r in reports:
        if r.family == "unclassified":
            # ASCII only: the console stream this is captured on under Task
            # Scheduler is cp1252, and non-ASCII lands in scheduled_runs.log
            # as mojibake.
            log.warning(
                "Unrecognized monthly-looking PDF: %s (period %s) - archiving to "
                "%s/, NOT ingested. If IHMCL has renamed the main monthly report, "
                "teach classify() the new name or the dashboard will stop updating.",
                r.filename, r.period, UNCLASSIFIED_DIR.name,
            )

    result.planned, result.variants = plan_downloads(reports, local_state())
    for v in result.variants:
        log.warning(
            "Variant for already-covered period %s: %s (not downloading)",
            v.period,
            v.filename,
        )

    log.info(
        "%d monthly report links on page, %d new to download",
        monthly_count,
        len(result.planned),
    )
    for r in result.planned:
        log.info("  plan: [%s] %s -> %s", r.family, r.filename, dest_dir(r))

    if dry_run:
        return result

    for r in result.planned:
        try:
            result.downloaded.append((r, download_one(session, r)))
        except Exception as exc:
            msg = f"Download failed for {r.filename}: {exc}"
            log.error(msg)
            result.errors.append(msg)
        time.sleep(1.5)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Download new IHMCL ETC report PDFs into downloads/."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="list what would be downloaded"
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        force=True,  # build_json_export configures logging at import time
    )
    result = crawl(dry_run=args.dry_run)
    if result.fatal:
        return 1
    if args.dry_run:
        print(f"DRY RUN: {len(result.planned)} file(s) would be downloaded")
        return 0
    print(
        f"Downloaded {len(result.downloaded)} file(s); {len(result.errors)} error(s)"
    )
    return 2 if result.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
