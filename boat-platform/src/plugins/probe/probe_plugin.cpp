// probe_plugin — a gateway conformance probe (v8 ABI).
//
// Purpose: verify the gateway's frame plumbing from *inside* the dispatch loop,
// which an external gRPC client cannot observe. It checks the invariants the v8
// refactor rests on:
//
//   1. Delivery      — on_frame fires for the bus types we declared.
//   2. Filtering     — on_frame does NOT fire for undeclared buses
//                      (any such delivery increments `unexpected_bus`, which
//                      should stay 0 if declared_buses pre-filtering works).
//   3. Self-sent tag — a frame we publish comes back through DispatchRx with
//                      SELF_SENT set; genuine wire RX frames do not.
//   4. Round-trip    — active mode injects a uniquely-tagged CAN frame and
//                      asserts the self-sent echo arrives within a timeout.
//
// Modes (config "mode"): "passive" observes/classifies only; "active" also
// injects probes; "both" (default) does both.
//
// Reporting: periodic stderr summary + PASS/FAIL lines, AND live counters
// published to the signal bus (probe.*) for `boat` / dashboards.
//
// Config JSON (appended to the .so path as ?{...}):
//   {"iface":"vcan0","buses":["can"],"mode":"both","probe_id":"0x7FF",
//    "probe_period_ticks":1000,"echo_timeout_ticks":50,"report_period_ticks":5000}

#include <boat/frame.h>
#include <boat/plugin.h>

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include <vector>

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

long CfgInt(const char* cfg, const char* key, long def) {
  if (cfg == nullptr) return def;
  const std::string needle = std::string("\"") + key + "\"";
  const char* p = std::strstr(cfg, needle.c_str());
  if (p == nullptr) return def;
  p = std::strchr(p + needle.size(), ':');
  if (p == nullptr) return def;
  ++p;
  while (*p == ' ' || *p == '\t' || *p == '"') ++p;
  return std::strtol(p, nullptr, 0);  // base 0 → accepts 0x.. and decimal
}

bool CfgHas(const char* cfg, const char* token) {
  return cfg != nullptr && std::strstr(cfg, token) != nullptr;
}

/* ── plugin state ───────────────────────────────────────────────────────── */

struct ProbePlugin {
  // config
  std::string iface = "vcan0";
  std::string declared_str = "[\"can\"]";  // returned verbatim by declared_buses
  bool decl_can = true, decl_canfd = false, decl_eth = false,
       decl_tcp = false, decl_pdu = false;
  bool active = true;
  uint32_t probe_id = 0x7FF;
  uint64_t probe_period_ticks = 1000;
  uint64_t echo_timeout_ticks = 50;
  uint64_t report_period_ticks = 5000;

  // wiring
  BoatFramePublishFn frame_publish_fn = nullptr;
  void* frame_publisher_ctx = nullptr;
  BoatBusPublishFn bus_publish_fn = nullptr;
  void* bus_publisher_ctx = nullptr;

  // state (guarded by mu; never held across frame_publish_fn)
  std::mutex mu;
  uint64_t tick = 0;
  uint64_t last_report_tick = 0;
  // counters
  uint64_t rx_total = 0, rx_self_sent = 0, rx_wire = 0;
  uint64_t rx_by_bus[6] = {0, 0, 0, 0, 0, 0};
  uint64_t unexpected_bus = 0;
  uint64_t probes_sent = 0, self_echoes = 0, wire_echoes = 0;
  uint64_t checks_pass = 0, checks_fail = 0;
  // active-mode pending probe
  bool pending = false;
  uint16_t pending_seq = 0;
  uint64_t pending_sent_tick = 0;
  uint16_t next_seq = 1;

  bool ExpectedBus(BoatBusType bus) const {
    switch (bus) {
      case BOAT_BUS_CAN:      return decl_can;
      case BOAT_BUS_CANFD:    return decl_can || decl_canfd;
      case BOAT_BUS_ETHERNET: return decl_eth;
      case BOAT_BUS_TCP:      return decl_tcp;
      case BOAT_BUS_PDU:      return decl_pdu;
      default:                return false;
    }
  }
};

