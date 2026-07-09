from __future__ import annotations

import json
import os
import sys
from typing import Optional

import typer

from boat.test.config import EnvironmentConfig, ManifestConfig
from boat.test.report import TestReport
from boat.test.runner import TestSuiteRunner

from .output import print_error, print_table

test_app = typer.Typer(help="Run tests and inspect test configurations.")


def _load_env_config(config_path: str) -> EnvironmentConfig:
    if not os.path.isfile(config_path):
        print_error(f"Config not found: {config_path}")
        sys.exit(1)
    try:
        cfg = EnvironmentConfig.from_file(config_path)
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        print_error(f"Failed to parse config: {exc}")
        sys.exit(1)

    issues = cfg.validate()
    if issues:
        for issue in issues:
            print_error(f"  Config issue: {issue}")
        print_error("Environment config has validation issues")
        sys.exit(1)

    return cfg


@test_app.command("list-environments")
def list_environments(
    ctx: typer.Context,
    path: str = typer.Option(
        "./config/tests", "--path", "-p",
        help="Directory to scan for environment configs",
        file_okay=False, dir_okay=True,
    ),
) -> None:
    """List available test environment configurations."""
    if not os.path.isdir(path):
        print_error(f"Directory not found: {path}")
        sys.exit(1)

    json_files = sorted(f for f in os.listdir(path) if f.endswith(".json") and f != "environment.schema.json")
    if not json_files:
        print_error(f"No environment configs found in {path}")
        sys.exit(1)

    rows = []
    for fname in json_files:
        fpath = os.path.join(path, fname)
        try:
            cfg = EnvironmentConfig.from_file(fpath)
        except Exception:
            continue
        bus_items = [f"{name}={bus.interface}" for name, bus in sorted(cfg.buses.items())]
        bus_summary = ", ".join(bus_items)
        dut_summary = cfg.dut.summary() if cfg.dut else "—"
        rows.append([fname, cfg.name, bus_summary, dut_summary, cfg.gateway.summary()])

    print_table(
        ["file", "name", "buses", "dut", "gateway"],
        rows, ctx.obj["json_mode"],
    )


@test_app.command("show-config")
def show_config(
    ctx: typer.Context,
    config_path: str = typer.Option(
        ..., "--config", "-c",
        help="Path to environment config JSON",
        exists=True, file_okay=True, dir_okay=False,
    ),
) -> None:
    """Show parsed environment configuration in detail."""
    cfg = _load_env_config(config_path)

    if ctx.obj["json_mode"]:
        print(json.dumps(cfg.to_dict(), indent=2))
        return

    from rich.console import Console
    from rich.table import Table
    from rich.tree import Tree
    from rich import box

    console = Console(file=sys.stdout)

    tree = Tree(f"[bold]{cfg.name}[/bold]")
    tree.add(f"Schema: {cfg.schema_version}")
    if cfg.description:
        tree.add(f"Description: {cfg.description}")

    # Gateway
    gw = tree.add(f"[cyan]Gateway[/cyan]")
    gw.add(f"Address: {cfg.gateway.address}")
    gw.add(f"Tick:    {cfg.gateway.tick_ms}ms")
    if cfg.gateway.binary:
        gw.add(f"Binary:  {cfg.gateway.binary}")
    if cfg.gateway.pdu_database:
        gw.add(f"PDU DB:  {cfg.gateway.pdu_database}")

    # Buses
    bus_node = tree.add(f"[cyan]Buses[/cyan]")
    for name in sorted(cfg.buses):
        bus = cfg.buses[name]
        parts = [f"[green]{bus.type}[/green]", bus.interface]
        if bus.bitrate:
            parts.append(f"{bus.bitrate} bps")
        if bus.fd:
            parts.append("FD")
        if bus.multicast_group:
            parts.append(f"mcast={bus.multicast_group}:{bus.port}")
        bus_node.add(f"{name}: {' '.join(parts)}")

    # DUT
    if cfg.dut:
        dut_node = tree.add(f"[cyan]DUT[/cyan]")
        dut_node.add(f"Name: {cfg.dut.name}")
        dut_node.add(f"Type: {cfg.dut.type}")
        if cfg.dut.so_path:
            dut_node.add(f"Plugin: {cfg.dut.so_path}")

    # Plugins
    if cfg.plugins:
        plug_node = tree.add(f"[cyan]Plugins[/cyan]")
        for p in cfg.plugins:
            plug_node.add(f"{p.so_path}")

    console.print(tree)


