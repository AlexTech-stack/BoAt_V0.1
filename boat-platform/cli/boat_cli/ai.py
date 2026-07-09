"""boat ai — AI-powered assistants for the BoAt platform.

Subcommands:
  config      Manage AI configuration (~/.config/boat/ai.toml).
  scenario    Generate scenario JSON from a plain-language description.
  bus-setup   Get shell commands to configure CAN/Ethernet buses.
  cli         Translate intent into the correct `boat ...` command.
  plugin      Generate a BoAt plugin Python script from a description.
"""
from __future__ import annotations

import os
import py_compile
import sys
import tempfile
from pathlib import Path

import typer

from . import ai_backend, ai_config
from .bus_setup_context import build_system_prompt as bus_setup_prompt
from .cli_context import build_system_prompt as cli_prompt
from .output import print_error
from .plugin_context import build_system_prompt as plugin_prompt
from .scenario_context import build_system_prompt as scenario_prompt

ai_app = typer.Typer(help="AI-powered assistants for the BoAt platform.")
_cfg_app = typer.Typer(help="Manage AI configuration (~/.config/boat/ai.toml).")
ai_app.add_typer(_cfg_app, name="config")


# ── Config subcommands ───────────────────────────────────────────────────────


@_cfg_app.command("show")
def config_show() -> None:
    """Print current AI configuration."""
    cfg = ai_config.load()
    typer.echo(f"config file : {cfg.config_path}")
    typer.echo(f"endpoint    : {cfg.endpoint}")
    typer.echo(f"model       : {cfg.model}")
    typer.echo(f"timeout     : {cfg.timeout}s")
    typer.echo(f"api_key     : {cfg.masked_api_key}")


@_cfg_app.command("set")
def config_set(
    endpoint: str = typer.Option(None, "--endpoint", help="LLM server base URL, e.g. http://localhost:11434/v1"),
    model: str = typer.Option(None, "--model", help="Model identifier, e.g. qwen2.5-coder:3b"),
    timeout: int = typer.Option(None, "--timeout", help="HTTP timeout in seconds"),
    api_key: str = typer.Option(None, "--api-key", help="API key for auth (stored in ~/.config/boat/ai.toml — outside git)."),
) -> None:
    """Update AI configuration. Only supplied flags are changed."""
    if not any([endpoint, model, timeout is not None, api_key is not None]):
        print_error("Provide at least one of --endpoint, --model, --timeout, --api-key")
        raise typer.Exit(1)
    cfg = ai_config.save(endpoint=endpoint, model=model, timeout=timeout, api_key=api_key)
    typer.echo(f"Saved to {cfg.config_path}")
    typer.echo(f"endpoint : {cfg.endpoint}")
    typer.echo(f"model    : {cfg.model}")
    typer.echo(f"timeout  : {cfg.timeout}s")
    if cfg.api_key:
        typer.echo(f"api_key  : {cfg.masked_api_key}")


# ── Scenario assistant ───────────────────────────────────────────────────────


@ai_app.command("scenario")
def ai_scenario(
    desc: str = typer.Option(..., "--desc", help="Plain-language description of the scenario."),
    model: str = typer.Option(None, "--model", help="Override configured model."),
    endpoint: str = typer.Option(None, "--endpoint", help="Override configured LLM endpoint."),
    api_key: str = typer.Option(None, "--api-key", help="Bearer token for endpoints that require auth (or set BOAT_AI_API_KEY)."),
) -> None:
    """Generate scenario JSON or Python code from a plain-language description.

    Example:
      boat ai scenario --desc "A 5-second simulation with vehicle dynamics plugin
      and a speed signal that faults to 0 at tick 200"
    """
    cfg = ai_config.load()
    effective_endpoint = endpoint or cfg.endpoint
    effective_model = model or cfg.model
    effective_timeout = cfg.timeout
    effective_key = api_key or os.environ.get("BOAT_AI_API_KEY", "") or cfg.api_key

    typer.echo("Building prompt context...", err=True)
    system_prompt = scenario_prompt()

    messages = [
        ai_backend.Message(role="system", content=system_prompt),
        ai_backend.Message(role="user", content=desc),
    ]

    typer.echo(f"Calling {effective_model} at {effective_endpoint} ...", err=True)
    try:
        reply = ai_backend.complete(
            endpoint=effective_endpoint,
            model=effective_model,
            messages=messages,
            timeout=effective_timeout,
            api_key=effective_key,
        )
    except ai_backend.AiBackendError as exc:
        print_error(str(exc))
        raise typer.Exit(1)

    typer.echo(reply)


# ── Bus-setup assistant ──────────────────────────────────────────────────────


