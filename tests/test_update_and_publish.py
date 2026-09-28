from contextlib import nullcontext
from types import SimpleNamespace
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

    def test_ensure_deployed_triggers_hook_and_waits_for_matching_index(self):
        stale = "2026-08-15T11:53:07+00:00"
        with (
            patch.object(publisher, "read_index", return_value={"generated_at": self.generated_at}),
            patch.object(publisher, "fetch_live_generated_at", side_effect=[stale, self.generated_at]),
            patch.object(publisher.time, "sleep"),
            patch.object(
                publisher,
                "urlopen",
                return_value=nullcontext(SimpleNamespace(status=201)),
            ) as urlopen,
            patch.dict("os.environ", {"VERCEL_DEPLOY_HOOK_URL": "https://example.test/deploy-hook"}),
        ):
            publisher.ensure_deployed()

        self.assertEqual(urlopen.call_args.args[0].method, "POST")

    def test_ensure_deployed_fails_if_production_stays_stale(self):
        with (
            patch.object(publisher, "read_index", return_value={"generated_at": self.generated_at}),
            patch.object(publisher, "fetch_live_generated_at", return_value="2026-08-15T11:53:07+00:00"),
            patch.object(publisher.time, "sleep", side_effect=AssertionError("should fail immediately")),
            patch.dict("os.environ", {"VERCEL_DEPLOY_HOOK_URL": ""}),
            self.assertRaisesRegex(RuntimeError, "VERCEL_DEPLOY_HOOK_URL"),
        ):
            publisher.ensure_deployed(wait_for_git=False)


if __name__ == "__main__":
    unittest.main()