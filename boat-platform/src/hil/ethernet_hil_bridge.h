#pragma once

#include <optional>
#include <string>

#include "core/event/event_bus.h"
#include "ethernet_bus_registry.h"

namespace boat::hil {

/* Bridges the EthernetBusRegistry with the platform EventBus.
 *
 * - AddPhysicalInterface() opens a RawSocketEthernetDriver and registers it.
 * - Every frame received on any registered interface is published to the
 *   EventBus as kEventTypeEthRx so other platform components can react.
 * - EventBus kEventTypeEthTx payloads (EthernetFrame) are forwarded to
 *   SendFrameAll() so they egress on every registered interface.
 */
class EthernetHilBridge {
 public:
  EthernetHilBridge(EthernetBusRegistry& registry, boat::core::EventBus& bus);
  ~EthernetHilBridge();

  /* Open a physical NIC and register it in the registry. */
  bool AddPhysicalInterface(const std::string& iface);

  void Stop();

  static constexpr uint32_t kEventTypeEthRx = 0xE7110001u;
  static constexpr uint32_t kEventTypeEthTx = 0xE7110002u;

 private:
  EthernetBusRegistry&  registry_;
  boat::core::EventBus& bus_;

  EthernetBusRegistry::RxCallbackId          rx_sub_id_{0};
  bool                                        subscribed_{false};
  std::optional<boat::core::EventBus::SubscriptionHandle> tx_sub_;
};

}  // namespace boat::hil
