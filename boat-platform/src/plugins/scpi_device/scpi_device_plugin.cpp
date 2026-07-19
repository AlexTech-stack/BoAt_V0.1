// scpi_device — a physical bench power supply / e-load over SCPI (v9 ABI).
//
// The physical counterpart of virtual_psu: same signal-bus contract, but backed
// by a real instrument via ScpiDeviceDriver (SCPI over TCP, the LXI/raw-socket
// port, commonly 5025). It demonstrates the IDeviceDriver seam — swap the
// virtual model for hardware without changing the signal names, so
// device_manager and the DeviceService see it identically.
//
// Signals consumed (on_signal):
//   psu.<id>.voltage.set   -> VOLT <v>
//   psu.<id>.current.set   -> CURR <v>   (current limit)
//   psu.<id>.enable        -> OUTP ON/OFF
// Signals published (bus_publish):
//   psu.<id>.voltage.meas  <- MEAS:VOLT?
//   psu.<id>.current.meas  <- MEAS:CURR?
//
// All instrument I/O runs on the plugin's own worker thread, so a slow or
// unreachable instrument never stalls the gateway tick loop. This plugin is
// inherently live-only (real hardware): without a reachable instrument it
// simply idles and publishes nothing; it is excluded from the determinism seed
// test and is never a replay target.
//
// Config JSON:  {"id":"main","host":"192.168.0.5","port":5025,"poll_ms":200}

#include <boat/frame.h>
#include <boat/plugin.h>

#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <thread>

#include "device/scpi_device_driver.h"
#include "device/tcp_line_transport.h"

namespace {

std::string CfgStr(const char* cfg, const char* key, std::string def) {
  if (cfg == nullptr) return def;
  const std::string needle = std::string("\"") + key + "\"";
  const char* p = std::strstr(cfg, needle.c_str());
  if (p == nullptr) return def;
  p = std::strchr(p + needle.size(), ':');
  if (p == nullptr) return def;
  ++p;
  while (*p == ' ' || *p == '\t') ++p;
  if (*p != '"') return def;
  ++p;
  const char* end = std::strchr(p, '"');
  if (end == nullptr) return def;
  return std::string(p, static_cast<std::size_t>(end - p));
}

long CfgInt(const char* cfg, const char* key, long def) {
  if (cfg == nullptr) return def;
  const std::string needle = std::string("\"") + key + "\"";
  const char* p = std::strstr(cfg, needle.c_str());
  if (p == nullptr) return def;
  p = std::strchr(p + needle.size(), ':');
  if (p == nullptr) return def;
  ++p;
  while (*p == ' ' || *p == '\t' || *p == '"') ++p;
  return std::strtol(p, nullptr, 0);
}

struct ScpiDevicePlugin {
  std::string id = "main";
  std::string host;
  uint16_t port = 5025;
  int poll_ms = 200;
  int reconnect_ms = 2000;

  std::unique_ptr<boat::hil::ScpiDeviceDriver> driver;

  BoatBusPublishFn bus_publish_fn = nullptr;
  void* bus_ctx = nullptr;

  std::string sig_v_set, sig_i_set, sig_enable, sig_v_meas, sig_i_meas;

  std::mutex mu;
  std::map<std::string, double> pending;  // channel -> setpoint

  std::atomic<bool> running{false};
  std::thread worker;

