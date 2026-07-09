"""BoAt interactive message CLI.

Commands
--------
load <path>                    Load a PDU database file.
var <varname> <MessageName>    Create a message variable from the database.
<varname>.<Signal> = <value>   Set a signal's physical value.
show <varname>                 Print all signal values for a variable.
send <varname>                 Pack and transmit the message via the gateway.
list                           List all message names in the loaded database.
vars                           List currently defined variables.
connect [host:port]            Set gateway address (default localhost:50051).
help                           Show this help.
exit / quit                    Exit the CLI.

Examples
--------
  load config/pdu_db_example.json
  var msg Motor_1
  msg.Clamp15 = 1
  msg.MotorSpeed = 100
  send msg
"""

from __future__ import annotations

import glob
import os
import re
import sys

# Allow running directly from the sdk/python directory.
_HERE = os.path.dirname(__file__)
_SDK  = os.path.join(_HERE, "..")
if _SDK not in sys.path:
    sys.path.insert(0, _SDK)

import grpc

from boat.client  import BoAtClient
from boat.message import Message
from boat.pdu_db  import PduDatabase
from boat.v1 import can_pb2, can_pb2_grpc, pdu_pb2, pdu_pb2_grpc


# ── sender helpers ────────────────────────────────────────────────────────────

def _send_can(client: BoAtClient, msg: Message) -> str:
    db   = msg.db
    data = msg.pack()
    frame = can_pb2.CanFrame(
        can_id=db["Identifier"],
        dlc=len(data),
        data=bytes(data),
        iface=db["Bus"],
        flags=0x04 if db["BusType"] == "CANFD" else 0,  # 0x04 = CANFD flag
    )
    req  = can_pb2.SendCanFrameRequest(frame=frame)
    resp = client.can.SendCanFrame(req)
    if resp.accepted:
        return f"Sent CAN frame: id=0x{db['Identifier']:X}  iface={db['Bus']}  data={data.hex()}"
    return "Gateway rejected CAN frame (no route or bus error)"


def _send_eth_pdu(client: BoAtClient, msg: Message) -> str:
    db      = msg.db
    payload = msg.pack()
    pdu     = pdu_pb2.PduFrame(pdu_id=db["PduId"], payload=payload)
    req     = pdu_pb2.SendPduRequest(pdu=pdu)
    resp    = client.pdu.SendPdu(req)
    if resp.accepted:
        return (f"Sent ETH_PDU: pdu_id=0x{db['PduId']:08X}  "
                f"payload={payload.hex()}  container={db['ContainerDbId']}")
    return "Gateway rejected ETH_PDU (no route configured for this PduId)"


def _send_message(client: BoAtClient, msg: Message) -> str:
    bt = msg.bus_type
    if bt in ("CAN", "CANFD"):
        return _send_can(client, msg)
    elif bt == "ETH_PDU":
        return _send_eth_pdu(client, msg)
    elif bt == "ETH":
        return "ETH containers cannot be sent directly — send the individual ETH_PDU entries instead."
    return f"Unsupported BusType '{bt}'"


# ── REPL ──────────────────────────────────────────────────────────────────────

_ASSIGN_RE = re.compile(
    r"^([A-Za-z_]\w*)\.([A-Za-z_]\w*)\s*=\s*(.+)$"
)

_COMMANDS = [
    "load", "var", "show", "send", "list", "vars",
    "connect", "help", "exit", "quit",
]


# ── tab completer ─────────────────────────────────────────────────────────────

