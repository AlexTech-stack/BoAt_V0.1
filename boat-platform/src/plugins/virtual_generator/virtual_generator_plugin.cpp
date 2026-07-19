// virtual_generator — a deterministic virtual alternator / generator (v9 ABI).
//
// Models an engine-driven alternator as named signals on the always-on signal
// bus: an RPM setpoint in, a regulated output voltage (and optional load
// current) out. Like virtual_psu it is pure signal-domain I/O — no frame bus.
//
// Signals consumed (on_signal):
//   gen.<id>.rpm.set          target shaft speed [rpm] (ramp-limited)
//   gen.<id>.enable           field on/off (0 = off, else on)
//   gen.<id>.load.resistance  load resistance [ohm] (0 = open, I = 0)
//
// Signals published (set_bus_publisher):
//   gen.<id>.rpm.meas             measured shaft speed [rpm]
//   gen.<id>.output_voltage.meas  regulated output [V]
//   gen.<id>.output_current.meas  V / R across the load [A]
//
// Voltage model (deterministic): below cut-in rpm the output rises
// proportionally from 0 to v_rest; above cut-in it climbs linearly to
// v_regulated at rated rpm and is clamped there — a simple regulated-alternator
// curve. RPM ramps toward the target by a fixed per-tick step.
//
// Config JSON:
//   {"id":"alt","rpm_set":0,"rpm_per_tick":20,"cut_in_rpm":800,"rated_rpm":6000,
//    "v_rest":12.6,"v_regulated":14.2,"r_load":0,"enabled":true}

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

struct VirtualGenerator {
  std::string id = "alt";
  double rpm_per_tick = 20.0;
  double cut_in_rpm = 800.0;
  double rated_rpm = 6000.0;
  double v_rest = 12.6;
  double v_regulated = 14.2;

  std::string sig_rpm_set, sig_enable, sig_r_load;              // inputs
  std::string sig_rpm_meas, sig_v_meas, sig_i_meas;            // outputs

  BoatBusPublishFn bus_publish_fn = nullptr;
  void* bus_publisher_ctx = nullptr;

  std::mutex mu;
  bool enabled = true;
  double rpm_target = 0.0;
  double r_load = 0.0;
  double rpm_meas = 0.0;
  double v_meas = 0.0;
  double i_meas = 0.0;
  double last_rpm_pub = std::nan("");
  double last_v_pub = std::nan("");
  double last_i_pub = std::nan("");

  double VoltageFor(double rpm) const {
    if (!enabled || rpm <= 0.0) return 0.0;
    if (rpm < cut_in_rpm) return v_rest * (rpm / cut_in_rpm);
    const double span = (rated_rpm > cut_in_rpm) ? (rated_rpm - cut_in_rpm) : 1.0;
    const double v = v_rest + (rpm - cut_in_rpm) / span * (v_regulated - v_rest);
    return v > v_regulated ? v_regulated : v;
  }
};

int gen_initialize(void* ctx, const char* config_json) {
  auto* p = static_cast<VirtualGenerator*>(ctx);
  const char* cfg = config_json;

  p->id = CfgStr(cfg, "id", "alt");
  p->rpm_per_tick = CfgDouble(cfg, "rpm_per_tick", 20.0);
  p->cut_in_rpm = CfgDouble(cfg, "cut_in_rpm", 800.0);
  p->rated_rpm = CfgDouble(cfg, "rated_rpm", 6000.0);
  p->v_rest = CfgDouble(cfg, "v_rest", 12.6);
  p->v_regulated = CfgDouble(cfg, "v_regulated", 14.2);
  p->enabled = CfgBool(cfg, "enabled", true);
  p->rpm_target = CfgDouble(cfg, "rpm_set", 0.0);
  p->r_load = CfgDouble(cfg, "r_load", 0.0);

  const std::string base = "gen." + p->id + ".";
  p->sig_rpm_set = base + "rpm.set";
  p->sig_enable = base + "enable";
  p->sig_r_load = base + "load.resistance";
  p->sig_rpm_meas = base + "rpm.meas";
  p->sig_v_meas = base + "output_voltage.meas";
  p->sig_i_meas = base + "output_current.meas";

  std::fprintf(stderr,
               "[virtual_generator] init id=%s rpm_set=%.0f cut_in=%.0f rated=%.0f "
               "v_rest=%.2f v_reg=%.2f r_load=%.3f enabled=%d\n",
               p->id.c_str(), p->rpm_target, p->cut_in_rpm, p->rated_rpm,
               p->v_rest, p->v_regulated, p->r_load, p->enabled ? 1 : 0);
  return 0;
}

