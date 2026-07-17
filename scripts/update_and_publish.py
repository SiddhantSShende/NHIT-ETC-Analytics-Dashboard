"""
NHIT | Crawl IHMCL -> rebuild JSON -> publish (git push -> Vercel deploy).

The single entry point for scheduled data refreshes. The Windows Task
Scheduler job ("NHIT ETC Data Refresh", monthly on the 15th) runs this; the
GitHub Actions workflow is a manual fallback only, because ihmcl.co.in
403-blocks GitHub's datacenter IPs. Steps:

    1. git pull --rebase          (converge with anything pushed elsewhere;
                                   best-effort)
    2. crawl IHMCL                (scripts/ihmcl_crawler.py — period-based
                                   diff, downloads only what is missing)
    3. rebuild JSON               (scripts/build_json_export.py — incremental:
                                   parses only periods with no monthly JSON,
                                   ~90s for a typical month)
    4. sanity gate                (new periods present, plaza_count > 0,
                                   validation_warnings did not increase)
    5. commit + push              (state-based staging: untracked report PDFs
                                   are force-added, downloads/json re-staged —
                                   so a crashed or unpushed previous run is
                                   picked up and completed by the next one)

Nothing is committed or pushed if any earlier step fails, so the dashboard
keeps serving the last good data.

Run:  python scripts/update_and_publish.py
          [--dry-run] [--no-push] [--force-build] [--allow-warnings]
Exit: 0 ok (including "nothing new"), 1 failure (nothing published),
      2 published but some downloads failed (partial).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from build_json_export import period_from_filename  # noqa: E402
from ihmcl_crawler import crawl  # noqa: E402

log = logging.getLogger("update_and_publish")

LOGS_DIR = ROOT / "logs"
LOCK_FILE = LOGS_DIR / "update.lock"
LOCK_STALE_SECONDS = 4 * 3600
INDEX_JSON = ROOT / "downloads" / "json" / "_index.json"
MONTHLY_DIR = ROOT / "downloads" / "json" / "monthly"
BUILD_TIMEOUT = 7200  # seconds. The incremental build only parses periods
# with no monthly JSON yet, so a normal month costs ~90s. This ceiling is
# sized for build_json_export.py --full (~105 min: pdfplumber table
# extraction over every PDF), which a human may run after a parser change.

# Directories whose PDFs are data sources; gitignored but force-added so the
# repo keeps a complete archive. This matters beyond archival: the GitHub
# Actions runner rebuilds from a fresh checkout, so every source PDF must be
# in the repo or its rebuild would silently lose months.
PDF_DATA_DIRS = [
    "downloads/vc_monthly",
    "downloads/ETC_Monthly_Data",
    "downloads/Monthly_Annual_Pass_Report",
    "downloads/MLFF_Plaza_Data",
]

# A credential prompt inside a scheduled task would hang invisibly — fail fast.
GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}


def setup_logging() -> None:
    LOGS_DIR.mkdir(exist_ok=True)
    logfile = LOGS_DIR / f"update_{datetime.now():%Y%m%d_%H%M%S}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.FileHandler(logfile, encoding="utf-8"),
            logging.StreamHandler(),
        ],
        force=True,  # build_json_export configures logging at import time
    )
    log.info("Log file: %s", logfile)


def acquire_lock() -> bool:
    LOGS_DIR.mkdir(exist_ok=True)
    try:
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        age = time.time() - LOCK_FILE.stat().st_mtime
        if age > LOCK_STALE_SECONDS:
            log.warning("Stale lock (%.1f h old) — taking over.", age / 3600)
            LOCK_FILE.unlink(missing_ok=True)
            return acquire_lock()
        log.error("Another update appears to be running (lock: %s).", LOCK_FILE)
        return False


def release_lock() -> None:
    LOCK_FILE.unlink(missing_ok=True)


def git(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, env=GIT_ENV
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}"
        )
    return proc


def preflight() -> None:
    branch = git(["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    if branch != "main":
        raise RuntimeError(f"On branch {branch!r}, expected 'main' — aborting.")
    if not git(["config", "user.email"], check=False).stdout.strip():
        raise RuntimeError("git user.email is not configured — cannot commit.")
    git(["remote", "get-url", "origin"])


def pull_latest() -> None:
    """Best-effort converge with origin before deciding what's new."""
    proc = git(["pull", "--rebase", "--autostash", "origin", "main"], check=False)
    if proc.returncode != 0:
        git(["rebase", "--abort"], check=False)
        log.warning(
            "Initial git pull failed (continuing with local state): %s",
            proc.stderr.strip(),
        )
    else:
        log.info("Pulled latest from origin/main.")


def untracked_data_pdfs() -> list[str]:
    """Report PDFs on disk not yet committed (ignored or plain untracked).

    This is what makes runs resumable: a previous run that downloaded files
    but crashed before pushing leaves them here for the next run to finish.
    """
    files: set[str] = set()
    for extra in ([], ["--ignored"]):
        out = git(
            ["ls-files", "--others", "--exclude-standard", *extra, "--", *PDF_DATA_DIRS]
        ).stdout
        files.update(
            line.strip()
            for line in out.splitlines()
            if line.strip().lower().endswith(".pdf")
        )
    return sorted(files)


def commits_ahead() -> int:
    git(["fetch", "origin", "main"], check=False)
    out = git(["rev-list", "--count", "origin/main..HEAD"], check=False).stdout.strip()
    return int(out) if out.isdigit() else 0


def read_index() -> dict:
    if not INDEX_JSON.exists():
        return {}
    return json.loads(INDEX_JSON.read_text(encoding="utf-8"))


