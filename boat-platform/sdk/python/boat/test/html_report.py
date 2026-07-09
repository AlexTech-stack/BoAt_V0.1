from __future__ import annotations

import html
import json
from typing import Any

from boat.test.report import TestReport


def generate_html_report(report: TestReport) -> str:
    """Generate a self-contained HTML report from a TestReport object."""
    parts: list[str] = []
    is_pass = report.verdict == "PASS"
    verdict_color = {
        "PASS": "#4caf50", "FAIL": "#f44336", "ERROR": "#ff9800", "SKIPPED": "#9e9e9e",
    }.get(report.verdict, "#9e9e9e")

    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="en">')
    parts.append("<head>")
    parts.append('<meta charset="UTF-8">')
    parts.append(f"<title>Test Report: {_e(report.test.name if report.test else '')}</title>")
    parts.append("<style>")
    parts.append(_CSS)
    parts.append("</style>")
    parts.append("</head>")
    parts.append("<body>")

    # ── Header ──────────────────────────────────────────────────────────────
    parts.append(f'<div class="header">')
    parts.append(f'  <h1>Test Report: {_e(report.test.name if report.test else "Untitled")}</h1>')
    parts.append(f'  <div class="verdict-bar" style="background:{verdict_color}">')
    parts.append(f'    {_e(report.verdict)}')
    if report.execution.duration_ms is not None:
        parts.append(f'    &nbsp;|&nbsp; {_s(report.execution.duration_ms)}')
    parts.append(f'  </div>')
    if report.summary:
        parts.append(f'  <div class="summary">{_e(report.summary)}</div>')
    parts.append("</div>")

    # ── Test Info ───────────────────────────────────────────────────────────
    parts.append('<div class="section">')
    parts.append('  <div class="section-title">Test Info</div>')
    parts.append('  <table class="info-table">')
    if report.test:
        _info_row(parts, "ID", report.test.id)
        _info_row(parts, "Name", report.test.name)
        _info_row(parts, "Group", report.test.group)
        _info_row(parts, "File", report.test.file)
        _info_row(parts, "Version", report.test.version)
        _info_row(parts, "Description", report.test.description)
    parts.append('  </table>')
    parts.append("</div>")

    # ── Execution ───────────────────────────────────────────────────────────
    parts.append('<div class="section">')
    parts.append('  <div class="section-title">Execution</div>')
    parts.append('  <table class="info-table">')
    _info_row(parts, "Started", report.execution.started_at)
    _info_row(parts, "Finished", report.execution.finished_at)
    _info_row(parts, "Duration", _s(report.execution.duration_ms) if report.execution.duration_ms is not None else None)
    _info_row(parts, "Verdict", report.verdict)
    _info_row(parts, "Runner", report.execution.runner_hostname)
    _info_row(parts, "User", report.execution.runner_user)
    _info_row(parts, "CI Build", report.execution.ci_build_id)
    if report.execution.ci_job_url:
        parts.append(f'    <tr><td>CI Job</td><td><a href="{_e(report.execution.ci_job_url)}">{_e(report.execution.ci_job_url)}</a></td></tr>')
    _info_row(parts, "Gateway Version", report.execution.gateway_version)
    _info_row(parts, "DUT Version", report.execution.dut_version)
    _info_row(parts, "Boat Version", report.execution.boat_version)
    parts.append('  </table>')
    parts.append("</div>")

    # ── Environment ─────────────────────────────────────────────────────────
    if report.environment:
        env_json = json.dumps(report.environment, indent=2)
        parts.append('<div class="section">')
        parts.append('  <div class="section-title">Environment</div>')
        parts.append(f'  <pre class="env-block">{_e(env_json)}</pre>')
        parts.append("</div>")

    # ── Preconditions ───────────────────────────────────────────────────────
    if report.preconditions:
        parts.append('<div class="section">')
        parts.append('  <div class="section-title">Preconditions</div>')
        parts.append('  <table class="data-table">')
        parts.append('    <tr><th>#</th><th>Action</th><th>Params</th><th>Result</th><th>Duration</th><th>Error</th></tr>')
        for pc in report.preconditions:
            params_str = json.dumps(pc.params) if pc.params else ""
            parts.append(f'    <tr>'
                         f'<td>{pc.id}</td>'
                         f'<td>{_e(pc.action)}</td>'
                         f'<td>{_e(params_str)}</td>'
                         f'<td>{_e(pc.result or "")}</td>'
                         f'<td>{_s(pc.duration_ms)}</td>'
                         f'<td class="error-cell">{_e(pc.error or "")}</td>'
                         f'</tr>')
        parts.append('  </table>')
        parts.append("</div>")

    # ── Steps ────────────────────────────────────────────────────────────────
    parts.append('<div class="section">')
    parts.append('  <div class="section-title">Steps</div>')
    for i, step in enumerate(report.steps):
        _render_step(parts, step, expand=i == 0)
    if not report.steps:
        parts.append('  <div class="empty">No steps recorded.</div>')
    parts.append("</div>")

    # ── Traces ──────────────────────────────────────────────────────────────
    if report.traces:
        parts.append('<div class="section">')
        parts.append('  <div class="section-title">Traces</div>')
        parts.append('  <table class="data-table">')
        parts.append('    <tr><th>ID</th><th>Path</th><th>Format</th><th>Size</th><th>Events</th><th>Checksum</th></tr>')
        for tr in report.traces:
            parts.append(f'    <tr>'
                         f'<td>{_e(tr.id)}</td>'
                         f'<td><a href="{_e(tr.path)}">{_e(tr.path)}</a></td>'
                         f'<td>{_e(tr.format)}</td>'
                         f'<td>{_s(tr.size_bytes)}</td>'
                         f'<td>{tr.event_count if tr.event_count is not None else ""}</td>'
                         f'<td style="font-family:monospace;font-size:0.8em">{_e(tr.checksum_sha256 or "")}</td>'
                         f'</tr>')
        parts.append('  </table>')
        parts.append("</div>")

    # ── Script ──────────────────────────────────────────────────────────────
    parts.append("<script>")
    parts.append(_JS)
    parts.append("</script>")
    parts.append("</body>")
    parts.append("</html>")

    return "\n".join(parts)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _e(s: Any) -> str:
    """HTML-escape a value for safe interpolation."""
    if s is None:
        return ""
    return html.escape(str(s))


