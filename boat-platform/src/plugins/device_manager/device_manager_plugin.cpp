// device_manager — aggregates signal-bus devices into a structured, queryable
// device model (v9 ABI). It is the delegate behind the gRPC DeviceService.
//
// Devices are not a new bus type: they live as named signals on the always-on
// signal bus following the "<kind>.<id>.<channel>[.<role>]" convention
// (psu.main.voltage.meas, relay.kl15.state, gen.alt.rpm.set, ...). This plugin:
//
//   * discovers devices by observing published measurement signals
//     (".meas" / ".state") via on_signal, and records their last values;
//   * advertises each device's controllable channels from a per-kind table;
//   * SetControl publishes the matching setpoint signal via the bus publisher;
//   * notifies state subscribers (DeviceService.StreamState) on each update.
//
// It touches no frame bus and exports an IDeviceManager* via the optional
// named-service symbols, exactly like pdu_router exposes IPduRouter.

#include <boat/frame.h>
#include <boat/plugin.h>

#include <chrono>
#include <cstdint>
#include <cstdio>
#include <map>
#include <mutex>
#include <sstream>
#include <string>
#include <vector>

#include "core/device_manager_interface.h"

namespace {

using boat::core::DeviceChannelInfo;
using boat::core::DeviceDescriptor;
using boat::core::DeviceKind;
using boat::core::IDeviceManager;

/* ── naming-convention helpers ──────────────────────────────────────────── */

std::vector<std::string> Split(const std::string& s, char sep) {
  std::vector<std::string> out;
  std::string cur;
  std::istringstream ss(s);
  while (std::getline(ss, cur, sep)) out.push_back(cur);
  return out;
}

DeviceKind KindFromPrefix(const std::string& prefix) {
  if (prefix == "psu") return DeviceKind::PowerSupply;
  if (prefix == "relay") return DeviceKind::Relay;
  if (prefix == "gen") return DeviceKind::Generator;
  return DeviceKind::GenericIo;
}

std::string UnitFor(const std::string& channel) {
  if (channel.find("voltage") != std::string::npos) return "V";
  if (channel.find("current") != std::string::npos) return "A";
  if (channel.find("rpm") != std::string::npos) return "rpm";
  if (channel.find("resistance") != std::string::npos || channel == "load") return "ohm";
  return "";
}

// The controllable channels each device kind advertises, independent of what
// has been observed yet, so a device can be commanded before its first
// measurement arrives.
std::vector<DeviceChannelInfo> ControlTable(DeviceKind kind) {
  auto mk = [](const char* n, const char* unit) {
    DeviceChannelInfo c;
    c.name = n;
    c.settable = true;
    c.unit = unit;
    return c;
  };
  switch (kind) {
    case DeviceKind::PowerSupply:
      return {mk("voltage", "V"), mk("enable", ""), mk("load", "ohm")};
    case DeviceKind::Relay:
      return {mk("state", "")};
    case DeviceKind::Generator:
      return {mk("rpm", "rpm"), mk("enable", "")};
    default:
      return {};
  }
}

// Map a (kind, device_id, channel) control request to the setpoint signal name
// to publish. Returns "" if the channel is not controllable for that kind.
std::string SetSignalFor(DeviceKind kind, const std::string& device_id,
                         const std::string& channel) {
  switch (kind) {
    case DeviceKind::PowerSupply:
      if (channel == "voltage") return device_id + ".voltage.set";
      if (channel == "enable") return device_id + ".enable";
      if (channel == "load" || channel == "resistance")
        return device_id + ".load.resistance";
      return "";
    case DeviceKind::Relay:
      if (channel == "state" || channel == "coil") return device_id + ".set";
      return "";
    case DeviceKind::Generator:
      if (channel == "rpm") return device_id + ".rpm.set";
      if (channel == "enable") return device_id + ".enable";
      return "";
    case DeviceKind::GenericIo:
      return device_id + "." + channel + ".set";
    case DeviceKind::Unspecified:
      return "";
  }
  return "";
}

// Parse a signal name into a device measurement, if it is one. Returns true and
// fills out-params for ".meas" signals and the relay's ".state" signal;
// returns false for setpoint/unknown signals.
bool ParseMeasurement(const std::string& name, std::string& device_id,
                      DeviceKind& kind, std::string& channel) {
  const auto tok = Split(name, '.');
  if (tok.size() < 3) return false;
  device_id = tok[0] + "." + tok[1];
  kind = KindFromPrefix(tok[0]);
  // remainder = tok[2..]
  const std::string& last = tok.back();
  if (last == "meas" && tok.size() >= 4) {
    channel.clear();
    for (std::size_t i = 2; i + 1 < tok.size(); ++i) {
      if (!channel.empty()) channel += ".";
      channel += tok[i];
    }
    return !channel.empty();
  }
  if (tok.size() == 3 && tok[2] == "state") {  // relay contact state
    channel = "state";
    return true;
  }
  return false;
}

/* ── device model ───────────────────────────────────────────────────────── */

struct DeviceManagerImpl : public IDeviceManager {
  // wiring
  BoatBusPublishFn bus_publish_fn = nullptr;
  void* bus_publisher_ctx = nullptr;

