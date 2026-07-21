from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Optional

from boat.test.allure_report import generate_allure_results
from boat.test.check import check_environment
from boat.test.config import EnvironmentConfig, ManifestConfig, ManifestTestEntry
from boat.test.exceptions import TestGatewayError
from boat.test.harness import TestHarness
from boat.test.html_report import generate_html_report
from boat.test.report import TestReport, MetaInfo, TestInfo, ExecutionInfo, PreconditionRecord


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _generate_junit_xml(report: TestReport) -> str:
    total = len(report.steps)
    failures = sum(1 for s in report.steps if s.verdict == "FAIL")
    errors = sum(1 for s in report.steps if s.verdict == "ERROR")
    skipped = sum(1 for s in report.steps if s.verdict == "SKIPPED")

    test_name = report.test.name if report.test else "unknown"
    suite_name = report.test.group or "default"
    duration_s = (report.execution.duration_ms or 0) / 1000

    lines: list[str] = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append(
        f'<testsuite name="{suite_name}" tests="{total}" '
        f'failures="{failures}" errors="{errors}" skipped="{skipped}" '
        f'time="{duration_s:.3f}" timestamp="{report.execution.started_at}">'
    )

    for step in report.steps:
        classname = f"{suite_name}.{test_name}"
        step_duration = (step.duration_ms or 0) / 1000
        if step.verdict == "FAIL":
            lines.append(f'  <testcase name="{step.name}" classname="{classname}" time="{step_duration:.3f}">')
            for a in step.assertions:
                if a.result != "PASS":
                    lines.append(f'    <failure message="{a.expression}" type="AssertionError">')
                    lines.append(f"      Expected: {a.expected}, Actual: {a.actual}")
                    lines.append("    </failure>")
            lines.append("  </testcase>")
        elif step.verdict == "ERROR":
            lines.append(f'  <testcase name="{step.name}" classname="{classname}" time="{step_duration:.3f}">')
            for a in step.assertions:
                if a.result != "PASS":
                    lines.append(f'    <error message="{a.expression}" type="Error">')
                    lines.append(f"      {a.actual}")
                    lines.append("    </error>")
            lines.append("  </testcase>")
        elif step.verdict == "SKIPPED":
            lines.append(f'  <testcase name="{step.name}" classname="{classname}" time="{step_duration:.3f}">')
            lines.append("    <skipped/>")
            lines.append("  </testcase>")
        else:
            lines.append(f'  <testcase name="{step.name}" classname="{classname}" time="{step_duration:.3f}"/>')

    lines.append("</testsuite>")
    return "\n".join(lines)


