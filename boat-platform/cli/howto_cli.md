# BoAt CLI — Installation & Usage

## Prerequisites

- Python >= 3.11
- A running `boat_gateway` (see top-level build instructions in AGENTS.md)
- Virtual CAN interface (for CAN commands):

```bash
sudo modprobe vcan
sudo ip link add vcan0 type vcan && sudo ip link set vcan0 up
```

## Installation

Install the Python SDK first, then the CLI (both as editable installs):

```bash
pip install -e ./boat-platform/sdk/python[dev]
pip install -e ./boat-platform/cli
```

Verify:

```bash
boat --help
```

## Connecting to the Gateway

By default the CLI connects to `localhost:50051`. Override with `--host`:

```bash
boat --host 192.168.1.100:50051 sim status
```

## Global Flags

| Flag | Description |
|------|-------------|
| `--host ADDRESS` | Gateway address (`host:port`, default `localhost:50051`) |
| `--json` | Output raw JSON arrays instead of Rich tables |

Place them before the subcommand. Every subcommand below accepts these flags.

## Subcommands Overview

```
boat sim          Simulation lifecycle (create, start, pause, step, stop, state, list, watch)
boat scenario     Scenario management (create, get, list, delete, validate)
boat replay       Trace replay (start, seek, stream, pause, resume, stop, from-events)
boat frame        Unified frame send/subscribe, list-ifaces (CAN, CANFD, Ethernet, TCP, PDU)
boat can-tp       CAN Transport Protocol (configure, send) — ISO 15765-2
boat pdu          PDU routing (send, route, remove-route, container, group, list-routes, subscribe)
boat plugin       Plugin management (register, list, info, unload)
boat db           PDU database inspection (list, show, signal-routes)
boat trace        Trace recording (start, stop, status)
boat test         System test runner (list-environments, run)
boat ai           AI assistants (scenario, bus-setup, cli, plugin, config)
```

## Workflows

### 1. Simulation Lifecycle

```bash
# List active simulations
boat sim list

# Create and start a simulation from a scenario
boat sim create --scenario-id my_scenario
boat sim start --simulation-id <id>

# Pause, step, resume
boat sim pause --simulation-id <id>
boat sim step --simulation-id <id> --ticks 500
boat sim start --simulation-id <id>

# Stop and clean up
boat sim stop --simulation-id <id>
```

### 2. Listing Available Interfaces

```bash
# Show all CAN and Ethernet interfaces registered on the gateway
boat frame list-ifaces

# JSON output for scripting
boat --json frame list-ifaces
```

### 3. Sending and Receiving Frames

```bash
# Send a CAN frame (interface auto-selected to e.g. vcan0 if omitted)
boat frame send --bus-type can --can-id 0x123 --data AABBCCDD

# Send a CAN FD frame (bus type determines FD flag, no separate --fd flag)
boat frame send --bus-type canfd --can-id 0x123 --data 00112233445566778899AABBCCDDEEFF

# Explicit interface selection
boat frame send --bus-type can --can-id 0x123 --iface can0 --data AABBCCDD

# Send an Ethernet frame
boat frame send --bus-type ethernet --ethertype 0x0800 --dst-ip 10.0.0.1 --data AABB

# Subscribe to incoming frames (streaming, press Ctrl+C to stop)
boat frame subscribe --bus-types can
boat frame subscribe --bus-types ethernet
boat frame subscribe --bus-types can,ethernet
```

### 4. PDU Routing (requires PduRouter plugin on the gateway)

```bash
# Configure a route
boat pdu route --id 0x100 --transport can --iface vcan0

# With a transmission schedule (cyclic every 100ms)
boat pdu route --id 0x100 --transport can --iface vcan0 --send-type cyclic --cycle-ms 100

# I-PDU groups
boat pdu group --id 1 --name "Safety" --pdu 0x100 --pdu 0x200 --disabled
boat pdu enable-group --id 1
boat pdu list-groups
```

### 5. CAN Transport Protocol (ISO 15765-2)

```bash
# Configure a CanTp session
boat can-tp configure --nsdu-id diag --source-addr 0x7E0 --target-addr 0x7E8

# Send a large PDU (auto-segmented)
boat can-tp send --nsdu-id diag --source-addr 0x7E0 --target-addr 0x7E8 --data 0123456789ABCDEF...
```

### 6. PDU Database Inspection

```bash
# List available databases
boat db list

# Show a specific database
boat db show --db pdu_db.json

# Show signal routes
boat db signal-routes --db pdu_db.json --signal MotorSpeed
```

### 7. Trace Recording & Replay

```bash
# Start recording
boat trace start --simulation-id <id>

# Stop recording
boat trace stop

# Replay a trace
boat replay start --trace-id <id>
```

### 8. AI Assistants

AI commands use an LLM backend (default: Ollama with `qwen2.5-coder:3b`):

```bash
# Configure AI endpoint
boat ai config set --endpoint http://localhost:11434/v1 --model qwen2.5-coder:3b

# Generate a scenario from a description
boat ai scenario "Create a CAN bus with two ECUs exchanging 0x100 and 0x200"

# Get CLI command help
boat ai cli "How do I subscribe to CAN frames?"

# Generate a bus-setup config
boat ai bus-setup "vcan0 with pdu_router"
```

## JSON Mode

Add `--json` to any command for script-friendly output:

```bash
boat --json sim list
boat --json pdu list-routes
```

Output is a JSON array of objects — pipe to `jq` for filtering:

```bash
boat --json pdu list-routes | jq '.[] | select(.transport == "CAN")'
```

## Shell Completion

Shell completions are auto-generated by Typer. To enable:

```bash
# bash
eval "$(_BOAT_COMPLETE=bash_source boat)"

# zsh
eval "$(_BOAT_COMPLETE=zsh_source boat)"

# fish
boat --install-completion fish
```

## Running Tests

```bash
pytest cli/tests -v
```
