#pragma once

#include <boat/plugin.h>

#include <atomic>
#include <chrono>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#include "device/device_driver.h"

namespace boat::hil {

/* Header-only glue that runs an IDeviceDriver behind the v9 signal contract on
   the plugin's own worker thread — so slow/unreachable-instrument I/O never
   stalls the gateway tick loop. Shared by the physical device plugins
   (gpio_relay, modbus_device, ...): each just builds its driver + channel maps
   and hands them here. */
class DeviceRunner {
 public:
  struct Channels {
    std::map<std::string, std::string> set_map;   // signal name -> driver channel
    std::vector<std::pair<std::string, std::string>> meas_map;  // channel -> signal
  };

  DeviceRunner(std::unique_ptr<IDeviceDriver> driver, Channels channels,
               int poll_ms = 200, int reconnect_ms = 2000)
      : driver_(std::move(driver)),
        channels_(std::move(channels)),
        poll_ms_(poll_ms),
        reconnect_ms_(reconnect_ms) {}

  ~DeviceRunner() { Stop(); }

  void SetBusPublisher(BoatBusPublishFn fn, void* ctx) {
    bus_fn_ = fn;
    bus_ctx_ = ctx;
  }

  void Start() {
    if (running_.exchange(true)) return;
    worker_ = std::thread(&DeviceRunner::Worker, this);
  }

  void Stop() {
    if (!running_.exchange(false)) return;
    if (worker_.joinable()) worker_.join();
    if (driver_) driver_->Close();
  }

  void OnSignal(const char* name, double value) {
    if (name == nullptr) return;
    auto it = channels_.set_map.find(name);
    if (it == channels_.set_map.end()) return;
    std::lock_guard<std::mutex> lock(mu_);
    pending_[it->second] = value;
  }

 private:
  void Worker() {
    while (running_.load()) {
      if (driver_ && !driver_->IsOpen()) {
        if (!driver_->Open()) {
          std::this_thread::sleep_for(std::chrono::milliseconds(reconnect_ms_));
          continue;
        }
      }
      std::map<std::string, double> todo;
      {
        std::lock_guard<std::mutex> lock(mu_);
        todo.swap(pending_);
      }
      for (const auto& [ch, val] : todo) driver_->Write(ch, val);

      if (bus_fn_ != nullptr) {
        for (const auto& [ch, sig] : channels_.meas_map) {
          double v = 0.0;
          if (driver_->Read(ch, v)) bus_fn_(bus_ctx_, sig.c_str(), v);
        }
      }
      std::this_thread::sleep_for(std::chrono::milliseconds(poll_ms_));
    }
  }

  std::unique_ptr<IDeviceDriver> driver_;
  Channels channels_;
  int poll_ms_;
  int reconnect_ms_;
  BoatBusPublishFn bus_fn_ = nullptr;
  void* bus_ctx_ = nullptr;
  std::mutex mu_;
  std::map<std::string, double> pending_;
  std::atomic<bool> running_{false};
  std::thread worker_;
};

}  // namespace boat::hil
