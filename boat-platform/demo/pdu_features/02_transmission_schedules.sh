#!/usr/bin/env bash
# Example: PDU Transmission Schedules
# Demonstrates Cyclic, OnChange, and Mixed transmission modes.
# Prerequisites: boat gateway running on localhost:50051

set -euo pipefail

echo "=== Transmission Schedule Examples ==="

echo ""
echo "--- Cyclic Mode ---"
echo "PDU 0x100 will be sent automatically every 100ms"
boat pdu route --id 0x100 --transport can --iface vcan0 \
  --send-type cyclic --cycle-ms 100

echo ""
echo "Send once to seed the payload, then OnTick() drives the rest"
boat pdu send --id 0x100 --data AABB

echo ""
echo "--- OnChange Mode with Fast Repetitions ---"
echo "PDU 0x200 sends immediately on payload change, then repeats 3x at 10ms"
boat pdu route --id 0x200 --transport can --iface vcan0 \
  --send-type onchange --fast-ms 10 --reps 3

echo ""
echo "Initial send sets the baseline payload"
boat pdu send --id 0x200 --data 01

echo ""
echo "Changing the payload triggers OnChange + 3 fast reps"
boat pdu send --id 0x200 --data 02

echo ""
echo "--- Mixed Mode ---"
echo "PDU 0x300 sends cyclically at 200ms AND triggers on change with 2 reps at 20ms"
boat pdu route --id 0x300 --transport can --iface vcan0 \
  --send-type mixed --cycle-ms 200 --fast-ms 20 --reps 2

echo ""
echo "List routes to verify schedule configuration"
boat pdu list-routes

echo ""
echo "--- Stopping a Schedule ---"
echo ""
echo "Option A: Set send-type to none (preserves route, stops auto-sends)"
echo "  boat pdu route --id 0x100 --transport can --iface vcan0 --send-type none"
echo ""
echo "Option B: Remove route entirely"
echo "  boat pdu remove-route --id 0x100"
echo ""
echo "Option C: Use I-PDU group (preserves config, silences the PDU)"
echo "  boat pdu group --id 1 --pdu 0x100"
echo "  boat pdu disable-group --id 1"
echo ""

echo "=== Done ==="
