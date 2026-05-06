"""
NHIT | Taxonomy ingestion.

BUILD-TIME ONLY. Reads data/Project details.xlsx during
scripts/build_json_export.py runs and writes downloads/json/_taxonomy.json.
server.py never imports openpyxl or this module — at runtime the
taxonomy comes from the static JSON sidecar.

Reads `data/Project details.xlsx` and produces a clean list of
(spv, project, round, excel_plaza, canonical_plaza) records. Excel
"Project Name" column has merged cells (blank rows mean "same project as
above"); this file forward-fills them.

Excel plaza names are imperfect — typos like "Patgoan"/"Patgaon",
"Daroda"/"Daroada", "Mudipar"/"Mudhipar" etc. We match them to the
canonical plaza names that actually appear in the parsed PDF data using
a normalised fuzzy comparison; rows that can't be matched are flagged
in the snapshot under `taxonomy_unmatched` so analysts can see them.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable, Optional

import openpyxl

EXCEL_PATH_DEFAULT = Path(__file__).resolve().parents[2] / "data" / "Project details.xlsx"

# Excel plaza name -> canonical NHIT plaza name. Use this for entries the
# fuzzy matcher refuses (typo too far from canonical, or one Excel name
# corresponds to a longer canonical name in the PDFs).
MANUAL_ALIASES: dict[str, str] = {
    "Boharipar":                 "Bahoripar Fee Plaza",
    "Kalajhar to Patacharkuchi": "Kalajhar Toll Plaza",
    # 'Usaka' has no corresponding PDF data — leave unmatched on purpose.
}

_NORMALISE_STRIP_RE = re.compile(
    r"\b(toll|fee)?\s*plaza\b|\b(toll|fee)\b", re.IGNORECASE
)


def _normalise(s: str) -> str:
    """Lowercase + collapse whitespace + remove plaza/toll suffixes
    + strip non-alphanumerics so 'UNDVARIYA TOLL PLAZA' and 'Undavariya'
    line up."""
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = _NORMALISE_STRIP_RE.sub(" ", s)
    s = re.sub(r"[^a-zA-Z0-9]+", " ", s).lower().strip()
    return re.sub(r"\s+", " ", s)


def _best_canonical(excel_plaza: str, canonical_plazas: Iterable[str]) -> Optional[str]:
    """Return the best canonical match for `excel_plaza`, or None if the
    similarity is too weak. Tries exact normalised match first, then
    SequenceMatcher above 0.78."""
    target = _normalise(excel_plaza)
    if not target:
        return None
    best, best_score = None, 0.0
    for c in canonical_plazas:
        n = _normalise(c)
        if n == target:
            return c
        score = SequenceMatcher(None, target, n).ratio()
        if score > best_score:
            best, best_score = c, score
    return best if best_score >= 0.78 else None


def load_taxonomy(excel_path: Path = EXCEL_PATH_DEFAULT,
                  canonical_plazas: list[str] | None = None) -> dict:
    """Parse the Excel taxonomy and (optionally) reconcile each row's
    plaza name with the list of canonical plazas that have parsed data.

    Returns a dict:
        {
          "rows": [{"s_no", "spv", "round", "project", "excel_plaza",
                    "canonical_plaza"}, …],
          "spvs": ["NWPPL", "NEPPL", "NSPPL"],
          "rounds": ["R1", "R2", "R3", "R4", "R5"],
          "unmatched": ["<excel_plaza>", …],
        }
    """
    if not excel_path.exists():
        raise FileNotFoundError(f"Taxonomy file missing: {excel_path}")

    wb = openpyxl.load_workbook(excel_path, data_only=True)
    ws = wb[wb.sheetnames[0]]

    rows = []
    last_project = None
    for raw in ws.iter_rows(values_only=True):
        # Drop the leading None columns Excel sometimes inserts.
        cells = list(raw)
        # Locate the data columns (S.No is the first integer in the row).
        try:
            s_no_idx = next(i for i, c in enumerate(cells) if isinstance(c, int))
        except StopIteration:
            continue
        try:
            s_no = int(cells[s_no_idx])
        except (TypeError, ValueError):
            continue

        spv = (cells[s_no_idx + 1] or "").strip() if cells[s_no_idx + 1] else ""
        project_raw = cells[s_no_idx + 2]
        plaza = (cells[s_no_idx + 3] or "").strip() if cells[s_no_idx + 3] else ""
        round_ = (cells[s_no_idx + 4] or "").strip() if cells[s_no_idx + 4] else ""

        if project_raw is None or not str(project_raw).strip():
            project = last_project or ""
        else:
            project = str(project_raw).strip()
            last_project = project

        if not (spv and plaza and round_):
            continue

        rows.append({
            "s_no":           s_no,
            "spv":            spv,
            "round":          round_,
            "project":        project,
            "excel_plaza":    plaza,
            "canonical_plaza": None,
        })

    if canonical_plazas is not None:
        canon_set = set(canonical_plazas)
        unmatched: list[str] = []
        for r in rows:
            alias = MANUAL_ALIASES.get(r["excel_plaza"])
            if alias and alias in canon_set:
                r["canonical_plaza"] = alias
                continue
            match = _best_canonical(r["excel_plaza"], canonical_plazas)
            r["canonical_plaza"] = match
            if not match:
                unmatched.append(r["excel_plaza"])
    else:
        unmatched = []

    spvs = sorted({r["spv"] for r in rows})
    rounds = sorted({r["round"] for r in rows})
    return {"rows": rows, "spvs": spvs, "rounds": rounds, "unmatched": unmatched}