def run_build() -> None:
    log.info("Rebuilding JSON exports (parsing only periods with no JSON yet)...")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_json_export.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=BUILD_TIMEOUT,
    )
    tail = "\n".join((proc.stdout + "\n" + proc.stderr).strip().splitlines()[-12:])
    log.info("Build output (tail):\n%s", tail)
    if proc.returncode != 0:
        raise RuntimeError(f"build_json_export.py exited {proc.returncode}")


def sanity_check(
    new_periods: set[str],
    prev_periods: set[str],
    prev_warnings: int,
    allow_warnings: bool,
) -> None:
    idx = read_index()
    periods = set(idx.get("periods", []))
    # A rebuild must never lose a month. This catches builds from an
    # incomplete source set (missing PDFs on a CI runner, OneDrive
    # dehydration, deleted directories) before they reach production.
    lost = prev_periods - periods
    if lost:
        raise RuntimeError(
            f"Rebuild lost existing periods: {', '.join(sorted(lost))} — "
            "source PDFs are missing. Blocking publish."
        )
    for p in sorted(new_periods):
        if p not in periods:
            raise RuntimeError(f"Period {p} missing from rebuilt _index.json.")
        month_file = MONTHLY_DIR / f"{p}.json"
        if not month_file.exists():
            raise RuntimeError(f"{month_file.name} was not generated.")
        doc = json.loads(month_file.read_text(encoding="utf-8"))
        if not doc.get("plaza_count"):
            raise RuntimeError(f"{month_file.name} has plaza_count 0 — bad parse.")
        log.info("Sanity OK: %s (%d plazas)", p, doc["plaza_count"])
    warnings = int(idx.get("validation_warnings", 0))
    if warnings > prev_warnings:
        msg = (
            f"validation_warnings rose {prev_warnings} -> {warnings}; "
            "new PDF may not match the expected layout."
        )
        if allow_warnings:
            log.warning("%s (--allow-warnings set, continuing)", msg)
        else:
            raise RuntimeError(msg + " Blocking publish (use --allow-warnings to override).")


def publish(new_periods: set[str]) -> None:
    for f in untracked_data_pdfs():
        git(["add", "-f", "--", f])
    git(["add", "-A", "--", "downloads/json"])
    if git(["diff", "--cached", "--quiet"], check=False).returncode == 0:
        log.info("Nothing staged — repository already reflects current data.")
    else:
        label = ", ".join(sorted(new_periods)) if new_periods else "report archive"
        msg = (
            f"data: auto-update {label} ({datetime.now():%Y-%m-%d})\n\n"
            "Automated IHMCL crawl via scripts/update_and_publish.py\n\n"
            "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
        )
        git(["commit", "-m", msg])
        log.info("Committed: data: auto-update %s", label)

    if commits_ahead() == 0:
        log.info("Nothing to push.")
        return
    proc = git(["pull", "--rebase", "--autostash", "origin", "main"], check=False)
    if proc.returncode != 0:
        git(["rebase", "--abort"], check=False)
        raise RuntimeError(
            "Rebase onto origin/main failed — manual intervention needed. "
            "The data commit remains local; the next run will retry the push. "
            f"Details: {proc.stderr.strip()}"
        )
    git(["push", "origin", "main"])
    log.info("Pushed to origin/main — Vercel will redeploy the dashboard.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Crawl IHMCL, rebuild dashboard JSON, and publish."
    )
    parser.add_argument("--dry-run", action="store_true", help="crawl plan only")
    parser.add_argument("--no-push", action="store_true", help="download + build, no git")
    parser.add_argument(
        "--force-build",
        action="store_true",
        help="rebuild and publish even when the crawler finds nothing new",
    )
    parser.add_argument("--allow-warnings", action="store_true")
    args = parser.parse_args(argv)

    setup_logging()

    if args.dry_run:
        result = crawl(dry_run=True)
        return 1 if result.fatal else 0

    if not acquire_lock():
        return 1
    try:
        if not args.no_push:
            preflight()
            pull_latest()

        result = crawl()
        if result.fatal:
            return 1

        pending_pdfs = untracked_data_pdfs()
        unpushed = 0 if args.no_push else commits_ahead()
        if not result.downloaded and not pending_pdfs and not unpushed and not args.force_build:
            log.info("Everything up to date — nothing downloaded, staged, or unpushed.")
            return 0

        # Periods needing a rebuild: this run's downloads plus any report PDFs
        # a previous crashed/unpushed run left uncommitted. Archive-only
        # families are excluded — nothing parses them, so claiming their period
        # is "new" would make sanity_check() demand a monthly JSON that no
        # build was ever going to produce.
        new_periods = {
            r.period for r, _ in result.downloaded
            if r.period and r.family in ("etc", "annual_pass")
        }
        new_periods.update(
            p
            for f in pending_pdfs
            if not f.startswith("downloads/MLFF_Plaza_Data")
            and (p := period_from_filename(Path(f).name))
        )

        if new_periods or args.force_build:
            prev_index = read_index()
            prev_warnings = int(prev_index.get("validation_warnings", 0))
            prev_periods = set(prev_index.get("periods", []))
            run_build()
            sanity_check(new_periods, prev_periods, prev_warnings, args.allow_warnings)
        else:
            log.info("Only archive files (MLFF) changed — skipping JSON rebuild.")

        if args.no_push:
            log.info("--no-push set: stopping before git stage/commit/push.")
        else:
            publish(new_periods)

        return 2 if result.errors else 0
    except Exception:
        log.exception("Update failed — nothing was published.")
        return 1
    finally:
        release_lock()


if __name__ == "__main__":
    raise SystemExit(main())
