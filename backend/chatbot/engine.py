"""
NHIT Chatbot Query Engine.

Deterministically answers natural-language questions about NHIT toll plaza
ETC data using the SNAPSHOT loaded by static_loader. Every number is read
directly from the JSON files in downloads/json/ — no LLM, no extrapolation,
no estimation.

Pipeline:
    answer(snapshot, message) -> {"reply": str, "matched": bool, "intent": str}

Supported intents:
    - help / greeting          : "hi", "what can you do"
    - list_plazas              : "list all plazas", "plazas in NSPPL"
    - list_spvs                : "what SPVs are there"
    - list_rounds              : "what rounds are there"
    - list_projects            : "what projects are there"
    - list_regions             : "list all RO" / "list cities"
    - list_vehicle_categories  : "what vehicle categories are tracked"
    - available_periods        : "what months", "what years"
    - count_X                  : "how many plazas in Punjab", "how many SPVs"
    - plaza_meta               : "where is Bassi", "what region is X in"
    - totals                   : "total revenue for Bassi in Jan 2026"
    - compare                  : plaza/SPV/Round/Region vs another
    - top_n / bottom_n         : "top 5 plazas by revenue"
    - trend                    : "show me Bassi monthly trend"
    - category_breakdown       : "vehicle category breakdown for Bassi"
    - category_metric          : "total Car / VC4 revenue across network"
    - best_month / worst_month : "which month had highest revenue"
    - growth                   : "MoM growth for Bassi", "YoY for the network"
    - statistics               : "average revenue per plaza", "median"
    - search_plazas            : "plazas starting with B", "plazas containing toll"
"""
from __future__ import annotations

import re
import statistics
from typing import Iterable

from ..core.analytics import aggregate_plazas_for_month, aggregate_plazas_for_range
from ..core.constants import MONTH_MAP, MONTH_NAMES


# ───────────────────────────── Formatting helpers ─────────────────────────────
def _fmt_inr(amount: float) -> str:
    a = float(amount or 0)
    if a >= 1e7:
        return f"₹{a / 1e7:.2f} Cr"
    if a >= 1e5:
        return f"₹{a / 1e5:.2f} L"
    return f"₹{a:,.0f}"


def _fmt_int(n: int) -> str:
    n = int(n or 0)
    if n >= 1e7:
        return f"{n / 1e7:.2f} Cr"
    if n >= 1e5:
        return f"{n / 1e5:.2f} L"
    return f"{n:,}"


def _fmt_pct(pct: float) -> str:
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.1f}%"


def _period_label(year: int, month: int) -> str:
    return f"{MONTH_NAMES[month]} {year}"


# ───────────────────────────── Indian state → cities map ──────────────────────
# Maps state names (lowercase) to lowercase city/PIU substrings present in the
# NHIT dataset. Built from the actual unique PIUs in downloads/json/_index.json
# so a question like "plazas in Punjab" resolves through real PIUs.
INDIAN_STATES: dict[str, list[str]] = {
    "punjab":          ["amritsar", "bhatinda", "jalandhar", "ludhiana"],
    "haryana":         ["ambala", "bhiwani", "gurgaon", "hisar", "rewari",
                        "rohtak", "sonepat", "sohna"],
    "rajasthan":       ["ajmer", "barmer", "bikaner", "chittorgarh", "dausa",
                        "hanumangarh", "jaipur", "jaisalmer", "jodhpur",
                        "kota", "sawaimadhopur", "udaipur", "morth rj"],
    "uttar pradesh":   ["agra", "aligarh", "azamgarh", "bareilly", "baghpat",
                        "ghaziabad", "gorakhpur", "hamirpur", "jhansi",
                        "kannauj", "kanpur", "lucknow", "mathura", "meerut",
                        "mirzapur", "moradabad", "najibabad", "prayagraj",
                        "raebareli", "varanasi"],
    "uttarakhand":     ["roorkee", "rudrapur"],
    "madhya pradesh":  ["bhopal", "chhatarpur", "chindwara", "gwalior", "harda",
                        "indore", "jabalpur", "katni", "khandwa", "ratlam",
                        "sagar", "ujjain", "vidisha"],
    "bihar":           ["aurangabad (bihar)", "begusarai", "chhapra",
                        "darbhanga", "gaya", "madhubani", "motihari", "munger",
                        "patna", "purnia", "sasaram"],
    "west bengal":     ["durgapur", "jalpaiguri", "kharagpur", "kolkata",
                        "kolkata north", "krishnagar", "malda", "purulia"],
    "maharashtra":     ["ahmednagar", "amravati - bza", "amravati - ngp",
                        "aurangabad", "chandrapur", "dhule", "jalgaon",
                        "kolhapur", "mumbai", "nagpur", "nanded", "nashik",
                        "pandharpur", "pune", "solapur", "thane", "washim",
                        "yavatmal"],
    "karnataka":       ["bagalkot", "bangalore", "chitradurga", "dharwad",
                        "gulbarga", "hasan", "honanvar", "hospet", "mangalore",
                        "mysuru", "piu-bengaluru (expressway)", "ramanagara",
                        "tumkur"],
    "andhra pradesh":  ["anantpur", "chittoor", "kadapa", "kurnool", "nellore",
                        "ongole", "rajahmundry", "tirupati", "vijayawada",
                        "vishakhapatnam", "vizianagaram"],
    "telangana":       ["gajwel", "hyderabad", "khammam", "mahabubnagar",
                        "mancherial", "nirmal", "sangareddy", "warangal"],
    "odisha":          ["balasore", "behrampur", "bhubaneswar", "dhenkanal",
                        "keonjhar", "rourkela", "sambalpur"],
    "tamil nadu":      ["chengalpattu", "chennai", "coimbatore", "dindigul",
                        "kancheepuram", "karaikudi", "karur", "krishnagiri",
                        "madurai", "nagercoil", "salem", "thanjavur",
                        "tiruvannamalai", "trichy", "tuticorin", "vellupuram",
                        "villupuram"],
    "kerala":          ["cochin/kochi", "kannur", "kozhikode", "palakkad"],
    "gujarat":         ["ahmedabad", "bhavnagar", "dwarka", "gandhidham",
                        "godhra", "palanpur", "rajkot", "somnath", "surat"],
    "goa":             ["vasco da gam"],
    "assam":           ["bongoigaon", "guwahati", "haflong"],
    "jharkhand":       ["daltonganj", "deoghar", "dhanbad", "gumla",
                        "hazaribagh", "ranchi", "sahibganj"],
    "chhattisgarh":    ["bilaspur", "korba", "raipur 1", "raipur 2", "dhamtari"],
    "jammu and kashmir": ["jammu", "srinagar", "udhampur"],
    "himachal pradesh": ["hamirpur", "mandi", "shimla"],
    "puducherry":      ["puducherry"],
    "delhi":           ["delhi", "vasant vihar"],
}
# Common aliases people actually type
INDIAN_STATE_ALIASES: dict[str, str] = {
    "up": "uttar pradesh",
    "u.p.": "uttar pradesh",
    "uk": "uttarakhand",
    "mp": "madhya pradesh",
    "m.p.": "madhya pradesh",
    "wb": "west bengal",
    "ap": "andhra pradesh",
    "tn": "tamil nadu",
    "tg": "telangana",
    "ka": "karnataka",
    "mh": "maharashtra",
    "j&k": "jammu and kashmir",
    "jk": "jammu and kashmir",
    "j and k": "jammu and kashmir",
    "hp": "himachal pradesh",
    "ch": "chhattisgarh",
    "od": "odisha",
    "orissa": "odisha",
}


# ───────────────────────────── Period parsing ────────────────────────────────
def _available_periods(snapshot: dict) -> list[tuple[int, int]]:
    return [(m["year"], m["month"]) for m in snapshot.get("months", [])]