void PublishSignals(ProbePlugin* p) {
  if (p->bus_publish_fn == nullptr) return;
  const auto emit = [&](const char* name, uint64_t v) {
    p->bus_publish_fn(p->bus_publisher_ctx, name, static_cast<double>(v));
  };
  emit("probe.rx_total", p->rx_total);
  emit("probe.rx_self_sent", p->rx_self_sent);
  emit("probe.rx_wire", p->rx_wire);
  emit("probe.unexpected_bus", p->unexpected_bus);
  emit("probe.probes_sent", p->probes_sent);
  emit("probe.self_echoes", p->self_echoes);
  emit("probe.wire_echoes", p->wire_echoes);
  emit("probe.checks_pass", p->checks_pass);
  emit("probe.checks_fail", p->checks_fail);
}

/* ── vtable implementations ─────────────────────────────────────────────── */

int probe_initialize(void* ctx, const char* config_json) {
  auto* p = static_cast<ProbePlugin*>(ctx);
  const char* cfg = config_json;

  p->iface = CfgStr(cfg, "iface", "vcan0");
  p->probe_id = static_cast<uint32_t>(CfgInt(cfg, "probe_id", 0x7FF));
  p->probe_period_ticks =
      static_cast<uint64_t>(CfgInt(cfg, "probe_period_ticks", 1000));
  p->echo_timeout_ticks =
      static_cast<uint64_t>(CfgInt(cfg, "echo_timeout_ticks", 50));
  p->report_period_ticks =
      static_cast<uint64_t>(CfgInt(cfg, "report_period_ticks", 5000));

  const std::string mode = CfgStr(cfg, "mode", "both");
  p->active = (mode == "active" || mode == "both");

  // declared buses: default ["can"] when no "buses" key is given.
  const bool has_buses = CfgHas(cfg, "\"buses\"");
  if (has_buses) {
    p->decl_canfd = CfgHas(cfg, "\"canfd\"");
    p->decl_can   = CfgHas(cfg, "\"can\"");
    p->decl_eth   = CfgHas(cfg, "\"eth\"");
    p->decl_tcp   = CfgHas(cfg, "\"tcp\"");
    p->decl_pdu   = CfgHas(cfg, "\"pdu\"");
  }
  std::string s = "[";
  bool first = true;
  const auto add = [&](const char* n) {
    if (!first) s += ",";
    s += "\"";
    s += n;
    s += "\"";
    first = false;
  };
  if (p->decl_can) add("can");
  if (p->decl_canfd) add("canfd");
  if (p->decl_eth) add("eth");
  if (p->decl_tcp) add("tcp");
  if (p->decl_pdu) add("pdu");
  if (first) {  // nothing recognized → fall back to can
    add("can");
    p->decl_can = true;
  }
  s += "]";
  p->declared_str = s;

  std::fprintf(stderr,
               "[probe] init iface=%s buses=%s mode=%s probe_id=0x%X "
               "period=%llu ticks timeout=%llu report=%llu\n",
               p->iface.c_str(), p->declared_str.c_str(),
               p->active ? "active" : "passive", p->probe_id,
               (unsigned long long)p->probe_period_ticks,
               (unsigned long long)p->echo_timeout_ticks,
               (unsigned long long)p->report_period_ticks);
  return 0;
}

