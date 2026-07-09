from __future__ import annotations

import typer

from boat.v1 import plugin_pb2

from .output import print_table

plugin_app = typer.Typer()


@plugin_app.command("register")
def register_plugin(ctx: typer.Context,
                    path: str = typer.Option(..., "--path"),
                    config: str = typer.Option("", "--config", "-c",
                       help="JSON config string for the plugin")) -> None:
    response = ctx.obj["client"].plugin.RegisterPlugin(
        plugin_pb2.RegisterPluginRequest(path=path, config_json=config))
    print_table(["plugin_id", "name"],
                [[response.plugin.plugin_id, response.plugin.name]],
                ctx.obj["json_mode"])


@plugin_app.command("list")
def list_plugins(ctx: typer.Context) -> None:
    response = ctx.obj["client"].plugin.ListPlugins(plugin_pb2.ListPluginsRequest())
    rows = [[item.plugin_id, item.name, bool(item.loaded)] for item in response.plugins]
    print_table(["plugin_id", "name", "loaded"], rows, ctx.obj["json_mode"])


@plugin_app.command("info")
def plugin_info(ctx: typer.Context, name: str) -> None:
    response = ctx.obj["client"].plugin.GetPluginInfo(plugin_pb2.GetPluginInfoRequest(plugin_id=name))
    print_table(
        ["plugin_id", "name", "version", "loaded"],
        [[response.plugin.plugin_id, response.plugin.name, response.plugin.version, bool(response.plugin.loaded)]],
        ctx.obj["json_mode"],
    )


@plugin_app.command("unload")
def unload_plugin(ctx: typer.Context, name: str) -> None:
    response = ctx.obj["client"].plugin.UnloadPlugin(plugin_pb2.UnloadPluginRequest(plugin_id=name))
    print_table(["unloaded"], [[bool(response.unloaded)]], ctx.obj["json_mode"])