def _quarter_to_months(q: int) -> tuple[int, int]:
    """Q1 → (1,3), Q2 → (4,6), Q3 → (7,9), Q4 → (10,12)."""
    q = max(1, min(int(q), 4))
    return (3 * q - 2, 3 * q)


def _extract_periods(message: str, available: list[tuple[int, int]]):
    """Returns (single, range) where single=(y,m) or range=((sy,sm),(ey,em))."""
    msg_lower = message.lower()

    # Quarter patterns: Q1 2025, first quarter 2025, Q1
    q_match = re.search(
        r"\b(?:q([1-4])|(first|second|third|fourth)\s+quarter)\b",
        msg_lower,
    )
    quarter_n = None
    if q_match:
        if q_match.group(1):
            quarter_n = int(q_match.group(1))
        else:
            quarter_n = {"first": 1, "second": 2, "third": 3, "fourth": 4}[q_match.group(2)]

    month_tokens = sorted(MONTH_MAP.keys(), key=len, reverse=True)
    month_pattern = r"\b(" + "|".join(re.escape(t) for t in month_tokens) + r")\b"
    months_found: list[int] = []
    for m in re.finditer(month_pattern, msg_lower, flags=re.IGNORECASE):
        mn = MONTH_MAP.get(m.group(1).lower())
        if mn and (not months_found or months_found[-1] != mn):
            months_found.append(mn)

    years_found = [int(y) for y in re.findall(r"\b(20\d{2})\b", message)]

    if quarter_n and years_found:
        sm, em = _quarter_to_months(quarter_n)
        y = years_found[0]
        return None, ((y, sm), (y, em))

    if len(months_found) >= 2 and years_found:
        sm = months_found[0]
        em = months_found[-1]
        sy = years_found[0]
        ey = years_found[-1] if len(years_found) > 1 else sy
        return None, ((sy, sm), (ey, em))

    if not months_found and years_found:
        y = years_found[0]
        in_year = sorted([m for (yy, m) in available if yy == y])
        if in_year:
            return None, ((y, in_year[0]), (y, in_year[-1]))

    if months_found and years_found:
        return (years_found[0], months_found[0]), None

    if months_found:
        m = months_found[0]
        for y, mm in sorted(available, reverse=True):
            if mm == m:
                return (y, m), None

    return None, None


def _detect_latest_keyword(message: str) -> bool:
    return bool(re.search(r"\b(latest|last\s+month|most\s+recent|recent\s+month)\b",
                          message, flags=re.IGNORECASE))


# ───────────────────────────── Plaza / scope parsing ─────────────────────────
def _all_plazas(snapshot: dict) -> list[str]:
    return list(snapshot["data"].keys())


def _plaza_meta(snapshot: dict, plaza: str) -> dict:
    """Return {piu, ro, periods_covered} for a plaza."""
    rec = snapshot.get("data", {}).get(plaza, {})
    if not rec:
        return {}
    sample = next(iter(rec.values()), {})
    return {
        "piu": sample.get("piu", ""),
        "ro": sample.get("ro", ""),
        "periods": sorted(rec.keys()),
    }


def _plaza_taxonomy(snapshot: dict, plaza: str) -> dict | None:
    for r in snapshot.get("taxonomy", {}).get("rows", []):
        if r.get("canonical_plaza") == plaza:
            return r
    return None


def _detect_plazas_in_message(message: str, all_plazas: list[str]) -> list[str]:
    """Find every plaza whose canonical name appears in the message. Longer
    names win; substring hits already absorbed by longer matches are dropped."""
    msg_lower = message.lower()
    matches: list[str] = []
    used_spans: list[tuple[int, int]] = []
    for p in sorted(all_plazas, key=lambda x: -len(x)):
        pl = p.lower()
        idx = msg_lower.find(pl)
        if idx == -1:
            continue
        end = idx + len(pl)
        if any(not (end <= s or idx >= e) for s, e in used_spans):
            continue
        matches.append(p)
        used_spans.append((idx, end))
    return matches


def _detect_spv_round(message: str, spvs: list[str], rounds: list[str]):
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
    return matched_spv, matched_round


def _detect_all_spvs(message: str, spvs: list[str]) -> list[str]:
    found = []
    for s in spvs:
        if re.search(rf"\b{re.escape(s)}\b", message, flags=re.IGNORECASE) and s not in found:
            found.append(s)
    return found


def _detect_all_rounds(message: str, rounds: list[str]) -> list[str]:
    found = []
    for r in rounds:
        if re.search(rf"\b{re.escape(r)}\b", message, flags=re.IGNORECASE) and r not in found:
            found.append(r)
    return found


def _scope_from_filters(snapshot: dict, spv: str | None, rnd: str | None):
    tax_rows = snapshot.get("taxonomy", {}).get("rows", [])
    rows = [
        r for r in tax_rows
        if r.get("canonical_plaza")
        and (not spv or r.get("spv") == spv)
        and (not rnd or r.get("round") == rnd)
    ]
    plazas = sorted({r["canonical_plaza"] for r in rows})
    bits = []
    if spv: bits.append(f"SPV {spv}")
    if rnd: bits.append(f"Round {rnd}")
    label = " · ".join(bits) if bits else "All NHIT Plazas"
    return plazas, label


# ───────────────────────────── Region / project parsing ──────────────────────
def _all_pius(snapshot: dict) -> set[str]:
    return {meta.get("piu", "") for plaza in snapshot["data"].values()
            for meta in plaza.values() if meta.get("piu")}


def _all_ros(snapshot: dict) -> set[str]:
    return {meta.get("ro", "") for plaza in snapshot["data"].values()
            for meta in plaza.values() if meta.get("ro")}


def _all_projects(snapshot: dict) -> set[str]:
    return {r.get("project", "") for r in snapshot.get("taxonomy", {}).get("rows", [])
            if r.get("project")}


def _plazas_in_piu(snapshot: dict, piu: str) -> list[str]:
    out = []
    pl = piu.lower()
    for plaza, recs in snapshot["data"].items():
        for meta in recs.values():
            if (meta.get("piu") or "").lower() == pl:
                out.append(plaza); break
    return sorted(out)


def _plazas_in_ro(snapshot: dict, ro: str) -> list[str]:
    out = []
    rl = ro.lower()
    for plaza, recs in snapshot["data"].items():
        for meta in recs.values():
            if (meta.get("ro") or "").lower() == rl:
                out.append(plaza); break
    return sorted(out)


def _plazas_in_state(snapshot: dict, state_key: str) -> list[str]:
    """Aggregate all plazas whose PIU matches any city in the state's mapping."""
    cities = INDIAN_STATES.get(state_key, [])
    if not cities:
        return []
    cities_set = set(cities)
    out = []
    for plaza, recs in snapshot["data"].items():
        for meta in recs.values():
            piu = (meta.get("piu") or "").lower()
            if piu in cities_set:
                out.append(plaza); break
    return sorted(out)


def _plazas_in_project(snapshot: dict, project: str) -> list[str]:
    pl = project.lower()
    rows = [r for r in snapshot.get("taxonomy", {}).get("rows", [])
            if r.get("canonical_plaza") and (r.get("project") or "").lower() == pl]
    return sorted({r["canonical_plaza"] for r in rows})


def _detect_state(message: str) -> tuple[str | None, list[str]]:
    """Return (canonical_state_name, cities) or (None, []) if not found."""
    msg = message.lower()
    # Aliases first
    for alias, full in INDIAN_STATE_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", msg):
            return full, INDIAN_STATES[full]
    for state, cities in INDIAN_STATES.items():
        if re.search(rf"\b{re.escape(state)}\b", msg):
            return state, cities
    return None, []


