// virtual_psu — a deterministic virtual bench power supply (v9 ABI).
//
// Models a single-channel programmable DC supply as named signals on the
// always-on signal bus. It consumes setpoint/command signals via on_signal and
// publishes measured voltage/current back via the bus publisher. It touches no
// frame bus — it is pure signal-domain I/O.
//
// Signals consumed (host -> plugin, via on_signal):
//   psu.<id>.voltage.set        target output voltage [V]   (clamped v_min..v_max)
//   psu.<id>.enable             output on/off (0 = off, else on)
//   psu.<id>.load.resistance    load resistance [ohm] (0 = open circuit, I = 0)
//
// Signals published (plugin -> host, via set_bus_publisher):
//   psu.<id>.voltage.meas       measured output voltage [V]
//   psu.<id>.current.meas       measured output current [A] = V / R, i_limit-capped
//
// Determinism: measured voltage ramps toward the target by a fixed per-tick
// delta, so with a fixed tick sequence and fixed setpoints the output is
// bit-identical run to run. Values are published only on change to keep the
// signal bus quiet once settled.
//
// Config JSON (appended to the .so path as ?{...}):
//   {"id":"main","v_set":13.5,"v_min":0,"v_max":60,"ramp_v_per_tick":0.05,
//    "r_load":0,"i_limit":0,"enabled":true}

#include <boat/frame.h>
#include <boat/plugin.h>

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>

