import unittest
from unittest.mock import patch

from scripts import update_and_publish as publisher


class TestProductionDeployment(unittest.TestCase):
    generated_at = "2026-09-15T04:41:57+00:00"

    def test_ensure_deployed_returns_when_production_matches(self):
        with (
            patch.object(publisher, "read_index", return_value={"generated_at": self.generated_at}),
            patch.object(publisher, "fetch_live_generated_at", return_value=self.generated_at),
        ):
            publisher.ensure_deployed()

    def test_ensure_deployed_waits_for_main_branch_cli_deployment(self):
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
            self.assertRaisesRegex(RuntimeError, "Deploy dashboard.*main"),
        ):
            publisher.ensure_deployed(wait_for_push=False)

    def test_ensure_deployed_fails_if_production_stays_stale(self):
        with (
            patch.object(publisher, "read_index", return_value={"generated_at": self.generated_at}),
            patch.object(publisher, "fetch_live_generated_at", return_value="2026-08-15T11:53:07+00:00"),
            patch.object(publisher.time, "sleep", side_effect=AssertionError("should fail immediately")),
            patch.object(publisher, "DEPLOY_TIMEOUT", 0),
            self.assertRaisesRegex(RuntimeError, "Deploy dashboard.*main"),
        ):
            publisher.ensure_deployed()


if __name__ == "__main__":
    unittest.main()