def _s(ms: int | None) -> str:
    """Format milliseconds as human-readable string."""
    if ms is None:
        return ""
    if ms < 1000:
        return f"{ms}ms"
    if ms < 60_000:
        return f"{ms / 1000:.1f}s"
    m = ms // 60_000
    s = (ms % 60_000) / 1000
    return f"{m}m {s:.0f}s"


def _info_row(parts: list[str], label: str, value: Any) -> None:
    if value is not None and value != "":
        parts.append(f'    <tr><td class="info-label">{_e(label)}</td><td>{_e(value)}</td></tr>')


def _render_step(parts: list[str], step, expand: bool = False) -> None:
    verdict_class = step.verdict.lower() if step.verdict else "skipped"
    badge_color = {
        "PASS": "#4caf50", "FAIL": "#f44336", "ERROR": "#ff9800", "SKIPPED": "#9e9e9e",
    }.get(step.verdict, "#9e9e9e")

    parts.append(f'<div class="step {"step-open" if expand else ""}">')
    parts.append(f'  <div class="step-header" onclick="toggleStep(this)">')
    parts.append(f'    <span class="step-badge" style="background:{badge_color}">{_e(step.verdict)}</span>')
    parts.append(f'    <strong>Step {step.id}: {_e(step.name)}</strong>')
    if step.duration_ms is not None:
        parts.append(f'    <span class="step-duration">{_s(step.duration_ms)}</span>')
    parts.append(f'  </div>')
    parts.append(f'  <div class="step-body">')

    if step.description:
        parts.append(f'    <div class="step-desc">{_e(step.description)}</div>')

    # Stimuli
    if step.stimuli:
        parts.append('    <div class="sub-section">Stimuli</div>')
        parts.append('    <table class="data-table">')
        parts.append('      <tr><th>Type</th><th>Bus</th><th>ID</th><th>Data</th><th>DLC</th><th>Dir</th><th>Tick</th><th>Timestamp</th></tr>')
        for s in step.stimuli:
            parts.append(f'      <tr>'
                         f'<td>{_e(s.type)}</td>'
                         f'<td>{_e(s.bus)}</td>'
                         f'<td>{_hex(s.can_id)}</td>'
                         f'<td style="font-family:monospace">{_e(s.data)}</td>'
                         f'<td>{s.dlc or ""}</td>'
                         f'<td>{_e(s.direction)}</td>'
                         f'<td>{s.step_sim_tick if s.step_sim_tick is not None else ""}</td>'
                         f'<td>{_ns(s.timestamp_ns)}</td>'
                         f'</tr>')
        parts.append('    </table>')

    # Observations
    if step.observations:
        parts.append('    <div class="sub-section">Observations</div>')
        parts.append('    <table class="data-table">')
        parts.append('      <tr><th>Type</th><th>Bus</th><th>ID</th><th>Data</th><th>DLC</th><th>Dir</th><th>Tick</th><th>Latency</th></tr>')
        for o in step.observations:
            parts.append(f'      <tr>'
                         f'<td>{_e(o.type)}</td>'
                         f'<td>{_e(o.bus)}</td>'
                         f'<td>{_hex(o.can_id)}</td>'
                         f'<td style="font-family:monospace">{_e(o.data)}</td>'
                         f'<td>{o.dlc or ""}</td>'
                         f'<td>{_e(o.direction)}</td>'
                         f'<td>{o.step_sim_tick if o.step_sim_tick is not None else ""}</td>'
                         f'<td>{_ns(o.latency_us)}</td>'
                         f'</tr>')
        parts.append('    </table>')

    # Expected
    if step.expected:
        parts.append('    <div class="sub-section">Expected</div>')
        parts.append('    <table class="data-table">')
        parts.append('      <tr><th>Type</th><th>Bus</th><th>ID</th><th>Data</th><th>Mask</th><th>Timeout</th></tr>')
        for e in step.expected:
            parts.append(f'      <tr>'
                         f'<td>{_e(e.type)}</td>'
                         f'<td>{_e(e.bus)}</td>'
                         f'<td>{_hex(e.can_id)}</td>'
                         f'<td style="font-family:monospace">{_e(e.data)}</td>'
                         f'<td style="font-family:monospace">{_e(e.data_mask)}</td>'
                         f'<td>{_s(e.timeout_ms) if e.timeout_ms else ""}</td>'
                         f'</tr>')
        parts.append('    </table>')

    # Assertions
    if step.assertions:
        parts.append('    <div class="sub-section">Assertions</div>')
        parts.append('    <table class="data-table">')
        parts.append('      <tr><th>Expression</th><th>Expected</th><th>Actual</th><th>Result</th></tr>')
        for a in step.assertions:
            cls = "assert-pass" if a.result == "PASS" else ("assert-fail" if a.result == "FAIL" else "assert-error")
            parts.append(f'      <tr class="{cls}">'
                         f'<td style="font-family:monospace">{_e(a.expression)}</td>'
                         f'<td>{_e(a.expected)}</td>'
                         f'<td>{_e(a.actual)}</td>'
                         f'<td><span class="badge badge-{cls}">{_e(a.result)}</span></td>'
                         f'</tr>')
        parts.append('    </table>')

    # Trace refs
    if step.trace_refs:
        parts.append('    <div class="sub-section">Trace References</div>')
        for ref in step.trace_refs:
            parts.append(f'    <div><a href="{_e(ref)}">{_e(ref)}</a></div>')

    # Attachments
    if step.attachments:
        parts.append('    <div class="sub-section">Attachments</div>')
        for att in step.attachments:
            label = f"{_e(att.name)} ({_e(att.mime or 'unknown')})" if att.name else _e(att.path)
            parts.append(f'    <div><a href="{_e(att.path)}">{label}</a></div>')

    parts.append('  </div>')
    parts.append('</div>')


