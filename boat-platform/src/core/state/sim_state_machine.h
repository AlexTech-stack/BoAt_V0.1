#pragma once

#include <functional>
#include <cstdint>
#include <mutex>
#include <utility>
#include <vector>

namespace boat::core {

enum class SimState { IDLE, RUNNING, PAUSED, STOPPED, ERROR };

class SimStateMachine {
 public:
  using Observer = std::function<void(SimState from, SimState to)>;
  using ObserverToken = std::uint64_t;

  bool Transition(SimState target);
  [[nodiscard]] SimState Current() const;
  [[nodiscard]] ObserverToken OnTransition(Observer observer);
  bool RemoveObserver(ObserverToken token);

 private:
  mutable std::mutex mutex_;
  SimState current_{SimState::IDLE};
  ObserverToken next_observer_token_{1};
  std::vector<std::pair<ObserverToken, Observer>> observers_;
};

}  // namespace boat::core
