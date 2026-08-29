from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from opsle_gearbox.core import (
    POLICY_SCHEMA,
    REQUEST_SCHEMA,
    GearboxError,
    GearboxRunner,
    canonical_json,
    sha256_file,
    validate_request,
)

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["PASS", "BLOCKED", "FAIL"]},
        "summary": {"type": "string", "maxLength": 1200},
        "changed_paths": {"type": "array", "items": {"type": "string"}, "maxItems": 40},
        "tests": {"type": "array", "items": {"type": "string"}, "maxItems": 30},
        "unresolved_issues": {"type": "array", "items": {"type": "string"}, "maxItems": 30},
    },
    "required": ["verdict", "summary", "changed_paths", "tests", "unresolved_issues"],
    "additionalProperties": False,
}


class FakeTransport:
    def __init__(self, **overrides):
        self.calls = 0
        self.last_request = None
        self.overrides = overrides

    def execute(self, *, request, workspace, timeout_seconds):
        self.calls += 1
        self.last_request = request
        if self.overrides.get("edit"):
            (workspace / "editable.txt").write_text("changed\n", encoding="utf-8")
        value = {
            "final": {
                "verdict": "PASS",
                "summary": "bounded helper completed",
                "changed_paths": ["editable.txt"] if self.overrides.get("edit") else [],
                "tests": [],
                "unresolved_issues": [],
            },
            "stdout": b"private helper transcript\n",
            "stderr": b"",
            "provider_sessions": 0,
            "commands": 1,
            "terminated": True,
            "model": "fixture-model",
            "effort": "low",
            "transport_id": "fixture-transport/v1",
        }
        value.update({key: item for key, item in self.overrides.items() if key != "edit"})
        return value


class GearboxTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "fixture@example.invalid"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "Fixture"], cwd=self.repo, check=True)
        (self.repo / "README.md").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "fixture"], cwd=self.repo, check=True, capture_output=True)
        self.policy = self.base / "policy.json"
        self.policy.write_bytes(canonical_json({
            "schema": POLICY_SCHEMA,
            "authority_id": "fixture-policy/v1",
            "gears": {
                "git-status": {
                    "kind": "deterministic",
                    "task_types": ["git"],
                    "argv_exact": ["git", "status", "--short", "--branch"],
                    "executable_path": str(Path(shutil.which("git"))),
                    "executable_sha256": sha256_file(Path(shutil.which("git"))),
                },
                "always-fail": {
                    "kind": "deterministic",
                    "task_types": ["fixture"],
                    "argv_exact": ["false"],
                    "executable_path": str(Path(shutil.which("false"))),
                    "executable_sha256": sha256_file(Path(shutil.which("false"))),
                },
                "fixture-helper": {
                    "kind": "helper",
                    "task_types": ["interpretation", "implementation"],
                    "model": "fixture-model",
                    "effort": "low",
                    "max_provider_sessions": 0,
                    "transport_id": "fixture-transport/v1",
                },
            },
        }))

    def tearDown(self):
        self.temporary.cleanup()

    def authority(self):
        return {"policy_path": str(self.policy), "policy_sha256": sha256_file(self.policy)}

    def deterministic(self):
        return {
            "schema": REQUEST_SCHEMA,
            "task": {"argv": ["git", "status", "--short", "--branch"]},
            "task_type": "git",
            "requested_gear": "git-status",
            "allowed_context": {"repository": str(self.repo), "selections": [], "writable_paths": []},
            "output_contract": {"schema": OUTPUT_SCHEMA},
            "authority": self.authority(),
            "budget": {
                "timeout_seconds": 10,
                "max_raw_bytes": 65536,
                "max_return_bytes": 8192,
                "max_context_bytes": 0,
                "max_commands": 1,
                "max_provider_sessions": 0,
            },
        }

    def helper(self, *, writable=False):
        source = self.repo / ("editable.txt" if writable else "sample.py")
        source.write_text("original\n" if writable else "def alpha():\n    return 1\n\ndef omega():\n    return 2\n", encoding="utf-8")
        selection = {
            "path": source.name,
            "kind": "file" if writable else "symbols",
            "source_sha256": sha256_file(source),
        }
        if not writable:
            selection["symbols"] = ["alpha"]
        return {
            "schema": REQUEST_SCHEMA,
            "task": "Inspect the admitted context and return one compact result.",
            "task_type": "implementation" if writable else "interpretation",
            "requested_gear": "fixture-helper",
            "allowed_context": {
                "repository": str(self.repo),
                "selections": [selection],
                "writable_paths": [source.name] if writable else [],
            },
            "output_contract": {"schema": OUTPUT_SCHEMA},
            "authority": self.authority(),
            "budget": {
                "timeout_seconds": 10,
                "max_raw_bytes": 65536,
                "max_return_bytes": 8192,
                "max_context_bytes": 65536,
                "max_commands": 2,
                "max_provider_sessions": 0,
            },
        }

    def test_deterministic_execution_is_compact_provider_free_and_idempotent(self):
        runner = GearboxRunner(self.base / "state", mechanism_revision="fixture-revision")
        first = runner.run(self.deterministic())
        second = runner.run(self.deterministic())
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "completed")
        self.assertEqual(first["metrics"]["provider_sessions"], 0)
        self.assertEqual(first["metrics"]["execution_attempts"], 1)
        self.assertEqual(first["metrics"]["fallback_attempts"], 0)
        self.assertEqual(first["metrics"]["primary_wait_mode"], "blocking_subprocess_wait")
        self.assertNotIn("## main", json.dumps(first))
        run = self.base / "state" / "runs" / first["run_id"]
        self.assertTrue((run / "raw/stdout.raw").is_file())
        receipt = json.loads((run / "value-receipt.json").read_text())
        self.assertEqual(receipt["schema"], "opsle.value-receipt.v1")

    def test_policy_hash_drift_fails_before_execution(self):
        request = self.deterministic()
        self.policy.write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(GearboxError, "hash drifted"):
            validate_request(request)

    def test_nonzero_deterministic_result_escalates_without_fallback(self):
        request = self.deterministic()
        request["task"] = {"argv": ["false"]}
        request["task_type"] = "fixture"
        request["requested_gear"] = "always-fail"
        result = GearboxRunner(self.base / "state").run(request)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["exit_code"], 1)
        self.assertEqual(result["metrics"]["fallback_attempts"], 0)
        self.assertEqual(result["escalation"]["reason"], "NONZERO_EXIT")

    def test_deterministic_argv_must_match_exactly(self):
        request = self.deterministic()
        request["task"]["argv"].append("--ignored")
        with self.assertRaisesRegex(GearboxError, "exactly match"):
            validate_request(request)

    def test_deterministic_command_budget_is_exactly_one(self):
        request = self.deterministic()
        request["budget"]["max_commands"] = 0
        with self.assertRaisesRegex(GearboxError, "one-command budget"):
            validate_request(request)

    def test_unknown_request_field_fails_closed(self):
        request = self.deterministic()
        request["fallback"] = True
        with self.assertRaisesRegex(GearboxError, "unknown fields"):
            validate_request(request)

    def test_helper_requires_an_explicit_transport_and_never_falls_back(self):
        result = GearboxRunner(self.base / "state").run(self.helper())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["metrics"]["execution_attempts"], 1)
        self.assertEqual(result["metrics"]["fallback_attempts"], 0)
        self.assertIn("transport is unavailable", result["summary"])

    def test_symbol_context_is_content_addressed_and_private(self):
        transport = FakeTransport()
        result = GearboxRunner(self.base / "state", helper_transport=transport).run(self.helper())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(transport.calls, 1)
        self.assertNotIn("allowed_context", transport.last_request)
        self.assertNotIn(str(self.repo), json.dumps(transport.last_request))
        workspace = self.base / "state" / "runs" / result["run_id"] / "workspace"
        packet = (workspace / "sample.py").read_text()
        self.assertIn("symbol=alpha", packet)
        self.assertNotIn("omega", packet)
        self.assertEqual(result["context"]["manifest_sha256"], sha256_file(workspace / "GEARBOX_CONTEXT.json"))

    def test_writable_helper_changes_staged_copy_only(self):
        transport = FakeTransport(edit=True)
        request = self.helper(writable=True)
        result = GearboxRunner(self.base / "state", helper_transport=transport).run(request)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["changed_paths"], ["editable.txt"])
        self.assertEqual((self.repo / "editable.txt").read_text(), "original\n")

    def test_helper_model_effort_and_command_budgets_fail_closed(self):
        drift = GearboxRunner(self.base / "model-state", helper_transport=FakeTransport(model="wrong"))
        self.assertEqual(drift.run(self.helper())["status"], "failed")
        over = GearboxRunner(self.base / "command-state", helper_transport=FakeTransport(commands=3))
        result = over.run(self.helper())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["metrics"]["fallback_attempts"], 0)

    def test_helper_transport_identity_and_changed_path_claim_are_bound(self):
        identity = GearboxRunner(
            self.base / "identity-state",
            helper_transport=FakeTransport(transport_id="wrong-transport"),
        ).run(self.helper())
        self.assertEqual(identity["status"], "failed")
        self.assertIn("transport, model, or effort drifted", identity["summary"])

        final = {
            "verdict": "PASS",
            "summary": "incorrect mutation claim",
            "changed_paths": [],
            "tests": [],
            "unresolved_issues": [],
        }
        claim = GearboxRunner(
            self.base / "claim-state",
            helper_transport=FakeTransport(edit=True, final=final),
        ).run(self.helper(writable=True))
        self.assertEqual(claim["status"], "failed")
        self.assertIn("changed-path claim", claim["summary"])

    def test_transport_exception_preserves_unknown_provider_state(self):
        class BrokenTransport:
            def execute(self, **_kwargs):
                raise RuntimeError("fixture transport loss")

        result = GearboxRunner(
            self.base / "state", helper_transport=BrokenTransport()
        ).run(self.helper())
        self.assertEqual(result["status"], "uncertain")
        self.assertIsNone(result["metrics"]["provider_sessions"])
        receipt = json.loads(
            (self.base / "state" / "runs" / result["run_id"] / "value-receipt.json").read_text()
        )
        measurement = next(
            item for item in receipt["measurements"] if item["id"] == "provider_sessions"
        )
        self.assertEqual(measurement["class"], "OBSERVED")
        self.assertFalse(measurement["aggregation"]["safe"])

    def test_helper_must_terminate(self):
        result = GearboxRunner(
            self.base / "state", helper_transport=FakeTransport(terminated=False)
        ).run(self.helper())
        self.assertEqual(result["status"], "failed")
        self.assertIn("termination is unverified", result["summary"])

    def test_sensitive_and_symlink_context_is_rejected(self):
        request = self.helper()
        request["allowed_context"]["selections"][0]["path"] = ".env"
        with self.assertRaisesRegex(GearboxError, "sensitive"):
            validate_request(request)
        target = self.repo / "target.py"
        target.write_text("def exact():\n    return 1\n", encoding="utf-8")
        os.symlink("target.py", self.repo / "linked.py")
        request = self.helper()
        request["allowed_context"]["selections"] = [{
            "path": "linked.py", "kind": "file", "source_sha256": sha256_file(target),
        }]
        result = GearboxRunner(self.base / "state", helper_transport=FakeTransport()).run(request)
        self.assertEqual(result["status"], "failed")
        self.assertIn("symlink", result["summary"])

    def test_context_hash_drift_rejects_before_transport(self):
        request = self.helper()
        request["allowed_context"]["selections"][0]["source_sha256"] = "0" * 64
        transport = FakeTransport()
        result = GearboxRunner(self.base / "state", helper_transport=transport).run(request)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(transport.calls, 0)

    def test_context_ceiling_rejects_without_transport(self):
        request = self.helper()
        request["budget"]["max_context_bytes"] = 1
        transport = FakeTransport()
        result = GearboxRunner(self.base / "state", helper_transport=transport).run(request)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(transport.calls, 0)

    def test_malformed_helper_final_fails_closed(self):
        transport = FakeTransport(final={"verdict": "PASS"})
        result = GearboxRunner(self.base / "state", helper_transport=transport).run(self.helper())
        self.assertEqual(result["status"], "failed")
        self.assertIn("is missing", result["summary"])

    def test_helper_output_contract_requires_the_compact_fields(self):
        request = self.helper()
        request["output_contract"]["schema"] = {
            "type": "object", "properties": {}, "required": [],
        }
        with self.assertRaisesRegex(GearboxError, "standard compact fields"):
            validate_request(request)

    def test_cli_keeps_operator_indicator_off_stdout(self):
        request_path = self.base / "request.json"
        request_path.write_bytes(canonical_json(self.deterministic()))
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
        completed = subprocess.run(
            [
                sys.executable, "-m", "opsle_gearbox.cli", "--request", str(request_path),
                "--state", str(self.base / "cli-state"),
            ],
            check=True,
            capture_output=True,
            env=environment,
        )
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["status"], "completed")
        self.assertNotIn(b"[Gearbox]", completed.stdout)
        self.assertIn(b"[Gearbox] deterministic completed", completed.stderr)


if __name__ == "__main__":
    unittest.main()