  mutable std::mutex mu;
  std::map<std::string, DeviceDescriptor> devices;  // device_id -> descriptor

  std::mutex sub_mu;
  std::map<SubId, StateCallback> subscribers;
  SubId next_sub_id = 1;

  // Find-or-create a device and seed its controllable channels once.
  DeviceDescriptor& Ensure(const std::string& device_id, DeviceKind kind) {
    auto it = devices.find(device_id);
    if (it != devices.end()) return it->second;
    DeviceDescriptor d;
    d.device_id = device_id;
    d.kind = kind;
    d.channels = ControlTable(kind);  // advertise settable channels up front
    return devices.emplace(device_id, std::move(d)).first->second;
  }

  DeviceChannelInfo& EnsureChannel(DeviceDescriptor& d, const std::string& name) {
    for (auto& c : d.channels) {
      if (c.name == name) return c;
    }
    DeviceChannelInfo c;
    c.name = name;
    c.unit = UnitFor(name);
    d.channels.push_back(c);
    return d.channels.back();
  }

  // Called from on_signal for every observed measurement.
  void OnMeasurement(const std::string& device_id, DeviceKind kind,
                     const std::string& channel, double value) {
    {
      std::lock_guard<std::mutex> lock(mu);
      DeviceDescriptor& d = Ensure(device_id, kind);
      DeviceChannelInfo& c = EnsureChannel(d, channel);
      c.readable = true;
      c.has_value = true;
      c.value = value;
      if (c.unit.empty()) c.unit = UnitFor(channel);
    }
    NotifySubscribers(device_id, channel, value);
  }

  void NotifySubscribers(const std::string& device_id, const std::string& channel,
                         double value) {
    const auto ts = static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::system_clock::now().time_since_epoch())
            .count());
    std::vector<StateCallback> snapshot;
    {
      std::lock_guard<std::mutex> lock(sub_mu);
      snapshot.reserve(subscribers.size());
      for (auto& [id, cb] : subscribers) {
        (void)id;
        snapshot.push_back(cb);
      }
    }
    for (auto& cb : snapshot) {
      if (cb) cb(device_id, channel, value, ts);
    }
  }

  /* ── IDeviceManager ───────────────────────────────────────────────────── */

  std::vector<DeviceDescriptor> ListDevices() const override {
    std::lock_guard<std::mutex> lock(mu);
    std::vector<DeviceDescriptor> out;
    out.reserve(devices.size());
    for (const auto& [id, d] : devices) {
      (void)id;
      out.push_back(d);
    }
    return out;
  }

  bool GetDevice(const std::string& device_id,
                 DeviceDescriptor& out) const override {
    std::lock_guard<std::mutex> lock(mu);
    auto it = devices.find(device_id);
    if (it == devices.end()) return false;
    out = it->second;
    return true;
  }

  bool SetControl(const std::string& device_id, const std::string& channel,
                  double value, std::string& err) override {
    // Resolve the target kind: use the discovered device if known, else infer
    // from the id prefix so a device can be driven before it is discovered.
    DeviceKind kind = DeviceKind::Unspecified;
    {
      std::lock_guard<std::mutex> lock(mu);
      auto it = devices.find(device_id);
      if (it != devices.end()) kind = it->second.kind;
    }
    if (kind == DeviceKind::Unspecified) {
      const auto tok = Split(device_id, '.');
      if (!tok.empty()) kind = KindFromPrefix(tok[0]);
    }
    const std::string signal = SetSignalFor(kind, device_id, channel);
    if (signal.empty()) {
      err = "channel '" + channel + "' is not settable for device '" +
            device_id + "'";
      return false;
    }
    // Publish outside any lock — bus_publish dispatches synchronously back
    // through the signal bus (and this plugin's own on_signal).
    if (bus_publish_fn == nullptr) {
      err = "device_manager has no signal-bus publisher";
      return false;
    }
    bus_publish_fn(bus_publisher_ctx, signal.c_str(), value);
    return true;
  }

  SubId SubscribeState(StateCallback cb) override {
    std::lock_guard<std::mutex> lock(sub_mu);
    const SubId id = next_sub_id++;
    subscribers[id] = std::move(cb);
    return id;
  }

  void UnsubscribeState(SubId id) override {
    std::lock_guard<std::mutex> lock(sub_mu);
    subscribers.erase(id);
  }
};

