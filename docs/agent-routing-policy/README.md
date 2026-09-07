# Routing policy lineage

Imported from opsle/agent-routing-policy at `43fc2a72d2c8494b2dcdca7b5a209de61d8fe2d8` under its preserved Apache-2.0 license. Gearbox remains independently usable. `src/opsle_gearbox/route.py` owns filtering, route selection, exact phase routing and explicit reasons; `tests/test_route.py` verifies it. These historical specifications are reference constraints, not claims that every research hypothesis is implemented. No second policy engine or dependency on Tasks is introduced.

Migration manifest: https://github.com/opsle/tasks/blob/main/docs/migrations/20260907-consolidation.md