class _Completer:
    """Context-aware readline completer for the BoAt CLI."""

    def __init__(self, cli: "BoAtCli") -> None:
        self._cli     = cli
        self._matches: list[str] = []

    def complete(self, text: str, state: int) -> str | None:
        if state == 0:
            self._matches = self._completions(text)
        try:
            return self._matches[state]
        except IndexError:
            return None

    def _completions(self, text: str) -> list[str]:
        try:
            import readline
            line = readline.get_line_buffer()
        except ImportError:
            line = text

        stripped = line.lstrip()
        parts    = stripped.split()
        # Are we completing the first token or beyond?
        after_space = line.endswith(" ") or line.endswith("\t")

        # ── varname.Signal  (dot-notation assignment) ──────────────────
        if "." in text:
            varname, partial = text.split(".", 1)
            msg = self._cli._vars.get(varname)
            if msg is not None:
                return [f"{varname}.{s}" for s in msg.signal_names()
                        if s.startswith(partial)]
            return []

        # ── first token: commands + variable names ─────────────────────
        if not parts or (len(parts) == 1 and not after_space):
            candidates = _COMMANDS + list(self._cli._vars)
            return [c for c in candidates if c.startswith(text)]

        cmd = parts[0].lower()

        # ── show / send → variable names ──────────────────────────────
        if cmd in ("show", "send"):
            if len(parts) == 1 or (len(parts) == 2 and not after_space):
                return [v for v in self._cli._vars if v.startswith(text)]

        # ── var <name> <MessageName> → message names on 3rd token ─────
        if cmd == "var":
            on_third = (len(parts) == 2 and after_space) or \
                       (len(parts) == 3 and not after_space)
            if on_third and self._cli._db is not None:
                return [n for n in self._cli._db.names() if n.startswith(text)]

        # ── load → filesystem paths ────────────────────────────────────
        if cmd == "load":
            pattern = (text or "") + "*"
            return glob.glob(pattern) or []

        # ── connect → nothing useful to complete ──────────────────────
        return []


