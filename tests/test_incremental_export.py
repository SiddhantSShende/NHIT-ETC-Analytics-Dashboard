import unittest

from scripts.build_json_export import source_is_new


class TestIncrementalSourceDetection(unittest.TestCase):
    def test_late_report_source_is_reprocessed_for_an_existing_period(self):
        self.assertTrue(
            source_is_new(
                "downloads/Monthly_Annual_Pass_Report/2026/Sep-2026-Annual-Pass-Data.pdf",
                ["downloads/vc_monthly/Sep-2026.pdf"],
            )
        )

    def test_existing_report_source_is_not_reprocessed(self):
        source = "downloads/Monthly_Annual_Pass_Report/2026/Sep-2026-Annual-Pass-Data.pdf"
        self.assertFalse(source_is_new(source, [source]))


if __name__ == "__main__":
    unittest.main()