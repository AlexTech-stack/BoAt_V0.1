// Exercises ModbusTcpDeviceDriver against an in-process mock Modbus-TCP
// instrument over a real loopback socket — no hardware required.

#include <catch2/catch_test_macros.hpp>

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <atomic>
#include <cstdint>
#include <cstring>
#include <thread>

#include "device/modbus_tcp_driver.h"

using boat::hil::ModbusTcpDeviceDriver;

namespace {

// Mock Modbus-TCP PSU: FC06 writes a register; FC03 reads it back. Register 1
// (current) is derived as register 0 (voltage) / 3 to emulate a 3-ohm load.
class MockModbusServer {
 public:
  MockModbusServer() {
    listen_fd_ = ::socket(AF_INET, SOCK_STREAM, 0);
    REQUIRE(listen_fd_ >= 0);
    int one = 1;
    ::setsockopt(listen_fd_, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    addr.sin_port = 0;
    REQUIRE(::bind(listen_fd_, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) == 0);
    REQUIRE(::listen(listen_fd_, 1) == 0);
    socklen_t len = sizeof(addr);
    ::getsockname(listen_fd_, reinterpret_cast<sockaddr*>(&addr), &len);
    port_ = ntohs(addr.sin_port);
    thread_ = std::thread([this] { Serve(); });
  }

  ~MockModbusServer() {
    running_ = false;
    if (listen_fd_ >= 0) { ::shutdown(listen_fd_, SHUT_RDWR); ::close(listen_fd_); }
    if (thread_.joinable()) thread_.join();
  }

  uint16_t port() const { return port_; }

 private:
  void Serve() {
    int c = ::accept(listen_fd_, nullptr, nullptr);
    if (c < 0) return;
    std::uint16_t regs[8] = {0};
    while (running_) {
      std::uint8_t buf[260];
      const ssize_t n = ::recv(c, buf, sizeof(buf), 0);
      if (n < 12) break;  // MBAP(7) + PDU
      const std::uint8_t func = buf[7];
      const std::uint16_t addr = (buf[8] << 8) | buf[9];
      std::uint8_t resp[260];
      std::memcpy(resp, buf, 7);  // echo MBAP header
      std::size_t pdu_len = 0;
      if (func == 0x06) {  // write single register
        const std::uint16_t val = (buf[10] << 8) | buf[11];
        if (addr < 8) regs[addr] = val;
        if (addr == 0) regs[1] = static_cast<std::uint16_t>(val / 3);  // I = V/3Ω
        std::memcpy(resp + 7, buf + 7, 5);  // echo the request PDU
        pdu_len = 5;
      } else if (func == 0x03) {  // read holding registers
        const std::uint16_t reg = (addr < 8) ? regs[addr] : 0;
        resp[7] = 0x03;
        resp[8] = 0x02;  // byte count
        resp[9] = static_cast<std::uint8_t>(reg >> 8);
        resp[10] = static_cast<std::uint8_t>(reg & 0xFF);
        pdu_len = 4;
      } else {
        break;
      }
      const std::uint16_t mbap_len = static_cast<std::uint16_t>(pdu_len + 1);
      resp[4] = static_cast<std::uint8_t>(mbap_len >> 8);
      resp[5] = static_cast<std::uint8_t>(mbap_len & 0xFF);
      ::send(c, resp, 7 + pdu_len, 0);
    }
    ::close(c);
  }

  int listen_fd_ = -1;
  uint16_t port_ = 0;
  std::atomic<bool> running_{true};
  std::thread thread_;
};

}  // namespace

TEST_CASE("ModbusTcpDeviceDriver drives a mock Modbus instrument over TCP",
          "[unit][device]") {
  MockModbusServer server;

  ModbusTcpDeviceDriver driver(
      "127.0.0.1", server.port(),
      ModbusTcpDeviceDriver::PowerSupplyDefaults(), /*unit_id=*/1);

  REQUIRE(driver.Open());
  REQUIRE(driver.Write("voltage", 24.0));  // 24.00 V -> register 2400 (scale 0.01)

  double v = 0.0, i = 0.0;
  REQUIRE(driver.Read("voltage", v));
  REQUIRE(v == 24.0);
  REQUIRE(driver.Read("current", i));
  REQUIRE(i == 8.0);  // register 2400/3 = 800 -> 8.00 A

  REQUIRE(driver.Write("voltage", 12.0));
  REQUIRE(driver.Read("voltage", v));
  REQUIRE(v == 12.0);

  double dummy = 0.0;
  REQUIRE_FALSE(driver.Write("bogus", 1.0));
  REQUIRE_FALSE(driver.Read("bogus", dummy));

  driver.Close();
  REQUIRE_FALSE(driver.IsOpen());
}
