import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import ihmcl_crawler
from scripts import update_and_publish as publisher


class TestProductionDeployment(unittest.TestCase):
    generated_at = "2026-09-15T04:41:57+00:00"

    def test_ensure_deployed_returns_when_production_matches(self):
        with (
            patch.object(publisher, "read_index", return_value={"generated_at": self.generated_at}),
            patch.object(publisher, "fetch_live_generated_at", return_value=self.generated_at),
        ):
            publisher.ensure_deployed()

    def test_ensure_deployed_waits_for_main_branch_git_deployment(self):
        stale = "2026-08-15T11:53:07+00:00"
        with (
            patch.object(publisher, "read_index", return_value={"generated_at": self.generated_at}),
            patch.object(publisher, "fetch_live_generated_at", side_effect=[stale, self.generated_at]),
            patch.object(publisher.time, "sleep"),
        ):
            publisher.ensure_deployed()

    def test_ensure_deployed_fails_immediately_without_a_new_push(self):
        with (
            patch.object(publisher, "read_index", return_value={"generated_at": self.generated_at}),
            patch.object(publisher, "fetch_live_generated_at", return_value="2026-08-15T11:53:07+00:00"),
            patch.object(publisher.time, "sleep", side_effect=AssertionError("should fail immediately")),
            self.assertRaisesRegex(RuntimeError, "--force-build"),
        ):
            publisher.ensure_deployed(wait_for_push=False)

    def test_ensure_deployed_fails_if_production_stays_stale(self):
        with (
            patch.object(publisher, "read_index", return_value={"generated_at": self.generated_at}),
            patch.object(publisher, "fetch_live_generated_at", return_value="2026-08-15T11:53:07+00:00"),
            patch.object(publisher.time, "sleep", side_effect=AssertionError("should fail immediately")),
            patch.object(publisher, "DEPLOY_TIMEOUT", 0),
            self.assertRaisesRegex(RuntimeError, "latest production deployment is Ready"),
        ):
            publisher.ensure_deployed()


def write_month(directory: Path, period: str, plazas: int, per_plaza_count: int | None):
    """Write a minimal monthly JSON; per_plaza_count=None means no ETC totals."""
    rows = [
        {"total": ({"count": per_plaza_count, "amount": 1.0} if per_plaza_count else None)}
        for _ in range(plazas)
    ]
    (directory / f"{period}.json").write_text(
        json.dumps({"plaza_count": plazas, "plazas": rows}), encoding="utf-8"
    )


