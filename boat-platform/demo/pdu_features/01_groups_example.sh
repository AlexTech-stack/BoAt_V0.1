#!/usr/bin/env bash
# Example: I-PDU Groups
# Demonstrates how to create, enable, disable, and list PDU groups.
# Prerequisites: boat gateway running on localhost:50051

set -euo pipefail

echo "=== I-PDU Groups Example ==="

echo ""
echo "1. Configure a CAN route for PDU 0x100"
boat pdu route --id 0x100 --transport can --iface vcan0

echo ""
echo "2. Configure a CAN route for PDU 0x200"
boat pdu route --id 0x200 --transport can --iface vcan0

echo ""
echo "3. Create a group containing both PDUs, initially disabled"
boat pdu group --id 1 --name "EngineSignals" --pdu 0x100 --pdu 0x200 --disabled

echo ""
echo "4. Try to send a PDU (will fail — group is disabled)"
boat pdu send --id 0x100 --data 010203 || true

echo ""
echo "5. Enable the group"
boat pdu enable-group --id 1

echo ""
echo "6. Send now succeeds"
boat pdu send --id 0x100 --data 010203

echo ""
echo "7. List all groups"
boat pdu list-groups

echo ""
echo "8. Disable the group again"
boat pdu disable-group --id 1

echo "=== Done ==="
