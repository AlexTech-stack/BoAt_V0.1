from boat.test.html_report import generate_html_report
from boat.test.report import (
    TestReport, TestInfo, TestStepRecord, AssertionRecord,
    StimulusRecord, ObservationRecord, ExpectedRecord, PreconditionRecord, TraceRef,
    ExecutionInfo,
)


def _make_report(verdict="PASS") -> TestReport:
    r = TestReport()
    r.test = TestInfo(id="TC-1", name="Speed Test", group="powertrain",
                      version="1.0", description="Verify speed response")
    r.execution = ExecutionInfo(started_at="2026-06-16T12:00:00Z",
                                 finished_at="2026-06-16T12:00:05Z",
                                 duration_ms=5000, verdict=verdict,
                                 runner_hostname="hil-01", runner_user="jenkins",
                                 gateway_version="v2.1.0", dut_version="fw-v4.3",
                                 boat_version="1.0.0")
    r.verdict = verdict
    return r


def _add_full_step(r: TestReport, verdict="PASS") -> None:
    step = TestStepRecord(id=1, name="Send RPM Request", verdict=verdict,
                           description="Send 0x100 with RPM=500")
    step.stimuli.append(StimulusRecord(type="can", bus="can1", can_id=0x100,
                                        data="01F4", dlc=2, direction="TX",
                                        timestamp_ns=1749600001000000000))
    step.observations.append(ObservationRecord(type="can", bus="can2", can_id=0x300,
                                                data="01F4", dlc=2, direction="RX",
                                                timestamp_ns=1749600001050000000,
                                                latency_us=50))
    step.expected.append(ExpectedRecord(type="can", bus="can2", can_id=0x300,
                                         data="01F4", timeout_ms=200))
    step.add_assertion(AssertionRecord.pass_("frame.can_id == 0x300"))
    step.add_assertion(AssertionRecord.fail("frame.data == expected", "01F4", "02F4"))
    r.add_step(step)


class TestHtmlReportGenerator:
    def test_generates_valid_html(self) -> None:
        r = _make_report()
        html = generate_html_report(r)
        assert html.startswith("<!DOCTYPE html>")
        assert html.strip().endswith("</html>")
        assert len(html) > 200

    def test_contains_test_name(self) -> None:
        r = _make_report()
        html = generate_html_report(r)
        assert "Speed Test" in html

    def test_contains_verdict(self) -> None:
        r = _make_report("PASS")
        html = generate_html_report(r)
        assert "PASS" in html

    def test_contains_fail_verdict(self) -> None:
        r = _make_report("FAIL")
        html = generate_html_report(r)
        assert "FAIL" in html

    def test_contains_step_names(self) -> None:
        r = _make_report()
        _add_full_step(r)
        html = generate_html_report(r)
        assert "Send RPM Request" in html

    def test_assertion_colors(self) -> None:
        r = _make_report()
        _add_full_step(r)
        html = generate_html_report(r)
        assert "assert-pass" in html
        assert "assert-fail" in html

    def test_html_escapes_special_chars(self) -> None:
        r = _make_report()
        r.test.name = "AT&T <Test>"
        html = generate_html_report(r)
        assert "&amp;" in html
        assert "&lt;" in html
        assert "&gt;" in html
        assert "<Test>" not in html

    def test_empty_report(self) -> None:
        r = _make_report()
        html = generate_html_report(r)
        assert "No steps recorded" in html

    def test_environment_section(self) -> None:
        r = _make_report()
        r.environment = {"name": "virtual-ci", "gateway": {"address": "x:1"}}
        html = generate_html_report(r)
        assert "virtual-ci" in html

    def test_trace_links(self) -> None:
        r = _make_report()
        r.traces.append(TraceRef(id="t1", path="traces/test.blf", format="blf", size_bytes=4096))
        html = generate_html_report(r)
        assert "test.blf" in html

    def test_preconditions(self) -> None:
        r = _make_report()
        r.preconditions.append(PreconditionRecord(id=1, action="load_scenario",
                                                   params={"id": "s1"}, result="OK"))
        html = generate_html_report(r)
        assert "load_scenario" in html
        assert "s1" in html

    def test_stimulus_observation_expected_tables(self) -> None:
        r = _make_report()
        _add_full_step(r)
        html = generate_html_report(r)
        assert "Stimuli" in html
        assert "Observations" in html
        assert "Expected" in html
        assert "0x100" in html
        assert "0x300" in html
        assert "01F4" in html

    def test_execution_info(self) -> None:
        r = _make_report()
        html = generate_html_report(r)
        assert "hil-01" in html
        assert "jenkins" in html
        assert "v2.1.0" in html
        assert "fw-v4.3" in html

    def test_generated_at_not_empty(self) -> None:
        r = _make_report()
        html = generate_html_report(r)
        assert "boat-test" in html or "Test Report" in html
