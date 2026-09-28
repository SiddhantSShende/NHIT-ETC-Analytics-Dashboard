import unittest

from scripts.ihmcl_crawler import classify


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


if __name__ == "__main__":
    unittest.main()