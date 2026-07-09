import json

from boat.test.report import (
    TestReport, TestStepRecord, AssertionRecord,
    StimulusRecord, ObservationRecord, ExpectedRecord,
)


class TestTestReport:
    def test_minimal_report(self) -> None:
        r = TestReport()
        assert r.meta.report_schema_version == "1.0"
        assert r.meta.generator == "boat-test"
        assert r.execution.verdict == "RUNNING"

    def test_add_step_and_finish(self) -> None:
        r = TestReport()
        step = TestStepRecord(id=1, name="Step 1")
        step.add_stimulus(type="can", bus="can1", can_id=0x100, data="01F4")
        step.add_observation(type="can", bus="can2", can_id=0x200, data="02F4")
        step.add_expected(type="can", bus="can2", can_id=0x200, timeout_ms=200)
        step.add_assertion(AssertionRecord.pass_("frame.id == 0x200"))
        step.add_assertion(AssertionRecord.pass_("frame.data == '02F4'"))

        assert len(step.stimuli) == 1
        assert len(step.observations) == 1
        assert len(step.expected) == 1
        assert len(step.assertions) == 2

        step.finish()
        assert step.verdict == "PASS"

        r.add_step(step)
        r.finish()
        assert r.verdict == "PASS"
        assert r.summary is not None
        assert "1 steps" in r.summary
        assert "2/2 assertions passed" in r.summary

    def test_fail_on_bad_assertion(self) -> None:
        r = TestReport()
        step = TestStepRecord(id=1, name="Fail step")
        step.add_assertion(AssertionRecord.fail("frame.id == 0x300", "0x300", "0x400"))
        step.finish()
        assert step.verdict == "FAIL"
        r.add_step(step)
        r.finish()
        assert r.verdict == "FAIL"

    def test_add_precondition(self) -> None:
        r = TestReport()
        r.add_precondition(id=1, action="load_scenario", params={"id": "s1"}, result="OK")
        assert len(r.preconditions) == 1
        assert r.preconditions[0].action == "load_scenario"

    def test_add_trace(self) -> None:
        r = TestReport()
        r.add_trace(id="trace1", path="trace.blf", format="blf", size_bytes=4096)
        assert len(r.traces) == 1
        assert r.traces[0].format == "blf"

    def test_to_json_round_trip(self) -> None:
        r = TestReport()
        step = TestStepRecord(id=1, name="S1")
        step.add_assertion(AssertionRecord.pass_("ok"))
        step.finish()
        r.add_step(step)
        r.finish()

        json_str = r.to_json()
        parsed = json.loads(json_str)

        assert parsed["meta"]["report_schema_version"] == "1.0"
        assert parsed["verdict"] == "PASS"
        assert len(parsed["steps"]) == 1

        restored = TestReport.from_dict(parsed)
        assert restored.verdict == "PASS"
        assert restored.steps[0].name == "S1"

    def test_from_file(self, tmp_path) -> None:
        r = TestReport()
        step = TestStepRecord(id=1, name="S1")
        step.add_assertion(AssertionRecord.pass_("ok"))
        step.finish()
        r.add_step(step)
        r.finish()

        path = tmp_path / "report.json"
        r.save(str(path))

        loaded = TestReport.from_file(str(path))
        assert loaded.verdict == "PASS"
        assert loaded.steps[0].name == "S1"
