import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import ihmcl_crawler as crawler
from scripts.ihmcl_crawler import classify

BASE = "https://ihmcl.co.in/wp-content/uploads/2026/10/"


class TestCrawlerClassification(unittest.TestCase):
    def test_vc_wise_monthly_data_report_is_classified_as_periodic_mlff(self):
        report = classify(
            "https://ihmcl.co.in/reports/VC_Wise_Monthly_Data_Aug_2026.pdf"
        )

        self.assertEqual(report.family, "mlff")
        self.assertEqual(report.period, "2026-08")

    def test_september_etc_report_is_classified(self):
        report = classify("https://ihmcl.co.in/reports/Sept-2026-ETC-Data.pdf")

        self.assertEqual(report.family, "etc")
        self.assertEqual(report.period, "2026-09")

    def test_september_annual_pass_report_is_classified(self):
        report = classify(
            "https://ihmcl.co.in/reports/Sept-2026-Annual-Pass-Data.pdf"
        )

        self.assertEqual(report.family, "annual_pass")
        self.assertEqual(report.period, "2026-09")

    # Real filenames IHMCL published for Sep/Oct 2026. The crawler archived
    # both etc reports as "unclassified" and the dashboard froze at August.
    def test_current_ihmcl_main_report_names_are_etc(self):
        for name, period in (
            ("VC-wise-ETC-Data-Sept-2026.pdf", "2026-09"),
            ("VC-wise-ETC-Data-Oct-2026.pdf", "2026-10"),
        ):
            with self.subTest(name=name):
                report = classify(BASE + name)
                self.assertEqual(report.family, "etc")
                self.assertEqual(report.period, period)

    def test_set_typo_annual_pass_is_september_and_saved_under_clean_name(self):
        url = BASE + "Set-2026-Annual-Pass-Data.pdf"
        report = classify(url)

        self.assertEqual(report.family, "annual_pass")
        self.assertEqual(report.period, "2026-09")
        self.assertEqual(report.filename, "Sep-2026-Annual-Pass-Data.pdf")
        self.assertEqual(report.url, url)  # still fetched from IHMCL's real URL

    def test_typo_fix_does_not_touch_other_names(self):
        self.assertEqual(crawler.normalize_name("Settings-2026.pdf"), "Settings-2026.pdf")
        self.assertEqual(crawler.normalize_name("Sept-2026-ETC-Data.pdf"), "Sept-2026-ETC-Data.pdf")

    def test_oct_annual_pass_report_is_classified(self):
        report = classify(BASE + "Oct-2026-Annual-Pass-Data.pdf")

        self.assertEqual((report.family, report.period), ("annual_pass", "2026-10"))

    def test_annual_pass_does_not_need_to_start_with_a_month(self):
        report = classify(BASE + "Annual-Pass-Data-Nov-2026.pdf")

        self.assertEqual((report.family, report.period), ("annual_pass", "2026-11"))

    def test_unknown_monthly_looking_name_is_unclassified_not_dropped(self):
        report = classify(BASE + "Plaza-Wise-Toll-Nov-2026.pdf")

        self.assertEqual((report.family, report.period), ("unclassified", "2026-11"))

    def test_non_monthly_documents_are_ignored(self):
        self.assertIsNone(classify(BASE + "Parking-Sites-Onboarded.pdf"))
        self.assertIsNone(classify(BASE + "ANNUAL-REPORT-2025-26.pdf"))


