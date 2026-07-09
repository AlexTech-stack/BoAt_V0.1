#include "simulation/simulation_context.h"

namespace boat::core {

SimulationContext::SimulationContext(std::uint64_t seed,
                                     std::size_t worker_threads)
    : clock_(0),
      determinism_(seed),
      event_bus_(),
      signal_router_(),
      fault_injector_(determinism_),
      state_machine_(),
      plugin_manager_(),
      scheduler_(clock_, event_bus_, determinism_, worker_threads) {}

}  // namespace boat::core