void gen_on_signal(void* ctx, const char* name, double value) {
  auto* p = static_cast<VirtualGenerator*>(ctx);
  if (name == nullptr) return;
  std::lock_guard<std::mutex> lock(p->mu);
  if (p->sig_rpm_set == name) {
    p->rpm_target = value < 0.0 ? 0.0 : value;
  } else if (p->sig_enable == name) {
    p->enabled = (value != 0.0);
  } else if (p->sig_r_load == name) {
    p->r_load = value < 0.0 ? 0.0 : value;
  }
}

void gen_on_tick(void* ctx, uint64_t /*tick*/) {
  auto* p = static_cast<VirtualGenerator*>(ctx);

  double rpm_pub = 0.0, v_pub = 0.0, i_pub = 0.0;
  bool changed = false;
  {
    std::lock_guard<std::mutex> lock(p->mu);
    const double target = p->enabled ? p->rpm_target : 0.0;
    const double delta = target - p->rpm_meas;
    if (std::fabs(delta) <= p->rpm_per_tick) {
      p->rpm_meas = target;
    } else {
      p->rpm_meas += (delta > 0.0 ? p->rpm_per_tick : -p->rpm_per_tick);
    }
    p->v_meas = p->VoltageFor(p->rpm_meas);
    p->i_meas = (p->r_load > 0.0) ? (p->v_meas / p->r_load) : 0.0;

    if (std::isnan(p->last_rpm_pub) || std::fabs(p->rpm_meas - p->last_rpm_pub) > kEps ||
        std::isnan(p->last_v_pub) || std::fabs(p->v_meas - p->last_v_pub) > kEps ||
        std::isnan(p->last_i_pub) || std::fabs(p->i_meas - p->last_i_pub) > kEps) {
      changed = true;
      p->last_rpm_pub = p->rpm_meas;
      p->last_v_pub = p->v_meas;
      p->last_i_pub = p->i_meas;
      rpm_pub = p->rpm_meas;
      v_pub = p->v_meas;
      i_pub = p->i_meas;
    }
  }

  if (changed && p->bus_publish_fn != nullptr) {
    p->bus_publish_fn(p->bus_publisher_ctx, p->sig_rpm_meas.c_str(), rpm_pub);
    p->bus_publish_fn(p->bus_publisher_ctx, p->sig_v_meas.c_str(), v_pub);
    p->bus_publish_fn(p->bus_publisher_ctx, p->sig_i_meas.c_str(), i_pub);
  }
}

void gen_set_bus_publisher(void* ctx, BoatBusPublishFn fn, void* publisher_ctx) {
  auto* p = static_cast<VirtualGenerator*>(ctx);
  p->bus_publish_fn = fn;
  p->bus_publisher_ctx = publisher_ctx;
}

void gen_shutdown(void* ctx) {
  auto* p = static_cast<VirtualGenerator*>(ctx);
  std::lock_guard<std::mutex> lock(p->mu);
  std::fprintf(stderr, "[virtual_generator] shutdown id=%s rpm=%.0f v=%.2f\n",
               p->id.c_str(), p->rpm_meas, p->v_meas);
}

}  // namespace

extern "C" BoatPlugin* boat_plugin_create() {
  static BoatPluginVTable kVTable = [] {
    BoatPluginVTable vt{};
    vt.initialize          = &gen_initialize;
    vt.on_tick             = &gen_on_tick;
    vt.shutdown            = &gen_shutdown;
    vt.set_publisher       = nullptr;
    vt.set_bus_publisher   = &gen_set_bus_publisher;
    vt.set_pdu_publisher   = nullptr;
    vt.on_frame            = nullptr;
    vt.set_frame_publisher = nullptr;
    vt.declared_buses      = nullptr;
    vt.on_signal           = &gen_on_signal;
    return vt;
  }();

  auto* plugin = new BoatPlugin{};
  plugin->vtable = &kVTable;
  plugin->ctx = new VirtualGenerator{};
  return plugin;
}

extern "C" void boat_plugin_destroy(BoatPlugin* plugin) {
  if (plugin == nullptr) return;
  if (plugin->vtable != nullptr && plugin->vtable->shutdown != nullptr) {
    plugin->vtable->shutdown(plugin->ctx);
  }
  delete static_cast<VirtualGenerator*>(plugin->ctx);
  delete plugin;
}

extern "C" uint32_t boat_plugin_abi_version() { return BOAT_PLUGIN_ABI_VERSION; }
