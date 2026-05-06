import unittest

from backend.core.analytics import aggregate_plazas_for_month, aggregate_plazas_for_range


class TestAnalytics(unittest.TestCase):
    def setUp(self):
        self.snapshot = {
            "data": {
                "Plaza A": {
                    "2025-01": {
                        "categories": [
                            {"name": "Car", "count": 10, "amount": 100.0},
                        ]
                    },
                    "2025-02": {
                        "categories": [
                            {"name": "Car", "count": 5, "amount": 60.0},
                        ]
                    },
                },
                "Plaza B": {
                    "2025-01": {
                        "categories": [
                            {"name": "Car", "count": 20, "amount": 200.0},
                        ]
                    },
                    "2025-02": {
                        "categories": [
                            {"name": "Bus", "count": 2, "amount": 80.0},
                        ]
                    },
                },
            }
        }

    def test_aggregate_single_month_all_plazas(self):
        rec = aggregate_plazas_for_month(self.snapshot, ["Plaza A", "Plaza B"], 2025, 1)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["total_count"], 30)
        self.assertEqual(rec["total_amount"], 300.0)
        self.assertEqual(rec["category_count"], 1)

    def test_aggregate_single_month_specific_plaza(self):
        rec = aggregate_plazas_for_month(self.snapshot, ["Plaza A"], 2025, 1)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["total_count"], 10)
        self.assertEqual(rec["total_amount"], 100.0)

    def test_aggregate_range(self):
        rec = aggregate_plazas_for_range(self.snapshot, ["Plaza A"], 2025, 1, 2025, 2)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["total_count"], 15)
        self.assertEqual(rec["total_amount"], 160.0)
        self.assertEqual(len(rec["months_included"]), 2)


if __name__ == "__main__":
    unittest.main()
