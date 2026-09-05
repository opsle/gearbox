"""Small deterministic route selector for task-pipeline phases."""

from __future__ import annotations

import json
import sys
from typing import Any

REQUEST_SCHEMA = "opsle.gearbox.route-request.v1"
RESULT_SCHEMA = "opsle.gearbox.route-result.v1"
PHASES = {"PLAN", "BUILD", "TEST", "DEPLOY", "IDEAS"}
CHOICES = {"auto", "codex", "claude"}


class RouteError(ValueError):
    """The caller supplied an invalid or impossible routing request."""


def select_route(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"schema", "phase", "choice", "available"}:
        raise RouteError("route request must contain exactly schema, phase, choice, and available")
    if value["schema"] != REQUEST_SCHEMA:
        raise RouteError("unsupported route request schema")

    phase = value["phase"]
    choice = value["choice"]
    available = value["available"]
    if phase not in PHASES:
        raise RouteError("unknown phase")
    if choice not in CHOICES:
        raise RouteError("unknown provider choice")
    if not isinstance(available, dict) or set(available) != {"codex", "claude", "deterministic"}:
        raise RouteError("available must contain exactly codex, claude, and deterministic")
    if any(not isinstance(item, bool) for item in available.values()):
        raise RouteError("availability values must be booleans")

    if phase in {"TEST", "DEPLOY"}:
        route = "deterministic" if available["deterministic"] else "noop"
        reason = f"{phase.lower()} command configured" if available["deterministic"] else f"no {phase.lower()} command configured"
    elif choice != "auto":
        if not available[choice]:
            raise RouteError(f"{choice} is not available")
        route = choice
        reason = "chosen by user"
    elif available["codex"]:
        route = "codex"
        reason = "auto selected first available provider"
    elif available["claude"]:
        route = "claude"
        reason = "auto selected first available provider"
    else:
        raise RouteError("no provider is available")

    return {
        "schema": RESULT_SCHEMA,
        "phase": phase,
        "route": route,
        "reason": reason,
    }


def main() -> int:
    try:
        request = json.load(sys.stdin)
        result = select_route(request)
        sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n")
        return 0
    except (RouteError, json.JSONDecodeError) as error:
        sys.stdout.write(json.dumps({
            "schema": "opsle.gearbox.route-error.v1",
            "status": "failed",
            "error": str(error),
        }, separators=(",", ":"), sort_keys=True) + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
