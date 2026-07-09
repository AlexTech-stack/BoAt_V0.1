#pragma once

#include "core/frame.h"

namespace boat::hil {
class CanBusRegistry;
class EthernetBusRegistry;
}  // namespace boat::hil

namespace boat::gateway {

// FrameSink is the single path through which any frame reaches a hardware bus.
//
// The gateway core owns the stateless transport substrate: every producer that
// wants to put a frame on the wire — plugins (via the frame publisher), the
// replay engine, and the gRPC FrameService — funnels through Publish().  This
// keeps "frame -> wire" in exactly one place (no FrameForwarder plugin, no
// per-producer registry wiring) and lets the registry own loopback tagging and
// RX dispatch.
//
// TCP and PDU are not wire buses: they are stateful conversations handled by
// plugins and flow through PluginManager::DispatchFrame, not this sink.
class FrameSink {
 public:
  FrameSink(boat::hil::CanBusRegistry& can_registry,
            boat::hil::EthernetBusRegistry& eth_registry)
      : can_registry_(can_registry), eth_registry_(eth_registry) {}

  // Transmit a frame onto its bus.  CAN/CANFD -> CAN registry, ETHERNET -> Eth
  // registry.  An empty iface broadcasts to all interfaces of that bus.
  // Returns true if the frame was routed to a registry, false for bus types
  // that are not transmitted here (TCP/PDU/unspecified).
  bool Publish(const boat::core::Frame& frame);

 private:
  boat::hil::CanBusRegistry& can_registry_;
  boat::hil::EthernetBusRegistry& eth_registry_;
};

}  // namespace boat::gateway