void probe_on_frame(void* ctx, const BoatFrame* frame) {
  auto* p = static_cast<ProbePlugin*>(ctx);
  if (frame == nullptr) return;

  const BoatBusType bus = frame->bus_type;
  bool self_sent = false;
  bool marker = false;
  uint16_t seq = 0;
  if (bus == BOAT_BUS_CAN || bus == BOAT_BUS_CANFD) {
    self_sent = (frame->meta.can.flags & BOAT_CAN_FLAG_SELF_SENT) != 0;
    // recognize our own probe payload: 'P''B' seq_hi seq_lo ....
    if (frame->meta.can.can_id == p->probe_id && frame->payload != nullptr &&
        frame->payload_len >= 4 && frame->payload[0] == 'P' &&
        frame->payload[1] == 'B') {
      marker = true;
      seq = static_cast<uint16_t>((frame->payload[2] << 8) | frame->payload[3]);
    }
  } else if (bus == BOAT_BUS_ETHERNET) {
    self_sent = (frame->meta.eth.flags & BOAT_ETH_FLAG_SELF_SENT) != 0;
  }

  bool report_unexpected = false;
  {
    std::lock_guard<std::mutex> lock(p->mu);
    ++p->rx_total;
    if (bus < 6) ++p->rx_by_bus[bus];
    if (self_sent) ++p->rx_self_sent; else ++p->rx_wire;
    if (!p->ExpectedBus(bus)) {
      ++p->unexpected_bus;
      report_unexpected = true;
    }
    if (marker) {
      if (self_sent) {
        ++p->self_echoes;
        if (p->pending && seq == p->pending_seq) {
          p->pending = false;
          ++p->checks_pass;
          std::fprintf(stderr,
                       "[probe] PASS self-sent echo seq=%u rtt=%llu ticks\n",
                       seq, (unsigned long long)(p->tick - p->pending_sent_tick));
        }
      } else {
        // Non-self-sent copy of our own frame: normal on vcan (kernel loopback),
        // absent on real HW without an echoing peer. Informational only.
        ++p->wire_echoes;
      }
    }
  }
  if (report_unexpected) {
    std::fprintf(stderr,
                 "[probe] UNEXPECTED frame on bus=%d — not in declared_buses "
                 "(%s); declared_buses pre-filter may be broken\n",
                 static_cast<int>(bus), p->declared_str.c_str());
  }
}

void probe_on_tick(void* ctx, uint64_t tick) {
  auto* p = static_cast<ProbePlugin*>(ctx);

  // Phase 1: decide whether to emit a probe (under lock, no publish inside).
  bool do_emit = false;
  uint16_t seq = 0;
  {
    std::lock_guard<std::mutex> lock(p->mu);
    p->tick = tick;
    if (p->active && p->frame_publish_fn != nullptr && !p->pending &&
        p->probe_period_ticks > 0 && (tick % p->probe_period_ticks) == 0) {
      seq = p->next_seq++;
      p->pending = true;
      p->pending_seq = seq;
      p->pending_sent_tick = tick;
      do_emit = true;
    }
  }

  // Phase 2: publish outside the lock. frame_publish_fn dispatches synchronously
  // back into probe_on_frame, which locks independently — holding the lock here
  // would deadlock.
  if (do_emit) {
    std::vector<uint8_t> payload(8, 0);
    payload[0] = 'P';
    payload[1] = 'B';
    payload[2] = static_cast<uint8_t>(seq >> 8);
    payload[3] = static_cast<uint8_t>(seq & 0xFF);
    payload[4] = static_cast<uint8_t>(tick & 0xFF);
    payload[5] = static_cast<uint8_t>((tick >> 8) & 0xFF);
    auto bf = BoatFrameOwner::Can(p->iface, p->probe_id, 8, 0, std::move(payload));
    p->frame_publish_fn(p->frame_publisher_ctx, bf.get());
    std::lock_guard<std::mutex> lock(p->mu);
    ++p->probes_sent;
  }

  // Phase 3: timeout check + periodic report (under lock; signals emitted here).
  bool timed_out = false;
  uint16_t to_seq = 0;
  bool do_report = false;
  {
    std::lock_guard<std::mutex> lock(p->mu);
    if (p->pending && (tick - p->pending_sent_tick) > p->echo_timeout_ticks) {
      p->pending = false;
      ++p->checks_fail;
      timed_out = true;
      to_seq = p->pending_seq;
    }
    if (p->report_period_ticks > 0 &&
        (tick - p->last_report_tick) >= p->report_period_ticks) {
      p->last_report_tick = tick;
      do_report = true;
      std::fprintf(
          stderr,
          "[probe] tick=%llu rx=%llu(self=%llu wire=%llu) "
          "bus[can=%llu canfd=%llu eth=%llu tcp=%llu pdu=%llu] unexpected=%llu "
          "probes=%llu self_echoes=%llu wire_echoes=%llu pass=%llu fail=%llu\n",
          (unsigned long long)tick, (unsigned long long)p->rx_total,
          (unsigned long long)p->rx_self_sent, (unsigned long long)p->rx_wire,
          (unsigned long long)p->rx_by_bus[BOAT_BUS_CAN],
          (unsigned long long)p->rx_by_bus[BOAT_BUS_CANFD],
          (unsigned long long)p->rx_by_bus[BOAT_BUS_ETHERNET],
          (unsigned long long)p->rx_by_bus[BOAT_BUS_TCP],
          (unsigned long long)p->rx_by_bus[BOAT_BUS_PDU],
          (unsigned long long)p->unexpected_bus,
          (unsigned long long)p->probes_sent,
          (unsigned long long)p->self_echoes,
          (unsigned long long)p->wire_echoes,
          (unsigned long long)p->checks_pass,
          (unsigned long long)p->checks_fail);
      PublishSignals(p);
    }
  }
  if (timed_out) {
    std::fprintf(stderr,
                 "[probe] FAIL no self-sent echo for seq=%u within %llu ticks "
                 "(frame publish or self-sent tagging broken?)\n",
                 to_seq, (unsigned long long)p->echo_timeout_ticks);
  }
  (void)do_report;
}