def _detect_piu(message: str, snapshot: dict) -> str | None:
    msg = message.lower()
    pius = sorted(_all_pius(snapshot), key=lambda x: -len(x))
    for p in pius:
        if not p or p == "0":
            continue
        if re.search(rf"\b{re.escape(p.lower())}\b", msg):
            return p
    return None


def _detect_ro(message: str, snapshot: dict) -> str | None:
    """Match RO names; require the user to mention RO/region/regional office
    near the name to avoid false positives on city names that double as RO
    names (e.g. Mumbai as both PIU and RO)."""
    msg = message.lower()
    ros = sorted(_all_ros(snapshot), key=lambda x: -len(x))
    only_if_ro_word = bool(re.search(r"\b(ro|regional\s+office)\b", msg))
    for r in ros:
        if not r:
            continue
        if re.search(rf"\b{re.escape(r.lower())}\b", msg):
            if only_if_ro_word or r.lower() in ("lucknow - east", "lucknow - west", "morth rj"):
                return r
    return None


def _detect_project(message: str, snapshot: dict) -> str | None:
    msg = message.lower()
    projs = sorted(_all_projects(snapshot), key=lambda x: -len(x))
    for p in projs:
        if not p:
            continue
        # Project names have hyphens / multiple words; use case-insensitive substring
        if p.lower() in msg:
            return p
    return None


# ───────────────────────────── Top-level scope resolver ──────────────────────
def _resolve_scope(message: str, snapshot: dict):
    """Resolve message → (plazas, label, kind, meta).
    Priority: explicit plaza > region (state/PIU/RO/project) > SPV/Round > whole network."""
    all_p = _all_plazas(snapshot)
    spvs = snapshot.get("taxonomy", {}).get("spvs", [])
    rounds = snapshot.get("taxonomy", {}).get("rounds", [])

    plazas_in_msg = _detect_plazas_in_message(message, all_p)
    if len(plazas_in_msg) == 1:
        return plazas_in_msg, plazas_in_msg[0], "plaza", {"plaza": plazas_in_msg[0]}
    if len(plazas_in_msg) >= 2:
        return plazas_in_msg, " vs ".join(plazas_in_msg), "plaza_compare", {
            "plazas": plazas_in_msg,
        }

    # State / PIU / RO / project
    state, _ = _detect_state(message)
    if state:
        plazas = _plazas_in_state(snapshot, state)
        if plazas:
            return plazas, f"{state.title()}", "state", {"state": state}

    piu = _detect_piu(message, snapshot)
    if piu:
        plazas = _plazas_in_piu(snapshot, piu)
        if plazas:
            return plazas, f"PIU {piu}", "piu", {"piu": piu}

    ro = _detect_ro(message, snapshot)
    if ro:
        plazas = _plazas_in_ro(snapshot, ro)
        if plazas:
            return plazas, f"RO {ro}", "ro", {"ro": ro}

    proj = _detect_project(message, snapshot)
    if proj:
        plazas = _plazas_in_project(snapshot, proj)
        if plazas:
            return plazas, f"Project {proj}", "project", {"project": proj}

    spv, rnd = _detect_spv_round(message, spvs, rounds)
    if spv or rnd:
        plazas, label = _scope_from_filters(snapshot, spv, rnd)
        if plazas:
            return plazas, label, "spv_round", {"spv": spv, "round": rnd}
        if spv and rnd:
            spv_plazas, _ = _scope_from_filters(snapshot, spv, None)
            if spv_plazas:
                actual_rounds = sorted({
                    r["round"] for r in snapshot.get("taxonomy", {}).get("rows", [])
                    if r.get("canonical_plaza") and r.get("spv") == spv
                })
                note = (f"NOTE: {spv} has no Round {rnd} in this portfolio — "
                        f"only Round(s) {', '.join(actual_rounds)}")
                return spv_plazas, f"SPV {spv} ({note})", "spv_round", {
                    "spv": spv, "round": None, "note": note,
                }
        return [], f"No plazas match {spv or ''} {rnd or ''}".strip(), "empty", {}

    return sorted(all_p), "All NHIT Plazas (whole network)", "all", {}


# ───────────────────────────── Intent regexes ────────────────────────────────
_RX_HELP = re.compile(
    r"^\s*(hi|hello|hey|hola|namaste|good\s+(morning|afternoon|evening)|"
    r"help|what\s+can\s+you\s+(do|answer)|how\s+can\s+you\s+help|"
    r"who\s+are\s+you|what\s+are\s+you|about(\s+you)?)\s*[?!.]*\s*$",
    re.IGNORECASE,
)
_RX_THANKS = re.compile(
    r"^\s*(thank(s| you)|thx|cool|nice|great|awesome|ok|okay)\s*[?!.]*\s*$",
    re.IGNORECASE,
)
_RX_TOP = re.compile(
    r"\b(top|highest|most|largest|biggest|best|leading|number\s*one|busiest)\b",
    re.IGNORECASE,
)
_RX_BOTTOM = re.compile(
    r"\b(bottom|lowest|least|smallest|worst|fewest|minimum|quietest)\b",
    re.IGNORECASE,
)
_RX_COMPARE = re.compile(
    r"\b(compare|comparison|vs\.?|versus|difference\s+between)\b",
    re.IGNORECASE,
)
_RX_TREND = re.compile(
    r"\b(trend|monthly\s+trend|over\s+time|month[-\s]by[-\s]month|history|"
    r"historical|growth|growing|declining)\b",
    re.IGNORECASE,
)
_RX_CATEGORY = re.compile(
    r"\b(category|categories|vehicle\s+(type|class|category|categories|breakdown)|"
    r"by\s+vehicle|breakdown\s+by)\b",
    re.IGNORECASE,
)
_RX_REVENUE_KW = re.compile(
    r"\b(revenue|amount|toll|earning|collection|collected|income|money)\b",
    re.IGNORECASE,
)
_RX_TXN_KW = re.compile(
    r"\b(transaction|txn|count|volume|traffic|trip|crossing|footfall)s?\b",
    re.IGNORECASE,
)
_RX_AVG = re.compile(
    r"\b(average|avg|mean|median)\b",
    re.IGNORECASE,
)
_RX_GROWTH = re.compile(
    r"\b(mom|m-?o-?m|month[-\s]on[-\s]month|month[-\s]over[-\s]month|"
    r"yoy|y-?o-?y|year[-\s]on[-\s]year|year[-\s]over[-\s]year|growth\s+rate|"
    r"change|change\s+in)\b",
    re.IGNORECASE,
)
_RX_BEST_MONTH = re.compile(
    r"\b(?:(?:best|highest|peak|most|busiest)\s+(?:revenue\s+|earning\s+|busiest\s+)?month|"
    r"which\s+month\s+(?:had|has|saw|was|is)\s+(?:the\s+)?(?:highest|most|biggest|peak|best))\b",
    re.IGNORECASE,
)
_RX_WORST_MONTH = re.compile(
    r"\b(?:(?:worst|lowest|quietest|weakest|fewest)\s+(?:revenue\s+|earning\s+)?month|"
    r"which\s+month\s+(?:had|has|saw|was|is)\s+(?:the\s+)?(?:lowest|least|fewest|worst|smallest))\b",
    re.IGNORECASE,
)
_RX_COUNT = re.compile(
    r"\bhow\s+many\b",
    re.IGNORECASE,
)
_RX_LOCATION_Q = re.compile(
    r"\b(where\s+is|location\s+of|"
    r"(?:which|what)\s+(?:region|piu|ro|state|spv|round|project|city)|"
    r"(?:region|state|city|piu|ro|spv|round|project)\s+(?:of|for|is)|"
    r"tell\s+me\s+about)\b",
    re.IGNORECASE,
)
_RX_SEARCH_PLAZAS = re.compile(
    r"\bplazas?\s+(starting|beginning)\s+with\s+([a-z])\b|"
    r"\bplazas?\s+(containing|with\s+name|named?)\s+(\w+)\b",
    re.IGNORECASE,
)
_RX_LIST = re.compile(
    r"\b(list|show\s+me|give\s+me|what\s+are\s+the|all)\b",
    re.IGNORECASE,
)


