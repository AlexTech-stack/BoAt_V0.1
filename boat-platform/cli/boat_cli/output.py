from __future__ import annotations

import json
import sys
from typing import Any

from rich.console import Console
from rich.table import Table


def print_table(columns: list[str], rows: list[list[Any]], json_mode: bool) -> None:
    if json_mode:
        payload = [dict(zip(columns, row, strict=False)) for row in rows]
        print(json.dumps(payload))
        return

    table = Table()
    for column in columns:
        table.add_column(column)
    for row in rows:
        table.add_row(*[str(value) for value in row])
    Console(file=sys.stdout).print(table)


def print_error(msg: str) -> None:
    Console(stderr=True).print(f"[red]{msg}[/red]")
