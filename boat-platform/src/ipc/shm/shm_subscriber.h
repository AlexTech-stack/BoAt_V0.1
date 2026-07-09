#pragma once

#include <atomic>
#include <chrono>
#include <functional>
#include <optional>
#include <string>
#include <thread>
#include <spdlog/spdlog.h>

#include <iox2/node.hpp>
#include <iox2/port_factory_publish_subscribe.hpp>
#include <iox2/service_name.hpp>
#include <iox2/service_type.hpp>
#include <iox2/subscriber.hpp>
#include "ipc/ipc_channel_selector.h"

namespace boat::ipc {

template <typename T>
class ShmSubscriber {
 public:
  using OnReceive = std::function<void(const T&)>;

  ShmSubscriber(std::string topic_name, OnReceive on_receive)
      : topic_name_(std::move(topic_name)), on_receive_(std::move(on_receive)) {}

  bool Open() {
    const std::string service_topic = IpcChannelSelector::ResolveTopicName(topic_name_);
    const auto service_name = iox2::ServiceName::create(service_topic.c_str());
    if (service_name.has_error()) {
      spdlog::error("Failed to create SHM service name for topic {}", service_topic);
      return false;
    }

    auto node = iox2::NodeBuilder().template create<iox2::ServiceType::Ipc>();
    if (node.has_error()) {
      spdlog::error("Failed to create SHM node for topic {}", service_topic);
      return false;
    }

    auto service = node->service_builder(service_name.value())
                       .template publish_subscribe<T>()
                       .open_or_create();
    if (service.has_error()) {
      spdlog::error("Failed to open/create SHM service for topic {}", service_topic);
      return false;
    }

    auto subscriber = service->subscriber_builder().create();
    if (subscriber.has_error()) {
      spdlog::error("Failed to create SHM subscriber for topic {}", service_topic);
      return false;
    }

    node_.emplace(std::move(node.value()));
    service_.emplace(std::move(service.value()));
    subscriber_.emplace(std::move(subscriber.value()));

    running_.store(true);
    poll_thread_ = std::thread(&ShmSubscriber::PollLoop, this);
    spdlog::info("Opened SHM subscriber on topic {}", service_topic);
    return true;
  }

  void Close() {
    running_.store(false);
    if (poll_thread_.joinable()) {
      poll_thread_.join();
    }
    subscriber_.reset();
    service_.reset();
    node_.reset();
    spdlog::info("Closed SHM subscriber on topic {}", topic_name_);
  }

 private:
  void PollLoop() {
    while (running_.load()) {
      if (!subscriber_.has_value()) {
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
        continue;
      }

      std::this_thread::sleep_for(std::chrono::milliseconds(1));
      while (true) {
        auto sample = subscriber_->receive();
        if (sample.has_error() || !sample->has_value()) {
          break;
        }
        on_receive_(*sample->value());
      }
    }
  }

  std::string topic_name_;
  OnReceive on_receive_;
  std::optional<iox2::Node<iox2::ServiceType::Ipc>> node_;
  std::optional<iox2::PortFactoryPublishSubscribe<iox2::ServiceType::Ipc, T, void>> service_;
  std::optional<iox2::Subscriber<iox2::ServiceType::Ipc, T, void>> subscriber_;
  std::atomic<bool> running_{false};
  std::thread poll_thread_;
};

}  // namespace boat::ipc
