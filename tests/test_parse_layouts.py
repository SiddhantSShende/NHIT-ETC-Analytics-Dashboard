"""Parser coverage for the report layouts IHMCL has shipped.

The tables below are shaped exactly like what pdfplumber returned for the real
Sep/Oct 2026 PDFs (see scripts/build_json_export.py::parse_vc_wise).
"""
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import build_json_export as build

ROOT = Path(__file__).resolve().parent.parent

TITLE = ["VC Wise Monthly ETC FASTag Data for the Month of X"] + [None] * 16
CATS = ["CAR_JEEP", "LCV", "BUS_TRUCK", "3_AXLE", "4-6_AXLE", "OSV"]
CAR = "Car / Jeep / Van (VC4)"


class FakePage:
    def __init__(self, tables):
        self._tables = tables

    def extract_tables(self):
        return self._tables


class FakePdf:
    def __init__(self, tables):
        # One table per page, like the real reports: a list of rows each.
        self.pages = [FakePage([t]) for t in tables]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def parse_with(pages, fn=build.parse_vc_wise):
    with patch.object(build.pdfplumber, "open", lambda path: FakePdf(pages)):
        return fn(Path("fake.pdf"))


def header(prefix=(), totals=True):
    cols = [*prefix, "PLAZA_NAME", "PIU", "RO"]
    for c in CATS:
        cols += [f"{c}_CNT", f"{c}_AMT"]
    if totals:
        cols += ["TOTAL_CNT", "TOTAL_AMT"]
    return cols


class TestSeptemberLayout(unittest.TestCase):
    """PLAZA_NAME first, TOTAL columns present, thousands-separated and
    space-split numbers (pdfplumber splits digits like '1 5,194')."""

    def pages(self):
        return [[
            TITLE[:17],
            header(),
            ["Arifke Fee Plaza", "Ludhiana", "Chandigarh",
             "1 5,194", "2,84,370", "522", "20,075", "1,010", "8 8,835",
             "1 75", "17,425", "749", "1,11,365", "-", "-", "17,650", "5,22,070"],
        ]]

    def test_rows_columns_and_totals_are_read(self):
        per_plaza, totals = parse_with(self.pages())

        plaza = per_plaza["Arifke Fee Plaza"]
        self.assertEqual((plaza["piu"], plaza["ro"]), ("Ludhiana", "Chandigarh"))
        self.assertEqual(plaza["vehicles"][CAR], {"count": 15194, "amount": 284370.0})
        self.assertNotIn("Oversized Vehicle (VC11+)", plaza["vehicles"])  # '-' cells skipped
        self.assertEqual(totals["Arifke Fee Plaza"], {"count": 17650, "amount": 522070.0})

    def test_extracted_total_matches_the_pdf_total_so_validation_stays_quiet(self):
        per_plaza, totals = parse_with(self.pages())

        self.assertEqual(
            build.validate_totals("sept", per_plaza, totals), 0
        )

    def test_a_wrong_pdf_total_is_flagged(self):
        pages = self.pages()
        pages[0][2][-2] = "99,999"  # TOTAL_CNT disagrees with the categories

        per_plaza, totals = parse_with(pages)

        self.assertGreater(build.validate_totals("sept", per_plaza, totals), 0)


