"""Shared constants — kept in one place so the parser, build script and
server agree on the canonical NHIT plaza list and month tables."""
from __future__ import annotations

import calendar

NHIT_PLAZAS: list[str] = [
    "Aroli", "Bahoripar Fee Plaza", "Balibhasa", "Bankapur", "Bassi",
    "Bhadarabad TOLL PLAZA", "Bhojpuri Toll Plaza", "Chalageri",
    "Chamari Toll Plaza", "Chhapar TOLL PLAZA", "Dahalapara", "Daroada",
    "Dasarkhed Toll Plaza", "Dhaneshwar", "Dukkavanipalam Toll Plaza",
    "Faridpur Toll Plaza", "Gadanki toll plaza", "Galia", "Hattargi", "Hebbalu",
    "KHEMANA TOLL PLAZA", "Kalajhar Toll Plaza", "Kalaparru Toll Plaza",
    "Kelapur", "Khawasa Toll Plaza", "Kherwasani", "Kognoli",
    "Kurankhed Toll Plaza", "Madai Fee Plaza", "Madapam Toll Plaza",
    "Mahasamudram", "Maigalganj Toll Plaza", "Marripalem Toll Plaza",
    "Mohtara Toll Plaza", "Mokha Toll Plaza", "Mudhipar TOLL PLAZA",
    "Nashirabad Toll Plaza", "Nathavalasa", "Odaki Pipkhar",
    "Patgaon Toll Plaza", "Pullur Toll Plaza", "Raibha", "Raksha",
    "Tarapoungi plaza", "Taroda-Kasba Toll Plaza", "UNDVARIYA TOLL PLAZA",
    "VeeraValli TOLL PLAZA",
]

MONTH_MAP: dict[str, int] = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

MONTH_NAMES: dict[int, str] = {
    1: "January", 2: "February", 3: "March",     4: "April",
    5: "May",     6: "June",     7: "July",      8: "August",
    9: "September", 10: "October", 11: "November", 12: "December",
}

# Canonical display order for vehicle categories. Mirrors CATEGORY_ORDER in
# frontend/js/utils.js so backend output stays in sync with the dashboard.
CATEGORY_ORDER: list[str] = [
    "Car / Jeep / Van (VC4)",
    "Light Commercial Vehicle (VC5)",
    "Bus / Truck - 2 Axle (VC6)",
    "3-Axle Vehicle (VC7)",
    "4-6 Axle (VC8/9/10)",
    "Oversized Vehicle (VC11+)",
]

# Short labels for compact UI surfaces (chatbot summaries, exports, etc).
CATEGORY_SHORT: dict[str, str] = {
    "Car / Jeep / Van (VC4)":         "CJV",
    "Light Commercial Vehicle (VC5)": "LCV",
    "Bus / Truck - 2 Axle (VC6)":     "BUS/2A-Truck",
    "3-Axle Vehicle (VC7)":           "3A-Truck",
    "4-6 Axle (VC8/9/10)":            "MAV",
    "Oversized Vehicle (VC11+)":      "OSV",
}


def days_in_month(year: int, month: int) -> int:
    """Number of days in (year, month). Handles leap years correctly."""
    return calendar.monthrange(year, month)[1]