# Recognised vehicle category aliases (lower → canonical token used in JSON)
VEHICLE_ALIASES: dict[str, str] = {
    "car": "Car / Jeep / Van (VC4)",
    "cars": "Car / Jeep / Van (VC4)",
    "jeep": "Car / Jeep / Van (VC4)",
    "van": "Car / Jeep / Van (VC4)",
    "vc4": "Car / Jeep / Van (VC4)",
    "lcv": "Light Commercial Vehicle (VC5)",
    "light commercial": "Light Commercial Vehicle (VC5)",
    "vc5": "Light Commercial Vehicle (VC5)",
    "bus": "Bus / Truck - 2 Axle (VC6)",
    "truck": "Bus / Truck - 2 Axle (VC6)",
    "2 axle": "Bus / Truck - 2 Axle (VC6)",
    "two axle": "Bus / Truck - 2 Axle (VC6)",
    "vc6": "Bus / Truck - 2 Axle (VC6)",
    "3 axle": "3-Axle Vehicle (VC7)",
    "three axle": "3-Axle Vehicle (VC7)",
    "vc7": "3-Axle Vehicle (VC7)",
    "heavy": "4-6 Axle (VC8/9/10)",
    "4 axle": "4-6 Axle (VC8/9/10)",
    "5 axle": "4-6 Axle (VC8/9/10)",
    "6 axle": "4-6 Axle (VC8/9/10)",
    "vc8": "4-6 Axle (VC8/9/10)",
    "vc9": "4-6 Axle (VC8/9/10)",
    "vc10": "4-6 Axle (VC8/9/10)",
    "oversized": "Oversized Vehicle (VC11+)",
    "vc11": "Oversized Vehicle (VC11+)",
    "vc12": "Oversized Vehicle (VC11+)",
}


def _detect_vehicle_category(message: str) -> str | None:
    msg = message.lower()
    for alias in sorted(VEHICLE_ALIASES.keys(), key=lambda x: -len(x)):
        if re.search(rf"\b{re.escape(alias)}\b", msg):
            return VEHICLE_ALIASES[alias]
    return None


def _wants_revenue(msg: str) -> bool:
    return bool(_RX_REVENUE_KW.search(msg))


def _wants_txn(msg: str) -> bool:
    return bool(_RX_TXN_KW.search(msg))


def _parse_top_n(msg: str, default: int = 5) -> int:
    m = re.search(r"\btop\s+(\d{1,3})\b", msg, re.IGNORECASE)
    if m: return max(1, min(int(m.group(1)), 50))
    m = re.search(r"\bbottom\s+(\d{1,3})\b", msg, re.IGNORECASE)
    if m: return max(1, min(int(m.group(1)), 50))
    m = re.search(r"\b(\d{1,3})\s+(?:plazas?|highest|lowest|top|bottom)\b", msg, re.IGNORECASE)
    if m: return max(1, min(int(m.group(1)), 50))
    return default


# ───────────────────────────── Period / aggregation utility ──────────────────
def _build_period_phrase(single, rng, snapshot):
    if single:
        return _period_label(*single)
    if rng:
        (sy, sm), (ey, em) = rng
        if (sy, sm) == (ey, em):
            return _period_label(sy, sm)
        return f"{_period_label(sy, sm)} – {_period_label(ey, em)}"
    months = _available_periods(snapshot)
    if not months:
        return "the available period"
    sy, sm = months[0]
    ey, em = months[-1]
    return f"all available data ({_period_label(sy, sm)} – {_period_label(ey, em)})"


def _aggregate_for_scope(snapshot, plazas, single, rng):
    if single:
        y, m = single
        return aggregate_plazas_for_month(snapshot, plazas, y, m), single, None
    if rng:
        (sy, sm), (ey, em) = rng
        return aggregate_plazas_for_range(snapshot, plazas, sy, sm, ey, em), None, rng
    months = _available_periods(snapshot)
    if not months:
        return None, None, None
    sy, sm = months[0]
    ey, em = months[-1]
    return aggregate_plazas_for_range(snapshot, plazas, sy, sm, ey, em), None, ((sy, sm), (ey, em))


# ───────────────────────────── Block formatters ──────────────────────────────
def _format_summary_block(rec, scope_label, period_phrase, plazas_total):
    if not rec:
        return f"**No data available** for **{scope_label}** in {period_phrase}."

    return "\n".join([
        f"**{scope_label}** — {period_phrase}",
        "",
        f"• Plazas with data: **{len(rec.get('plazas_included', []))}** of {plazas_total}",
        f"• Total revenue: **{_fmt_inr(rec['total_amount'])}** "
        f"(₹{rec['total_amount']:,.0f})",
        f"• Total transactions: **{_fmt_int(rec['total_count'])}** "
        f"({rec['total_count']:,})",
        f"• Average fare per transaction: **₹{rec['avg_per_txn']:,.2f}**",
        f"• Average daily revenue: **{_fmt_inr(rec.get('avg_revenue_per_day', 0))}**",
        f"• Average daily transactions: **{_fmt_int(rec.get('avg_count_per_day', 0))}**",
        f"• Top category by revenue: **{rec['top_by_amount']['name']}** "
        f"({_fmt_inr(rec['top_by_amount']['amount'])})",
        f"• Top category by volume: **{rec['top_by_count']['name']}** "
        f"({_fmt_int(rec['top_by_count']['count'])} txns)",
    ])


def _format_category_block(rec, scope_label, period_phrase):
    if not rec:
        return f"**No data available** for **{scope_label}** in {period_phrase}."
    lines = [f"**Vehicle category breakdown — {scope_label}** ({period_phrase})", ""]
    for c in rec["categories"]:
        lines.append(
            f"• **{c['name']}** — "
            f"{_fmt_int(c['count'])} txns ({c['share_count']:.1f}%), "
            f"{_fmt_inr(c['amount'])} ({c['share_amount']:.1f}%), "
            f"avg fare ₹{c['avg_fare']:,.2f}"
        )
    lines.append("")
    lines.append(
        f"**Total:** {_fmt_int(rec['total_count'])} txns · "
        f"{_fmt_inr(rec['total_amount'])}"
    )
    return "\n".join(lines)


def _format_trend_block(snapshot, plazas, scope_label, single=None, rng=None):
    available = _available_periods(snapshot)
    if rng:
        (sy, sm), (ey, em) = rng
        wanted = [(y, m) for (y, m) in available if (sy, sm) <= (y, m) <= (ey, em)]
    else:
        wanted = available
    if not wanted:
        return f"**No trend data** available for **{scope_label}**."

    lines = [f"**Monthly trend — {scope_label}**", ""]
    grand_count = 0
    grand_amount = 0.0
    for (y, m) in wanted:
        rec = aggregate_plazas_for_month(snapshot, plazas, y, m)
        if not rec:
            lines.append(f"• {_period_label(y, m)} — no data")
            continue
        grand_count += rec["total_count"]
        grand_amount += rec["total_amount"]
        lines.append(
            f"• **{_period_label(y, m)}** — "
            f"{_fmt_int(rec['total_count'])} txns · "
            f"{_fmt_inr(rec['total_amount'])}"
        )
    lines.append("")
    lines.append(
        f"**Period total:** {_fmt_int(grand_count)} txns · "
        f"{_fmt_inr(grand_amount)}"
    )
    return "\n".join(lines)


