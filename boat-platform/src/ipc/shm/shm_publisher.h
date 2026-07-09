#pragma once

#include <atomic>
#include <cstdint>
#include <optional>
#include <string>

#include <spdlog/spdlog.h>

#include <iox2/node.hpp>
#include <iox2/port_factory_publish_subscribe.hpp>
#include <iox2/publisher.hpp>
#include <iox2/service_name.hpp>
#include <iox2/service_type.hpp>

#include "ipc/ipc_channel_selector.h"

namespace boat::ipc {

template <typename T>
class ShmPublisher {
 public:
  explicit ShmPublisher(std::string topic_name) : topic_name_(std::move(topic_name)) {}

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

    auto publisher = service->publisher_builder().create();
    if (publisher.has_error()) {
      spdlog::error("Failed to create SHM publisher for topic {}", service_topic);
      return false;
    }

    node_.emplace(std::move(node.value()));
    service_.emplace(std::move(service.value()));
    publisher_.emplace(std::move(publisher.value()));
    spdlog::info("Opened SHM publisher on topic {}", service_topic);
    return true;
  }

  void Publish(const T& sample) {
    if (!publisher_.has_value()) {
      return;
    }

    auto result = publisher_->send_copy(sample);
    if (result.has_error()) {
      overflow_count_.fetch_add(1, std::memory_order_relaxed);
      spdlog::warn("SHM publisher overflow on topic {} count={}", topic_name_, overflow_count_.load());
    }
  }

  void Close() {
    publisher_.reset();
    service_.reset();
    node_.reset();
    spdlog::info("Closed SHM publisher on topic {}", topic_name_);
  }

  [[nodiscard]] bool IsOpen() const { return publisher_.has_value(); }

 private:
  std::string topic_name_;
  std::optional<iox2::Node<iox2::ServiceType::Ipc>> node_;
  std::optional<iox2::PortFactoryPublishSubscribe<iox2::ServiceType::Ipc, T, void>> service_;
  std::optional<iox2::Publisher<iox2::ServiceType::Ipc, T, void>> publisher_;
  std::atomic<std::uint64_t> overflow_count_{0};
};

}  // namespace boat::ipc
