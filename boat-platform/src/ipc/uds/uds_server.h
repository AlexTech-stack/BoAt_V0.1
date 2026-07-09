#pragma once

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <functional>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

#include "boat/v1/control.pb.h"
#include "ipc/shm/shm_payload_sample.h"
#include "ipc/shm/shm_subscriber.h"
#include "ipc/uds/uds_types.h"

namespace boat::ipc {

class UdsServer {
 public:
  using CommandHandler =
      std::function<boat::v1::UdsControlResponse(const boat::v1::UdsControlMessage& message, int client_fd)>;

  UdsServer(std::string socket_path, CommandHandler handler);
  ~UdsServer();

  bool Start();
  void Stop();

 private:
  void AcceptLoop();
  void ClientLoop(int fd);
  void EnqueueLargePayloadShm(const ShmPayloadSample& sample);
  bool DequeueLargePayloadShm(std::uint64_t payload_token, std::string& out, std::chrono::milliseconds timeout);
  void EvictOldestLocked();
  void RemoveTokenFromOrderLocked(std::uint64_t payload_token);

  std::string socket_path_;
  std::string large_payload_topic_;
  CommandHandler handler_;
  std::atomic<bool> running_{false};
  int listen_fd_{-1};
  std::thread accept_thread_;
  std::vector<std::thread> client_threads_;

  std::mutex large_payload_mutex_;
  std::condition_variable large_payload_cv_;
  std::unordered_map<std::uint64_t, std::string> large_payload_by_token_;
  std::deque<std::uint64_t> large_payload_order_;
  std::optional<ShmSubscriber<ShmPayloadSample>> large_payload_subscriber_;
};

}  // namespace boat::ipc
