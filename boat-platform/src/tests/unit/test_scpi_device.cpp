// Exercises ScpiDeviceDriver + TcpLineTransport end-to-end against an in-process
// mock SCPI instrument over a real loopback TCP socket — no hardware required.

#include <catch2/catch_test_macros.hpp>

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <atomic>
#include <cstdio>
#include <cstring>
#include <memory>
#include <string>
#include <thread>

#include "device/scpi_device_driver.h"
#include "device/tcp_line_transport.h"

using boat::hil::ScpiDeviceDriver;
using boat::hil::TcpLineTransport;

namespace {

// A minimal SCPI power-supply simulator: accepts one client, tracks the last
// VOLT setpoint, reports it back for MEAS:VOLT?, and Ohm's-law current for
// MEAS:CURR? over a fixed 3 ohm load.
class MockScpiServer {
 public:
  MockScpiServer() {
    listen_fd_ = ::socket(AF_INET, SOCK_STREAM, 0);
    REQUIRE(listen_fd_ >= 0);
    int one = 1;
    ::setsockopt(listen_fd_, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    addr.sin_port = 0;  // ephemeral
    REQUIRE(::bind(listen_fd_, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) == 0);
    REQUIRE(::listen(listen_fd_, 1) == 0);
    socklen_t len = sizeof(addr);
    REQUIRE(::getsockname(listen_fd_, reinterpret_cast<sockaddr*>(&addr), &len) == 0);
    port_ = ntohs(addr.sin_port);
    thread_ = std::thread([this] { Serve(); });
  }

  ~MockScpiServer() {
    running_.store(false);
    if (listen_fd_ >= 0) ::shutdown(listen_fd_, SHUT_RDWR), ::close(listen_fd_);
    if (thread_.joinable()) thread_.join();
  }

  uint16_t port() const { return port_; }

 private:
  void Serve() {
    int c = ::accept(listen_fd_, nullptr, nullptr);
    if (c < 0) return;
    std::string buf;
    double volt = 0.0;
    const double load_ohm = 3.0;
    while (running_.load()) {
      char tmp[256];
      const ssize_t n = ::recv(c, tmp, sizeof(tmp), 0);
      if (n <= 0) break;
      buf.append(tmp, static_cast<size_t>(n));
      size_t nl;
      while ((nl = buf.find('\n')) != std::string::npos) {
        std::string line = buf.substr(0, nl);
        buf.erase(0, nl + 1);
        if (!line.empty() && line.back() == '\r') line.pop_back();

        std::string reply;
        if (line == "*IDN?") {
          reply = "BoAt,MockPSU,0,1.0\n";
        } else if (line == "MEAS:VOLT?") {
          char r[32];
          std::snprintf(r, sizeof(r), "%g\n", volt);
          reply = r;
        } else if (line == "MEAS:CURR?") {
          char r[32];
          std::snprintf(r, sizeof(r), "%g\n", volt / load_ohm);
          reply = r;
        } else if (line.rfind("VOLT ", 0) == 0) {
          volt = std::strtod(line.c_str() + 5, nullptr);
        }  // OUTP/CURR: accepted, no reply
        if (!reply.empty()) ::send(c, reply.data(), reply.size(), 0);
      }
    }
    ::close(c);
  }

  int listen_fd_ = -1;
  uint16_t port_ = 0;
  std::atomic<bool> running_{true};
  std::thread thread_;
};

}  // namespace

TEST_CASE("ScpiDeviceDriver drives a mock SCPI instrument over TCP",
          "[unit][device]") {
  MockScpiServer server;

  ScpiDeviceDriver driver(
      std::make_unique<TcpLineTransport>("127.0.0.1", server.port()),
      ScpiDeviceDriver::PowerSupplyDefaults(), /*read_timeout_ms=*/1000);

  REQUIRE(driver.Open());
  REQUIRE(driver.identity() == "BoAt,MockPSU,0,1.0");

  // Setpoint out, measurement back.
  REQUIRE(driver.Write("voltage", 24.0));

  double v = 0.0, i = 0.0;
  REQUIRE(driver.Read("voltage", v));
  REQUIRE(v == 24.0);
  REQUIRE(driver.Read("current", i));
  REQUIRE(i == 8.0);  // 24 V / 3 ohm

  // Change setpoint, re-read.
  REQUIRE(driver.Write("voltage", 12.0));
  REQUIRE(driver.Read("voltage", v));
  REQUIRE(v == 12.0);

  // An unmapped channel is rejected, not sent.
  double dummy = 0.0;
  REQUIRE_FALSE(driver.Write("bogus", 1.0));
  REQUIRE_FALSE(driver.Read("bogus", dummy));

  driver.Close();
  REQUIRE_FALSE(driver.IsOpen());
}