  void Worker() {
    bool announced = false;
    while (running.load()) {
      if (driver && !driver->IsOpen()) {
        if (!driver->Open()) {
          std::this_thread::sleep_for(std::chrono::milliseconds(reconnect_ms));
          continue;
        }
        std::fprintf(stderr, "[scpi_device] %s connected to %s:%u  idn='%s'\n",
                     id.c_str(), host.c_str(), port, driver->identity().c_str());
        announced = true;
      }
      (void)announced;

      // Apply any pending setpoints.
      std::map<std::string, double> todo;
      {
        std::lock_guard<std::mutex> lock(mu);
        todo.swap(pending);
      }
      for (const auto& [ch, val] : todo) {
        driver->Write(ch, val);
      }

      // Poll measurements and publish.
      if (bus_publish_fn != nullptr) {
        double v = 0.0, i = 0.0;
        if (driver->Read("voltage", v)) {
          bus_publish_fn(bus_ctx, sig_v_meas.c_str(), v);
        }
        if (driver->Read("current", i)) {
          bus_publish_fn(bus_ctx, sig_i_meas.c_str(), i);
        }
      }
      std::this_thread::sleep_for(std::chrono::milliseconds(poll_ms));
    }
  }
};

int scpi_initialize(void* ctx, const char* config_json) {
  auto* p = static_cast<ScpiDevicePlugin*>(ctx);
  const char* cfg = config_json;

  p->id = CfgStr(cfg, "id", "main");
  p->host = CfgStr(cfg, "host", "");
  p->port = static_cast<uint16_t>(CfgInt(cfg, "port", 5025));
  p->poll_ms = static_cast<int>(CfgInt(cfg, "poll_ms", 200));

  const std::string base = "psu." + p->id + ".";
  p->sig_v_set = base + "voltage.set";
  p->sig_i_set = base + "current.set";
  p->sig_enable = base + "enable";
  p->sig_v_meas = base + "voltage.meas";
  p->sig_i_meas = base + "current.meas";

  if (p->host.empty()) {
    std::fprintf(stderr,
                 "[scpi_device] %s: no host configured — idle (no hardware)\n",
                 p->id.c_str());
    return 0;
  }

  p->driver = std::make_unique<boat::hil::ScpiDeviceDriver>(
      std::make_unique<boat::hil::TcpLineTransport>(p->host, p->port),
      boat::hil::ScpiDeviceDriver::PowerSupplyDefaults(), /*read_timeout_ms=*/500);
  p->running.store(true);
  p->worker = std::thread(&ScpiDevicePlugin::Worker, p);
  std::fprintf(stderr, "[scpi_device] %s: connecting to %s:%u (SCPI)\n",
               p->id.c_str(), p->host.c_str(), p->port);
  return 0;
}

void scpi_on_tick(void* /*ctx*/, uint64_t /*tick*/) {}

void scpi_on_signal(void* ctx, const char* name, double value) {
  auto* p = static_cast<ScpiDevicePlugin*>(ctx);
  if (name == nullptr) return;
  std::string channel;
  if (p->sig_v_set == name) channel = "voltage";
  else if (p->sig_i_set == name) channel = "current";
  else if (p->sig_enable == name) channel = "enable";
  else return;
  std::lock_guard<std::mutex> lock(p->mu);
  p->pending[channel] = value;
}

void scpi_set_bus_publisher(void* ctx, BoatBusPublishFn fn, void* publisher_ctx) {
  auto* p = static_cast<ScpiDevicePlugin*>(ctx);
  p->bus_publish_fn = fn;
  p->bus_ctx = publisher_ctx;
}

void scpi_shutdown(void* ctx) {
  auto* p = static_cast<ScpiDevicePlugin*>(ctx);
  p->running.store(false);
  if (p->worker.joinable()) p->worker.join();
  if (p->driver) p->driver->Close();
  std::fprintf(stderr, "[scpi_device] %s shutdown\n", p->id.c_str());
}

}  // namespace

extern "C" BoatPlugin* boat_plugin_create() {
  static BoatPluginVTable kVTable = [] {
    BoatPluginVTable vt{};
    vt.initialize          = &scpi_initialize;
    vt.on_tick             = &scpi_on_tick;
    vt.shutdown            = &scpi_shutdown;
    vt.set_publisher       = nullptr;
    vt.set_bus_publisher   = &scpi_set_bus_publisher;
    vt.set_pdu_publisher   = nullptr;
    vt.on_frame            = nullptr;
    vt.set_frame_publisher = nullptr;
    vt.declared_buses      = nullptr;
    vt.on_signal           = &scpi_on_signal;
    return vt;
  }();

  auto* plugin = new BoatPlugin{};
  plugin->vtable = &kVTable;
  plugin->ctx = new ScpiDevicePlugin{};
  return plugin;
}

extern "C" void boat_plugin_destroy(BoatPlugin* plugin) {
  if (plugin == nullptr) return;
  if (plugin->vtable != nullptr && plugin->vtable->shutdown != nullptr) {
    plugin->vtable->shutdown(plugin->ctx);
  }
  delete static_cast<ScpiDevicePlugin*>(plugin->ctx);
  delete plugin;
}

extern "C" uint32_t boat_plugin_abi_version() { return BOAT_PLUGIN_ABI_VERSION; }
