# Test Structure

## Hierarchy

```
TestSuite
  └── TestSet (logical grouping)
        └── TestCase (atomic test)
```

- **TestSuite** — the top-level container. Encompasses all TestSets.
- **TestSet** — a logical grouping of TestCases around a specific feature or area (e.g. CAN, CLI, Plugins, PDU).
- **TestCase** — an atomic test with preconditions, steps, expected outcome, verdict, and result notes.

A TestCase may belong to multiple TestSets. For example, "Send CAN Frame via CLI" belongs to both the `CAN` TestSet and the `CLI` TestSet.

## Naming Convention

- Files: `TestSetName.md` (PascalCase)
- TestCases: `TC_<TestSet>_<number>_<short_description>` (e.g. `TC_CAN_001_send_frame_cli`)
- TestSets referenced inside a TestCase use brackets: `[CAN]`, `[CLI]`

## TestCase Template

```markdown
### TC_<TestSet>_<NNN>_<short_description>

**TestSets:** [TestSet1], [TestSet2], ...

**Preconditions:**
- precondition 1
- precondition 2

**TestSteps:**
1. step one
2. step two

**Expected:**
- expected outcome

**Verdict:** OK / NOK / INCONCLUSIVE / NOT_TESTED

**Result:**
comments, issue references, or empty
```

## Verdicts

| Verdict       | Meaning |
|---------------|---------|
| OK            | Test passed — actual matches expected |
| NOK           | Test failed — actual differs from expected |
| INCONCLUSIVE  | Result ambiguous (e.g. flaky, infra issue, missing info) |
| NOT_TESTED    | Intentionally skipped (preconditions not met, blocked by earlier failure, out of scope) |

## Scope

Tests in this folder operate at the **system level** — they test the platform as an end-user would interact with it:

- CLI commands (`boat ...`)
- gRPC API (via `boat`, `candump`, Python SDK)
- Gateway lifecycle (start, stop, plugin loading)
- CAN / Ethernet frame send/receive
- PDU routing, groups, transmission schedules
- Plugin behaviour (load, unload, trigger, filter)
- Error handling and edge cases

Everything is executed against a running gateway instance. No unit-test mocking, no internal API access.
