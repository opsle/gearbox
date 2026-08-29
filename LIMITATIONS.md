# Known limitations

- No production cognitive/provider transport is bundled or accepted.
- Helper isolation is an injected-transport responsibility and is not proven by
  the core interface.
- Context Firewall reduction is not bundled; the compact result intentionally
  exposes only terminal facts, hashes, counts, and artifact locators.
- Symbol selection supports Python only.
- The reference JSON-schema validator implements a documented subset, not full
  JSON Schema.
- Deterministic commands are exact policy entries rather than a portable command
  catalog or semantic tool registry.
- Raw byte ceilings are evaluated after deterministic subprocess completion;
  an authorized command can temporarily produce more bytes than its ceiling.
- Run idempotence is filesystem-local and does not provide distributed locking.
- The prototype does not apply staged helper writes to the source repository.
- No controlled benchmark establishes correctness preservation, intelligence
  savings, context savings, latency reduction, monetary value, or avoided
  provider sessions.
- A provider-session count of zero is direct telemetry only, not a counterfactual
  savings claim.
