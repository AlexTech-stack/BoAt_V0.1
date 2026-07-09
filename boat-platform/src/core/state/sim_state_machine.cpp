#include "state/sim_state_machine.h"

#include <algorithm>
#include <unordered_map>
#include <unordered_set>

namespace boat::core {

namespace {
const std::unordered_map<SimState, std::unordered_set<SimState>> kTransitions = {
    {SimState::IDLE, {SimState::RUNNING}},
    {SimState::RUNNING, {SimState::PAUSED, SimState::STOPPED, SimState::ERROR}},
    {SimState::PAUSED, {SimState::RUNNING, SimState::STOPPED, SimState::ERROR}},
    {SimState::STOPPED, {SimState::IDLE}},
    {SimState::ERROR, {SimState::IDLE}},
};
}  // namespace

bool SimStateMachine::Transition(SimState target) {
  std::vector<Observer> observers;
  SimState from = SimState::ERROR;

  {
    std::lock_guard<std::mutex> lock(mutex_);
    from = current_;
    auto it = kTransitions.find(current_);
    if (it == kTransitions.end() || it->second.find(target) == it->second.end()) {
      return false;
    }
    current_ = target;
    observers.reserve(observers_.size());
    for (const auto& [token, observer] : observers_) {
      (void)token;
      observers.push_back(observer);
    }
  }

  for (const auto& observer : observers) {
    observer(from, target);
  }
  return true;
}

SimState SimStateMachine::Current() const {
  std::lock_guard<std::mutex> lock(mutex_);
  return current_;
}

SimStateMachine::ObserverToken SimStateMachine::OnTransition(Observer observer) {
  std::lock_guard<std::mutex> lock(mutex_);
  const ObserverToken token = next_observer_token_++;
  observers_.emplace_back(token, std::move(observer));
  return token;
}

bool SimStateMachine::RemoveObserver(ObserverToken token) {
  std::lock_guard<std::mutex> lock(mutex_);
  const auto previous_size = observers_.size();
  observers_.erase(std::remove_if(observers_.begin(), observers_.end(),
                                  [token](const auto& registered) { return registered.first == token; }),
                   observers_.end());
  return observers_.size() != previous_size;
}

}  // namespace boat::core