def _rank_plazas(snapshot, plazas, single, rng, metric: str):
    rows = []
    for p in plazas:
        rec, _, _ = _aggregate_for_scope(snapshot, [p], single, rng)
        if not rec:
            continue
        rows.append((p, rec["total_count"], rec["total_amount"]))
    key = (lambda r: r[2]) if metric == "revenue" else (lambda r: r[1])
    rows.sort(key=key, reverse=True)
    return rows


def _format_ranking_block(rows, scope_label, period_phrase, metric, n=5,
                          ascending=False, total_pool=None):
    if not rows:
        return f"**No data** for **{scope_label}** in {period_phrase}."
    if ascending:
        rows = list(reversed(rows))
        title = f"Bottom {min(n, len(rows))} plazas"
    else:
        title = f"Top {min(n, len(rows))} plazas"
    metric_word = "revenue" if metric == "revenue" else "transactions"
    pool_phrase = f" (out of {total_pool} plazas with data)" if total_pool else ""
    lines = [
        f"**{title} by {metric_word} — {scope_label}**{pool_phrase}",
        f"_{period_phrase}_",
        "",
    ]
    for i, (p, count, amount) in enumerate(rows[:n], start=1):
        lines.append(
            f"{i}. **{p}** — {_fmt_inr(amount)} · {_fmt_int(count)} txns"
        )
    return "\n".join(lines)


# ───────────────────────────── List-style intents ────────────────────────────
def _answer_help() -> str:
    return (
        "**👋 NHIT Analytics Assistant**\n"
        "\nI answer questions directly from the NHIT toll-data JSON files. "
        "Every number is exact — no AI guessing.\n\n"
        "**What I can answer:**\n"
        "• **Totals** — \"Total revenue for Bassi in Jan 2026\"\n"
        "• **Comparisons** — \"Compare NSPPL and NEPPL\", \"Bassi vs Aroli\"\n"
        "• **Top / bottom** — \"Top 10 plazas by revenue in 2025\"\n"
        "• **Trends** — \"Show me Bassi's monthly trend\"\n"
        "• **Vehicle categories** — \"Car revenue across the network\"\n"
        "• **Geography** — \"How many plazas are in Punjab?\", \"Plazas in Lucknow\"\n"
        "• **Statistics** — \"Average revenue per plaza\", \"Best month for NWPPL\"\n"
        "• **Growth** — \"MoM growth for the network\", \"YoY for Bassi\"\n"
        "• **Lists** — \"List all SPVs / Rounds / projects / plazas\"\n"
        "• **Coverage** — \"What months are available?\"\n"
        "• **Plaza info** — \"Where is Bassi?\", \"What region is Aroli in?\"\n"
        "\nAsk anything you'd ask the dashboard."
    )


def _answer_thanks() -> str:
    return "Anytime! Ask me anything else about the toll data."


def _answer_list_plazas(snapshot, message):
    spvs = snapshot.get("taxonomy", {}).get("spvs", [])
    rounds = snapshot.get("taxonomy", {}).get("rounds", [])
    spv, rnd = _detect_spv_round(message, spvs, rounds)

    state, _ = _detect_state(message)
    piu = _detect_piu(message, snapshot)
    ro = _detect_ro(message, snapshot)
    proj = _detect_project(message, snapshot)

    if spv or rnd:
        plazas, label = _scope_from_filters(snapshot, spv, rnd)
    elif state:
        plazas = _plazas_in_state(snapshot, state); label = state.title()
    elif piu:
        plazas = _plazas_in_piu(snapshot, piu); label = f"PIU {piu}"
    elif ro:
        plazas = _plazas_in_ro(snapshot, ro); label = f"RO {ro}"
    elif proj:
        plazas = _plazas_in_project(snapshot, proj); label = f"Project {proj}"
    else:
        plazas = sorted(_all_plazas(snapshot)); label = "All NHIT Plazas"

    if not plazas:
        return f"No plazas found for **{label}**."
    head = f"**{label}** — {len(plazas)} plaza(s)"
    if len(plazas) > 60:
        sample = ", ".join(plazas[:30])
        return (
            f"{head}.\n\nFirst 30: {sample} …\n\n"
            f"_(Full list has {len(plazas)} entries — refine by SPV / Round / "
            f"State / RO / PIU to narrow down.)_"
        )
    return f"{head}\n\n" + "\n".join(f"• {p}" for p in plazas)


def _answer_list_spvs(snapshot):
    spvs = snapshot.get("taxonomy", {}).get("spvs", [])
    if not spvs:
        return "No SPVs are defined in the taxonomy."
    lines = [f"**SPVs in the NHIT portfolio** — {len(spvs)} total", ""]
    for s in spvs:
        plazas, _ = _scope_from_filters(snapshot, s, None)
        lines.append(f"• **{s}** — {len(plazas)} plaza(s)")
    return "\n".join(lines)


def _answer_list_rounds(snapshot):
    rounds = snapshot.get("taxonomy", {}).get("rounds", [])
    if not rounds:
        return "No rounds are defined in the taxonomy."
    lines = [f"**Rounds in the NHIT portfolio** — {len(rounds)} total", ""]
    for r in rounds:
        plazas, _ = _scope_from_filters(snapshot, None, r)
        lines.append(f"• **{r}** — {len(plazas)} plaza(s)")
    return "\n".join(lines)


def _answer_list_projects(snapshot):
    rows = snapshot.get("taxonomy", {}).get("rows", [])
    by_proj: dict[str, set] = {}
    for r in rows:
        proj = r.get("project")
        cp = r.get("canonical_plaza")
        if proj and cp:
            by_proj.setdefault(proj, set()).add(cp)
    if not by_proj:
        return "No projects are defined in the taxonomy."
    lines = [f"**Projects in the NHIT portfolio** — {len(by_proj)} total", ""]
    for proj, plazas in sorted(by_proj.items()):
        lines.append(f"• **{proj}** — {len(plazas)} plaza(s)")
    return "\n".join(lines)


def _answer_list_regions(snapshot, kind="ro"):
    if kind == "ro":
        ros = sorted(_all_ros(snapshot))
        if not ros:
            return "No regional offices found in the data."
        return (
            f"**Regional Offices (RO)** — {len(ros)} total\n\n"
            + ", ".join(ros)
        )
    pius = sorted(p for p in _all_pius(snapshot) if p and p != "0")
    if not pius:
        return "No PIUs found in the data."
    if len(pius) > 60:
        sample = ", ".join(pius[:50])
        return (
            f"**Project Implementation Units (PIU)** — {len(pius)} total\n\n"
            f"First 50: {sample} …"
        )
    return (
        f"**Project Implementation Units (PIU)** — {len(pius)} total\n\n"
        + ", ".join(pius)
    )


def _answer_list_vehicle_categories(snapshot) -> str:
    cats: set[str] = set()
    for plaza in snapshot["data"].values():
        for rec in plaza.values():
            for c in rec.get("categories", []):
                cats.add(c["name"])
    cats_sorted = sorted(cats)
    if not cats_sorted:
        return "No vehicle categories found in the data."
    return (
        f"**Vehicle categories tracked** — {len(cats_sorted)} total\n\n"
        + "\n".join(f"• {c}" for c in cats_sorted)
    )


def _answer_available_periods(snapshot):
    months = snapshot.get("months", [])
    if not months:
        return "No monthly data is available."
    labels = [m["label"] for m in months]
    return f"**Available data** — {len(months)} month(s)\n\n{', '.join(labels)}"