class BoAtCli:
    PROMPT = "boat> "

    def __init__(self, gateway: str = "localhost:50051", db_path: str | None = None) -> None:
        self._gateway = gateway
        self._client: BoAtClient | None = None
        self._db:     PduDatabase | None = None
        self._vars:   dict[str, Message]  = {}

        if db_path:
            self._load_db(db_path)

    # ------------------------------------------------------------------

    def _connect(self) -> BoAtClient:
        if self._client is None:
            self._client = BoAtClient(self._gateway)
        return self._client

    def _load_db(self, path: str) -> None:
        self._db = PduDatabase(path)
        print(f"Loaded {len(self._db.names())} messages from '{path}'")

    # ------------------------------------------------------------------
    # Command dispatch

    def _cmd_load(self, args: str) -> None:
        path = args.strip()
        if not path:
            print("Usage: load <path>")
            return
        try:
            self._load_db(path)
        except Exception as e:
            print(f"Error: {e}")

    def _cmd_connect(self, args: str) -> None:
        addr = args.strip() or "localhost:50051"
        self._gateway = addr
        self._client  = None
        print(f"Gateway set to {addr}")

    def _cmd_var(self, args: str) -> None:
        parts = args.split(None, 1)
        if len(parts) != 2:
            print("Usage: var <varname> <MessageName>")
            return
        varname, msgname = parts
        if self._db is None:
            print("No database loaded. Use: load <path>")
            return
        entry = self._db.by_name(msgname)
        if entry is None:
            print(f"Message '{msgname}' not found. Available: {self._db.names()}")
            return
        self._vars[varname] = Message(entry)
        print(f"'{varname}' = {msgname}  (DbId={entry['DbId']}  "
              f"BusType={entry['BusType']}  {entry['signalcount']} signals)")

    def _cmd_show(self, args: str) -> None:
        varname = args.strip()
        if varname not in self._vars:
            print(f"Unknown variable '{varname}'. Defined: {list(self._vars)}")
            return
        print(repr(self._vars[varname]))

    def _cmd_send(self, args: str) -> None:
        varname = args.strip()
        if varname not in self._vars:
            print(f"Unknown variable '{varname}'. Defined: {list(self._vars)}")
            return
        try:
            result = _send_message(self._connect(), self._vars[varname])
            print(result)
        except grpc.RpcError as e:
            print(f"gRPC error: {e.details()}")
        except Exception as e:
            print(f"Error: {e}")

    def _cmd_list(self) -> None:
        if self._db is None:
            print("No database loaded.")
            return
        for name in sorted(self._db.names()):
            e = self._db.by_name(name)
            print(f"  {e['DbId']:5d}  {e['BusType']:<8}  {name}")

    def _cmd_vars(self) -> None:
        if not self._vars:
            print("No variables defined.")
            return
        for varname, msg in self._vars.items():
            print(f"  {varname}  →  {msg.name}  (DbId={msg.db_id})")

    def _cmd_assign(self, varname: str, signal: str, value_str: str) -> None:
        if varname not in self._vars:
            print(f"Unknown variable '{varname}'.")
            return
        try:
            value = float(value_str)
        except ValueError:
            print(f"Invalid value '{value_str}': expected a number.")
            return
        try:
            self._vars[varname].set(signal, value)
            print(f"  {varname}.{signal} = {value}")
        except KeyError as e:
            print(f"Error: {e}")

    def _cmd_help(self) -> None:
        print(__doc__)

    # ------------------------------------------------------------------
    # Readline / tab completion

    def _setup_readline(self) -> None:
        try:
            import readline
        except ImportError:
            return  # Windows without pyreadline — just skip

        completer = _Completer(self)
        readline.set_completer(completer.complete)
        # Remove '.' from delimiters so "varname.Signal" arrives as one token.
        readline.set_completer_delims(" \t\n=")
        readline.parse_and_bind("tab: complete")

    # ------------------------------------------------------------------
    # Main loop

    def run(self) -> None:
        print("BoAt CLI  —  type 'help' for commands, 'exit' to quit.")
        self._setup_readline()
        while True:
            try:
                line = input(self.PROMPT).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not line or line.startswith("#"):
                continue

            # <varname>.<Signal> = <value>
            m = _ASSIGN_RE.match(line)
            if m:
                self._cmd_assign(m.group(1), m.group(2), m.group(3).strip())
                continue

            parts  = line.split(None, 1)
            cmd    = parts[0].lower()
            rest   = parts[1] if len(parts) > 1 else ""

            if cmd in ("exit", "quit"):
                break
            elif cmd == "load":
                self._cmd_load(rest)
            elif cmd == "connect":
                self._cmd_connect(rest)
            elif cmd == "var":
                self._cmd_var(rest)
            elif cmd == "show":
                self._cmd_show(rest)
            elif cmd == "send":
                self._cmd_send(rest)
            elif cmd == "list":
                self._cmd_list()
            elif cmd == "vars":
                self._cmd_vars()
            elif cmd in ("help", "?"):
                self._cmd_help()
            else:
                print(f"Unknown command '{cmd}'. Type 'help' for usage.")


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    import argparse

    # Forward to one-shot CLI when a subcommand is present (skip flags first).
    _SUBCOMMANDS = {"can", "pdu", "eth", "db"}
    _first_pos = None
    _skip = False
    for _a in sys.argv[1:]:
        if _skip:
            _skip = False; continue
        if _a in ("--db", "--gateway"):
            _skip = True; continue
        if not _a.startswith("-"):
            _first_pos = _a; break
    if _first_pos in _SUBCOMMANDS:
        from boat.cmd import main as cmd_main
        sys.exit(cmd_main())

    parser = argparse.ArgumentParser(description="BoAt interactive message CLI")
    parser.add_argument("--db",      default=None,              help="PDU database JSON file to load on startup")
    parser.add_argument("--gateway", default="localhost:50051",  help="Gateway gRPC address (default: localhost:50051)")
    args = parser.parse_args()

    cli = BoAtCli(gateway=args.gateway, db_path=args.db)
    cli.run()


if __name__ == "__main__":
    main()
