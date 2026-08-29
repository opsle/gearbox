# Security boundary

## Trusted inputs

- the primary developer or reviewed caller;
- the exact authority-policy bytes bound by SHA-256;
- the local operating system and Python runtime;
- the injected helper transport and any isolation it claims to enforce.

## Untrusted inputs

- task requests before admission;
- source repositories and selected files;
- deterministic command output;
- helper stdout, stderr, mutations, and final result;
- stale, malformed, partial, or over-budget evidence.

## Enforced by this core

- strict request and policy fields;
- exact deterministic argv;
- no shell interpolation;
- one attempt and no fallback;
- exact repository identity;
- normalized, non-sensitive, non-symlink content paths;
- source hashes and revalidation;
- helper context and write allowlists;
- model, effort, command, provider-session, output, and cleanup checks;
- raw evidence retained outside the compact return;
- terminal escalation on drift or uncertainty.

## Not enforced by this core

The `HelperTransport` boundary cannot itself prove filesystem, network,
credential, subprocess, provider, or model isolation. A production transport
MUST enforce those controls and SHOULD emit independently verifiable receipts.
No production helper transport is bundled in version 0.1.0.

Deterministic policy authors are responsible for choosing commands that are safe
for the declared task. Exact admission prevents request expansion; it does not
make an unsafe authorized command safe.

Report vulnerabilities privately through GitHub's security advisory interface.
