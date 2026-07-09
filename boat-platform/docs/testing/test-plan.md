# Test Plan

## Scope

Validate correctness, determinism, performance, reliability, and safety for:

- simulation core
- plugin lifecycle and ABI boundaries
- IPC channels (iceoryx2, UDS, gRPC)
- persistence and replay
- HIL integration paths
- CLI and SDK behavior

## Test Levels and Ownership

| Level | Owner | Execution |
|---|---|---|
| Unit | Test Engineer + module owners | Per-commit CI |
| Integration | Test Engineer | Per-PR and nightly |
| Determinism | Test Manager + Test Engineer | Per-PR gate |
| HIL/E2E | Test Manager | Scheduled and release candidate |

## Entry Criteria

- Requirements baselined in `project-plan.md`
- API contract baselined in `api/api-specification.md`
- Test environments provisioned (Docker, virtual CAN, optional hardware lab)

## Exit Criteria

- Unit and integration suites pass in CI matrix
- Determinism checks pass with bit-exact trace output
- Critical and high severity defects resolved or risk-accepted
- Release notes and known limitations documented

## Execution Schedule

1. **Sprint-level:** unit and integration test development alongside feature work
2. **Milestone-level:** determinism sweeps, performance baselines, regression packs
3. **Pre-release:** HIL qualification suite, packaging verification, smoke tests on release artifacts

## Deliverables

- Automated test suites (`tests/unit`, `tests/integration`, `tests/determinism`, `tests/hil`)
- CI test reports and coverage reports
- Determinism diff artifacts
- Defect reports and milestone quality summaries

