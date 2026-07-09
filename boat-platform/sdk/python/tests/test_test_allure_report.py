import json
import os

from boat.test.allure_report import generate_allure_results
from boat.test.report import (
    TestReport, TestInfo, ExecutionInfo, TestStepRecord, AssertionRecord,
    PreconditionRecord, TraceRef, Attachment,
)


def _make_report(verdict="PASS") -> TestReport:
    r = TestReport()
    r.test = TestInfo(id="TC-001", name="Speed Test", group="powertrain",
                      version="1.2", description="Verify speed")
    r.execution = ExecutionInfo(started_at="2026-06-16T12:00:00Z",
                                 finished_at="2026-06-16T12:00:05Z",
                                 duration_ms=5000, verdict=verdict)
    r.verdict = verdict
    return r


def _add_step(r: TestReport, verdict="PASS") -> None:
    s = TestStepRecord(id=1, name="Send RPM", verdict=verdict, duration_ms=100,
                        started_at="2026-06-16T12:00:01Z")
    s.add_assertion(AssertionRecord.pass_("frame.id == 0x300"))
    if verdict == "FAIL":
        s.add_assertion(AssertionRecord.fail("frame.data == expected", "01F4", "02F4"))
    r.add_step(s)


class TestGenerateAllureResults:
    def test_creates_result_file(self, tmp_path) -> None:
        r = _make_report()
        files = generate_allure_results(r, str(tmp_path))
        assert len(files) >= 1
        assert any(f.endswith("-result.json") for f in files)

    def test_result_contains_required_fields(self, tmp_path) -> None:
        r = _make_report()
        files = generate_allure_results(r, str(tmp_path))
        result_file = [f for f in files if f.endswith("-result.json")][0]
        with open(result_file) as f:
            data = json.load(f)
        assert data["name"] == "Speed Test"
        assert data["status"] == "passed"
        assert data["stage"] == "finished"
        assert "start" in data
        assert "stop" in data
        assert "labels" in data
        assert "steps" in data

    def test_status_mapping_pass(self, tmp_path) -> None:
        r = _make_report("PASS")
        files = generate_allure_results(r, str(tmp_path))
        with open(files[0]) as f:
            assert json.load(f)["status"] == "passed"

    def test_status_mapping_fail(self, tmp_path) -> None:
        r = _make_report("FAIL")
        files = generate_allure_results(r, str(tmp_path))
        with open(files[0]) as f:
            assert json.load(f)["status"] == "failed"

    def test_status_mapping_error(self, tmp_path) -> None:
        r = _make_report("ERROR")
        files = generate_allure_results(r, str(tmp_path))
        with open(files[0]) as f:
            assert json.load(f)["status"] == "broken"

    def test_status_mapping_skipped(self, tmp_path) -> None:
        r = _make_report("SKIPPED")
        files = generate_allure_results(r, str(tmp_path))
        with open(files[0]) as f:
            assert json.load(f)["status"] == "skipped"

    def test_steps_in_result(self, tmp_path) -> None:
        r = _make_report()
        _add_step(r)
        files = generate_allure_results(r, str(tmp_path))
        with open(files[0]) as f:
            data = json.load(f)
        assert len(data["steps"]) == 1
        assert "Step 1" in data["steps"][0]["name"]

    def test_precondition_steps(self, tmp_path) -> None:
        r = _make_report()
        r.preconditions.append(PreconditionRecord(id=1, action="load_scenario",
                                                   params={"id": "s1"}, result="OK"))
        files = generate_allure_results(r, str(tmp_path))
        with open(files[0]) as f:
            data = json.load(f)
        assert any("Precondition" in s["name"] for s in data["steps"])

    def test_labels_include_group(self, tmp_path) -> None:
        r = _make_report()
        files = generate_allure_results(r, str(tmp_path))
        with open(files[0]) as f:
            data = json.load(f)
        labels = {l["name"]: l["value"] for l in data["labels"]}
        assert labels.get("parentSuite") == "powertrain"
        assert labels.get("testId") == "TC-001"

    def test_attachments_from_traces(self, tmp_path) -> None:
        r = _make_report()
        r.traces.append(TraceRef(id="trace1", path="test.blf", format="blf"))
        files = generate_allure_results(r, str(tmp_path))
        with open(files[0]) as f:
            data = json.load(f)
        assert len(data["attachments"]) == 1
        assert data["attachments"][0]["source"] == "test.blf"

    def test_environment_properties(self, tmp_path) -> None:
        r = _make_report()
        r.environment = {"name": "virtual-ci", "tick_ms": 10}
        files = generate_allure_results(r, str(tmp_path))
        prop_files = [f for f in files if f.endswith("environment.properties")]
        assert len(prop_files) >= 1

    def test_assertion_substeps(self, tmp_path) -> None:
        r = _make_report("FAIL")
        _add_step(r, "FAIL")
        files = generate_allure_results(r, str(tmp_path))
        with open(files[0]) as f:
            data = json.load(f)
        step = data["steps"][0]
        assert len(step["steps"]) == 2
        assert step["steps"][1]["status"] == "failed"
