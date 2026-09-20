# ops/ci — canonical lifecycle for opsle/gearbox

```
AI → PR → TEST → MERGE → TEST merged SHA → DEPLOY → VERIFY
```

| Command | Runs on | Holds |
| --- | --- | --- |
| `ops/ci/test` | `platform-ci,gearbox` runner (`ci.yml`, job `quality`) | no production credentials |
| `ops/ci/deploy SHA` | `gearbox-deploy` runner (`deploy.yml`) | write access (POSIX ACL) to `/opt/opsle-components/gearbox` |
| `ops/ci/verify SHA` | deploy runner, or anywhere | nothing; read-only |

- **Merge enforcement:** `opsle` is on a free GitHub plan (no rulesets, branch protection or auto-merge). Merge only with `gh pr merge --squash --delete-branch` after `quality` passes.
- **Exact SHA:** `deploy.yml` is a `workflow_run` of the `push`-to-`main` CI run and checks out and deploys `workflow_run.head_sha`. `ops/ci/deploy` refuses anything that is not the tip of main (exit 75 = superseded) or a checkout that is not exactly that SHA.
- **Atomic switch:** releases are deployed under `/opt/opsle-components/gearbox/releases/<sha>` and atomically pointed to by `current`.
- **Single release owner:** `deploy.yml` does nothing unless repository variable `GEARBOX_DEPLOY_ENABLED` is `true`.

## Operator gates (root; not doable by agents)

Run `bash ops/ci/operator-gate test deploy` in a terminal. It mints the registration tokens and installs the self-hosted runners.