@test_app.command("validate-config")
def validate_config(
    ctx: typer.Context,
    config_path: str = typer.Option(
        ..., "--config", "-c",
        help="Path to environment config JSON",
        exists=True, file_okay=True, dir_okay=False,
    ),
) -> None:
    """Validate an environment config against the schema."""
    cfg = _load_env_config(config_path)
    issues = cfg.validate()

    if ctx.obj["json_mode"]:
        print(json.dumps({"valid": len(issues) == 0, "issues": issues}, indent=2))
        return

    from rich.console import Console
    from rich import box

    console = Console(file=sys.stdout)
    if not issues:
        console.print(f"[green]✓[/green] Config [bold]{cfg.name}[/bold] is valid")
    else:
        console.print(f"[red]✗[/red] Config [bold]{cfg.name}[/bold] has {len(issues)} issue(s):")
        for issue in issues:
            console.print(f"  [red]•[/red] {issue}")
        sys.exit(1)


@test_app.command("check-env")
def check_env(
    ctx: typer.Context,
    config_path: str = typer.Option(
        ..., "--config", "-c",
        help="Path to environment config JSON",
        exists=True, file_okay=True, dir_okay=False,
    ),
) -> None:
    """Run pre-flight checks on an environment configuration."""
    cfg = _load_env_config(config_path)

    from boat.test.check import check_environment

    issues = check_environment(cfg)

    if ctx.obj["json_mode"]:
        import json as _json
        print(_json.dumps({"name": cfg.name, "valid": len(issues) == 0, "issues": issues}, indent=2))
        return

    from rich.console import Console
    console = Console(file=sys.stdout)

    if not issues:
        console.print(f"[green]\u2713[/green] Environment [bold]{cfg.name}[/bold] is ready")
    else:
        console.print(f"[red]\u2717[/red] Environment [bold]{cfg.name}[/bold] has {len(issues)} issue(s):")
        for issue in issues:
            console.print(f"  [red]\u2717[/red] {issue}")
        sys.exit(1)


@test_app.command("run")
def run(
    ctx: typer.Context,
    manifest_path: str = typer.Argument(
        ..., help="Path to test suite manifest JSON",
        exists=True, file_okay=True, dir_okay=False,
    ),
    config_override: str = typer.Option(
        None, "--config", "-c",
        help="Override environment config path from manifest",
    ),
    report_dir: str = typer.Option(
        "./reports", "--report-dir", "-r",
        help="Root directory for test reports",
    ),
    stop_on_failure: bool = typer.Option(
        False, "--stop-on-failure",
        help="Stop after the first test failure",
    ),
    no_html: bool = typer.Option(
        False, "--no-html",
        help="Skip HTML report generation",
    ),
    allure: Optional[str] = typer.Option(
        None, "--allure",
        help="Enable Allure report generation and write results to this directory",
    ),
    parallel: int = typer.Option(
        1, "--parallel", "-n",
        help="Run tests in parallel using N workers",
    ),
    matrix: Optional[str] = typer.Option(
        None, "--matrix",
        help="Comma-separated list of environment configs to run as a test matrix",
    ),
    preflight: bool = typer.Option(
        False, "--preflight",
        help="Run pre-flight environment checks before executing tests",
    ),
    trace_format: str = typer.Option(
        "blf", "--trace-format",
        help="Trace recording format (blf, asc, pcap)",
    ),
    recorder_url: Optional[str] = typer.Option(
        None, "--recorder-url",
        help="Recorder daemon URL (e.g. http://localhost:8083). Enables trace recording.",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v",
        help="Verbose output",
    ),
) -> None:
    """Run a test suite defined in a manifest JSON."""
    try:
        manifest = ManifestConfig.from_file(manifest_path)
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        print_error(f"Failed to parse manifest: {exc}")
        sys.exit(1)

    # If --matrix is given, run against multiple env configs
    if matrix:
        env_paths = [p.strip() for p in matrix.split(",") if p.strip()]
    else:
        env_path = config_override or manifest.environment_config
        if not env_path:
            print_error("No environment config specified in manifest or --config")
            sys.exit(1)
        env_paths = [env_path]

    all_ok = True
    for env_path in env_paths:
        env_config = _load_env_config(env_path)
        env_report_dir = os.path.join(report_dir, env_config.name) if len(env_paths) > 1 else report_dir

        runner = TestSuiteRunner(
            manifest=manifest,
            env_config=env_config,
            report_dir=env_report_dir,
        stop_on_failure=stop_on_failure,
            verbose=verbose or ctx.obj.get("json_mode", False),
            generate_html=not no_html,
            allure_dir=allure,
            parallel=parallel,
            preflight=preflight,
            recorder_url=recorder_url,
            trace_format=trace_format,
        )
        ec = runner.run()
        if ec != 0:
            all_ok = False

    sys.exit(0 if all_ok else 1)