def _hex(val: int | None) -> str:
    if val is None:
        return ""
    return f"0x{val:X}"


def _ns(val: int | None) -> str:
    if val is None:
        return ""
    if val < 1_000:
        return f"{val}ns"
    if val < 1_000_000:
        return f"{val / 1_000:.1f}µs"
    if val < 1_000_000_000:
        return f"{val / 1_000_000:.2f}ms"
    return f"{val / 1_000_000_000:.3f}s"


_CSS = """
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; max-width: 1100px; margin: 0 auto; padding: 24px; background: #fafafa; color: #222; line-height: 1.5; }
.header { margin-bottom: 24px; }
.header h1 { font-size: 24px; font-weight: 600; color: #111; }
.verdict-bar { display: inline-block; padding: 6px 16px; border-radius: 4px; color: #fff; font-weight: 700; font-size: 16px; margin-top: 8px; }
.summary { margin-top: 8px; color: #555; font-size: 14px; }
.section { margin: 24px 0; }
.section-title { font-size: 18px; font-weight: 600; color: #333; padding-bottom: 6px; border-bottom: 2px solid #1976d2; margin-bottom: 12px; }
table { width: 100%; border-collapse: collapse; margin: 8px 0; }
th { background: #f0f0f0; padding: 6px 10px; text-align: left; font-weight: 600; font-size: 13px; border-bottom: 2px solid #ddd; }
td { padding: 5px 10px; font-size: 13px; border-bottom: 1px solid #eee; vertical-align: top; }
.info-table td:first-child { width: 160px; font-weight: 600; color: #555; }
.data-table { margin: 6px 0 12px 0; }
.info-label { color: #555; font-weight: 600; }
pre.env-block { background: #f5f5f5; padding: 12px; border-radius: 4px; font-size: 12px; overflow-x: auto; border: 1px solid #e0e0e0; }
.step { border: 1px solid #e0e0e0; border-radius: 4px; margin: 8px 0; overflow: hidden; background: #fff; }
.step-header { padding: 10px 14px; cursor: pointer; display: flex; align-items: center; gap: 10px; user-select: none; }
.step-header:hover { background: #f5f5f5; }
.step-header::before { content: "▶"; font-size: 12px; color: #999; transition: transform 0.15s; }
.step-open > .step-header::before { transform: rotate(90deg); }
.step-body { display: none; padding: 10px 14px 14px; border-top: 1px solid #e0e0e0; background: #fcfcfc; }
.step-open > .step-body { display: block; }
.step-badge { display: inline-block; padding: 2px 10px; border-radius: 3px; color: #fff; font-size: 11px; font-weight: 700; letter-spacing: 0.5px; }
.step-duration { margin-left: auto; color: #888; font-size: 12px; }
.step-desc { margin-bottom: 10px; color: #555; font-size: 13px; font-style: italic; }
.sub-section { font-weight: 600; font-size: 13px; color: #555; margin: 8px 0 4px; padding-top: 6px; border-top: 1px dashed #ddd; }
.badge { display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 11px; font-weight: 700; color: #fff; }
.badge-assert-pass { background: #4caf50; }
.badge-assert-fail { background: #f44336; }
.badge-assert-error { background: #ff9800; }
.assert-pass { background: #f1f8e9; }
.assert-fail { background: #fce4ec; }
.assert-error { background: #fff3e0; }
.error-cell { color: #d32f2f; font-size: 12px; }
.empty { color: #999; font-style: italic; padding: 8px 0; }
a { color: #1976d2; text-decoration: none; }
a:hover { text-decoration: underline; }
"""

_JS = """
function toggleStep(header) {
  var step = header.parentElement;
  step.classList.toggle('step-open');
}
"""