/* ── vtable implementations ─────────────────────────────────────────────── */

int dm_initialize(void* /*ctx*/, const char* /*config_json*/) { return 0; }

void dm_on_tick(void* /*ctx*/, uint64_t /*tick*/) {}

void dm_on_signal(void* ctx, const char* name, double value) {
  auto* p = static_cast<DeviceManagerImpl*>(ctx);
  if (name == nullptr) return;
  std::string device_id, channel;
  DeviceKind kind;
  if (ParseMeasurement(name, device_id, kind, channel)) {
    p->OnMeasurement(device_id, kind, channel, value);
  }
}

void dm_set_bus_publisher(void* ctx, BoatBusPublishFn fn, void* publisher_ctx) {
  auto* p = static_cast<DeviceManagerImpl*>(ctx);
  p->bus_publish_fn = fn;
  p->bus_publisher_ctx = publisher_ctx;
}

void dm_shutdown(void* ctx) {
  auto* p = static_cast<DeviceManagerImpl*>(ctx);
  std::lock_guard<std::mutex> lock(p->mu);
  std::fprintf(stderr, "[device_manager] shutdown: %zu device(s) tracked\n",
               p->devices.size());
}

}  // namespace

/* ── C ABI entry points ─────────────────────────────────────────────────── */

extern "C" BoatPlugin* boat_plugin_create() {
  static BoatPluginVTable kVTable = [] {
    BoatPluginVTable vt{};
    vt.initialize          = &dm_initialize;
    vt.on_tick             = &dm_on_tick;
    vt.shutdown            = &dm_shutdown;
    vt.set_publisher       = nullptr;
    vt.set_bus_publisher   = &dm_set_bus_publisher;
    vt.set_pdu_publisher   = nullptr;
    vt.on_frame            = nullptr;
    vt.set_frame_publisher = nullptr;
    vt.declared_buses      = nullptr;
    vt.on_signal           = &dm_on_signal;
    return vt;
  }();

  auto* plugin = new BoatPlugin{};
  plugin->vtable = &kVTable;
  plugin->ctx = new DeviceManagerImpl{};
  return plugin;
}

extern "C" void boat_plugin_destroy(BoatPlugin* plugin) {
  if (plugin == nullptr) return;
  if (plugin->vtable != nullptr && plugin->vtable->shutdown != nullptr) {
    plugin->vtable->shutdown(plugin->ctx);
  }
  delete static_cast<DeviceManagerImpl*>(plugin->ctx);
  delete plugin;
}

extern "C" uint32_t boat_plugin_abi_version() { return BOAT_PLUGIN_ABI_VERSION; }

/* ── optional named C++ service export (for DeviceServiceImpl) ───────────── */

extern "C" const char* boat_plugin_service_name() { return "device_manager"; }

extern "C" void* boat_plugin_service_ptr(void* ctx) {
  return static_cast<IDeviceManager*>(static_cast<DeviceManagerImpl*>(ctx));
}
