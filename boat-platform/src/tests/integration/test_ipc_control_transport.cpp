#include <catch2/catch_test_macros.hpp>

#include <atomic>
#include <chrono>
#include <filesystem>
#include <mutex>
#include <random>
#include <string>
#include <thread>
#include <vector>

#include "boat/v1/control.pb.h"
#include "ipc/ipc_channel_selector.h"
#include "ipc/uds/uds_client.h"
#include "ipc/uds/uds_server.h"

namespace {

std::string UniqueSocketPath() {
  static std::mt19937_64 rng{std::random_device{}()};
  const auto dir = std::filesystem::temp_directory_path();
  return (dir / ("boat_ipc_ctrl_" + std::to_string(rng()) + ".sock")).string();
}

}  // namespace

TEST_CASE("UDS control payload below 4 KB is delivered inline on UDS", "[integration][ipc]") {
  const std::string socket_path = UniqueSocketPath();
  std::atomic<std::size_t> received_size{0};

  boat::ipc::UdsServer server(socket_path, [&received_size](const boat::v1::UdsControlMessage& m, int) {
    received_size.store(m.payload_bytes().size());
    boat::v1::UdsControlResponse r;
    r.set_ok(true);
    return r;
  });
  REQUIRE(server.Start());
  std::this_thread::sleep_for(std::chrono::milliseconds(20));

  boat::ipc::UdsClient client;
  REQUIRE(client.Connect(socket_path));

  constexpr std::size_t kBelowThreshold = boat::ipc::IpcChannelSelector::kShmThresholdBytes - 1;
  std::string payload(kBelowThreshold, 'a');
  const auto response = client.SendQueryStateCommand(payload);
  REQUIRE(response.ok());
  REQUIRE(received_size.load() == kBelowThreshold);

  client.Disconnect();
  server.Stop();
}

TEST_CASE("UDS control payload at 4 KB threshold is delivered via SHM", "[integration][ipc]") {
  const std::string socket_path = UniqueSocketPath();
  std::atomic<std::size_t> received_size{0};

  boat::ipc::UdsServer server(socket_path, [&received_size](const boat::v1::UdsControlMessage& m, int) {
    received_size.store(m.payload_bytes().size());
    boat::v1::UdsControlResponse r;
    r.set_ok(true);
    return r;
  });
  REQUIRE(server.Start());
  std::this_thread::sleep_for(std::chrono::milliseconds(20));

  boat::ipc::UdsClient client;
  REQUIRE(client.Connect(socket_path));

  constexpr std::size_t kAtThreshold = boat::ipc::IpcChannelSelector::kShmThresholdBytes;
  std::string payload(kAtThreshold, 'b');
  const auto response = client.SendQueryStateCommand(payload);
  REQUIRE(response.ok());
  REQUIRE(received_size.load() == kAtThreshold);

  client.Disconnect();
  server.Stop();
}

TEST_CASE("UDS concurrent large SHM payloads stay correlated per client", "[integration][ipc]") {
  const std::string socket_path = UniqueSocketPath();
  std::mutex mu;
  std::vector<std::string> received;

  boat::ipc::UdsServer server(socket_path, [&](const boat::v1::UdsControlMessage& m, int) {
    std::lock_guard<std::mutex> lock(mu);
    received.push_back(m.payload_bytes());
    boat::v1::UdsControlResponse r;
    r.set_ok(true);
    return r;
  });
  REQUIRE(server.Start());
  std::this_thread::sleep_for(std::chrono::milliseconds(20));

  constexpr std::size_t kSize = boat::ipc::IpcChannelSelector::kShmThresholdBytes;
  std::string pa(kSize, 'A');
  std::string pb(kSize, 'B');

  std::atomic<bool> go{false};
  std::thread t1([&] {
    while (!go.load()) {
      std::this_thread::yield();
    }
    boat::ipc::UdsClient c1;
    REQUIRE(c1.Connect(socket_path));
    REQUIRE(c1.SendQueryStateCommand(pa).ok());
    c1.Disconnect();
  });
  std::thread t2([&] {
    while (!go.load()) {
      std::this_thread::yield();
    }
    boat::ipc::UdsClient c2;
    REQUIRE(c2.Connect(socket_path));
    REQUIRE(c2.SendQueryStateCommand(pb).ok());
    c2.Disconnect();
  });
  go.store(true);
  t1.join();
  t2.join();

  server.Stop();
  REQUIRE(received.size() == 2);
  bool seen_a = false;
  bool seen_b = false;
  for (const auto& p : received) {
    REQUIRE(p.size() == kSize);
    if (p == pa) {
      seen_a = true;
    }
    if (p == pb) {
      seen_b = true;
    }
  }
  REQUIRE(seen_a);
  REQUIRE(seen_b);
}

TEST_CASE("UDS large SHM payloads use instance-scoped topics", "[integration][ipc]") {
  const std::string path_a = UniqueSocketPath();
  const std::string path_b = UniqueSocketPath();
  std::mutex mu;
  std::vector<std::string> received_a;
  std::vector<std::string> received_b;

  boat::ipc::UdsServer server_a(path_a, [&](const boat::v1::UdsControlMessage& m, int) {
    std::lock_guard<std::mutex> lock(mu);
    received_a.push_back(m.payload_bytes());
    boat::v1::UdsControlResponse r;
    r.set_ok(true);
    return r;
  });
  boat::ipc::UdsServer server_b(path_b, [&](const boat::v1::UdsControlMessage& m, int) {
    std::lock_guard<std::mutex> lock(mu);
    received_b.push_back(m.payload_bytes());
    boat::v1::UdsControlResponse r;
    r.set_ok(true);
    return r;
  });
  REQUIRE(server_a.Start());
  REQUIRE(server_b.Start());
  std::this_thread::sleep_for(std::chrono::milliseconds(30));

  constexpr std::size_t kSize = boat::ipc::IpcChannelSelector::kShmThresholdBytes;
  std::string pa(kSize, 'P');
  std::string pb(kSize, 'Q');
  pa.front() = 'a';
  pb.front() = 'z';

  boat::ipc::UdsClient client_a;
  boat::ipc::UdsClient client_b;
  REQUIRE(client_a.Connect(path_a));
  REQUIRE(client_b.Connect(path_b));
  REQUIRE(client_a.SendQueryStateCommand(pa).ok());
  REQUIRE(client_b.SendQueryStateCommand(pb).ok());

  client_a.Disconnect();
  client_b.Disconnect();
  server_a.Stop();
  server_b.Stop();

  REQUIRE(received_a.size() == 1);
  REQUIRE(received_b.size() == 1);
  REQUIRE(received_a[0] == pa);
  REQUIRE(received_b[0] == pb);
}
