#include "pdu/pdu_router.h"

#include <boat/plugin.h>
#include <core/pdu_router_interface.h>
#include <core/plugin/plugin_manager.h>

#include <cstring>

namespace {

struct PduRouterPlugin {
  boat::hil::PduRouter router;
};

int pdu_router_initialize(void* ctx, const char* /*config_json*/) {
  auto* p = static_cast<PduRouterPlugin*>(ctx);
  if (!p) return -1;
  return 0;
}

void pdu_router_on_tick(void* ctx, uint64_t tick) {
  auto* p = static_cast<PduRouterPlugin*>(ctx);
  if (!p) return;
  p->router.OnTick(tick);
}

void pdu_router_shutdown(void* ctx) {
  auto* p = static_cast<PduRouterPlugin*>(ctx);
  if (!p) return;
  p->router.Stop();
}

void pdu_router_set_frame_publisher(void* ctx, BoatFramePublishFn fn,
                                     void* pub_ctx) {
  auto* p = static_cast<PduRouterPlugin*>(ctx);
  if (!p) return;
  if (fn && pub_ctx) {
    p->router.SetFramePublisher([fn, pub_ctx](const BoatFrame& bf) {
      fn(pub_ctx, &bf);
    });
  }
}

void pdu_router_on_frame(void* ctx, const BoatFrame* frame) {
  auto* p = static_cast<PduRouterPlugin*>(ctx);
  if (!p || !frame) return;
  if (frame->bus_type == BOAT_BUS_CAN || frame->bus_type == BOAT_BUS_CANFD) {
    boat::hil::CanFrame cf{};
    cf.can_id = frame->meta.can.can_id;
    cf.dlc    = frame->meta.can.dlc;
    cf.flags  = frame->meta.can.flags;
    const auto copy_len = frame->payload_len > 64 ? 64U : frame->payload_len;
    if (frame->payload && copy_len > 0)
      std::memcpy(cf.data, frame->payload, copy_len);
    p->router.OnCanFrame(cf, frame->iface ? frame->iface : "");
  } else if (frame->bus_type == BOAT_BUS_ETHERNET) {
    boat::hil::EthernetFrame ef{};
    ef.ethertype = frame->meta.eth.ethertype;
    ef.vlan_id   = frame->meta.eth.vlan_id;
    if (frame->payload && frame->payload_len > 0)
      ef.payload.assign(frame->payload, frame->payload + frame->payload_len);
    p->router.OnEthernetFrame(ef, frame->iface ? frame->iface : "");
  }
}

const char* pdu_router_declared_buses(void* /*ctx*/) {
  return "[\"can\",\"eth\"]";
}

BoatPluginVTable gVTable = [] {
  BoatPluginVTable vt{};
  vt.initialize          = &pdu_router_initialize;
  vt.on_tick             = &pdu_router_on_tick;
  vt.shutdown            = &pdu_router_shutdown;
  vt.set_publisher       = nullptr;
  vt.set_bus_publisher   = nullptr;
  vt.set_pdu_publisher   = nullptr;
  vt.on_frame            = &pdu_router_on_frame;
  vt.set_frame_publisher = &pdu_router_set_frame_publisher;
  vt.declared_buses      = &pdu_router_declared_buses;
  return vt;
}();

}  // namespace

extern "C" BoatPlugin* boat_plugin_create() {
  auto* state = new PduRouterPlugin{};
  auto* plugin = new BoatPlugin{};
  plugin->vtable = &gVTable;
  plugin->ctx    = state;
  return plugin;
}

extern "C" void boat_plugin_destroy(BoatPlugin* plugin) {
  if (!plugin) return;
  if (plugin->vtable && plugin->vtable->shutdown)
    plugin->vtable->shutdown(plugin->ctx);
  delete static_cast<PduRouterPlugin*>(plugin->ctx);
  delete plugin;
}

extern "C" uint32_t boat_plugin_abi_version() { return BOAT_PLUGIN_ABI_VERSION; }

// Exposes this plugin's router as the "pdu_router" service so
// PluginManager::Load() registers it and PduServiceImpl can find it via
// FindService("pdu_router") -- see boat/plugin.h's service-export docs.
extern "C" const char* boat_plugin_service_name() { return "pdu_router"; }

extern "C" void* boat_plugin_service_ptr(void* ctx) {
  auto* p = static_cast<PduRouterPlugin*>(ctx);
  if (!p) return nullptr;
  return static_cast<boat::core::IPduRouter*>(&p->router);
}