class TestSuiteRunner:
    __test__ = False

    def __init__(
        self,
        manifest: ManifestConfig,
        env_config: EnvironmentConfig,
        report_dir: str = "./reports",
        stop_on_failure: bool = False,
        verbose: bool = False,
        generate_html: bool = True,
        allure_dir: Optional[str] = None,
        parallel: int = 1,
        preflight: bool = False,
        recorder_url: Optional[str] = None,
        trace_format: str = "blf",
    ) -> None:
        self.manifest = manifest
        self.env_config = env_config
        self.report_dir = report_dir
        self.stop_on_failure = stop_on_failure
        self.verbose = verbose
        self.generate_html = generate_html
        self.allure_dir = allure_dir
        self.parallel = parallel
        self.preflight = preflight
        self.recorder_url = recorder_url
        self.trace_format = trace_format
        self._results: list[dict[str, Any]] = []

    def run(self) -> int:
        if self.preflight:
            issues = check_environment(self.env_config)
            if issues:
                print("Pre-flight check failed:", file=sys.stderr)
                for issue in issues:
                    print(f"  \u2717 {issue}", file=sys.stderr)
                return 1
            if self.verbose:
                print("[test] Pre-flight check passed", file=sys.stderr)

        if self.parallel > 1 and len(self.manifest.tests) > 1:
            return self._run_parallel()

        return self._run_sequential()

    # ── Sequential ─────────────────────────────────────────────────────────

    def _run_sequential(self) -> int:
        harness = TestHarness(self.env_config,
                              recorder_url=self.recorder_url,
                              trace_format=self.trace_format)
        exit_code = 0

        try:
            harness.start()
            if self.verbose:
                print(f"[test] Gateway at {self.env_config.gateway.address}", file=sys.stderr)

            self._run_actions(harness, self.manifest.setup, "setup")

            for entry in self.manifest.tests:
                passed = self._run_single_test(harness, entry)
                if not passed:
                    exit_code = 1
                    if self.stop_on_failure:
                        break

            self._run_actions(harness, self.manifest.teardown, "teardown")

        except TestGatewayError as exc:
            print(f"Gateway error: {exc}", file=sys.stderr)
            exit_code = 1
        except Exception as exc:
            print(f"Runner error: {exc}", file=sys.stderr)
            exit_code = 1
        finally:
            try:
                harness.stop(report_dir=self.report_dir)
            except Exception:
                pass

        self._print_summary()
        return exit_code

    # ── Parallel ───────────────────────────────────────────────────────────

    def _run_parallel(self) -> int:
        harness = TestHarness(self.env_config,
                              recorder_url=self.recorder_url,
                              trace_format=self.trace_format)
        exit_code = 0

        try:
            harness.start()
            if self.verbose:
                print(f"[test] Gateway at {self.env_config.gateway.address}", file=sys.stderr)

            self._run_actions(harness, self.manifest.setup, "setup")

            with ThreadPoolExecutor(max_workers=self.parallel) as pool:
                fut_to_entry = {
                    pool.submit(self._run_single_test, harness, e): e
                    for e in self.manifest.tests
                }
                for future in as_completed(fut_to_entry):
                    entry = fut_to_entry[future]
                    try:
                        passed = future.result()
                        if not passed:
                            exit_code = 1
                            if self.stop_on_failure:
                                if self.verbose:
                                    print(f"[test] Stopping: {entry.id} failed", file=sys.stderr)
                                pool.shutdown(wait=False, cancel_futures=True)
                                break
                    except Exception as exc:
                        print(f"  [ERROR] {entry.id}: {exc}", file=sys.stderr)
                        exit_code = 1

            self._run_actions(harness, self.manifest.teardown, "teardown")

        except TestGatewayError as exc:
            print(f"Gateway error: {exc}", file=sys.stderr)
            exit_code = 1
        except Exception as exc:
            print(f"Runner error: {exc}", file=sys.stderr)
            exit_code = 1
        finally:
            try:
                harness.stop(report_dir=self.report_dir)
            except Exception:
                pass

        self._print_summary()
        return exit_code

    # ── Shared ─────────────────────────────────────────────────────────────

    def _run_actions(self, harness: TestHarness, actions: list, phase: str) -> None:
        for action in actions:
            if self.verbose:
                print(f"[test] {phase}: {action.action} {action.params}", file=sys.stderr)
            if action.action == "load_scenario":
                from boat.v1 import scenario_pb2
                content = json.dumps(action.params.get("content", {}))
                req = scenario_pb2.CreateScenarioRequest(
                    scenario=scenario_pb2.Scenario(
                        scenario_id=action.params.get("id", "scn"),
                        name=action.params.get("name", action.params.get("id", "scn")),
                        content=content,
                    )
                )
                harness.client.scenario.CreateScenario(req)
            elif action.action == "configure_pdu_route":
                from boat.v1 import pdu_pb2
                params = action.params
                route = pdu_pb2.PduRoute(
                    pdu_id=params.get("pdu_id", 0),
                    transport=getattr(pdu_pb2, params.get("transport", "CAN")),
                    iface=params.get("iface", ""),
                    schedule=pdu_pb2.PduSchedule(
                        send_type=getattr(pdu_pb2, f'SEND_TYPE_{params.get("send_type", "NONE").upper()}',
                                           pdu_pb2.SEND_TYPE_NONE),
                        cycle_ms=params.get("cycle_ms", 0),
                        fast_ms=params.get("fast_ms", 0),
                        repetitions=params.get("repetitions", 0),
                    ),
                )
                harness.client.pdu.ConfigureRoute(
                    pdu_pb2.ConfigureRouteRequest(route=route)
                )

    def _run_single_test(self, harness: TestHarness, entry: ManifestTestEntry) -> bool:
        ts = _timestamp()
        folder_name = f"{ts}_{entry.id}"
        folder_path = os.path.join(self.report_dir, folder_name)
        os.makedirs(folder_path, exist_ok=True)

        report = TestReport()
        report.test = TestInfo(id=entry.id, name=entry.name, description=entry.description,
                               group=self.manifest.name, steps=entry.steps)
        report.environment = harness.config.snapshot()
        report.execution.started_at = _now_iso()

        if self.verbose:
            print(f"[test] Running {entry.id}: {entry.name}", file=sys.stderr)

        cmd_str = entry.file
        started = datetime.now(timezone.utc)

        try:
            result = subprocess.run(
                cmd_str.split(),
                capture_output=True,
                text=True,
                timeout=entry.timeout_s,
            )
            passed = result.returncode == 0
        except subprocess.TimeoutExpired:
            result = None
            passed = False
        except FileNotFoundError:
            result = None
            passed = False

        finished = datetime.now(timezone.utc)
        duration_ms = int((finished - started).total_seconds() * 1000)

        report.execution.finished_at = _now_iso()
        report.execution.duration_ms = duration_ms
        report.execution.verdict = "PASS" if passed else "FAIL"
        report.verdict = "PASS" if passed else "FAIL"

        if result:
            report.add_trace(
                id=f"{entry.id}_stdout",
                path=os.path.join(folder_path, "stdout.txt"),
                format="binary",
            )
            with open(os.path.join(folder_path, "stdout.txt"), "w") as f:
                f.write(result.stdout)
            if result.stderr:
                report.add_trace(
                    id=f"{entry.id}_stderr",
                    path=os.path.join(folder_path, "stderr.txt"),
                    format="binary",
                )
                with open(os.path.join(folder_path, "stderr.txt"), "w") as f:
                    f.write(result.stderr)

        report.save(os.path.join(folder_path, "report.json"))

        junit = _generate_junit_xml(report)
        with open(os.path.join(folder_path, "report.junit.xml"), "w") as f:
            f.write(junit)

        if self.generate_html:
            html = generate_html_report(report)
            with open(os.path.join(folder_path, "report.html"), "w") as f:
                f.write(html)

        if self.allure_dir:
            per_test_allure = os.path.join(folder_path, "allure")
            generate_allure_results(report, per_test_allure)
            generate_allure_results(report, self.allure_dir)

        self._results.append({
            "id": entry.id,
            "name": entry.name,
            "verdict": report.verdict,
            "duration_ms": duration_ms,
            "report_dir": folder_path,
        })

        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {entry.id} ({duration_ms}ms)", file=sys.stderr)
        return passed

    def _print_summary(self) -> None:
        total = len(self._results)
        passed = sum(1 for r in self._results if r["verdict"] == "PASS")
        failed = total - passed
        print(file=sys.stderr)
        print(f"Results: {passed}/{total} passed, {failed} failed", file=sys.stderr)
        for r in self._results:
            status_icon = "\u2713" if r["verdict"] == "PASS" else "\u2717"
            print(f"  {status_icon} {r['id']}: {r['verdict']} ({r['duration_ms']}ms)", file=sys.stderr)
