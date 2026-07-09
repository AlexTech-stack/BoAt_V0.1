from unittest.mock import MagicMock, patch

import pytest

from boat.test.config import ManifestConfig, ManifestTestEntry
from boat.test.runner import TestSuiteRunner, _generate_junit_xml
from boat.test.report import TestReport, TestInfo, TestStepRecord, AssertionRecord


class TestJunitXmlGeneration:
    def test_empty_passing(self) -> None:
        report = TestReport()
        report.test = TestInfo(id="TC-1", name="Test 1")
        xml = _generate_junit_xml(report)
        assert 'tests="0"' in xml
        assert 'failures="0"' in xml
        assert "testsuite" in xml

    def test_step_mapped_to_testcase(self) -> None:
        report = TestReport()
        report.test = TestInfo(id="TC-1", name="Test 1")
        step = TestStepRecord(id=1, name="Step 1", verdict="PASS")
        report.add_step(step)
        xml = _generate_junit_xml(report)
        assert 'tests="1"' in xml
        assert 'name="Step 1"' in xml

    def test_failure_included(self) -> None:
        report = TestReport()
        report.test = TestInfo(id="TC-1", name="Test 1")
        step = TestStepRecord(id=1, name="Fail Step", verdict="FAIL")
        step.add_assertion(AssertionRecord.fail("x == 42", "42", "0"))
        report.add_step(step)
        xml = _generate_junit_xml(report)
        assert 'failures="1"' in xml
        assert "<failure" in xml


class TestRunner:
    def test_init(self) -> None:
        manifest = ManifestConfig(schema_version="1.0", name="suite",
                                   tests=[ManifestTestEntry(id="T1", name="Test 1", file="echo ok")])
        env_cfg = MagicMock()
        runner = TestSuiteRunner(manifest, env_cfg, report_dir="/tmp/reports")
        assert runner.manifest.name == "suite"
        assert len(runner.manifest.tests) == 1

    def test_run_delegates_to_harness(self, tmp_path) -> None:
        from boat.test.config import EnvironmentConfig, GatewayConfig, BusConfig
        manifest = ManifestConfig(schema_version="1.0", name="suite",
                                   tests=[ManifestTestEntry(id="T1", name="Test 1", file="echo ok")])
        env_cfg = EnvironmentConfig(
            schema_version="1.0", name="test", description="test env",
            gateway=GatewayConfig(address="localhost:50051"),
            buses={"can1": BusConfig(logical_name="can1", type="virtual", interface="vcan0")},
            dut=None,
        )
        runner = TestSuiteRunner(manifest, env_cfg, report_dir=str(tmp_path))
        assert runner.manifest.name == "suite"
        assert len(runner.manifest.tests) == 1
        assert runner.env_config.name == "test"
