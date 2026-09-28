import unittest

from scripts.ihmcl_crawler import classify


class TestCrawlerClassification(unittest.TestCase):
    def test_vc_wise_monthly_data_report_is_classified_as_periodic_mlff(self):
        report = classify(
            "https://ihmcl.co.in/reports/VC_Wise_Monthly_Data_Aug_2026.pdf"
        )

        self.assertEqual(report.family, "mlff")
        self.assertEqual(report.period, "2026-08")


if __name__ == "__main__":
    unittest.main()