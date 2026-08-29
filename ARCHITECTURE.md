# Architecture

```text
primary developer
       |
       v
request + content-addressed authority
       |
       v
admission and gear selection
       |
       +----------------------+
       |                      |
       v                      v
exact deterministic argv   bounded helper transport
                              |
                     staged allowlisted context
       |                      |
       +----------+-----------+
                  v
            raw private evidence
                  |
       external Context Firewall adapter
                  |
                  v
compact result + value receipt + operator indicator
                  |
                  v
primary developer retains completion ownership
```

## Internal modules

The reference core currently keeps its small executable surface in
`src/opsle_gearbox/core.py`:

- authority and policy validation;
- request and budget admission;
- safe source selection and staging;
- deterministic executor;
- injected helper transport boundary;
- output-contract and staged-change validation;
- compact result and Visible Value receipt generation.

`src/opsle_gearbox/cli.py` provides the deterministic command-line interface.

## External dependencies

- **Context Firewall**: deterministic adapters for command/helper raw evidence.
- **Decision Evidence Protocol**: independent receipt/result conformance.
- **Agent Trajectory Profiler**: observational execution telemetry.
- **Routing Policy**: chooses a model/provider profile after cognitive
  admission; the core policy currently binds an already selected profile.
- **Execution Authorization / Resource Claims**: stronger external authority
  where a deployment requires capabilities, leases, or fencing.
- **Ephemeral Workers / Verifiable Handoff**: optional isolation and durable
  artifact transfer.

## Explicit exclusions

The repository owns no objective graph, durable scheduler, queue, wakeup,
discovery, recovery ladder, global pause, persistent hierarchy, provider pool,
or product completion state.
