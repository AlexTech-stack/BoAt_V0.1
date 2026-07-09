#include "ipc/uds/uds_server.h"

#include <spdlog/spdlog.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <cstdio>
#include <string>

#include "boat/v1/control.pb.h"
#include "ipc/ipc_channel_selector.h"
#include "ipc/ipc_payload_dispatch.h"
#include "ipc/uds/uds_framing.h"

namespace boat::ipc {

UdsServer::UdsServer(std::string socket_path, CommandHandler handler)
    : socket_path_(std::move(socket_path)), handler_(std::move(handler)) {}

UdsServer::~UdsServer() { Stop(); }

bool UdsServer::Start() {
  if (running_.exchange(true)) {
    return true;
  }

  listen_fd_ = ::socket(AF_UNIX, SOCK_STREAM, 0);
  if (listen_fd_ < 0) {
    running_.store(false);
    return false;
  }

  const std::string resolved_socket_path = IpcChannelSelector::ResolveSocketPath(socket_path_);
  if (resolved_socket_path != socket_path_) {
    spdlog::warn("UDS socket '{}' normalized to '{}'", socket_path_, resolved_socket_path);
    socket_path_ = resolved_socket_path;
  }

  large_payload_topic_ = LargeControlPayloadShmTopicForSocket(socket_path_);
  const std::string shm_topic = IpcChannelSelector::TopicName(
      "ipc", "uds_control_payload_" + IpcChannelSelector::ShmInstanceIdFromSocketPath(socket_path_));

  ::unlink(socket_path_.c_str());
  sockaddr_un addr{};
  addr.sun_family = AF_UNIX;
  std::snprintf(addr.sun_path, sizeof(addr.sun_path), "%s", socket_path_.c_str());

  if (::bind(listen_fd_, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0 ||
      ::listen(listen_fd_, SOMAXCONN) < 0) {
    ::close(listen_fd_);
    listen_fd_ = -1;
    running_.store(false);
    return false;
  }

  large_payload_subscriber_.emplace(shm_topic,
                                      [this](const ShmPayloadSample& sample) { EnqueueLargePayloadShm(sample); });
  if (!large_payload_subscriber_->Open()) {
    spdlog::error("UdsServer: failed to open SHM subscriber for large control payloads");
    large_payload_subscriber_.reset();
    ::close(listen_fd_);
    listen_fd_ = -1;
    running_.store(false);
    ::unlink(socket_path_.c_str());
    return false;
  }

  accept_thread_ = std::thread(&UdsServer::AcceptLoop, this);
  spdlog::info("UDS server started at {}", socket_path_);
  return true;
}

void UdsServer::Stop() {
  if (!running_.exchange(false)) {
    return;
  }

  if (listen_fd_ >= 0) {
    ::shutdown(listen_fd_, SHUT_RDWR);
    ::close(listen_fd_);
    listen_fd_ = -1;
  }

  if (accept_thread_.joinable()) {
    accept_thread_.join();
  }

  for (auto& thread : client_threads_) {
    if (thread.joinable()) {
      thread.join();
    }
  }
  client_threads_.clear();

  if (large_payload_subscriber_.has_value()) {
    large_payload_subscriber_->Close();
    large_payload_subscriber_.reset();
  }
  {
    std::lock_guard<std::mutex> lock(large_payload_mutex_);
    large_payload_by_token_.clear();
    large_payload_order_.clear();
  }

  ::unlink(socket_path_.c_str());
  spdlog::info("UDS server stopped at {}", socket_path_);
}

void UdsServer::AcceptLoop() {
  while (running_.load()) {
    const int client_fd = ::accept(listen_fd_, nullptr, nullptr);
    if (client_fd < 0) {
      continue;
    }
    client_threads_.emplace_back(&UdsServer::ClientLoop, this, client_fd);
  }
}

void UdsServer::ClientLoop(int fd) {
  std::string payload;
  while (running_.load() && ReadFrame(fd, payload)) {
    boat::v1::UdsControlMessage msg;
    if (!msg.ParseFromString(payload)) {
      boat::v1::UdsControlResponse bad;
      bad.set_ok(false);
      bad.set_message("invalid control message");
      std::string out;
      bad.SerializeToString(&out);
      WriteFrame(fd, out);
      continue;
    }

    const auto receive_shm = [this](std::uint64_t token, std::string& out) {
      return DequeueLargePayloadShm(token, out, std::chrono::milliseconds(2000));
    };
    if (!ResolveInboundUdsControlPayload(&msg, large_payload_topic_, receive_shm)) {
      boat::v1::UdsControlResponse bad;
      bad.set_ok(false);
      bad.set_message("failed to resolve shm control payload");
      std::string out;
      bad.SerializeToString(&out);
      WriteFrame(fd, out);
      continue;
    }

    boat::v1::UdsControlResponse response = handler_(msg, fd);
    std::string out;
    response.SerializeToString(&out);
    if (!WriteFrame(fd, out)) {
      break;
    }
  }

  ::close(fd);
}

void UdsServer::EvictOldestLocked() {
  if (large_payload_order_.empty()) {
    return;
  }
  const std::uint64_t evict = large_payload_order_.front();
  large_payload_order_.pop_front();
  large_payload_by_token_.erase(evict);
}

void UdsServer::RemoveTokenFromOrderLocked(const std::uint64_t payload_token) {
  for (auto it = large_payload_order_.begin(); it != large_payload_order_.end(); ++it) {
    if (*it == payload_token) {
      large_payload_order_.erase(it);
      return;
    }
  }
}

void UdsServer::EnqueueLargePayloadShm(const ShmPayloadSample& sample) {
  const std::uint64_t token = sample.payload_token;
  if (token == 0U) {
    spdlog::warn("UdsServer: ignoring SHM sample with zero payload_token");
    return;
  }
  {
    std::lock_guard<std::mutex> lock(large_payload_mutex_);
    constexpr std::size_t kMaxQueued = 64;
    while (large_payload_by_token_.size() >= kMaxQueued) {
      EvictOldestLocked();
    }
    large_payload_order_.push_back(token);
    large_payload_by_token_[token] = sample.ToString();
  }
  large_payload_cv_.notify_all();
}

bool UdsServer::DequeueLargePayloadShm(const std::uint64_t payload_token, std::string& out,
                                       const std::chrono::milliseconds timeout) {
  if (payload_token == 0U) {
    return false;
  }
  std::unique_lock<std::mutex> lock(large_payload_mutex_);
  const auto deadline = std::chrono::steady_clock::now() + timeout;
  while (running_.load() && large_payload_by_token_.find(payload_token) == large_payload_by_token_.end()) {
    if (std::chrono::steady_clock::now() >= deadline) {
      return false;
    }
    large_payload_cv_.wait_until(lock, deadline, [this, payload_token] {
      return !running_.load() || large_payload_by_token_.find(payload_token) != large_payload_by_token_.end();
    });
  }
  const auto it = large_payload_by_token_.find(payload_token);
  if (it == large_payload_by_token_.end()) {
    return false;
  }
  out = std::move(it->second);
  large_payload_by_token_.erase(it);
  RemoveTokenFromOrderLocked(payload_token);
  return true;
}

}  // namespace boat::ipc
