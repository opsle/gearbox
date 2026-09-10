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
        "profiles": {
            "codex": {"model": None, "effort": None},
            "claude": {"model": None, "effort": None},
        },
    }


class RouteTests(unittest.TestCase):
    def test_explicit_provider_wins(self):
        self.assertEqual(select_route(request(choice="claude"))["route"], "claude")

    def test_auto_is_deterministic(self):
        plan = select_route(request())
        self.assertEqual(plan["route"], "codex")
        self.assertEqual(plan["model"], "provider-default")
        self.assertEqual(plan["effort"], "medium")
        self.assertEqual(plan["effort_selection"], "automatic")
        self.assertEqual(select_route(request(codex=False))["route"], "claude")

    def test_explicit_model_and_effort_survive_routing(self):
        value = request("BUILD")
        value["profiles"]["codex"] = {"model": "gpt-fixture", "effort": "xhigh"}
        result = select_route(value)
        self.assertEqual(result["model"], "gpt-fixture")
        self.assertEqual(result["effort"], "xhigh")
        self.assertEqual(result["model_selection"], "explicit")
        self.assertEqual(result["effort_selection"], "explicit")

    def test_test_and_deploy_are_deterministic_or_noop(self):
        deterministic = select_route(request("TEST", deterministic=True))
        self.assertEqual(deterministic["route"], "deterministic")
        self.assertIsNone(deterministic["model"])
        self.assertIsNone(deterministic["effort"])
        self.assertEqual(select_route(request("DEPLOY"))["route"], "noop")

    def test_unavailable_explicit_provider_fails(self):
        with self.assertRaises(RouteError):
            select_route(request(choice="claude", claude=False))

    def test_unhashable_phase_and_choice_fail_as_route_errors(self):
        invalid_phase = request()
        invalid_phase["phase"] = []
        invalid_choice = request()
        invalid_choice["choice"] = {}
        with self.assertRaisesRegex(RouteError, "unknown phase"):
            select_route(invalid_phase)
        with self.assertRaisesRegex(RouteError, "unknown provider choice"):
            select_route(invalid_choice)

    def test_cli_reads_one_json_request_from_stdin(self):
        environment = {**os.environ, "PYTHONPATH": "src"}
        result = subprocess.run(
            [sys.executable, "-m", "opsle_gearbox.route"],
            input=json.dumps(request("BUILD")), text=True, capture_output=True, env=environment, check=True,
        )
        self.assertEqual(json.loads(result.stdout)["route"], "codex")


if __name__ == "__main__":
    unittest.main()