# ───────────────────────────── Counting intents ──────────────────────────────
def _answer_count(snapshot, message):
    """Resolve 'how many ___' questions — plazas/SPVs/rounds/projects/months/etc."""
    msg = message.lower()

    if re.search(r"\b(spvs?|special\s+purpose\s+vehicles?)\b", msg):
        n = len(snapshot.get("taxonomy", {}).get("spvs", []))
        return f"There are **{n} SPV(s)** in the NHIT portfolio: " \
               + ", ".join(snapshot.get("taxonomy", {}).get("spvs", []))

    if re.search(r"\brounds?\b", msg):
        n = len(snapshot.get("taxonomy", {}).get("rounds", []))
        return f"There are **{n} Round(s)** in the NHIT portfolio: " \
               + ", ".join(snapshot.get("taxonomy", {}).get("rounds", []))

    if re.search(r"\bprojects?\b", msg):
        n = len(_all_projects(snapshot))
        return f"There are **{n} project(s)** in the NHIT portfolio."

    if re.search(r"\b(months?|periods?)\b", msg):
        n = len(snapshot.get("months", []))
        return f"There are **{n} month(s)** of data available."

    if re.search(r"\b(states?|regions?\s+of\s+india)\b", msg):
        return f"The NHIT data covers plazas across roughly {len(INDIAN_STATES)} states / UTs."

    if re.search(r"\b(regional\s+offices?|ro)\b", msg) and not re.search(r"\brevenue|amount\b", msg):
        n = len(_all_ros(snapshot))
        return f"There are **{n} Regional Office(s) (RO)** in the data."

    if re.search(r"\b(piu|project\s+implementation\s+units?|cities)\b", msg):
        n = len([p for p in _all_pius(snapshot) if p and p != "0"])
        return f"There are **{n} PIU(s) / cities** in the data."

    if re.search(r"\b(vehicle\s+(categories|types|classes))\b", msg):
        cats: set[str] = set()
        for plaza in snapshot["data"].values():
            for rec in plaza.values():
                for c in rec.get("categories", []):
                    cats.add(c["name"])
        return f"There are **{len(cats)} vehicle category(ies)** tracked: " \
               + ", ".join(sorted(cats))

    if re.search(r"\bplazas?\b", msg):
        plazas, label, kind, _ = _resolve_scope(message, snapshot)
        n = len(plazas)
        return f"There are **{n} plaza(s)** in **{label}**."

    return None


# ───────────────────────────── Plaza meta intent ─────────────────────────────
def _answer_plaza_meta(snapshot, plaza):
    meta = _plaza_meta(snapshot, plaza)
    tax = _plaza_taxonomy(snapshot, plaza) or {}
    if not meta:
        return f"No metadata found for **{plaza}**."
    state_match = None
    piu_lower = (meta.get("piu") or "").lower()
    for state, cities in INDIAN_STATES.items():
        if piu_lower in cities:
            state_match = state.title(); break
    lines = [f"**{plaza}** — plaza details", ""]
    if meta.get("piu"):  lines.append(f"• PIU (city): **{meta['piu']}**")
    if meta.get("ro"):   lines.append(f"• Regional Office: **{meta['ro']}**")
    if state_match:      lines.append(f"• State: **{state_match}**")
    if tax.get("spv"):   lines.append(f"• SPV: **{tax['spv']}**")
    if tax.get("round"): lines.append(f"• Round: **{tax['round']}**")
    if tax.get("project"): lines.append(f"• Project: **{tax['project']}**")
    if meta.get("periods"):
        lines.append(f"• Months of data: **{len(meta['periods'])}** "
                     f"({meta['periods'][0]} → {meta['periods'][-1]})")
    return "\n".join(lines)


# ───────────────────────────── Statistics intents ────────────────────────────
def _answer_statistics(snapshot, message):
    plazas, scope_label, scope_kind, _ = _resolve_scope(message, snapshot)
    available = _available_periods(snapshot)
    single, rng = _extract_periods(message, available)
    if not single and not rng and _detect_latest_keyword(message) and available:
        single = available[-1]
    period_phrase = _build_period_phrase(single, rng, snapshot)

    metric = "revenue" if _wants_revenue(message) or not _wants_txn(message) else "transactions"
    rows = _rank_plazas(snapshot, plazas, single, rng, metric)
    if not rows:
        return f"No data for **{scope_label}** in {period_phrase}."

    values = [(r[2] if metric == "revenue" else r[1]) for r in rows]
    mean = statistics.mean(values)
    median = statistics.median(values)
    stdev = statistics.pstdev(values) if len(values) > 1 else 0.0
    total = sum(values)

    fmt = _fmt_inr if metric == "revenue" else _fmt_int
    return (
        f"**{metric.title()} statistics — {scope_label}**\n"
        f"_{period_phrase}_\n\n"
        f"• Plazas with data: **{len(rows)}**\n"
        f"• Total: **{fmt(total)}**\n"
        f"• Average per plaza: **{fmt(mean)}**\n"
        f"• Median per plaza: **{fmt(median)}**\n"
        f"• Std. deviation: **{fmt(stdev)}**\n"
        f"• Highest: **{rows[0][0]}** ({fmt(values[0])})\n"
        f"• Lowest: **{rows[-1][0]}** ({fmt(values[-1])})"
    )


# ───────────────────────────── Best/worst month ──────────────────────────────
def _answer_best_worst_month(snapshot, message, ascending=False):
    plazas, scope_label, _, _ = _resolve_scope(message, snapshot)
    metric = "revenue" if _wants_revenue(message) or not _wants_txn(message) else "transactions"
    available = _available_periods(snapshot)
    if not available:
        return "No monthly data is available."
    rows = []
    for (y, m) in available:
        rec = aggregate_plazas_for_month(snapshot, plazas, y, m)
        if not rec:
            continue
        rows.append((y, m, rec["total_count"], rec["total_amount"]))
    if not rows:
        return f"No data for **{scope_label}**."
    rows.sort(key=lambda r: r[3 if metric == "revenue" else 2], reverse=not ascending)
    fmt = _fmt_inr if metric == "revenue" else _fmt_int
    label = "lowest" if ascending else "highest"
    lines = [
        f"**{label.title()}-{metric} month — {scope_label}**", "",
    ]
    for i, (y, m, c, a) in enumerate(rows[:5], 1):
        v = a if metric == "revenue" else c
        lines.append(f"{i}. **{_period_label(y, m)}** — {fmt(v)} "
                     f"({_fmt_int(c)} txns · {_fmt_inr(a)})")
    return "\n".join(lines)


# ───────────────────────────── MoM / YoY growth ──────────────────────────────
def _answer_growth(snapshot, message):
    plazas, scope_label, _, _ = _resolve_scope(message, snapshot)
    metric = "revenue" if _wants_revenue(message) or not _wants_txn(message) else "transactions"
    available = _available_periods(snapshot)
    if len(available) < 2:
        return "Need at least two months of data to compute growth."

    is_yoy = bool(re.search(r"\b(yoy|y-?o-?y|year[-\s]on[-\s]year|year[-\s]over[-\s]year)\b",
                            message, flags=re.IGNORECASE))

    rows = []
    for (y, m) in available:
        rec = aggregate_plazas_for_month(snapshot, plazas, y, m)
        if not rec:
            continue
        rows.append((y, m, rec["total_count"], rec["total_amount"]))
    if len(rows) < 2:
        return f"Not enough monthly data for **{scope_label}** to compute growth."

    fmt = _fmt_inr if metric == "revenue" else _fmt_int
    lines = [
        f"**{'YoY' if is_yoy else 'MoM'} growth ({metric}) — {scope_label}**",
        "",
    ]
    if is_yoy:
        # pair each (y,m) with (y-1,m)
        idx = {(y, m): (c, a) for (y, m, c, a) in rows}
        for (y, m, c, a) in rows:
            prev = idx.get((y - 1, m))
            if not prev:
                continue
            cur = a if metric == "revenue" else c
            old = prev[1] if metric == "revenue" else prev[0]
            pct = ((cur - old) / old * 100) if old else 0.0
            lines.append(
                f"• **{_period_label(y, m)}** vs {_period_label(y - 1, m)}: "
                f"{fmt(cur)} (was {fmt(old)}) → {_fmt_pct(pct)}"
            )
    else:
        for i in range(1, len(rows)):
            (y, m, c, a) = rows[i]
            (py, pm, pc, pa) = rows[i - 1]
            cur = a if metric == "revenue" else c
            old = pa if metric == "revenue" else pc
            pct = ((cur - old) / old * 100) if old else 0.0
            lines.append(
                f"• **{_period_label(y, m)}** vs {_period_label(py, pm)}: "
                f"{fmt(cur)} (was {fmt(old)}) → {_fmt_pct(pct)}"
            )
    if len(lines) <= 2:
        return f"Not enough overlapping months for **{scope_label}** to compute "\
               f"{'YoY' if is_yoy else 'MoM'} growth."
    return "\n".join(lines)