class TestOctoberLayout(unittest.TestCase):
    """MONTH column first, plain integers, and NO TOTAL_CNT/TOTAL_AMT columns."""

    KHERKI = ["Oct-26", "Kherki Daula Toll Plaza", "Dwarka", "Delhi",
              "1340304", "40736850", "116530", "13567845", "131645", "31648510",
              "15905", "5293305", "61788", "28500195", "35", "17205"]

    def pages(self):
        return [
            [TITLE[:16], header(prefix=("MONTH",), totals=False), self.KHERKI],
            # A continuation page: no header row, plaza column still index 1.
            [[
                "Oct-26", "Loni Toll Plaza", "Ghaziabad", "Delhi",
                "100", "1000", "10", "100", "0", "0", "0", "0", "0", "0", "0", "0",
            ], [
                "Oct-26", "Loni Toll Plaza", "Ghaziabad", "Delhi",
                "50", "500", "5", "50", "0", "0", "0", "0", "0", "0", "0", "0",
            ], [
                "Oct-26", "", "", "", "9", "9", "9", "9", "9", "9", "9", "9", "9", "9", "9", "9",
            ]],
        ]

    def test_plaza_is_found_after_the_month_column(self):
        per_plaza, _ = parse_with(self.pages())

        plaza = per_plaza["Kherki Daula Toll Plaza"]
        self.assertEqual((plaza["piu"], plaza["ro"]), ("Dwarka", "Delhi"))
        self.assertEqual(plaza["vehicles"][CAR], {"count": 1340304, "amount": 40736850.0})
        self.assertNotIn("Oct-26", per_plaza)  # the month cell is never a plaza

    def test_all_six_categories_are_summed(self):
        per_plaza, _ = parse_with(self.pages())

        total = per_plaza["Kherki Daula Toll Plaza"]["total_extracted"]
        self.assertEqual(total["count"], 1340304 + 116530 + 131645 + 15905 + 61788 + 35)
        self.assertEqual(
            total["amount"], 40736850 + 13567845 + 31648510 + 5293305 + 28500195 + 17205
        )

    def test_repeated_plaza_rows_are_added_together(self):
        per_plaza, _ = parse_with(self.pages())

        self.assertEqual(per_plaza["Loni Toll Plaza"]["vehicles"][CAR]["count"], 150)

    def test_blank_plaza_rows_are_skipped(self):
        per_plaza, _ = parse_with(self.pages())

        self.assertEqual(set(per_plaza), {"Kherki Daula Toll Plaza", "Loni Toll Plaza"})

    def test_no_totals_means_no_pdf_totals_and_no_crash(self):
        per_plaza, totals = parse_with(self.pages())

        self.assertEqual(totals, {})
        self.assertEqual(build.validate_totals("oct", per_plaza, totals), 0)


class TestAnnualPassLayouts(unittest.TestCase):
    def test_seven_column_2026_layout_uses_total_qualified(self):
        pages = [[
            ["Month-Year", "Plaza Name", "PIU", "RO", "Sum of QUALIFIED_SJ_TXN",
             "Sum of QUALIFIED_RJ_TXN", "Sum of TOTAL_QUALIFIED_TXN"],
            ["Sep-26", "Arifke Fee Plaza", "Ludhiana", "Chandigarh", "2882", "1034", "3916"],
            ["Sep-26", "18th Mile Toll Plaza", "Malda", "Kolkata", "29523", "0", "29523"],
        ]]

        rows = parse_with(pages, build.parse_annual_pass)

        self.assertEqual(rows["Arifke Fee Plaza"]["transaction_count"], 3916)
        self.assertEqual(rows["18th Mile Toll Plaza"]["transaction_count"], 29523)
        self.assertEqual(rows["Arifke Fee Plaza"]["piu"], "Ludhiana")


class TestPeriodFromFilename(unittest.TestCase):
    def test_every_main_report_naming_style(self):
        for name, period in (
            ("Aug-2026.pdf", "2026-08"),
            ("June-2026.pdf", "2026-06"),
            ("Jul-2026-1.pdf", "2026-07"),
            ("Sept-2025-ETC-Data.pdf", "2025-09"),
            ("VC-wise-ETC-Data-Sept-2026.pdf", "2026-09"),
            ("VC-wise-ETC-Data-Oct-2026.pdf", "2026-10"),
            ("Sep-2026-Annual-Pass-Data.pdf", "2026-09"),
        ):
            with self.subTest(name=name):
                self.assertEqual(build.period_from_filename(name), period)

    def test_unparseable_name_has_no_period(self):
        self.assertIsNone(build.period_from_filename("Set-2026-Annual-Pass-Data.pdf"))


@unittest.skipUnless(
    (ROOT / "downloads/vc_monthly/VC-wise-ETC-Data-Oct-2026.pdf").exists(),
    "October 2026 report not downloaded",
)
class TestRealOctoberReport(unittest.TestCase):
    """The real PDF, pinned to totals verified by summing its table independently."""

    def test_real_pdf_parses_to_the_verified_totals(self):
        per_plaza, totals = build.parse_vc_wise(
            ROOT / "downloads/vc_monthly/VC-wise-ETC-Data-Oct-2026.pdf"
        )

        self.assertEqual(len(per_plaza), 1273)
        self.assertEqual(totals, {})  # this report has no TOTAL columns
        self.assertEqual(sum(p["total_extracted"]["count"] for p in per_plaza.values()), 366_695_294)
        self.assertEqual(
            round(sum(p["total_extracted"]["amount"] for p in per_plaza.values())),
            62_437_304_810,
        )
        kherki = per_plaza["Kherki Daula Toll Plaza"]["vehicles"][CAR]
        self.assertEqual(kherki, {"count": 1340304, "amount": 40736850.0})


if __name__ == "__main__":
    unittest.main()