class TestUnclassifiedHandling(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.vc = root / "vc_monthly"
        self.unclassified = root / "unclassified"
        self.vc.mkdir()
        self.unclassified.mkdir()
        (self.vc / "Oct-2026.pdf").write_bytes(b"x")  # latest ingested: 2026-10
        for attr, value in (
            ("VC_MONTHLY_DIR", self.vc),
            ("UNCLASSIFIED_DIR", self.unclassified),
            ("ETC_MONTHLY_DIR", root / "none"),
            ("ANNUAL_PASS_DIR", root / "none"),
            ("MLFF_DIR", root / "none"),
        ):
            patcher = patch.object(crawler, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _report(self, name, period):
        return crawler.RemoteReport(BASE + name, name, "unclassified", period)

    def test_renamed_main_report_is_promoted_by_its_title(self):
        (self.unclassified / "Brand-New-Name.pdf").write_bytes(b"x")
        report = self._report("Brand-New-Name.pdf", "2026-11")
        downloaded = []

        with patch.object(crawler, "looks_like_main_etc_report", return_value=True):
            unresolved = crawler.promote_unclassified([report], downloaded)

        self.assertEqual(unresolved, [])
        self.assertTrue((self.vc / "Brand-New-Name.pdf").exists())
        self.assertFalse((self.unclassified / "Brand-New-Name.pdf").exists())
        promoted, path = downloaded[0]
        self.assertEqual((promoted.family, promoted.period), ("etc", "2026-11"))
        self.assertEqual(path, self.vc / "Brand-New-Name.pdf")

    def test_unidentified_file_is_left_alone_and_returned(self):
        (self.unclassified / "Something-Nov-2026.pdf").write_bytes(b"x")
        report = self._report("Something-Nov-2026.pdf", "2026-11")
        downloaded = []

        with patch.object(crawler, "looks_like_main_etc_report", return_value=False):
            unresolved = crawler.promote_unclassified([report], downloaded)

        self.assertEqual(unresolved, [report])
        self.assertEqual(downloaded, [])
        self.assertTrue((self.unclassified / "Something-Nov-2026.pdf").exists())

    def test_period_already_covered_is_not_promoted(self):
        (self.unclassified / "Other-Oct-2026.pdf").write_bytes(b"x")
        report = self._report("Other-Oct-2026.pdf", "2026-10")
        downloaded = []

        with patch.object(crawler, "looks_like_main_etc_report", return_value=True):
            crawler.promote_unclassified([report], downloaded)

        self.assertEqual(downloaded, [])
        self.assertFalse((self.vc / "Other-Oct-2026.pdf").exists())

    def test_crawl_reports_error_when_newer_unrecognised_report_is_stuck(self):
        links = [BASE + f"Mon{i}.pdf" for i in range(6)]
        stuck = classify(BASE + "Plaza-Wise-Toll-Nov-2026.pdf")
        monthly = [
            crawler.RemoteReport(u, f"x{i}", "etc", f"2026-0{i + 1}")
            for i, u in enumerate(links)
        ]
        (self.unclassified / stuck.filename).write_bytes(b"x")

        with (
            patch.object(crawler, "build_session"),
            patch.object(crawler, "fetch_reports_page", return_value=""),
            patch.object(crawler, "extract_pdf_links", return_value=links + ["stuck"]),
            patch.object(
                crawler, "classify", side_effect=[*monthly, stuck]
            ),
            patch.object(crawler, "looks_like_main_etc_report", return_value=False),
            patch.object(crawler, "download_one", return_value=self.vc / "Oct-2026.pdf"),
            patch.object(crawler.time, "sleep"),
        ):
            result = crawler.crawl()

        self.assertEqual(len(result.errors), 1)
        self.assertIn("Plaza-Wise-Toll-Nov-2026.pdf", result.errors[0])
        self.assertIn("NOT ingested", result.errors[0])


# Every report filename on https://ihmcl.co.in/etc-transaction-reports/ as of
# 2026-11-11, with how it must be classified. IHMCL has renamed the main report
# three times in six months; this pins every style it has used so a change to
# classify() cannot quietly turn a month into "unclassified" again.
LIVE_PAGE_SNAPSHOT = [
    # main ETC report: <Mon>-<Year>.pdf, <Mon>-<Year>-ETC-Data.pdf, VC-wise-ETC-Data-<Mon>-<Year>.pdf
    ("Jan-2025.pdf", "etc", "2025-01"),
    ("Jun-2025.pdf", "etc", "2025-06"),
    ("Aug-2025-ETC-Data.pdf", "etc", "2025-08"),
    ("Sept-2025-ETC-Data.pdf", "etc", "2025-09"),
    ("Oct-2025-ETC-Data.pdf", "etc", "2025-10"),
    ("Nov-2025.pdf", "etc", "2025-11"),
    ("Dec-2025.pdf", "etc", "2025-12"),
    ("Jan-2026.pdf", "etc", "2026-01"),
    ("May-2026.pdf", "etc", "2026-05"),
    ("June-2026.pdf", "etc", "2026-06"),
    ("Jul-2026-1.pdf", "etc", "2026-07"),
    ("Aug-2026.pdf", "etc", "2026-08"),
    ("VC-wise-ETC-Data-Sept-2026.pdf", "etc", "2026-09"),
    ("VC-wise-ETC-Data-Oct-2026.pdf", "etc", "2026-10"),
    # annual pass
    ("Dec-2025-Annual-Pass-Data.pdf", "annual_pass", "2025-12"),
    ("Sept-2025-Annual-Pass-Data.pdf", "annual_pass", "2025-09"),
    ("Aug-2025-Annual-Pass-Data-2.pdf", "annual_pass", "2025-08"),
    ("Jan-2026-Annual-Pass-Data.pdf", "annual_pass", "2026-01"),
    ("Aug-2026-Annual-Pass-Data.pdf", "annual_pass", "2026-08"),
    ("Set-2026-Annual-Pass-Data.pdf", "annual_pass", "2026-09"),  # IHMCL's typo
    ("Oct-2026-Annual-Pass-Data.pdf", "annual_pass", "2026-10"),
    # MLFF (archive + separate JSON, never part of the ETC totals)
    ("MLFF-Plaza-Data-May-26_v1.pdf", "mlff", "2026-05"),
    ("VC_Wise_Monthly_Data_June_2026.pdf", "mlff", "2026-06"),
    ("VC_Wise_Monthly_Data_July_2026-1.pdf", "mlff", "2026-07"),
    ("VC_Wise_Monthly_Data_Aug_2026.pdf", "mlff", "2026-08"),
    ("VC_Wise_Monthly_Data_Sep_2026.pdf", "mlff", "2026-09"),
    # everything else on the page is not a monthly report
    ("Parking-Sites-Onboarded.pdf", None, None),
    ("Fastag-Booklet-.pdf", None, None),
    ("ATMS_11-53.pdf", None, None),
    ("214_ATMS.pdf", None, None),
    ("DIRECTOR-REPORT.pdf", None, None),
    ("ANNUAL-RETURN-2024-25.pdf", None, None),
    ("ANNUAL-REPORT-2025-26.pdf", None, None),
    ("FINAL-ANNUAL-REPORT-2024.pdf", None, None),
    ("FAQ-Annual-Pass_12.08.25-3.pdf", None, None),
    ("Scheme-Guidelines-for-On-boarding-of-Border-Check-Post.pdf", None, None),
    ("Web_ready_Report-Workstream-1-NETC-Impact-Assessment.pdf", None, None),
]


class TestLivePageSnapshot(unittest.TestCase):
    def test_every_filename_classifies_as_expected(self):
        for name, family, period in LIVE_PAGE_SNAPSHOT:
            with self.subTest(name=name):
                report = classify(BASE + name)
                if family is None:
                    self.assertIsNone(report)
                else:
                    self.assertIsNotNone(report)
                    self.assertEqual((report.family, report.period), (family, period))

    def test_no_monthly_report_in_the_snapshot_is_left_unclassified(self):
        for name, family, _ in LIVE_PAGE_SNAPSHOT:
            report = classify(BASE + name)
            self.assertFalse(report and report.family == "unclassified", name)

    def test_main_report_name_is_case_and_separator_tolerant(self):
        for name in (
            "VC-wise-ETC-Data-Nov-2026.pdf",
            "VC_Wise_ETC_Data_Nov_2026.pdf",
            "vc-wise-etc-data-november-2026.pdf",
            "VC wise ETC Data Nov 2026.pdf",
        ):
            with self.subTest(name=name):
                report = classify(BASE + name)
                self.assertEqual((report.family, report.period), ("etc", "2026-11"))

    def test_url_encoded_names_are_decoded(self):
        report = classify(BASE + "VC%20wise%20ETC%20Data%20Nov%202026.pdf")

        self.assertEqual((report.family, report.period), ("etc", "2026-11"))


class TestPlanDownloads(unittest.TestCase):
    def local(self, **kw):
        return crawler.LocalState(
            etc=kw.get("etc", {}), ap=kw.get("ap", {}),
            mlff_names=set(), unclassified_names=set(),
        )

    def test_missing_september_and_october_are_planned(self):
        reports = [
            classify(BASE + "Aug-2026.pdf"),
            classify(BASE + "VC-wise-ETC-Data-Sept-2026.pdf"),
            classify(BASE + "VC-wise-ETC-Data-Oct-2026.pdf"),
        ]
        plan, variants = crawler.plan_downloads(
            reports, self.local(etc={"2026-08": "Aug-2026.pdf"})
        )

        self.assertEqual([r.period for r in plan], ["2026-09", "2026-10"])
        self.assertEqual(variants, [])

    def test_typo_annual_pass_is_not_redownloaded_once_saved_under_clean_name(self):
        report = classify(BASE + "Set-2026-Annual-Pass-Data.pdf")
        plan, variants = crawler.plan_downloads(
            [report], self.local(ap={"2026-09": "Sep-2026-Annual-Pass-Data.pdf"})
        )

        self.assertEqual((plan, variants), ([], []))

    def test_up_to_date_plan_is_empty(self):
        reports = [classify(BASE + "VC-wise-ETC-Data-Oct-2026.pdf")]
        plan, _ = crawler.plan_downloads(
            reports, self.local(etc={"2026-10": "VC-wise-ETC-Data-Oct-2026.pdf"})
        )

        self.assertEqual(plan, [])


if __name__ == "__main__":
    unittest.main()
