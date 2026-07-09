#pragma once

#include <cstddef>
#include <functional>
#include <memory>
#include <mutex>
#include <unordered_map>
#include <vector>

#include "pdu/ipdumcontainer.h"
#include "pdu/pdu_types.h"
#include "pdu/transmission_engine.h"
#include "can_bus_registry.h"
#include "core/pdu_router_interface.h"
#include "ethernet_bus_registry.h"

namespace boat::hil {

/* Routes AUTOSAR-style PDUs over CAN and Ethernet transports.
 *
 * Routes are keyed by PDU ID.  Each route specifies which transport and
 * interface to use when sending, and how incoming frames on that interface
 * map back to PDU IDs.
 *
 * Ethernet PDU framing (custom, SOME/IP-inspired):
 *   Ethertype = route.ethertype (default 0x88B5)
 *   Payload   = [4 bytes PDU ID big-endian] + actual PDU payload
 *
 * CAN PDU framing:
 *   CAN ID = route.can_id if non-zero, else route.pdu_id
 *   CAN data = PDU payload (up to 8 bytes classic / 64 bytes FD)
 */
class PduRouter : public boat::core::IPduRouter {
 public:
  PduRouter(CanBusRegistry& can, EthernetBusRegistry& eth);
  PduRouter();  // plugin mode — use SetFramePublisher
  ~PduRouter();

  // Plugin mode: set the single frame publisher for CAN/Eth/PDU output.
  void SetFramePublisher(std::function<void(const BoatFrame&)> fn);

  // Make these public for the v8 plugin wrapper to call from on_frame.
  using CanRx = std::function<void(const CanFrame&, const std::string&)>;
  using EthRx = std::function<void(const EthernetFrame&, const std::string&)>;
  void OnCanFrame(const CanFrame& frame, const std::string& iface);
  void OnEthernetFrame(const EthernetFrame& frame, const std::string& iface);

  // Add or replace a per-PDU routing rule.
  void AddRoute(const PduRoute& route);

  // Remove a per-PDU routing rule and its schedule.
  void RemoveRoute(uint32_t pdu_id);

  // Register a container: all member PDU IDs will be multiplexed into one
  // IpduM Ethernet frame whenever any of them is sent.
  void AddContainer(const PduContainerDef& def);

  // Send a PDU; looks up the route (or container) and forwards.
  // Returns false if no route/container is configured, the PDU's group is
  // disabled, or the send fails.
  bool SendPdu(uint32_t pdu_id, const std::vector<uint8_t>& payload);

  using RxCallback = std::function<void(const PduFrame&)>;
  using SubId = std::size_t;

  // pdu_ids: empty vector = subscribe to all PDUs.
  SubId Subscribe(std::vector<uint32_t> pdu_ids, RxCallback cb);
  void  Unsubscribe(SubId id);

  std::vector<PduRoute> ListRoutes() const;

  void Stop();

  // ── I-PDU group management ──────────────────────────────────────────
  void AddGroup(const PduGroup& group);
  void EnableGroup(uint32_t group_id);
  void DisableGroup(uint32_t group_id);
  bool IsGroupEnabled(uint32_t group_id) const;
  std::vector<PduGroup> ListGroups() const;

  // ── Transmission engine ─────────────────────────────────────────────
  // Called by the external scheduler (TickScheduler or node tick thread).
  void OnTick(uint64_t tick_ms);

  // ── Deadline monitoring ─────────────────────────────────────────────
  void ConfigureDeadline(uint32_t pdu_id, const PduDeadlineConfig& cfg);

 private:
  void DispatchPdu(const PduFrame& pdu);

  bool SendContainer(const PduContainerDef& def,
                     const std::vector<IpduMEntry>& entries);

  // Returns false if the PDU belongs to a disabled group.
  bool IsPduGated(uint32_t pdu_id) const;

  CanBusRegistry*      can_{nullptr};
  EthernetBusRegistry* eth_{nullptr};

  // Plugin mode: set to non-null when loaded as a v8 plugin.
  std::function<void(const BoatFrame&)> frame_publisher_;

  mutable std::mutex routes_mutex_;
  std::unordered_map<uint32_t, PduRoute> routes_;  // keyed by pdu_id

  // Container buffering — each slot holds the last written payload for one PDU.
  struct ContainerSlot {
    uint32_t             pdu_id;
    std::vector<uint8_t> payload;  // empty = never written
  };
  struct ContainerBuffer {
    PduContainerDef        def;
    std::vector<ContainerSlot> slots;
  };
  mutable std::mutex containers_mutex_;
  std::unordered_map<uint32_t, ContainerBuffer> containers_;   // container_id buf
  std::unordered_map<uint32_t, uint32_t>        pdu_to_container_;  // pdu_id cid

  std::mutex subs_mutex_;
  struct Subscription {
    std::vector<uint32_t> pdu_ids;  // empty = all
    RxCallback cb;
  };
  std::unordered_map<SubId, Subscription> subscriptions_;
  SubId next_sub_id_{0};

  CanBusRegistry::RxCallbackId      can_sub_id_{0};
  EthernetBusRegistry::RxCallbackId eth_sub_id_{0};
  bool                               subscribed_{false};

  // ── I-PDU groups ────────────────────────────────────────────────────
  mutable std::mutex groups_mutex_;
  std::unordered_map<uint32_t, PduGroup> groups_;             // group_id group
  std::unordered_map<uint32_t, uint32_t> pdu_to_group_;       // pdu_id group_id

  // ── Transmission engine ─────────────────────────────────────────────
  std::unique_ptr<TransmissionEngine> tx_engine_;

  // ── Deadline monitoring ─────────────────────────────────────────────
  struct DeadlineState {
    PduDeadlineConfig cfg;
    uint64_t last_rx_tick_ms{0};
    bool     timeout_fired{false};
  };
  mutable std::mutex deadline_mutex_;
  std::unordered_map<uint32_t, DeadlineState> deadlines_;
};

}  // namespace boat::hil