@ai_app.command("bus-setup")
def ai_bus_setup(
    query: str = typer.Option(..., "--query", help="What bus setup do you need help with?"),
    model: str = typer.Option(None, "--model", help="Override configured model."),
    endpoint: str = typer.Option(None, "--endpoint", help="Override configured LLM endpoint."),
    api_key: str = typer.Option(None, "--api-key", help="Bearer token for endpoints that require auth (or set BOAT_AI_API_KEY)."),
) -> None:
    """Get shell commands to configure CAN/Ethernet buses for the BoAt gateway.

    Example:
      boat ai bus-setup --query "Set up two vcan interfaces and start the gateway"
    """
    cfg = ai_config.load()
    effective_endpoint = endpoint or cfg.endpoint
    effective_model = model or cfg.model
    effective_timeout = cfg.timeout
    effective_key = api_key or os.environ.get("BOAT_AI_API_KEY", "") or cfg.api_key

    typer.echo("Building prompt context...", err=True)
    system_prompt = bus_setup_prompt()

    messages = [
        ai_backend.Message(role="system", content=system_prompt),
        ai_backend.Message(role="user", content=query),
    ]

    typer.echo(f"Calling {effective_model} at {effective_endpoint} ...", err=True)
    try:
        reply = ai_backend.complete(
            endpoint=effective_endpoint,
            model=effective_model,
            messages=messages,
            timeout=effective_timeout,
            api_key=effective_key,
        )
    except ai_backend.AiBackendError as exc:
        print_error(str(exc))
        raise typer.Exit(1)

    typer.echo(reply)


# ── CLI assistant ────────────────────────────────────────────────────────────


@ai_app.command("cli")
def ai_cli(
    query: str = typer.Option(..., "--query", help="What do you want to do with the CLI?"),
    model: str = typer.Option(None, "--model", help="Override configured model."),
    endpoint: str = typer.Option(None, "--endpoint", help="Override configured LLM endpoint."),
    api_key: str = typer.Option(None, "--api-key", help="Bearer token for endpoints that require auth (or set BOAT_AI_API_KEY)."),
) -> None:
    """Translate your intent into the correct `boat ...` command.

    Example:
      boat ai cli --query "Send a CAN frame with ID 0x100 every 100ms on vcan0"
    """
    cfg = ai_config.load()
    effective_endpoint = endpoint or cfg.endpoint
    effective_model = model or cfg.model
    effective_timeout = cfg.timeout
    effective_key = api_key or os.environ.get("BOAT_AI_API_KEY", "") or cfg.api_key

    typer.echo("Building prompt context...", err=True)
    system_prompt = cli_prompt()

    messages = [
        ai_backend.Message(role="system", content=system_prompt),
        ai_backend.Message(role="user", content=query),
    ]

    typer.echo(f"Calling {effective_model} at {effective_endpoint} ...", err=True)
    try:
        reply = ai_backend.complete(
            endpoint=effective_endpoint,
            model=effective_model,
            messages=messages,
            timeout=effective_timeout,
            api_key=effective_key,
        )
    except ai_backend.AiBackendError as exc:
        print_error(str(exc))
        raise typer.Exit(1)

    typer.echo(reply)


# ── Plugin generation ────────────────────────────────────────────────────────


@ai_app.command("plugin")
def ai_plugin(
    desc: str = typer.Option(..., "--desc", help='Plain-language description of what the plugin should do.'),
    out: str = typer.Option("", "--out", help="Output file path. Defaults to a name derived from the description."),
    model: str = typer.Option(None, "--model", help="Override configured model."),
    endpoint: str = typer.Option(None, "--endpoint", help="Override configured LLM endpoint."),
    api_key: str = typer.Option(None, "--api-key", help="Bearer token for endpoints that require auth (or set BOAT_AI_API_KEY)."),
    quality: bool = typer.Option(False, "--quality", help="Use a higher-quality (slower) model if configured endpoint supports it."),
) -> None:
    """Generate a BoAt plugin Python script from a plain-language description.

    The prompt is sent to your local LLM server (default: Ollama on localhost).
    Configure the endpoint with: boat ai config set --endpoint <url>

    Example:
      boat ai plugin --desc "Listen on vcan0 for ID 0x100, publish speed to the bus"
    """
    cfg = ai_config.load()
    effective_endpoint = endpoint or cfg.endpoint
    effective_model = model or cfg.model
    effective_timeout = cfg.timeout
    effective_key = (
        api_key
        or os.environ.get("BOAT_AI_API_KEY", "")
        or cfg.api_key
    )

    gateway_host = "localhost:50051"

    typer.echo("Building prompt context...", err=True)
    system_prompt = plugin_prompt(gateway_host=gateway_host)

    user_message = (
        f"Generate a BoAt plugin that does the following:\n\n{desc}\n\n"
        "Output only the Python code."
    )

    messages = [
        ai_backend.Message(role="system", content=system_prompt),
        ai_backend.Message(role="user", content=user_message),
    ]

    typer.echo(f"Calling {effective_model} at {effective_endpoint} ...", err=True)
    try:
        raw = ai_backend.complete(
            endpoint=effective_endpoint,
            model=effective_model,
            messages=messages,
            timeout=effective_timeout,
            api_key=effective_key,
        )
    except ai_backend.AiBackendError as exc:
        print_error(str(exc))
        raise typer.Exit(1)

    code = ai_backend.extract_code(raw)

    error = _validate(code)
    if error:
        typer.echo(f"Validation error in first attempt ({error}), retrying...", err=True)
        retry_message = (
            f"{user_message}\n\n"
            f"Your previous attempt had an error when executed:\n{error}\n\n"
            "Common causes:\n"
            "- bytes([N]) where N > 255 — use int.to_bytes() or a list of byte values "
            "each in 0-255, e.g. bytes([0x04, 0xD2]) for 1234\n"
            "- Calling a method not listed in the SDK API Reference\n"
            "Fix the error and output only the corrected Python code."
        )
        messages = [
            ai_backend.Message(role="system", content=system_prompt),
            ai_backend.Message(role="user", content=user_message),
            ai_backend.Message(role="assistant", content=raw),
            ai_backend.Message(role="user", content=retry_message),
        ]
        try:
            raw = ai_backend.complete(
                endpoint=effective_endpoint,
                model=effective_model,
                messages=messages,
                timeout=effective_timeout,
                api_key=effective_key,
            )
        except ai_backend.AiBackendError as exc:
            print_error(str(exc))
            raise typer.Exit(1)
        code = ai_backend.extract_code(raw)
        error = _validate(code)
        if error:
            print_error(f"Generated code still has errors after retry: {error}")
            typer.echo("--- generated code ---")
            typer.echo(code)
            raise typer.Exit(1)

    out_path = Path(out) if out else _derive_filename(desc)
    out_path.write_text(code + "\n", encoding="utf-8")

    typer.echo(f"\nGenerated plugin written to: {out_path}")
    typer.echo("\n--- preview (first 30 lines) ---")
    for line in code.splitlines()[:30]:
        typer.echo(line)
    if len(code.splitlines()) > 30:
        typer.echo(f"... ({len(code.splitlines()) - 30} more lines)")


