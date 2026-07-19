#pragma once

#include <cstdint>
#include <functional>
#include <string>
#include <vector>

namespace boat::core {

/* Interface the device_manager plugin exposes to the gRPC DeviceService.
   The plugin registers itself via PluginManager::RegisterService("device_manager",
   this) during initialize(); DeviceServiceImpl looks it up via FindService and
   delegates all calls -- the same pattern the pdu_router plugin uses for
   IPduRouter. Keeps all device logic in the plugin; the gateway core stays a
   thin dispatcher. */

enum class DeviceKind {
  Unspecified = 0,
  PowerSupply = 1,
  Relay       = 2,
  Generator   = 3,
  GenericIo   = 4,
};

struct DeviceChannelInfo {
  std::string name;
  bool        settable  = false;
  bool        readable  = false;
  bool        has_value = false;
  double      value     = 0.0;
  std::string unit;
};

struct DeviceDescriptor {
  std::string                    device_id;
  DeviceKind                     kind = DeviceKind::Unspecified;
  std::vector<DeviceChannelInfo> channels;
};

class IDeviceManager {
 public:
  virtual ~IDeviceManager() = default;

  /* Snapshot of all devices discovered so far (from observed measurement
     signals) plus their known controllable channels. */
  virtual std::vector<DeviceDescriptor> ListDevices() const = 0;

  /* Look up a single device by id. Returns false if not (yet) discovered. */
  virtual bool GetDevice(const std::string& device_id,
                         DeviceDescriptor& out) const = 0;

  /* Drive a controllable channel by publishing its setpoint signal. Returns
     false and fills err if the channel is unknown or not settable. */
  virtual bool SetControl(const std::string& device_id,
                          const std::string& channel, double value,
                          std::string& err) = 0;

  /* Streaming: register a callback invoked on every observed measurement
     update. Callbacks run on the signal-dispatch thread; keep them cheap. */
  using StateCallback = std::function<void(const std::string& device_id,
                                           const std::string& channel,
                                           double value, std::uint64_t ts_ns)>;
  using SubId = std::uint64_t;
  virtual SubId SubscribeState(StateCallback cb) = 0;
  virtual void  UnsubscribeState(SubId id) = 0;
};

}  // namespace boat::core
