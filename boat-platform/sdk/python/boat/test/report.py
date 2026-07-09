from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional


@dataclass
class MetaInfo:
    __test__ = False
    report_schema_version: str = "1.0"
    generated_at: str = ""
    generator: str = "boat-test"

    def __post_init__(self) -> None:
        if not self.generated_at:
            self.generated_at = datetime.now(timezone.utc).isoformat()


@dataclass
class TestInfo:
    __test__ = False
    id: str
    name: str
    group: Optional[str] = None
    file: Optional[str] = None
    version: Optional[str] = None
    description: Optional[str] = None


@dataclass
class ExecutionInfo:
    __test__ = False
    started_at: str = ""
    finished_at: Optional[str] = None
    duration_ms: Optional[int] = None
    verdict: str = "RUNNING"
    runner_hostname: Optional[str] = None
    runner_user: Optional[str] = None
    ci_build_id: Optional[str] = None
    ci_job_url: Optional[str] = None
    gateway_version: Optional[str] = None
    dut_version: Optional[str] = None
    boat_version: Optional[str] = None
    test_script_version: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.started_at:
            self.started_at = datetime.now(timezone.utc).isoformat()

    def finish(self) -> None:
        self.finished_at = datetime.now(timezone.utc).isoformat()
        start = datetime.fromisoformat(self.started_at)
        end = datetime.fromisoformat(self.finished_at)
        self.duration_ms = int((end - start).total_seconds() * 1000)


@dataclass
class PreconditionRecord:
    __test__ = False
    id: int
    action: str
    params: dict = field(default_factory=dict)
    result: Optional[str] = None
    duration_ms: Optional[int] = None
    error: Optional[str] = None


@dataclass
class StimulusRecord:
    type: str  # can | eth | pdu | signal
    bus: Optional[str] = None
    can_id: Optional[int] = None
    data: Optional[str] = None
    dlc: Optional[int] = None
    direction: str = "TX"
    timestamp_ns: Optional[int] = None
    step_sim_tick: Optional[int] = None


@dataclass
class ObservationRecord:
    type: str  # can | eth | pdu | signal
    bus: Optional[str] = None
    can_id: Optional[int] = None
    data: Optional[str] = None
    dlc: Optional[int] = None
    direction: str = "RX"
    timestamp_ns: Optional[int] = None
    step_sim_tick: Optional[int] = None
    latency_us: Optional[int] = None


@dataclass
class ExpectedRecord:
    type: str  # can | eth | pdu | signal
    bus: Optional[str] = None
    can_id: Optional[int] = None
    data: Optional[str] = None
    data_mask: Optional[str] = None
    timeout_ms: Optional[int] = None


@dataclass
class AssertionRecord:
    expression: str
    expected: str
    actual: str
    result: str  # PASS | FAIL | ERROR

    @classmethod
    def pass_(cls, expression: str, expected: str = "true", actual: str = "true") -> AssertionRecord:
        return cls(expression=expression, expected=expected, actual=actual, result="PASS")

    @classmethod
    def fail(cls, expression: str, expected: str, actual: str) -> AssertionRecord:
        return cls(expression=expression, expected=expected, actual=actual, result="FAIL")

    @classmethod
    def error(cls, expression: str, message: str) -> AssertionRecord:
        return cls(expression=expression, expected="OK", actual=message, result="ERROR")


@dataclass
class TraceRef:
    id: str
    path: str
    format: str  # blf | asc | pcap | binary
    size_bytes: Optional[int] = None
    event_count: Optional[int] = None
    checksum_sha256: Optional[str] = None


@dataclass
class Attachment:
    name: str
    path: str
    mime: Optional[str] = None


@dataclass
class TestStepRecord:
    __test__ = False
    id: int
    name: str
    description: Optional[str] = None
    started_at: Optional[str] = None
    duration_ms: Optional[int] = None
    verdict: str = "SKIPPED"
    stimuli: list[StimulusRecord] = field(default_factory=list)
    observations: list[ObservationRecord] = field(default_factory=list)
    expected: list[ExpectedRecord] = field(default_factory=list)
    assertions: list[AssertionRecord] = field(default_factory=list)
    trace_refs: list[str] = field(default_factory=list)
    attachments: list[Attachment] = field(default_factory=list)

    def add_stimulus(self, **kwargs: Any) -> StimulusRecord:
        r = StimulusRecord(**kwargs)
        self.stimuli.append(r)
        return r

    def add_observation(self, **kwargs: Any) -> ObservationRecord:
        r = ObservationRecord(**kwargs)
        self.observations.append(r)
        return r

    def add_expected(self, **kwargs: Any) -> ExpectedRecord:
        r = ExpectedRecord(**kwargs)
        self.expected.append(r)
        return r

    def add_assertion(self, record: AssertionRecord) -> None:
        self.assertions.append(record)
        if record.result == "FAIL":
            self.verdict = "FAIL"
        elif record.result == "ERROR":
            self.verdict = "ERROR"

    def finish(self) -> None:
        if self.verdict == "SKIPPED":
            self.verdict = "PASS" if not any(
                a.result != "PASS" for a in self.assertions
            ) else "FAIL"


