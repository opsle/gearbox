from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

from opsle_gearbox.route import REQUEST_SCHEMA, RouteError, select_route


def request(phase="PLAN", choice="auto", *, codex=True, claude=True, deterministic=False):
    return {
        "schema": REQUEST_SCHEMA,
        "phase": phase,
        "choice": choice,
        "available": {"codex": codex, "claude": claude, "deterministic": deterministic},
    }


class RouteTests(unittest.TestCase):
    def test_explicit_provider_wins(self):
        self.assertEqual(select_route(request(choice="claude"))["route"], "claude")

    def test_auto_is_deterministic(self):
        self.assertEqual(select_route(request())["route"], "codex")
        self.assertEqual(select_route(request(codex=False))["route"], "claude")

    def test_test_and_deploy_are_deterministic_or_noop(self):
        self.assertEqual(select_route(request("TEST", deterministic=True))["route"], "deterministic")
        self.assertEqual(select_route(request("DEPLOY"))["route"], "noop")

    def test_unavailable_explicit_provider_fails(self):
        with self.assertRaises(RouteError):
            select_route(request(choice="claude", claude=False))

    def test_cli_reads_one_json_request_from_stdin(self):
        environment = {**os.environ, "PYTHONPATH": "src"}
        result = subprocess.run(
            [sys.executable, "-m", "opsle_gearbox.route"],
            input=json.dumps(request("BUILD")), text=True, capture_output=True, env=environment, check=True,
        )
        self.assertEqual(json.loads(result.stdout)["route"], "codex")


if __name__ == "__main__":
    unittest.main()
