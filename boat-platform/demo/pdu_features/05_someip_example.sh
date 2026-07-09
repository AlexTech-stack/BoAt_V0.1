#!/usr/bin/env bash
# Example: SOME/IP Plugin — Service-oriented middleware over Ethernet
#
# Prerequisites:
#   sudo ip link add veth0 type veth peer name veth1
#   sudo ip link set veth0 up && sudo ip link set veth1 up

set -euo pipefail

echo "=== SOME/IP Example ==="

# Build the SOME/IP plugin
echo ""
echo "1. Building SOME/IP plugin..."
cd "$(git rev-parse --show-toplevel)/boat-platform"
cmake --build --preset debug --target someip 2>/dev/null || \
  cmake --build --preset debug 2>/dev/null

PLUGIN_PATH="./build/debug/src/plugins/someip/someip.so"

echo ""
echo "2. Start gateway with SOME/IP plugin"
echo "   BOAT_NODE_PLUGINS=$PLUGIN_PATH \\"
echo "     BOAT_ETH_INTERFACES=veth0 \\"
echo "     ./build/debug/src/gateway/grpc_gateway/boat_gateway"
echo "   (Start in a separate terminal)"

echo ""
echo "3. The plugin listens on the configured SOME/IP-SD port (default 30490)"
echo "   for Service Discovery messages and the configured UDP ports for"
echo "   service requests."

echo ""
echo "4. When a REQUEST arrives for a locally-offered service:"
echo "   - Plugin matches service_id against local_services"
echo "   - Builds a RESPONSE with echoed payload"
echo "   - Sends it via set_eth_publisher"

echo ""
echo "5. SOME/IP-SD (simplified):"
echo "   - FIND_SERVICE → OFFER_SERVICE"
echo "   - SUBSCRIBE → SUBSCRIBE_ACK"
echo ""

echo "=== Done ==="
