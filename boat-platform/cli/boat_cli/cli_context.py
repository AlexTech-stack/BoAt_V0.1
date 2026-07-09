"""System prompt builder for `boat ai cli`.

Injects the full CLI command reference so the LLM can translate user intent
into the correct `boat ...` invocation.
"""
from __future__ import annotations

_CLI_REFERENCE = """\
## BoAt CLI Reference — all subcommands and flags

### sim — Simulation lifecycle
  boat sim create --scenario <id>             # create from scenario
  boat sim start <sim_id>                     # start
  boat sim pause <sim_id>                     # pause
  boat sim step <sim_id> [--ticks N]          # step N ticks (default 1)
  boat sim stop <sim_id>                      # stop
  boat sim reset <sim_id>                     # reset
  boat sim state <sim_id>                     # get state
  boat sim list                               # list all
  boat sim watch <sim_id>                     # stream state updates

### scenario — Scenario management
  boat scenario create --file <path>          # upload JSON file
  boat scenario get <id>                      # fetch by id
  boat scenario list                          # list all
  boat scenario validate --file <path>        # validate JSON
  boat scenario delete <id>                   # delete

### replay — Trace replay (server-side; required for Ethernet/.pcap)
  boat replay import <file> --trace-id <id>   # convert+upload .asc/.blf/.pcap
  boat replay start --trace <id>              # start replay
  boat replay stream --trace <id>             # start + stream in one call
  boat replay seek --tick <n>                 # seek to tick
  boat replay pause|resume|stop                # control an active replay

### plugin — Plugin management
  boat plugin register --path <so_path>       # load a .so plugin
  boat plugin list                            # list loaded plugins
  boat plugin info <id>                       # get plugin details
  boat plugin unload <id>                     # unload plugin

### frame — unified CAN + Ethernet send/subscribe (FrameService)
  boat frame list-ifaces                             # list interfaces the gateway has access to
  boat frame send --bus-type can --can-id <hex>      # send a raw CAN frame
         --iface <name> [--data <hex>] [--fd]
  boat frame send --bus-type eth --iface <name>      # send a raw Ethernet frame
         --data <hex> [--ethertype <hex>] [--src-mac <mac>] [--dst-mac <mac>]
  boat frame subscribe --bus-types can,eth            # stream frames
         [--iface <name>]

  Note: there is no standalone hardware-detection command anymore
  (the old `boat can detect` was retired). Use `ip link show type can`
  or `boat frame list-ifaces` (requires a running gateway) instead.

### can-tp — CAN Transport Protocol (ISO 15765-2)
  boat can-tp configure --nsdu-id <id>        # configure session
         --source-addr <hex> --target-addr <hex>
         [--bs N] [--stmin N] [--dlc 8|64] [--extended]
  boat can-tp send --nsdu-id <id>             # send large PDU
         --source-addr <hex> --target-addr <hex>
         --dlc 8|64 --data <hex>

### pdu — PDU routing and transmission
  boat pdu send --id <hex> --data <hex>       # send PDU via configured route
  boat pdu route --id <hex>                   # configure route with schedule
         --transport can|eth --iface <name>
         [--send-type none|cyclic|onchange|mixed]
         [--cycle-ms N] [--fast-ms N] [--reps N]
         [--can-id <hex>] [--ethertype <hex>]
         [--src-ip <ip>] [--dst-ip <ip>]
         [--src-port N] [--dst-port N] [--ttl N] [--vlan N]
  boat pdu remove-route --id <hex>            # remove route
  boat pdu list-routes                        # list all routes
  boat pdu container --msg <name>             # IpduM container
         [--id <hex>] [--iface <name>]
         [--src-ip <ip>] [--dst-ip <ip>] ...
  boat pdu group --id N --name <name>         # I-PDU group
         [--pdu <hex>]... [--enabled|--disabled]
  boat pdu enable-group --id N                # enable group
  boat pdu disable-group --id N               # disable group
  boat pdu list-groups                        # list groups
  boat pdu subscribe [--pdu-id <hex>]...       # stream PDUs

### db — PDU database inspection
  boat db list                                 # list all messages in DB
  boat db show --msg <name> [--bus <name>]    # show message details
  boat db signal-routes                        # list signal routing table

### gen — AI-assisted code generation (DEPRECATED, use 'boat ai')
  boat gen plugin --desc <text> [--out <path>]  # (moved to boat ai plugin)

### test — Test suite management
  boat test list-environments                  # list env configs
  boat test show-config <name>                 # show env config
  boat test validate-config <name>             # validate env config
  boat test check-env                          # check test prerequisites
  boat test run <manifest>                     # run tests from manifest
         [--env <name>] [--parallel] [--stop-on-failure]

### trace — Trace recording
  boat trace start [--asc|--blf|--pcap]       # start recording
  boat trace stop                              # stop recording
  boat trace status                            # recording status
  boat trace replay <file.asc|file.blf>       # inject CAN frames individually, real-time (CAN only)
                                                # for .pcap/Ethernet, use `boat replay import` + `start`/`stream` instead

### Global flags (available everywhere)
  --host localhost:50051                       # gateway address
  --json                                       # JSON output mode

## Common Workflow Examples

# 1. Full simulation flow
boat scenario create --file my_scenario.json
boat sim create --scenario <id>
boat sim start <sim_id>
boat sim watch <sim_id>
boat sim stop <sim_id>

# 2. Send a CAN frame
boat frame send --bus-type can --can-id 0x100 --iface vcan0 --data 0123

# 3. Configure cyclic PDU transmission
boat pdu route --id 0x100 --transport can --iface vcan0 \\
  --send-type cyclic --cycle-ms 100

# 4. List and inspect PDU database
boat db list
boat db show --msg "VehicleSpeed" --bus Powertrain_CAN

# 5. Configure CanTp session and send large data
boat can-tp configure --nsdu-id diag --source-addr 0x7E0 --target-addr 0x7E8
boat can-tp send --nsdu-id diag --source-addr 0x7E0 --target-addr 0x7E8 \\
  --dlc 8 --data 0123456789ABCDEF...

# 6. Run tests
boat test list-environments
boat test run manifest.json --env virtual --parallel
"""

_SYSTEM_INTRO = """\
You are a BoAt CLI assistant.  Your job is to translate the user's intent
into the correct `boat ...` CLI command.

Rules:
1. Output ONLY the CLI command and a brief explanation.
2. If the user's request is ambiguous, ask for clarification with the options.
3. Prefer the most specific subcommand (e.g. `boat frame send` over `boat pdu`).
4. Use --json flag when the user wants machine-readable output.
5. When a task requires multiple steps, list them in order.
6. If the task is better done via the Python SDK (e.g. complex logic),
   suggest `boat ai plugin` instead.

Available global flags: --host <addr>, --json (applies after the subcommand).
"""


def build_system_prompt() -> str:
    return _SYSTEM_INTRO + "\n\n" + _CLI_REFERENCE