# ───────────────────────────── Vehicle category metric ───────────────────────
def _answer_category_metric(snapshot, message, vehicle_name):
    """Total / per-period count + amount for a single vehicle category in a scope."""
    plazas, scope_label, _, _ = _resolve_scope(message, snapshot)
    available = _available_periods(snapshot)
    single, rng = _extract_periods(message, available)
    if not single and not rng and _detect_latest_keyword(message) and available:
        single = available[-1]

    rec, single_used, rng_used = _aggregate_for_scope(snapshot, plazas, single, rng)
    if not rec:
        return f"No data for **{scope_label}** in {_build_period_phrase(single, rng, snapshot)}."
    target = next((c for c in rec["categories"] if c["name"] == vehicle_name), None)
    if not target:
        return (f"No **{vehicle_name}** transactions found in **{scope_label}** "
                f"({_build_period_phrase(single_used, rng_used, snapshot)}).")
    period_phrase = _build_period_phrase(single_used, rng_used, snapshot)
    return (
        f"**{vehicle_name} — {scope_label}** ({period_phrase})\n\n"
        f"• Transactions: **{_fmt_int(target['count'])}** ({target['count']:,})\n"
        f"• Revenue: **{_fmt_inr(target['amount'])}** (₹{target['amount']:,.0f})\n"
        f"• Share of count: **{target['share_count']:.1f}%**\n"
        f"• Share of revenue: **{target['share_amount']:.1f}%**\n"
        f"• Average fare: **₹{target['avg_fare']:,.2f}**"
    )


# ───────────────────────────── Search / filter intents ───────────────────────
def _answer_search_plazas(snapshot, message):
    msg = message.lower()
    m_start = re.search(r"\bplazas?\s+(starting|beginning)\s+with\s+([a-z0-9])\b", msg)
    if m_start:
        letter = m_start.group(2)
        hits = sorted(p for p in _all_plazas(snapshot) if p.lower().startswith(letter))
        if not hits:
            return f"No plazas start with **{letter.upper()}**."
        head = f"**Plazas starting with '{letter.upper()}'** — {len(hits)} found"
        if len(hits) > 50:
            return f"{head}\n\nFirst 50: {', '.join(hits[:50])} …"
        return f"{head}\n\n" + "\n".join(f"• {p}" for p in hits)

    m_contain = re.search(r"\bplazas?\s+(containing|with\s+name|named?)\s+([a-z0-9]+)\b", msg)
    if m_contain:
        needle = m_contain.group(2)
        hits = sorted(p for p in _all_plazas(snapshot) if needle in p.lower())
        if not hits:
            return f"No plazas contain **'{needle}'**."
        head = f"**Plazas matching '{needle}'** — {len(hits)} found"
        if len(hits) > 50:
            return f"{head}\n\nFirst 50: {', '.join(hits[:50])} …"
        return f"{head}\n\n" + "\n".join(f"• {p}" for p in hits)

    return None