@dataclass
class TestReport:
    __test__ = False
    meta: MetaInfo = field(default_factory=MetaInfo)
    test: Optional[TestInfo] = None
    environment: dict = field(default_factory=dict)
    execution: ExecutionInfo = field(default_factory=ExecutionInfo)
    preconditions: list[PreconditionRecord] = field(default_factory=list)
    steps: list[TestStepRecord] = field(default_factory=list)
    traces: list[TraceRef] = field(default_factory=list)
    attachments: list[Attachment] = field(default_factory=list)
    verdict: str = "RUNNING"
    summary: Optional[str] = None

    def add_precondition(self, **kwargs: Any) -> PreconditionRecord:
        r = PreconditionRecord(**kwargs)
        self.preconditions.append(r)
        return r

    def add_step(self, step: TestStepRecord) -> None:
        self.steps.append(step)

    def add_trace(self, **kwargs: Any) -> TraceRef:
        r = TraceRef(**kwargs)
        self.traces.append(r)
        return r

    def finish(self) -> None:
        self.execution.finish()
        if self.verdict == "RUNNING":
            step_fails = [s for s in self.steps if s.verdict == "FAIL"]
            step_errors = [s for s in self.steps if s.verdict == "ERROR"]
            if step_errors:
                self.verdict = "ERROR"
            elif step_fails:
                self.verdict = "FAIL"
            else:
                self.verdict = "PASS"

        total_assertions = sum(len(s.assertions) for s in self.steps)
        passed = sum(1 for s in self.steps for a in s.assertions if a.result == "PASS")
        self.summary = (
            f"{len(self.steps)} steps, "
            f"{passed}/{total_assertions} assertions passed, "
            f"{len(self.preconditions)} preconditions"
        )

    def to_dict(self) -> dict:
        return _asdict_filtered(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def save(self, path: str) -> None:
        with open(path, "w") as f:
            f.write(self.to_json())

    @classmethod
    def from_file(cls, path: str) -> TestReport:
        with open(path) as f:
            d = json.load(f)
        return cls.from_dict(d)

    @classmethod
    def from_dict(cls, d: dict) -> TestReport:
        meta = MetaInfo(**d.get("meta", {}))
        test = TestInfo(**d["test"]) if d.get("test") else None
        execution = ExecutionInfo(**d.get("execution", {}))
        preconditions = [PreconditionRecord(**p) for p in d.get("preconditions", [])]
        steps = [_step_from_dict(s) for s in d.get("steps", [])]
        traces = [TraceRef(**t) for t in d.get("traces", [])]
        attachments = [Attachment(**a) for a in d.get("attachments", [])]
        return cls(
            meta=meta, test=test, environment=d.get("environment", {}),
            execution=execution, preconditions=preconditions, steps=steps,
            traces=traces, attachments=attachments,
            verdict=d.get("verdict", "RUNNING"), summary=d.get("summary"),
        )


def _step_from_dict(d: dict) -> TestStepRecord:
    stimuli = [StimulusRecord(**s) for s in d.get("stimuli", [])]
    observations = [ObservationRecord(**o) for o in d.get("observations", [])]
    expected = [ExpectedRecord(**e) for e in d.get("expected", [])]
    assertions = [AssertionRecord(**a) for a in d.get("assertions", [])]
    attachments = [Attachment(**a) for a in d.get("attachments", [])]
    return TestStepRecord(
        id=d["id"], name=d["name"], description=d.get("description"),
        started_at=d.get("started_at"), duration_ms=d.get("duration_ms"),
        verdict=d.get("verdict", "SKIPPED"),
        stimuli=stimuli, observations=observations, expected=expected,
        assertions=assertions, trace_refs=d.get("trace_refs", []),
        attachments=attachments,
    )


def _asdict_filtered(obj: Any) -> Any:
    """asdict but drop None values and empty lists."""
    result = asdict(obj)
    return _strip_empty(result)


def _strip_empty(d: Any) -> Any:
    if isinstance(d, dict):
        return {k: _strip_empty(v) for k, v in d.items()
                if v is not None and v != [] and v != {}}
    if isinstance(d, list):
        return [_strip_empty(v) for v in d]
    return d