namespace {

/* ── tiny config-JSON readers (same lightweight style as the other plugins) ── */

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

double CfgDouble(const char* cfg, const char* key, double def) {
  if (cfg == nullptr) return def;
  const std::string needle = std::string("\"") + key + "\"";
  const char* p = std::strstr(cfg, needle.c_str());
  if (p == nullptr) return def;
  p = std::strchr(p + needle.size(), ':');
  if (p == nullptr) return def;
  ++p;
  while (*p == ' ' || *p == '\t' || *p == '"') ++p;
  char* end = nullptr;
  const double v = std::strtod(p, &end);
  return end == p ? def : v;
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

constexpr double kEps = 1e-6;

/* ── plugin state ───────────────────────────────────────────────────────── */

struct VirtualPsu {
  // config / identity
  std::string id = "main";
  double v_min = 0.0;
  double v_max = 60.0;
  double ramp_v_per_tick = 0.05;
  double i_limit = 0.0;  // 0 = unlimited

  // resolved signal names (built once in initialize)
  std::string sig_v_set, sig_enable, sig_r_load;   // inputs
  std::string sig_v_meas, sig_i_meas;              // outputs

  // wiring
  BoatBusPublishFn bus_publish_fn = nullptr;
  void* bus_publisher_ctx = nullptr;

  // state (guarded by mu; never held across bus_publish_fn)
  std::mutex mu;
  bool enabled = true;
  double v_target = 13.5;
  double r_load = 0.0;   // 0 = open circuit
  double v_meas = 0.0;
  double i_meas = 0.0;
  double last_v_pub = std::nan("");
  double last_i_pub = std::nan("");

  double Clamp(double v) const {
    if (v < v_min) return v_min;
    if (v > v_max) return v_max;
    return v;
  }
};

/* ── vtable implementations ─────────────────────────────────────────────── */

int psu_initialize(void* ctx, const char* config_json) {
  auto* p = static_cast<VirtualPsu*>(ctx);
  const char* cfg = config_json;

  p->id = CfgStr(cfg, "id", "main");
  p->v_min = CfgDouble(cfg, "v_min", 0.0);
  p->v_max = CfgDouble(cfg, "v_max", 60.0);
  p->ramp_v_per_tick = CfgDouble(cfg, "ramp_v_per_tick", 0.05);
  p->i_limit = CfgDouble(cfg, "i_limit", 0.0);
  p->enabled = CfgBool(cfg, "enabled", true);
  p->v_target = p->Clamp(CfgDouble(cfg, "v_set", 13.5));
  p->r_load = CfgDouble(cfg, "r_load", 0.0);

  const std::string base = "psu." + p->id + ".";
  p->sig_v_set  = base + "voltage.set";
  p->sig_enable = base + "enable";
  p->sig_r_load = base + "load.resistance";
  p->sig_v_meas = base + "voltage.meas";
  p->sig_i_meas = base + "current.meas";

  std::fprintf(stderr,
               "[virtual_psu] init id=%s v_set=%.3f range=[%.1f,%.1f] "
               "ramp=%.4f V/tick i_limit=%.3f r_load=%.3f enabled=%d\n",
               p->id.c_str(), p->v_target, p->v_min, p->v_max,
               p->ramp_v_per_tick, p->i_limit, p->r_load, p->enabled ? 1 : 0);
  return 0;
}

void psu_on_signal(void* ctx, const char* name, double value) {
  auto* p = static_cast<VirtualPsu*>(ctx);
  if (name == nullptr) return;
  // Filter by name *before* locking — this also makes our own published
  // .meas signals (which loop back through the bus) a cheap no-op and avoids
  // any re-entrant lock acquisition.
  std::lock_guard<std::mutex> lock(p->mu);
  if (p->sig_v_set == name) {
    p->v_target = p->Clamp(value);
  } else if (p->sig_enable == name) {
    p->enabled = (value != 0.0);
  } else if (p->sig_r_load == name) {
    p->r_load = value < 0.0 ? 0.0 : value;
  }
}

void psu_on_tick(void* ctx, uint64_t /*tick*/) {
  auto* p = static_cast<VirtualPsu*>(ctx);

  double v_pub = 0.0, i_pub = 0.0;
  bool changed = false;
  {
    std::lock_guard<std::mutex> lock(p->mu);
    const double target = p->enabled ? p->v_target : 0.0;
    // Ramp toward target by a fixed per-tick step (deterministic).
    const double delta = target - p->v_meas;
    if (std::fabs(delta) <= p->ramp_v_per_tick) {
      p->v_meas = target;
    } else {
      p->v_meas += (delta > 0.0 ? p->ramp_v_per_tick : -p->ramp_v_per_tick);
    }
    // Ohm's law across the load; open circuit (R=0) draws no current.
    p->i_meas = (p->r_load > 0.0) ? (p->v_meas / p->r_load) : 0.0;
    if (p->i_limit > 0.0 && p->i_meas > p->i_limit) p->i_meas = p->i_limit;

    if (std::isnan(p->last_v_pub) || std::fabs(p->v_meas - p->last_v_pub) > kEps ||
        std::isnan(p->last_i_pub) || std::fabs(p->i_meas - p->last_i_pub) > kEps) {
      changed = true;
      p->last_v_pub = p->v_meas;
      p->last_i_pub = p->i_meas;
      v_pub = p->v_meas;
      i_pub = p->i_meas;
    }
  }

  // Publish outside the lock — bus_publish_fn dispatches synchronously and may
  // re-enter this plugin's on_signal (for the .meas echo), which locks mu.
  if (changed && p->bus_publish_fn != nullptr) {
    p->bus_publish_fn(p->bus_publisher_ctx, p->sig_v_meas.c_str(), v_pub);
    p->bus_publish_fn(p->bus_publisher_ctx, p->sig_i_meas.c_str(), i_pub);
  }
}

void psu_set_bus_publisher(void* ctx, BoatBusPublishFn fn, void* publisher_ctx) {
  auto* p = static_cast<VirtualPsu*>(ctx);
  p->bus_publish_fn = fn;
  p->bus_publisher_ctx = publisher_ctx;
}

void psu_shutdown(void* ctx) {
  auto* p = static_cast<VirtualPsu*>(ctx);
  std::lock_guard<std::mutex> lock(p->mu);
  std::fprintf(stderr, "[virtual_psu] shutdown id=%s v_meas=%.3f i_meas=%.3f\n",
               p->id.c_str(), p->v_meas, p->i_meas);
}

}  // namespace

/* ── C ABI entry points ─────────────────────────────────────────────────── */

extern "C" BoatPlugin* boat_plugin_create() {
  static BoatPluginVTable kVTable = [] {
    BoatPluginVTable vt{};
    vt.initialize          = &psu_initialize;
    vt.on_tick             = &psu_on_tick;
    vt.shutdown            = &psu_shutdown;
    vt.set_publisher       = nullptr;
    vt.set_bus_publisher   = &psu_set_bus_publisher;
    vt.set_pdu_publisher   = nullptr;
    vt.on_frame            = nullptr;   // signal-domain only; no frame traffic
    vt.set_frame_publisher = nullptr;
    vt.declared_buses      = nullptr;
    vt.on_signal           = &psu_on_signal;
    return vt;
  }();

  auto* plugin = new BoatPlugin{};
  plugin->vtable = &kVTable;
  plugin->ctx = new VirtualPsu{};
  return plugin;
}

extern "C" void boat_plugin_destroy(BoatPlugin* plugin) {
  if (plugin == nullptr) return;
  if (plugin->vtable != nullptr && plugin->vtable->shutdown != nullptr) {
    plugin->vtable->shutdown(plugin->ctx);
  }
  delete static_cast<VirtualPsu*>(plugin->ctx);
  delete plugin;
}

extern "C" uint32_t boat_plugin_abi_version() { return BOAT_PLUGIN_ABI_VERSION; }
