"""Small deterministic route selector for task-pipeline phases."""

from __future__ import annotations

import json
import sys
from typing import Any

REQUEST_SCHEMA = "opsle.gearbox.route-request.v2"
RESULT_SCHEMA = "opsle.gearbox.route-result.v2"
PHASES = {"PLAN", "BUILD", "TEST", "DEPLOY", "IDEAS"}
CHOICES = {"auto", "codex", "claude"}
EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}
AUTOMATIC_EFFORT = {"IDEAS": "low", "PLAN": "medium", "BUILD": "high"}


class RouteError(ValueError):
    """The caller supplied an invalid or impossible routing request."""


def select_route(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema", "phase", "choice", "available", "profiles"
    }:
        raise RouteError("route request must contain exactly schema, phase, choice, available, and profiles")
    if value["schema"] != REQUEST_SCHEMA:
        raise RouteError("unsupported route request schema")

    phase = value["phase"]
    choice = value["choice"]
    available = value["available"]
    if not isinstance(phase, str) or phase not in PHASES:
        raise RouteError("unknown phase")
    if not isinstance(choice, str) or choice not in CHOICES:
        raise RouteError("unknown provider choice")
    if not isinstance(available, dict) or set(available) != {"codex", "claude", "deterministic"}:
        raise RouteError("available must contain exactly codex, claude, and deterministic")
    if any(not isinstance(item, bool) for item in available.values()):
        raise RouteError("availability values must be booleans")
    profiles = value["profiles"]
    if not isinstance(profiles, dict) or set(profiles) != {"codex", "claude"}:
        raise RouteError("profiles must contain exactly codex and claude")
    for profile in profiles.values():
        if not isinstance(profile, dict) or set(profile) != {"model", "effort"}:
            raise RouteError("each provider profile must contain exactly model and effort")
        if profile["model"] is not None and (
            not isinstance(profile["model"], str) or not profile["model"].strip()
        ):
            raise RouteError("profile model must be a non-empty string or null")
        if profile["effort"] is not None and profile["effort"] not in EFFORTS:
            raise RouteError("profile effort is unsupported")

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

    profile = profiles[route] if route in {"codex", "claude"} else None
    model = (profile["model"] or "provider-default") if profile else None
    effort = (profile["effort"] or AUTOMATIC_EFFORT[phase]) if profile else None
    return {
        "schema": RESULT_SCHEMA,
        "phase": phase,
        "route": route,
        "reason": reason,
        "model": model,
        "effort": effort,
        "model_selection": "explicit" if profile and profile["model"] else (
            "automatic" if profile else "not-applicable"
        ),
        "effort_selection": "explicit" if profile and profile["effort"] else (
            "automatic" if profile else "not-applicable"
        ),
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
