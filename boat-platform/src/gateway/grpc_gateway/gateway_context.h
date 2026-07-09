#pragma once

#include "rpc_audit_log.h"
#include "core/plugin/plugin_manager.h"
#include "core/scenario/scenario_loader.h"
#include "core/signal/signal_bus.h"
#include "core/simulation/simulation_context.h"
#include "event_store/event_store.h"
#include "replay_engine/replay_engine.h"
#include "trace_store/trace_store.h"

// Forward-declare to avoid pulling the entire HIL header tree into every
// translation unit that includes gateway_context.h.
namespace boat::hil {
class CanBusRegistry;
class EthernetBusRegistry;
}  // namespace boat::hil

namespace boat::gateway {

class FrameSink;

struct GatewayContext {
  boat::core::SimulationContext& sim;

  boat::core::SignalBus& signal_bus;
  boat::core::ScenarioLoader& scenario_loader;
  boat::store::SqliteEventStore& event_store;
  boat::store::FlatFileTraceStore& trace_store;
  boat::replay::ReplayController& replay_controller;
  boat::hil::CanBusRegistry& can_bus_registry;
  boat::hil::EthernetBusRegistry& ethernet_bus_registry;
  boat::core::PluginManager& plugin_manager;
  FrameSink& frame_sink;
  RpcAuditLog& audit_log;
};

}  // namespace boat::gateway
