# Agent Gearbox

> Stop using intelligence for work that does not require intelligence.

Agent Gearbox lets a powerful primary developer delegate routine operations and
bounded work to deterministic software or less expensive models, then receive
only the compact result needed to continue.

This repository contains the public provider-free reference core. It is a
`PROTOTYPED` mechanism, not evidence of comparative benefit or production
readiness.

## What exists

- exact, content-addressed authority policies;
- deterministic-versus-helper gear admission;
- exact model, effort, provider-session, command, context, raw-output, return,
  and timeout budgets;
- deterministic commands admitted by exact argv rather than shell text;
- content-addressed `file`, `lines`, `symbols`, and `new` context selections;
- private staged workspaces that never write into the source repository;
- one injected helper transport, one attempt, no retry or fallback;
- raw artifact hashes and locators outside the compact result;
- one compact result on stdout and a named operator indicator on stderr;
- one small deterministic route selector for `PLAN`, `BUILD`, `TEST`,
  `DEPLOY`, and `IDEAS` pipeline phases;
- an `opsle.value-receipt.v1` sidecar with bounded observational claims.

The bundled CLI executes deterministic gears only. Cognitive execution requires
a separately supplied `HelperTransport` integration. The core rejects a helper
request when no transport is present; it never silently falls back.

## What does not exist

Gearbox is not a durable supervisor, queue, scheduler, discovery engine,
retry/recovery controller, persistent agent hierarchy, exact-session resume
mechanism, general autonomous-task platform, dynamic discovery engine, or
probabilistic provider router. Its small route selector uses only explicit
caller-supplied availability and a fixed rule.

Context Firewall is an external integration. Gearbox decides where bounded work
executes; Context Firewall decides what decision-relevant evidence returns. The
reference core returns hashes, terminal facts, artifact locators, and escalation
state without embedding raw process output.

## Request shape

```json
{
  "schema": "opsle.gearbox.request.v1",
  "task": {
    "argv": ["git", "status", "--short", "--branch"]
  },
  "task_type": "git",
  "requested_gear": "git-status",
  "allowed_context": {
    "repository": "/absolute/git/root",
    "selections": [],
    "writable_paths": []
  },
  "output_contract": {
    "schema": {
      "type": "object",
      "properties": {},
      "required": []
    }
  },
  "authority": {
    "policy_path": "/absolute/reviewed-policy.json",
    "policy_sha256": "lowercase-sha256"
  },
  "budget": {
    "timeout_seconds": 30,
    "max_raw_bytes": 1048576,
    "max_return_bytes": 8192,
    "max_context_bytes": 0,
    "max_commands": 1,
    "max_provider_sessions": 0
  }
}
```

Policies bind a gear name to one kind, authorized task types, and either an
exact deterministic argv plus executable path/hash or a helper
transport/model/effort/session profile. The request binds the policy by SHA-256.

## CLI

After reviewing and hashing a policy and request:

```text
PYTHONPATH=src python3 -m opsle_gearbox.cli \
  --request /private/request.json \
  --state /private/gearbox-state \
  --receipt /private/value-receipt.json
```

Canonical result JSON is written to stdout. The concise `[Gearbox] ...`
indicator is written to stderr. Raw stdout/stderr and helper evidence stay under
the mode-0700 state root.

For a small task pipeline, route one phase by sending a request on stdin:

```text
PYTHONPATH=src python3 -m opsle_gearbox.route <<'JSON'
{"schema":"opsle.gearbox.route-request.v2","phase":"BUILD","choice":"auto","available":{"codex":true,"claude":true,"deterministic":false},"profiles":{"codex":{"model":null,"effort":null},"claude":{"model":null,"effort":null}}}
JSON
```

The selector returns one route: `codex`, `claude`, `deterministic`, or `noop`,
plus the selected model and reasoning effort for provider routes. Null profile
values select the provider default model and the bounded phase effort policy;
explicit profile values survive unchanged.
`TEST` and `DEPLOY` are deterministic when a command is configured and otherwise
become explicit no-ops. It performs no execution, retries, discovery, or state.

## Verification

```text
tools/verify
```

The suite is provider-free and covers deterministic execution, idempotence,
authority drift, exact command admission, context hashing and selection,
symlink/sensitive-path rejection, helper transport absence, model/effort,
command and context budgets, staged-only writes, termination, compact result,
Visible Value receipt, and stdout/stderr separation.

## Documents

- [THEORY.md](THEORY.md) — falsifiable problem and mechanism.
- [SPEC.md](SPEC.md) — normative request, execution, and result contract.
- [ARCHITECTURE.md](ARCHITECTURE.md) — component and dependency boundaries.
- [SECURITY.md](SECURITY.md) — trust assumptions and fail-closed behavior.
- [LIMITATIONS.md](LIMITATIONS.md) — current evidence ceiling.
- [PROVENANCE.md](PROVENANCE.md) — source extraction and licensing record.
- [BENCHMARK.md](BENCHMARK.md) — future controlled evidence plan.

## License

AGPL-3.0-only. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

## Routing policy

Agent Routing Policy is consolidated into Gearbox. Its [specifications and provenance](docs/agent-routing-policy/README.md) map to the existing route implementation and tests.
