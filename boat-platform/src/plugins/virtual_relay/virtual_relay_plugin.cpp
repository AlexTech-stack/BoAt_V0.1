// virtual_relay — a deterministic virtual relay / contactor (v9 ABI).
//
// Models a single relay (e.g. an ignition KL15 or main-power KL30 contactor) as
// named signals on the always-on signal bus. It consumes a coil-command signal
// via on_signal and publishes the settled contact state via the bus publisher.
// It touches no frame bus — pure signal-domain I/O.
//
// Signals consumed (host -> plugin, via on_signal):
//   relay.<id>.set      commanded coil state (0 = open, else closed)
//
// Signals published (plugin -> host, via set_bus_publisher):
//   relay.<id>.state    settled contact state (0.0 = open, 1.0 = closed)
//
// A configurable debounce (in ticks) models contact settling: the output only
// follows the command after it has been stable for debounce_ticks. Determinism
// holds because settling is counted in ticks, not wall-clock time.
//
// Config JSON (appended to the .so path as ?{...}):
//   {"id":"kl15","default_closed":false,"debounce_ticks":0}

#include <boat/frame.h>
#include <boat/plugin.h>

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>

namespace {

/* ── tiny config-JSON readers ───────────────────────────────────────────── */

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
  if (std::strncmp(p, "true", 4) == 0) return true;
  if (std::strncmp(p, "false", 5) == 0) return false;
  return std::strtod(p, nullptr) != 0.0;
}

/* ── plugin state ───────────────────────────────────────────────────────── */

struct VirtualRelay {
  std::string id = "kl15";
  uint64_t debounce_ticks = 0;

  std::string sig_set;    // input:  relay.<id>.set
  std::string sig_state;  // output: relay.<id>.state

  BoatBusPublishFn bus_publish_fn = nullptr;
  void* bus_publisher_ctx = nullptr;

  // state (guarded by mu; never held across bus_publish_fn)
  std::mutex mu;
  bool cmd_closed = false;      // last commanded state
  bool contact_closed = false;  // settled contact state
  uint64_t stable_ticks = 0;    // ticks since command last matched contact
  bool published = false;       // has an initial state been emitted?
};

/* ── vtable implementations ─────────────────────────────────────────────── */

int relay_initialize(void* ctx, const char* config_json) {
  auto* p = static_cast<VirtualRelay*>(ctx);
  const char* cfg = config_json;

  p->id = CfgStr(cfg, "id", "kl15");
  p->debounce_ticks = static_cast<uint64_t>(CfgInt(cfg, "debounce_ticks", 0));
  const bool def_closed = CfgBool(cfg, "default_closed", false);
  p->cmd_closed = def_closed;
  p->contact_closed = def_closed;

  p->sig_set   = "relay." + p->id + ".set";
  p->sig_state = "relay." + p->id + ".state";

  std::fprintf(stderr,
               "[virtual_relay] init id=%s default_closed=%d debounce=%llu ticks\n",
               p->id.c_str(), def_closed ? 1 : 0,
               (unsigned long long)p->debounce_ticks);
  return 0;
}

void relay_on_signal(void* ctx, const char* name, double value) {
  auto* p = static_cast<VirtualRelay*>(ctx);
  if (name == nullptr) return;
  std::lock_guard<std::mutex> lock(p->mu);
  if (p->sig_set == name) {
    const bool want = (value != 0.0);
    if (want != p->cmd_closed) {
      p->cmd_closed = want;
      p->stable_ticks = 0;  // restart debounce window
    }
  }
}

void relay_on_tick(void* ctx, uint64_t /*tick*/) {
  auto* p = static_cast<VirtualRelay*>(ctx);

  bool do_publish = false;
  double state_val = 0.0;
  {
    std::lock_guard<std::mutex> lock(p->mu);
    // Settle the contact once the command has held for debounce_ticks.
    if (p->cmd_closed != p->contact_closed) {
      if (p->stable_ticks >= p->debounce_ticks) {
        p->contact_closed = p->cmd_closed;
        do_publish = true;
      } else {
        ++p->stable_ticks;
      }
    }
    if (!p->published) {  // emit the initial state once
      do_publish = true;
      p->published = true;
    }
    state_val = p->contact_closed ? 1.0 : 0.0;
  }

  if (do_publish && p->bus_publish_fn != nullptr) {
    p->bus_publish_fn(p->bus_publisher_ctx, p->sig_state.c_str(), state_val);
  }
}

void relay_set_bus_publisher(void* ctx, BoatBusPublishFn fn, void* publisher_ctx) {
  auto* p = static_cast<VirtualRelay*>(ctx);
  p->bus_publish_fn = fn;
  p->bus_publisher_ctx = publisher_ctx;
}

void relay_shutdown(void* ctx) {
  auto* p = static_cast<VirtualRelay*>(ctx);
  std::lock_guard<std::mutex> lock(p->mu);
  std::fprintf(stderr, "[virtual_relay] shutdown id=%s contact=%s\n",
               p->id.c_str(), p->contact_closed ? "closed" : "open");
}

}  // namespace

/* ── C ABI entry points ─────────────────────────────────────────────────── */

extern "C" BoatPlugin* boat_plugin_create() {
  static BoatPluginVTable kVTable = [] {
    BoatPluginVTable vt{};
    vt.initialize          = &relay_initialize;
    vt.on_tick             = &relay_on_tick;
    vt.shutdown            = &relay_shutdown;
    vt.set_publisher       = nullptr;
    vt.set_bus_publisher   = &relay_set_bus_publisher;
    vt.set_pdu_publisher   = nullptr;
    vt.on_frame            = nullptr;
    vt.set_frame_publisher = nullptr;
    vt.declared_buses      = nullptr;
    vt.on_signal           = &relay_on_signal;
    return vt;
  }();

  auto* plugin = new BoatPlugin{};
  plugin->vtable = &kVTable;
  plugin->ctx = new VirtualRelay{};
  return plugin;
}

extern "C" void boat_plugin_destroy(BoatPlugin* plugin) {
  if (plugin == nullptr) return;
  if (plugin->vtable != nullptr && plugin->vtable->shutdown != nullptr) {
    plugin->vtable->shutdown(plugin->ctx);
  }
  delete static_cast<VirtualRelay*>(plugin->ctx);
  delete plugin;
}

extern "C" uint32_t boat_plugin_abi_version() { return BOAT_PLUGIN_ABI_VERSION; }
