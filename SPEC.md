# Agent Gearbox specification

Status: normative draft
Version: `opsle.gearbox.request.v1` / `opsle.gearbox.result.v1`

Normative terms **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are used in their
ordinary specification sense.

## Request

A request MUST contain exactly:

- `schema`;
- `task`;
- `task_type`;
- `requested_gear`;
- `allowed_context`;
- `output_contract`;
- `authority`;
- `budget`.

Unknown fields MUST fail admission.

### Authority

`authority.policy_path` MUST identify an absolute, regular, non-symlink JSON
file. `authority.policy_sha256` MUST match its bytes. The policy MUST use
`opsle.gearbox.policy.v1`, name an `authority_id`, and define at least one gear.

Policy drift MUST fail before execution. A request cannot authorize a new gear,
task type, command, model, effort, or provider-session count.

### Gear admission

A deterministic gear binds one or more task types to one exact argv array, an
absolute or policy-relative executable path, and the executable's SHA-256. No
shell interpolation or request-supplied command variation is admitted. The
executable is revalidated before launch.

A helper gear binds task types to an exact transport identity, model, reasoning
effort, and maximum provider-session count. The helper transport is injected by
the caller and MUST be independently trusted to enforce its isolation and
provider contract.

Gearbox MUST invoke at most one gear and MUST NOT retry, fall back, recursively
delegate, or create discovered work.

### Context

`allowed_context.repository` MUST be the exact Git root. A selection is one of:

- `file`: complete file with admitted SHA-256;
- `lines`: ordered, non-overlapping ranges with admitted source SHA-256;
- `symbols`: unique qualified Python symbols with admitted source SHA-256;
- `new`: absent path explicitly declared writable.

Paths MUST be normalized repository-relative paths. Sensitive names, symlinks,
hard-linked files, boundary traversal, ambiguous/missing symbols, source drift,
non-UTF-8 partial selections, and hard-ceiling overflow MUST fail closed.

Only complete `file` and `new` selections MAY be writable. Writes occur only in
the staged workspace; this core never applies them to the source repository.
Unauthorized path creation or read-only mutation MUST fail the run.

### Budgets

The request MUST give literal bounds for timeout, raw bytes, compact return
bytes, context bytes, commands, and provider sessions. The provider-session
budget MUST equal the authorized gear profile. Missing observations MUST NOT be
invented as zeros, except where the selected implementation mechanically proves
the count (for example, the bundled deterministic executor proves zero provider
sessions).

### Output contract

The helper final MUST match the declared object schema. This reference validates
object properties, required fields, additional properties, enum values, arrays,
maximum item counts, strings, and maximum lengths. Unsupported schema behavior
MUST NOT be treated as validated.

## Execution

The normalized request hash determines the run identity. A completed identical
run MAY return its durable result. An incomplete run MUST NOT be duplicated.

Raw output MUST be retained under the private state root and MUST NOT appear in
the compact result. The executor waits through the process or injected transport
without consuming primary model turns. Timeout or uncertain cleanup requires a
terminal failure or escalation.

The helper's source context MUST be revalidated immediately before transport
execution. The transport MUST report model, effort, command count, provider
sessions, and termination state. Drift or budget excess MUST fail without retry.
The transport request MUST NOT expose the original repository path; it receives
the staged workspace separately and a context-manifest hash.

## Result

The compact result contains:

- request, policy, run, authority, and gear identities;
- one terminal status and bounded summary;
- exit status where applicable;
- changed staged paths;
- raw artifact locators, byte counts, and SHA-256 hashes;
- observed execution, fallback, provider-session, command, cleanup, wait, and
  raw-byte metrics;
- escalation state and reason;
- context packet accounting for helper runs;
- a value-receipt locator;
- a deterministic result hash.

The complete encoded result MUST fit `max_return_bytes`; otherwise delivery MUST
fail rather than truncate semantic fields.

## Visible Value

Every completed core execution writes an `opsle.value-receipt.v1` sidecar. Exact
measurements cover directly verified counts and byte lengths. Terminal status
and wait mode are observational states. Zero provider sessions MUST NOT be
reported as sessions saved or avoided.

The CLI writes canonical result JSON to stdout and one stable `[Gearbox]`
indicator to stderr.