class TestPlausibilityGate(unittest.TestCase):
    """Catches a misread layout when the PDF carries no TOTAL columns (Oct 2026)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        patcher = patch.object(publisher, "MONTHLY_DIR", self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        write_month(self.dir, "2026-09", 100, 1000)

    def check(self, allow=False):
        publisher.check_plausible_volume("2026-10", ["2026-09", "2026-10"], allow)

    def test_normal_month_to_month_movement_passes(self):
        write_month(self.dir, "2026-10", 102, 1090)  # ~ +11% volume like Sep->Oct

        self.check()

    def test_inflated_volume_is_blocked(self):
        write_month(self.dir, "2026-10", 100, 10_000)  # e.g. amount column read as count

        with self.assertRaisesRegex(RuntimeError, "transaction count"):
            self.check()

    def test_collapsed_volume_is_blocked(self):
        write_month(self.dir, "2026-10", 100, 300)

        with self.assertRaisesRegex(RuntimeError, "transaction count"):
            self.check()

    def test_half_the_plazas_missing_is_blocked(self):
        write_month(self.dir, "2026-10", 50, 1000)

        with self.assertRaisesRegex(RuntimeError, "plaza count"):
            self.check()

    def test_allow_warnings_overrides_the_block(self):
        write_month(self.dir, "2026-10", 100, 10_000)

        self.check(allow=True)

    def test_month_with_no_etc_totals_yet_is_skipped(self):
        write_month(self.dir, "2026-10", 100, None)  # only an Annual Pass file so far

        self.check()

    def test_first_ever_month_has_nothing_to_compare_with(self):
        write_month(self.dir, "2026-10", 100, 10_000)

        publisher.check_plausible_volume("2026-10", ["2026-10"], False)

    def test_compares_with_nearest_earlier_month_that_has_totals(self):
        write_month(self.dir, "2026-10", 100, 1000)
        write_month(self.dir, "2026-11", 100, 1050)
        (self.dir / "2026-10.json").write_text(  # Oct has plazas but no totals
            json.dumps({"plazas": [{"total": None}] * 100}), encoding="utf-8"
        )

        publisher.check_plausible_volume(
            "2026-11", ["2026-09", "2026-10", "2026-11"], False
        )  # judged against Sep (1000), not the empty Oct


class TestSanityCheckUsesTheGate(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        for target, value in (
            ("MONTHLY_DIR", self.dir),
            ("read_index", lambda: {
                "periods": ["2026-09", "2026-10"],
                "mlff_reports": [],
                "validation_warnings": 0,
            }),
        ):
            patcher = patch.object(publisher, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        write_month(self.dir, "2026-09", 100, 1000)

    def test_plausible_new_month_passes(self):
        write_month(self.dir, "2026-10", 101, 1100)

        publisher.sanity_check({"2026-10"}, {"2026-09"}, 0, False)

    def test_implausible_new_month_blocks_publish(self):
        write_month(self.dir, "2026-10", 101, 50_000)

        with self.assertRaisesRegex(RuntimeError, "Blocking publish"):
            publisher.sanity_check({"2026-10"}, {"2026-09"}, 0, False)


class TestMainExitCodes(unittest.TestCase):
    """A run must never look green when the dashboard did not fully update."""

    @staticmethod
    def report(period="2026-11"):
        return ihmcl_crawler.RemoteReport("https://x/y.pdf", "y.pdf", "etc", period)

    def run_main(self, result, sanity_error=None):
        with (
            patch.object(publisher, "setup_logging"),
            patch.object(publisher, "acquire_lock", return_value=True),
            patch.object(publisher, "release_lock"),
            patch.object(publisher, "crawl", return_value=result),
            patch.object(publisher, "untracked_data_pdfs", return_value=[]),
            patch.object(publisher, "read_index", return_value={}),
            patch.object(publisher, "run_build") as run_build,
            patch.object(publisher, "sanity_check", side_effect=sanity_error) as sanity,
        ):
            code = publisher.main(["--no-push"])
        return code, run_build, sanity

    def test_clean_run_exits_zero_and_builds(self):
        result = ihmcl_crawler.CrawlResult(downloaded=[(self.report(), Path("y.pdf"))])

        code, run_build, sanity = self.run_main(result)

        self.assertEqual(code, 0)
        run_build.assert_called_once()
        sanity.assert_called_once()
        self.assertEqual(sanity.call_args.args[0], {"2026-11"})

    def test_unrecognised_report_exits_two_not_zero(self):
        result = ihmcl_crawler.CrawlResult(
            downloaded=[(self.report(), Path("y.pdf"))],
            errors=["Unrecognized report Foo-Nov-2026.pdf ... NOT ingested"],
        )

        code, _, _ = self.run_main(result)

        self.assertEqual(code, 2)

    def test_unreachable_site_exits_one_and_builds_nothing(self):
        code, run_build, _ = self.run_main(ihmcl_crawler.CrawlResult(fatal="page down"))

        self.assertEqual(code, 1)
        run_build.assert_not_called()

    def test_failed_sanity_gate_exits_one(self):
        result = ihmcl_crawler.CrawlResult(downloaded=[(self.report(), Path("y.pdf"))])

        code, _, _ = self.run_main(result, sanity_error=RuntimeError("bad parse"))

        self.assertEqual(code, 1)

    def test_nothing_new_exits_zero_without_rebuilding(self):
        code, run_build, _ = self.run_main(ihmcl_crawler.CrawlResult())

        self.assertEqual(code, 0)
        run_build.assert_not_called()

    def test_stuck_report_is_flagged_even_when_nothing_else_is_new(self):
        result = ihmcl_crawler.CrawlResult(errors=["Unrecognized report ..."])

        code, _, _ = self.run_main(result)

        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()