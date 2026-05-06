import unittest

from backend.core.ingestor import (
    normalize_plaza_name,
    build_plaza_alias_map,
    bucket_rows_by_canonical_plaza,
)


class TestIngestor(unittest.TestCase):
    def test_normalize_plaza_name(self):
        self.assertEqual(normalize_plaza_name("Bhadarabad TOLL PLAZA"), "bhadarabad")
        self.assertEqual(normalize_plaza_name("  KHEMANA  Toll Plaza "), "khemana")

    def test_alias_map_prefers_preferred_name(self):
        rows = [
            {"plaza_name": "Bhadarabad Toll Plaza"},
            {"plaza_name": "Bhadarabad TOLL PLAZA"},
            {"plaza_name": "Bhadarabad Toll Plaza"},
        ]
        alias_map = build_plaza_alias_map(rows, preferred_names=["Bhadarabad TOLL PLAZA"])
        self.assertEqual(alias_map["Bhadarabad Toll Plaza"], "Bhadarabad TOLL PLAZA")

    def test_alias_map_uses_frequency(self):
        rows = [
            {"plaza_name": "Alpha Toll Plaza"},
            {"plaza_name": "Alpha Toll Plaza"},
            {"plaza_name": "Alpha Plaza"},
        ]
        alias_map = build_plaza_alias_map(rows)
        self.assertEqual(alias_map["Alpha Plaza"], "Alpha Toll Plaza")

    def test_bucket_rows_merges_variants(self):
        rows = [
            {"plaza_name": "Bhadarabad TOLL PLAZA", "vehicle_category": "Car", "count": 2, "amount": 20},
            {"plaza_name": "Bhadarabad Toll Plaza", "vehicle_category": "Car", "count": 3, "amount": 30},
        ]
        alias_map = build_plaza_alias_map(rows, preferred_names=["Bhadarabad TOLL PLAZA"])
        bucket = bucket_rows_by_canonical_plaza(rows, alias_map)
        canon = alias_map["Bhadarabad Toll Plaza"]
        self.assertEqual(bucket[canon]["Car"]["count"], 5)
        self.assertEqual(bucket[canon]["Car"]["amount"], 50.0)


if __name__ == "__main__":
    unittest.main()
