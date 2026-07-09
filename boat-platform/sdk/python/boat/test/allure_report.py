from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from typing import Any, Optional

from boat.test.report import TestReport


def generate_allure_results(report: TestReport, output_dir: str) -> list[str]:
    """Generate Allure JSON result files from a TestReport.

    Writes one ``{test_id}-result.json`` file per test case into *output_dir*,
    plus an ``environment.properties`` file if environment data is available.
    Returns the list of generated file paths.
    """
    os.makedirs(output_dir, exist_ok=True)
    generated: list[str] = []

    # ── Main result file ────────────────────────────────────────────────────
    result = _build_allure_result(report)
    fname = f"{_safe_id(report)}-result.json"
    fpath = os.path.join(output_dir, fname)
    with open(fpath, "w") as f:
        json.dump(result, f, indent=2)
    generated.append(fpath)

    # ── Environment properties ──────────────────────────────────────────────
    if report.environment:
        env_path = _write_environment_properties(report, output_dir)
        if env_path:
            generated.append(env_path)

    return generated


def _safe_id(report: TestReport) -> str:
    """Return a filesystem-safe identifier."""
    tid = report.test.id if report.test and report.test.id else str(uuid.uuid4())
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in tid)


def _iso_to_epoch_ms(iso_str: Optional[str]) -> Optional[int]:
    """Convert ISO 8601 string to epoch milliseconds."""
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str)
        return int(dt.timestamp() * 1000)
    except (ValueError, TypeError):
        return None


def _map_verdict(verdict: str) -> str:
    """Map boat verdict to Allure status."""
    mapping = {
        "PASS": "passed",
        "FAIL": "failed",
        "ERROR": "broken",
        "SKIPPED": "skipped",
        "RUNNING": "unknown",
    }
    return mapping.get(verdict, "unknown")


def _build_allure_result(report: TestReport) -> dict:
    test = report.test
    exec_ = report.execution

    start_ms = _iso_to_epoch_ms(exec_.started_at)
    stop_ms = _iso_to_epoch_ms(exec_.finished_at)

    result: dict[str, Any] = {
        "name": test.name if test else "Untitled",
        "status": _map_verdict(report.verdict),
        "stage": "finished",
        "start": start_ms or 0,
        "stop": stop_ms or 0,
        "fullName": f"{test.group}.{test.id}" if test and test.group else (test.id if test else ""),
        "labels": _build_labels(report),
        "steps": _build_steps(report),
        "attachments": _build_attachments(report),
    }

    if test and test.description:
        result["description"] = test.description
    if test and test.version:
        result["parameters"] = [{"name": "version", "value": test.version}]

    return result


def _build_labels(report: TestReport) -> list[dict[str, str]]:
    test = report.test
    labels: list[dict[str, str]] = []

    if test and test.group:
        labels.append({"name": "parentSuite", "value": test.group})
    if test:
        labels.append({"name": "suite", "value": test.name})
    if test and test.group:
        labels.append({"name": "package", "value": test.group})
    if test:
        labels.append({"name": "testId", "value": test.id})

    env_name = report.environment.get("name") if isinstance(report.environment, dict) else None
    if env_name:
        labels.append({"name": "host", "value": env_name})

    return labels


def _build_steps(report: TestReport) -> list[dict]:
    steps: list[dict] = []

    # Preconditions as setup steps
    for pc in report.preconditions:
        pc_start = _iso_to_epoch_ms(report.execution.started_at) if report.execution else None
        steps.append({
            "name": f"Precondition: {pc.action}",
            "status": "passed" if pc.result == "OK" else "failed",
            "stage": "finished",
            "start": pc_start,
            "stop": pc_start,
            "parameters": [{"name": k, "value": str(v)} for k, v in pc.params.items()],
        })

    # Test steps
    for s in report.steps:
        step_start = _iso_to_epoch_ms(s.started_at)
        step_stop = step_start + s.duration_ms if step_start is not None and s.duration_ms is not None else step_start

        sub_steps: list[dict] = []
        for a in s.assertions:
            sub_steps.append({
                "name": a.expression,
                "status": _map_verdict(a.result),
                "stage": "finished",
                "start": step_start,
                "stop": step_stop,
                "description": f"Expected: {a.expected}, Actual: {a.actual}" if a.result != "PASS" else "",
            })

        allure_step: dict[str, Any] = {
            "name": f"Step {s.id}: {s.name}",
            "status": _map_verdict(s.verdict),
            "stage": "finished",
            "start": step_start or 0,
            "stop": step_stop or 0,
            "steps": sub_steps,
        }

        if s.description:
            allure_step["description"] = s.description

        # Add stimuli as step attachments
        stim_attachments = []
        for stim in s.stimuli:
            stim_attachments.append({
                "name": f"Stimulus: {stim.type} {stim.bus}",
                "type": "application/json",
                "source": f"data:{json.dumps(_stim_to_dict(stim))}",
            })

        # Add observations as step attachments
        for obs in s.observations:
            stim_attachments.append({
                "name": f"Observation: {obs.type} {obs.bus}",
                "type": "application/json",
                "source": f"data:{json.dumps(_obs_to_dict(obs))}",
            })

        if stim_attachments:
            allure_step["attachments"] = stim_attachments

        steps.append(allure_step)

    return steps


def _build_attachments(report: TestReport) -> list[dict]:
    attachments: list[dict] = []
    for tr in report.traces:
        attachments.append({
            "name": tr.id,
            "type": "application/octet-stream",
            "source": tr.path,
        })
    for att in report.attachments:
        attachments.append({
            "name": att.name,
            "type": att.mime or "application/octet-stream",
            "source": att.path,
        })
    return attachments


def _stim_to_dict(stim) -> dict:
    d: dict[str, Any] = {}
    if stim.type:
        d["type"] = stim.type
    if stim.bus:
        d["bus"] = stim.bus
    if stim.can_id is not None:
        d["can_id"] = f"0x{stim.can_id:X}"
    if stim.data:
        d["data"] = stim.data
    if stim.direction:
        d["direction"] = stim.direction
    return d


def _obs_to_dict(obs) -> dict:
    d: dict[str, Any] = {}
    if obs.type:
        d["type"] = obs.type
    if obs.bus:
        d["bus"] = obs.bus
    if obs.can_id is not None:
        d["can_id"] = f"0x{obs.can_id:X}"
    if obs.data:
        d["data"] = obs.data
    if obs.direction:
        d["direction"] = obs.direction
    return d


def _write_environment_properties(report: TestReport, output_dir: str) -> Optional[str]:
    env = report.environment
    if not isinstance(env, dict):
        return None
    lines: list[str] = []
    for k, v in sorted(env.items()):
        if isinstance(v, (str, int, float, bool)):
            lines.append(f"{k}={v}")
    if not lines:
        return None
    fpath = os.path.join(output_dir, "environment.properties")
    with open(fpath, "w") as f:
        f.write("\n".join(lines) + "\n")
    return fpath
