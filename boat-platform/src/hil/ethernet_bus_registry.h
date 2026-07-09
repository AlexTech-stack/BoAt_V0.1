#pragma once

#include <atomic>
#include <cstddef>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

#include "ethernet/ethernet_frame.h"

#include "core/frame.h"

namespace boat::hil {

/* Manages a set of named Ethernet interfaces (one driver per interface).
 *
 * Thread-safe: Add/Send/Subscribe/Unsubscribe may be called from any thread.
 *
 * Each registered interface runs a background receive thread that calls
 * DispatchRx() on every incoming frame.
 */
class EthernetBusRegistry {
 public:
  ~EthernetBusRegistry();

  using RxCallbackId = std::size_t;
  /* Callback receives the frame and the interface it arrived on. */
  using RxCallback =
      std::function<void(const EthernetFrame&, const std::string& iface)>;

  /* Open driver, start RX thread, register under iface.
     Returns true on success. */
  bool Add(const std::string& iface,
           std::unique_ptr<IEthernetDriver> driver);

  /* Send a frame on the named interface.  Returns false if not found. */
  bool SendFrame(const std::string& iface, const EthernetFrame& frame);

  /* Send a frame on every registered interface. */
  void SendFrameAll(const EthernetFrame& frame);

  /* Subscribe to incoming frames.
     iface_filter = "" → all interfaces.
     ethertype_filter = 0 → all ethertypes.
     Returns an ID to pass to Unsubscribe. */
  RxCallbackId Subscribe(const std::string& iface_filter,
                         uint32_t           ethertype_filter,
                         RxCallback         cb);
  void Unsubscribe(RxCallbackId id);

  using FrameRxCallback = std::function<void(const boat::core::Frame&)>;
  RxCallbackId SubscribeFrame(FrameRxCallback cb);
  void UnsubscribeFrame(RxCallbackId id);

  std::vector<std::string> Interfaces() const;
  bool Has(const std::string& iface) const;

  void StopAll();

 private:
  void DispatchRx(const EthernetFrame& frame, const std::string& iface);

  struct IfaceEntry {
    std::string                      iface;
    std::unique_ptr<IEthernetDriver> driver;
    std::thread                      rx_thread;
    std::atomic<bool>                running{false};
  };

  struct Subscription {
    std::string iface_filter;
    uint32_t    ethertype_filter{0};
    RxCallback  cb;
  };

  mutable std::mutex                            ifaces_mutex_;
  std::unordered_map<std::string, IfaceEntry>   ifaces_;

  std::mutex                                    subs_mutex_;
  std::unordered_map<RxCallbackId, Subscription> subscriptions_;
  RxCallbackId next_id_{0};

  std::mutex                                    frame_subs_mutex_;
  std::unordered_map<RxCallbackId, FrameRxCallback> frame_subscriptions_;
  RxCallbackId next_frame_id_{0};
};

}  // namespace boat::hil
