// gpio_relay — a physical relay on a GPIO line (v9 ABI).
//
// Same signal contract as virtual_relay, backed by a real GPIO line via
// GpioRelayDriver behind the shared DeviceRunner. Live-only: without the GPIO
// chip/line it fails to open and idles; excluded from the determinism seed test
// and never a replay target.
//
// Signals: relay.<id>.set in; relay.<id>.state out.
//
// Config JSON:  {"id":"kl15","chip":"/dev/gpiochip0","line":17,"active_low":false,"poll_ms":100}

#include <boat/frame.h>
#include <boat/plugin.h>

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <string>

#include "device/device_runner.h"
#include "device/gpio_relay_driver.h"

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

bool CfgBool(const char* cfg, const char* key, bool def) {
  if (cfg == nullptr) return def;
  const std::string needle = std::string("\"") + key + "\"";
  const char* p = std::strstr(cfg, needle.c_str());
  if (p == nullptr) return def;
  p = std::strchr(p + needle.size(), ':');
  if (p == nullptr) return def;
  ++p;
  while (*p == ' ' || *p == '\t' || *p == '"') ++p;
  return std::strncmp(p, "true", 4) == 0;
}

struct GpioRelayPlugin {
  std::string id = "kl15";
  std::unique_ptr<boat::hil::DeviceRunner> runner;
};

int gr_initialize(void* ctx, const char* cfg) {
  auto* p = static_cast<GpioRelayPlugin*>(ctx);
  p->id = CfgStr(cfg, "id", "kl15");
  const std::string chip = CfgStr(cfg, "chip", "/dev/gpiochip0");
  const auto line = static_cast<unsigned int>(CfgInt(cfg, "line", 0));
  const bool active_low = CfgBool(cfg, "active_low", false);
  const int poll_ms = static_cast<int>(CfgInt(cfg, "poll_ms", 100));

  const std::string base = "relay." + p->id + ".";
  boat::hil::DeviceRunner::Channels ch;
  ch.set_map = {{base + "set", "state"}};
  ch.meas_map = {{"state", base + "state"}};

  auto driver = std::make_unique<boat::hil::GpioRelayDriver>(chip, line, active_low);
  p->runner = std::make_unique<boat::hil::DeviceRunner>(std::move(driver),
                                                        std::move(ch), poll_ms);
  p->runner->Start();
  std::fprintf(stderr, "[gpio_relay] %s: %s line %u%s\n", p->id.c_str(),
               chip.c_str(), line, active_low ? " (active-low)" : "");
  return 0;
}

void gr_on_tick(void*, uint64_t) {}

void gr_on_signal(void* ctx, const char* name, double value) {
  auto* p = static_cast<GpioRelayPlugin*>(ctx);
  if (p->runner) p->runner->OnSignal(name, value);
}

void gr_set_bus_publisher(void* ctx, BoatBusPublishFn fn, void* pctx) {
  auto* p = static_cast<GpioRelayPlugin*>(ctx);
  if (p->runner) p->runner->SetBusPublisher(fn, pctx);
}

void gr_shutdown(void* ctx) {
  auto* p = static_cast<GpioRelayPlugin*>(ctx);
  if (p->runner) p->runner->Stop();
  std::fprintf(stderr, "[gpio_relay] %s shutdown\n", p->id.c_str());
}

}  // namespace

extern "C" BoatPlugin* boat_plugin_create() {
  static BoatPluginVTable kVTable = [] {
    BoatPluginVTable vt{};
    vt.initialize        = &gr_initialize;
    vt.on_tick           = &gr_on_tick;
    vt.shutdown          = &gr_shutdown;
    vt.set_bus_publisher = &gr_set_bus_publisher;
    vt.on_signal         = &gr_on_signal;
    return vt;
  }();
  auto* plugin = new BoatPlugin{};
  plugin->vtable = &kVTable;
  plugin->ctx = new GpioRelayPlugin{};
  return plugin;
}

extern "C" void boat_plugin_destroy(BoatPlugin* plugin) {
  if (plugin == nullptr) return;
  if (plugin->vtable != nullptr && plugin->vtable->shutdown != nullptr) {
    plugin->vtable->shutdown(plugin->ctx);
  }
  delete static_cast<GpioRelayPlugin*>(plugin->ctx);
  delete plugin;
}

extern "C" uint32_t boat_plugin_abi_version() { return BOAT_PLUGIN_ABI_VERSION; }
