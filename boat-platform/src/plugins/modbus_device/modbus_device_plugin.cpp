// modbus_device — a physical power supply / e-load over Modbus-TCP (v9 ABI).
//
// Same signal contract as virtual_psu / scpi_device, backed by a Modbus-TCP
// instrument via ModbusTcpDeviceDriver. Demonstrates a second IDeviceDriver
// backend behind the shared DeviceRunner. Live-only: idles without a reachable
// instrument; excluded from the determinism seed test; never a replay target.
//
// Signals: psu.<id>.voltage.set / .current.set / .enable in;
//          psu.<id>.voltage.meas / .current.meas out.
//
// Config JSON:  {"id":"main","host":"192.168.0.7","port":502,"unit":1,"poll_ms":200}

#include <boat/frame.h>
#include <boat/plugin.h>

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <string>

#include "device/device_runner.h"
#include "device/modbus_tcp_driver.h"

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

struct ModbusDevicePlugin {
  std::string id = "main";
  std::unique_ptr<boat::hil::DeviceRunner> runner;
};

int mb_initialize(void* ctx, const char* cfg) {
  auto* p = static_cast<ModbusDevicePlugin*>(ctx);
  p->id = CfgStr(cfg, "id", "main");
  const std::string host = CfgStr(cfg, "host", "");
  const auto port = static_cast<uint16_t>(CfgInt(cfg, "port", 502));
  const auto unit = static_cast<uint8_t>(CfgInt(cfg, "unit", 1));
  const int poll_ms = static_cast<int>(CfgInt(cfg, "poll_ms", 200));

  const std::string base = "psu." + p->id + ".";
  boat::hil::DeviceRunner::Channels ch;
  ch.set_map = {
      {base + "voltage.set", "voltage"},
      {base + "current.set", "current"},
      {base + "enable", "enable"},
  };
  ch.meas_map = {
      {"voltage", base + "voltage.meas"},
      {"current", base + "current.meas"},
  };

  if (host.empty()) {
    std::fprintf(stderr, "[modbus_device] %s: no host configured — idle\n",
                 p->id.c_str());
    return 0;
  }
  auto driver = std::make_unique<boat::hil::ModbusTcpDeviceDriver>(
      host, port, boat::hil::ModbusTcpDeviceDriver::PowerSupplyDefaults(), unit);
  p->runner = std::make_unique<boat::hil::DeviceRunner>(std::move(driver),
                                                        std::move(ch), poll_ms);
  p->runner->Start();
  std::fprintf(stderr, "[modbus_device] %s: connecting to %s:%u (Modbus, unit=%u)\n",
               p->id.c_str(), host.c_str(), port, unit);
  return 0;
}

void mb_on_tick(void*, uint64_t) {}

void mb_on_signal(void* ctx, const char* name, double value) {
  auto* p = static_cast<ModbusDevicePlugin*>(ctx);
  if (p->runner) p->runner->OnSignal(name, value);
}

void mb_set_bus_publisher(void* ctx, BoatBusPublishFn fn, void* pctx) {
  auto* p = static_cast<ModbusDevicePlugin*>(ctx);
  if (p->runner) p->runner->SetBusPublisher(fn, pctx);
}

void mb_shutdown(void* ctx) {
  auto* p = static_cast<ModbusDevicePlugin*>(ctx);
  if (p->runner) p->runner->Stop();
  std::fprintf(stderr, "[modbus_device] %s shutdown\n", p->id.c_str());
}

}  // namespace

extern "C" BoatPlugin* boat_plugin_create() {
  static BoatPluginVTable kVTable = [] {
    BoatPluginVTable vt{};
    vt.initialize        = &mb_initialize;
    vt.on_tick           = &mb_on_tick;
    vt.shutdown          = &mb_shutdown;
    vt.set_bus_publisher = &mb_set_bus_publisher;
    vt.on_signal         = &mb_on_signal;
    return vt;
  }();
  auto* plugin = new BoatPlugin{};
  plugin->vtable = &kVTable;
  plugin->ctx = new ModbusDevicePlugin{};
  return plugin;
}

extern "C" void boat_plugin_destroy(BoatPlugin* plugin) {
  if (plugin == nullptr) return;
  if (plugin->vtable != nullptr && plugin->vtable->shutdown != nullptr) {
    plugin->vtable->shutdown(plugin->ctx);
  }
  delete static_cast<ModbusDevicePlugin*>(plugin->ctx);
  delete plugin;
}

extern "C" uint32_t boat_plugin_abi_version() { return BOAT_PLUGIN_ABI_VERSION; }