# ── Helpers (migrated from old gen.py) ────────────────────────────────────────


def _validate(code: str) -> str:
    """Syntax-check then dry-run with mocked SDK imports.

    Returns an error string if anything fails, empty string if the code is clean.
    """
    import ast

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return f"SyntaxError line {exc.lineno}: {exc.msg}"

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "bytes"
                and len(node.args) == 1
                and isinstance(node.args[0], ast.List)):
            continue
        for elt in node.args[0].elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, int):
                if elt.value > 255:
                    return (
                        f"ValueError line {elt.lineno}: bytes([{elt.value}]) — "
                        f"{elt.value} > 255. "
                        f"Use ({elt.value}).to_bytes(2, 'big') for a 2-byte big-endian value."
                    )

    import types

    class _MockMsg:
        can_id = 0x100; dlc = 2; data = bytes([0x01, 0x02]); flags = 0
        ethertype = 0x0800; payload = b"\x00\x01"
        src_mac = b"\x00" * 6; dst_mac = b"\xff" * 6
        iface = "vcan0"; timestamp_ns = 0
        name = "test.signal"; number_value = 0.0; string_value = ""
        bool_value = False; bytes_value = b""
        pdu_id = 0x100

    def _noop(*_a, **_kw): return None
    def _noop_bool(*_a, **_kw): return True

    _mock_base = type("_MockBase", (), {
        "__init__": lambda self, *a, **kw: None,
        "send": _noop_bool,
        "publish": _noop_bool,
        "run": _noop,
        "run_background": _noop,
        "stop": _noop,
        "on_frame": _noop,
        "on_signal": _noop,
        "on_pdu": _noop,
    })

    mocks: dict[str, types.ModuleType] = {}
    for mod_name, attr, cls in [
        ("boat.can_node", "CanNode", _mock_base),
        ("boat.bus_node", "BusNode", _mock_base),
        ("boat.ethernet_node", "EthernetNode", _mock_base),
        ("boat.pdu_node", "PduNode", _mock_base),
    ]:
        mod = types.ModuleType(mod_name)
        setattr(mod, attr, cls)
        mocks[mod_name] = mod

    saved = {k: sys.modules.get(k) for k in mocks}
    sys.modules.update(mocks)
    try:
        ns: dict = {"__name__": "__generated__"}
        exec(compile(code, "<generated>", "exec"), ns)

        for obj in ns.values():
            if not (isinstance(obj, type) and issubclass(obj, _mock_base)
                    and obj is not _mock_base):
                continue
            try:
                instance = obj()
            except Exception as exc:
                return f"{type(exc).__name__} in {obj.__name__}.__init__: {exc}"
            mock_msg = _MockMsg()
            for method, args in [
                ("on_frame", (mock_msg, "vcan0")),
                ("on_signal", (mock_msg,)),
                ("on_pdu", (mock_msg,)),
            ]:
                fn = getattr(instance, method, None)
                if fn and fn.__func__ is not getattr(_mock_base, method):
                    try:
                        fn(*args)
                    except Exception as exc:
                        return f"{type(exc).__name__} in {obj.__name__}.{method}: {exc}"
        return ""
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def _derive_filename(desc: str) -> Path:
    import re
    slug = re.sub(r"[^a-z0-9]+", "_", desc.lower())[:40].strip("_")
    return Path(f"{slug}_plugin.py")
