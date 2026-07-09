#pragma once

#include <cstdint>

#include "determinism/determinism_engine.h"
#include "event/event_bus.h"
#include "fault/fault_injector.h"
#include "plugin/plugin_manager.h"
#include "scheduler/sim_clock.h"
#include "scheduler/tick_scheduler.h"
#include "signal/signal_router.h"
#include "state/sim_state_machine.h"

namespace boat::core {

/* Owns all simulation-scoped components and handles their construction order.
   Created once per gateway; lives as long as the gateway.
   Infrastructure (HIL, storage, audit) is managed separately through
   GatewayContext. */
class SimulationContext {
 public:
  explicit SimulationContext(std::uint64_t seed,
                             std::size_t worker_threads = std::thread::hardware_concurrency());

  SimClock& clock() { return clock_; }
  DeterminismEngine& determinism() { return determinism_; }
  EventBus& event_bus() { return event_bus_; }
  SignalRouter& signal_router() { return signal_router_; }
  FaultInjector& fault_injector() { return fault_injector_; }
  SimStateMachine& state_machine() { return state_machine_; }
  PluginManager& plugin_manager() { return plugin_manager_; }
  TickScheduler& scheduler() { return scheduler_; }

 private:
  SimClock clock_;
  DeterminismEngine determinism_;
  EventBus event_bus_;
  SignalRouter signal_router_;
  FaultInjector fault_injector_;
  SimStateMachine state_machine_;
  PluginManager plugin_manager_;
  TickScheduler scheduler_;
};

}  // namespace boat::core
