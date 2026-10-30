# Track B Open Items and Team Requests

Assign an owner and attach evidence before closing each item.

| Priority | Item | Suggested owner | Closure evidence |
|---|---|---|---|
| High | Confirm canonical VSS signal list; reconcile battery voltage vs electric-motor coolant-temperature references | Track A + Track B | Agreed signal contract committed to config/docs |
| High | Confirm `telemetry_rollup_1m` schema, field meanings, and provenance values with Track A | Track A + Track B | Integration test against agreed schema |
| High | Verify Track A AI-hook and end-to-end query/explanation flow | Track A | Demonstrated end-to-end test; no merge to main before this |
| High | Implement or confirm application lifecycle for source start/stop, periodic flush, errors, shutdown | Track B | Lifecycle tests and documented invocation |
| High | Decide shutdown semantics for incomplete rollups and pending event captures | Track B + Track A | Explicit policy and tests |
| Medium | Validate real CAN with agreed DBC and vcan/target interface | Track B / vehicle integration | Reproducible environment and results |
| Medium | Validate live CARLA integration and collision/event capture | Track B / simulation owner | Reproducible scenario and results |
| Medium | Measure full application memory/cgroup usage against target | Runtime/platform owner | Reproducible measurement under representative load |
| Medium | Reconcile `docs/track_b/PROGRESS.md` with implementation/tests | Track B docs owner | Updated progress with evidence and date |
| Low | Confirm packaging/entry point and UI startup expectations | Track B + UI owner | Agreed CLI/UI behavior and launch test |

## Questions for the team
1. Which VSS signals are canonical for integration?
2. Does Track A accept the existing SQLite schema, or are migrations required?
3. What provenance vocabulary should be persisted (`SIMULATED`, `REPLAYED`, `REAL`, or implementation-specific strings)?
4. What flush cadence and retention guarantee are required?
5. Should shutdown wait for pending event-capture windows, cancel them, or persist pending state?
6. What evidence is required for live CAN/CARLA and memory-budget acceptance?
7. What command or UI entry point should the integrated application expose?

## Completion criteria
Do not mark Track B 100% complete until agreed required items are closed with recorded evidence. Passing unit tests is necessary evidence, but not sufficient by itself.