void probe_set_frame_publisher(void* ctx, BoatFramePublishFn fn,
                               void* publisher_ctx) {
  auto* p = static_cast<ProbePlugin*>(ctx);
  p->frame_publish_fn = fn;
  p->frame_publisher_ctx = publisher_ctx;
}

void probe_set_bus_publisher(void* ctx, BoatBusPublishFn fn,
                             void* publisher_ctx) {
  auto* p = static_cast<ProbePlugin*>(ctx);
  p->bus_publish_fn = fn;
  p->bus_publisher_ctx = publisher_ctx;
}

const char* probe_declared_buses(void* ctx) {
  auto* p = static_cast<ProbePlugin*>(ctx);
  return p->declared_str.c_str();
}

void probe_shutdown(void* ctx) {
  auto* p = static_cast<ProbePlugin*>(ctx);
  std::lock_guard<std::mutex> lock(p->mu);
  std::fprintf(stderr,
               "[probe] shutdown: rx=%llu self=%llu wire=%llu unexpected=%llu "
               "probes=%llu self_echoes=%llu pass=%llu fail=%llu\n",
               (unsigned long long)p->rx_total,
               (unsigned long long)p->rx_self_sent,
               (unsigned long long)p->rx_wire,
               (unsigned long long)p->unexpected_bus,
               (unsigned long long)p->probes_sent,
               (unsigned long long)p->self_echoes,
               (unsigned long long)p->checks_pass,
               (unsigned long long)p->checks_fail);
}

}  // namespace

/* ── C ABI entry points ─────────────────────────────────────────────────── */

extern "C" BoatPlugin* boat_plugin_create() {
  static BoatPluginVTable kVTable = [] {
    BoatPluginVTable vt{};
    vt.initialize          = &probe_initialize;
    vt.on_tick             = &probe_on_tick;
    vt.shutdown            = &probe_shutdown;
    vt.set_publisher       = nullptr;
    vt.set_bus_publisher   = &probe_set_bus_publisher;
    vt.set_pdu_publisher   = nullptr;
    vt.on_frame            = &probe_on_frame;
    vt.set_frame_publisher = &probe_set_frame_publisher;
    vt.declared_buses      = &probe_declared_buses;
    return vt;
  }();

  auto* plugin = new BoatPlugin{};
  plugin->vtable = &kVTable;
  plugin->ctx = new ProbePlugin{};
  return plugin;
}

extern "C" void boat_plugin_destroy(BoatPlugin* plugin) {
  if (plugin == nullptr) return;
  if (plugin->vtable != nullptr && plugin->vtable->shutdown != nullptr) {
    plugin->vtable->shutdown(plugin->ctx);
  }
  delete static_cast<ProbePlugin*>(plugin->ctx);
  delete plugin;
}

extern "C" uint32_t boat_plugin_abi_version() { return BOAT_PLUGIN_ABI_VERSION; }