# ───────────────────────────── Main entry point ──────────────────────────────
def answer(snapshot: dict, message: str) -> dict:
    msg = (message or "").strip()
    if not msg:
        return {"reply": "Please ask a question about the toll data.",
                "matched": False, "intent": "empty"}

    msg_lower = msg.lower()
    available = _available_periods(snapshot)

    # ── Help / greeting / thanks ─────────────────────────────────────────────
    if _RX_HELP.match(msg):
        return {"reply": _answer_help(), "matched": True, "intent": "help"}
    if _RX_THANKS.match(msg):
        return {"reply": _answer_thanks(), "matched": True, "intent": "thanks"}

    # ── List-style metadata intents ──────────────────────────────────────────
    if re.search(r"\b(list|all|available|how\s+many|what\s+are\s+the)\b\s*"
                 r"(spvs?|special\s+purpose\s+vehicles?)\b", msg_lower) \
            or re.fullmatch(r"\s*spvs?\s*\??", msg_lower):
        # "how many" SPVs is a count — resolved by counting branch below
        if _RX_COUNT.search(msg_lower):
            r = _answer_count(snapshot, msg)
            if r: return {"reply": r, "matched": True, "intent": "count_spvs"}
        return {"reply": _answer_list_spvs(snapshot),
                "matched": True, "intent": "list_spvs"}

    if re.search(r"\b(list|all|available|how\s+many|what\s+are\s+the)\b\s*rounds?\b", msg_lower) \
            or re.fullmatch(r"\s*rounds?\s*\??", msg_lower):
        if _RX_COUNT.search(msg_lower):
            r = _answer_count(snapshot, msg)
            if r: return {"reply": r, "matched": True, "intent": "count_rounds"}
        return {"reply": _answer_list_rounds(snapshot),
                "matched": True, "intent": "list_rounds"}

    if re.search(r"\b(list|all|available|how\s+many|what\s+are\s+the)\b\s*projects?\b",
                 msg_lower):
        if _RX_COUNT.search(msg_lower):
            r = _answer_count(snapshot, msg)
            if r: return {"reply": r, "matched": True, "intent": "count_projects"}
        return {"reply": _answer_list_projects(snapshot),
                "matched": True, "intent": "list_projects"}

    if re.search(r"\b(list|show)\s+(?:all\s+)?(regional\s+offices?|ros?)\b", msg_lower):
        return {"reply": _answer_list_regions(snapshot, "ro"),
                "matched": True, "intent": "list_ros"}

    if re.search(r"\b(list|show)\s+(?:all\s+)?(pius?|cities)\b", msg_lower):
        return {"reply": _answer_list_regions(snapshot, "piu"),
                "matched": True, "intent": "list_pius"}

    if re.search(r"\b(list|all|what\s+are\s+the|how\s+many)\b.*"
                 r"\b(vehicle\s+(categories|types|classes))\b", msg_lower):
        if _RX_COUNT.search(msg_lower):
            r = _answer_count(snapshot, msg)
            if r: return {"reply": r, "matched": True, "intent": "count_vehicle_categories"}
        return {"reply": _answer_list_vehicle_categories(snapshot),
                "matched": True, "intent": "list_vehicle_categories"}

    if re.search(r"\b(available|what)\s+(months?|years?|periods?|"
                 r"data\s+(do\s+you|is)\s+(have|available))\b", msg_lower) \
            or re.search(r"\b(when\s+was\s+the\s+data\s+last\s+updated|"
                         r"how\s+(recent|fresh)\s+is\s+the\s+data)\b", msg_lower):
        return {"reply": _answer_available_periods(snapshot),
                "matched": True, "intent": "available_periods"}

    # ── 'how many' counting intents ──────────────────────────────────────────
    if _RX_COUNT.search(msg_lower):
        r = _answer_count(snapshot, msg)
        if r:
            return {"reply": r, "matched": True, "intent": "count"}

    # ── Search by name ───────────────────────────────────────────────────────
    if _RX_SEARCH_PLAZAS.search(msg_lower):
        r = _answer_search_plazas(snapshot, msg)
        if r:
            return {"reply": r, "matched": True, "intent": "search_plazas"}

    # ── List plazas (broad fallback for "plazas in X") ───────────────────────
    if re.search(r"\b(list|all|available|what\s+are\s+the)\b.*\bplazas?\b", msg_lower) \
            or re.search(r"\bplazas?\s+(in|under|within|of|from)\b", msg_lower):
        single, rng = _extract_periods(msg, available)
        is_metric = _wants_revenue(msg) or _wants_txn(msg) \
            or _RX_TOP.search(msg) or _RX_BOTTOM.search(msg)
        if not single and not rng and not is_metric:
            return {"reply": _answer_list_plazas(snapshot, msg),
                    "matched": True, "intent": "list_plazas"}

    # ── Plaza meta-info ("where is Bassi") ──────────────────────────────────
    if _RX_LOCATION_Q.search(msg_lower):
        plazas_in_msg = _detect_plazas_in_message(msg, _all_plazas(snapshot))
        if plazas_in_msg:
            return {"reply": _answer_plaza_meta(snapshot, plazas_in_msg[0]),
                    "matched": True, "intent": "plaza_meta"}

    # ── Resolve scope + period for downstream intents ───────────────────────
    plazas, scope_label, scope_kind, scope_meta = _resolve_scope(msg, snapshot)
    single, rng = _extract_periods(msg, available)
    if not single and not rng and _detect_latest_keyword(msg) and available:
        single = available[-1]
    period_phrase = _build_period_phrase(single, rng, snapshot)

    # ── Best / worst month ──────────────────────────────────────────────────
    if _RX_BEST_MONTH.search(msg_lower):
        return {"reply": _answer_best_worst_month(snapshot, msg, ascending=False),
                "matched": True, "intent": "best_month"}
    if _RX_WORST_MONTH.search(msg_lower):
        return {"reply": _answer_best_worst_month(snapshot, msg, ascending=True),
                "matched": True, "intent": "worst_month"}

    # ── Growth (MoM / YoY) ──────────────────────────────────────────────────
    if _RX_GROWTH.search(msg_lower):
        return {"reply": _answer_growth(snapshot, msg),
                "matched": True, "intent": "growth"}

    # ── Compare two or more scopes ──────────────────────────────────────────
    spvs_in_msg = _detect_all_spvs(msg, snapshot.get("taxonomy", {}).get("spvs", []))
    rounds_in_msg = _detect_all_rounds(msg, snapshot.get("taxonomy", {}).get("rounds", []))

    compare_groups: list[tuple[str, list[str]]] = []
    if scope_kind == "plaza_compare":
        for p in (scope_meta.get("plazas") or plazas)[:5]:
            compare_groups.append((p, [p]))
    elif _RX_COMPARE.search(msg) and len(spvs_in_msg) >= 2:
        for s in spvs_in_msg[:5]:
            grp_plazas, _ = _scope_from_filters(snapshot, s, None)
            compare_groups.append((f"SPV {s}", grp_plazas))
    elif _RX_COMPARE.search(msg) and len(rounds_in_msg) >= 2:
        for r in rounds_in_msg[:5]:
            grp_plazas, _ = _scope_from_filters(snapshot, None, r)
            compare_groups.append((f"Round {r}", grp_plazas))

    if compare_groups:
        labels = [g[0] for g in compare_groups]
        sections = [f"**Comparison — {' vs '.join(labels)}** ({period_phrase})", ""]
        rows_for_diff: list[tuple[str, dict]] = []
        for label, group_plazas in compare_groups:
            rec, _, _ = _aggregate_for_scope(snapshot, group_plazas, single, rng)
            if not rec:
                sections.append(f"### {label}\n_No data for {period_phrase}._\n")
                continue
            rows_for_diff.append((label, rec))
            sections.append(
                f"### {label}\n"
                f"• Revenue: **{_fmt_inr(rec['total_amount'])}** "
                f"(₹{rec['total_amount']:,.0f})\n"
                f"• Transactions: **{_fmt_int(rec['total_count'])}** "
                f"({rec['total_count']:,})\n"
                f"• Avg fare/txn: **₹{rec['avg_per_txn']:,.2f}**\n"
                f"• Plazas with data: **{len(rec.get('plazas_included', []))}** "
                f"of {len(group_plazas)}\n"
                f"• Top category: **{rec['top_by_amount']['name']}** "
                f"({_fmt_inr(rec['top_by_amount']['amount'])})\n"
            )
        if len(rows_for_diff) >= 2:
            rows_for_diff.sort(key=lambda r: r[1]["total_amount"], reverse=True)
            top = rows_for_diff[0]
            sections.append(
                f"**Highest revenue:** {top[0]} ({_fmt_inr(top[1]['total_amount'])})"
            )
        return {"reply": "\n".join(sections), "matched": True, "intent": "compare"}

    # ── Top-N / Bottom-N rankings ───────────────────────────────────────────
    if (_RX_TOP.search(msg) or _RX_BOTTOM.search(msg)) and \
            scope_kind in ("all", "spv_round", "state", "piu", "ro", "project"):
        is_category_q = bool(re.search(r"\btop\s+(category|categor)", msg_lower))
        if not is_category_q:
            metric = "revenue" if _wants_revenue(msg) or not _wants_txn(msg) else "transactions"
            ascending = bool(_RX_BOTTOM.search(msg))
            n = _parse_top_n(msg, default=5)
            ranked = _rank_plazas(snapshot, plazas, single, rng, metric)
            return {
                "reply": _format_ranking_block(
                    ranked, scope_label, period_phrase, metric,
                    n=n, ascending=ascending, total_pool=len(ranked),
                ),
                "matched": True,
                "intent": "bottom_n" if ascending else "top_n",
            }

    # ── Statistics (avg/median/std-dev) ─────────────────────────────────────
    if _RX_AVG.search(msg_lower) and re.search(
        r"\b(per\s+plaza|across\s+(plazas?|the\s+network)|"
        r"plaza[-\s]level|by\s+plaza|each\s+plaza)\b", msg_lower):
        return {"reply": _answer_statistics(snapshot, msg),
                "matched": True, "intent": "statistics"}

    # ── Trend ───────────────────────────────────────────────────────────────
    if _RX_TREND.search(msg):
        return {
            "reply": _format_trend_block(snapshot, plazas, scope_label, single, rng),
            "matched": True,
            "intent": "trend",
        }

    # ── Vehicle category breakdown vs single category metric ────────────────
    vehicle = _detect_vehicle_category(msg)
    if _RX_CATEGORY.search(msg) and not vehicle:
        rec, single_used, rng_used = _aggregate_for_scope(snapshot, plazas, single, rng)
        ph = _build_period_phrase(single_used, rng_used, snapshot)
        return {"reply": _format_category_block(rec, scope_label, ph),
                "matched": True, "intent": "category_breakdown"}
    if vehicle and (_wants_revenue(msg) or _wants_txn(msg) or _RX_CATEGORY.search(msg)):
        return {"reply": _answer_category_metric(snapshot, msg, vehicle),
                "matched": True, "intent": "category_metric"}

    # ── Default: scope summary (totals) ─────────────────────────────────────
    rec, single_used, rng_used = _aggregate_for_scope(snapshot, plazas, single, rng)
    ph = _build_period_phrase(single_used, rng_used, snapshot)
    reply = _format_summary_block(rec, scope_label, ph, plazas_total=len(plazas))

    if scope_kind == "plaza" and rec:
        trend = snapshot.get("monthly_totals", {}).get(plazas[0], [])[-6:]
        if trend:
            tail = "\n\n**Last {} months:**\n".format(len(trend)) + "\n".join(
                f"• {t['label']} — {_fmt_int(t['count'])} txns · {_fmt_inr(t['amount'])}"
                for t in trend
            )
            reply = reply + tail

    return {"reply": reply, "matched": True, "intent": "totals"}
