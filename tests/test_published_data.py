"""Integrity of the data the dashboard actually serves (downloads/json/).

Everything the frontend shows derives from _index.json, so these checks are
about what a visitor would see: no missing months, no empty months.
"""
import json
import unittest
from pathlib import Path

from scripts import update_and_publish as publisher

JSON_DIR = Path(__file__).resolve().parent.parent / "downloads" / "json"
INDEX = JSON_DIR / "_index.json"


def month_after(period: str) -> str:
    y, m = int(period[:4]), int(period[5:])
    return f"{y + (m == 12):04d}-{(m % 12) + 1:02d}"


@unittest.skipUnless(INDEX.exists(), "no exported JSON in this checkout")
class TestPublishedData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = json.loads(INDEX.read_text(encoding="utf-8"))
        cls.periods = sorted(cls.index["periods"])

    def test_months_are_contiguous_with_no_gaps(self):
        expected = [self.periods[0]]
        while expected[-1] != self.periods[-1]:
            expected.append(month_after(expected[-1]))

        self.assertEqual(self.periods, expected, "a month is missing from the dashboard")

    def test_every_listed_month_has_a_file_with_plazas(self):
        for period in self.periods[-6:]:  # recent months; older ones are frozen
            with self.subTest(period=period):
                path = JSON_DIR / "monthly" / f"{period}.json"
                self.assertTrue(path.exists())
                self.assertGreater(json.loads(path.read_text(encoding="utf-8"))["plaza_count"], 0)

    def test_latest_months_pass_the_publish_plausibility_gate(self):
        for period in self.periods[-3:]:
            with self.subTest(period=period):
                publisher.check_plausible_volume(period, self.periods, allow_warnings=False)

    def test_no_validation_warnings_are_recorded(self):
        self.assertEqual(self.index.get("validation_warnings", 0), 0)

    def test_index_records_when_it_was_generated(self):
        self.assertTrue(self.index.get("generated_at"))


if __name__ == "__main__":
    unittest.main()
