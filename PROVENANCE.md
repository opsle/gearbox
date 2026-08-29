# Provenance

The public reference core was extracted on 2026-08-29 from the private active
Taslos Tasks repository at commit
`7734caf208366a0515cf4d78efc17a86363f2238`.

The predecessor Gearbox entered that repository in commit
`80f0830e26b938f6abe8fc688b9b1f49283ef34f` under `ops/gearbox/`. The reusable
algorithms adapted here include:

- strict request/key validation;
- exact repository identity and normalized path checks;
- content-addressed file, line, symbol, and new-file selections;
- safe file reads and source-drift checks;
- deterministic Python symbol range resolution;
- private raw artifacts and compact result accounting;
- provider-session, command, context, output, timeout, and cleanup bounds;
- one blocking delivery with no retry or fallback.

The public implementation was rewritten around the canonical primary-developer
Gearbox boundary established by `opsle/research` PR #7. It does not copy or
publish Taslos Tasks product code, databases, credentials, production state,
acceptance transcripts, private evidence, systemd units, installed Codex schema
paths, app-server configuration, or Durable Supervisor machinery.

The predecessor source is AGPL-3.0. This derived repository therefore preserves
AGPL-3.0-only licensing and records the original copyright in `NOTICE`.

Git history remains attributable through the exact source and introduction
commits above. Future extraction should cite both this public revision and the
private source revision when authorized to do so.
