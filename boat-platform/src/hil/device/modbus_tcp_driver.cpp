#include "device/modbus_tcp_driver.h"

#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>

#include <cerrno>
#include <cmath>
#include <cstring>
#include <utility>

namespace boat::hil {

ModbusTcpDeviceDriver::RegisterMap ModbusTcpDeviceDriver::PowerSupplyDefaults() {
  RegisterMap m;
  m.write = {
      {"voltage", {0, 0.01}},   // set voltage, centivolts
      {"current", {1, 0.01}},   // current limit, centiamps
      {"enable", {2, 1.0}},     // output on/off
  };
  m.read = {
      {"voltage", {0, 0.01}},   // measured voltage
      {"current", {1, 0.01}},   // measured current
  };
  return m;
}

ModbusTcpDeviceDriver::ModbusTcpDeviceDriver(std::string host, uint16_t port,
                                             RegisterMap regs, uint8_t unit_id,
                                             int read_timeout_ms)
    : host_(std::move(host)),
      port_(port),
      regs_(std::move(regs)),
      unit_id_(unit_id),
      read_timeout_ms_(read_timeout_ms) {}

ModbusTcpDeviceDriver::~ModbusTcpDeviceDriver() { Close(); }

bool ModbusTcpDeviceDriver::Open() {
  if (fd_ >= 0) return true;
  addrinfo hints{};
  hints.ai_family = AF_UNSPEC;
  hints.ai_socktype = SOCK_STREAM;
  addrinfo* result = nullptr;
  if (::getaddrinfo(host_.c_str(), std::to_string(port_).c_str(), &hints, &result) != 0) {
    return false;
  }
  int fd = -1;
  for (addrinfo* rp = result; rp != nullptr; rp = rp->ai_next) {
    fd = ::socket(rp->ai_family, rp->ai_socktype, rp->ai_protocol);
    if (fd < 0) continue;
    if (::connect(fd, rp->ai_addr, rp->ai_addrlen) == 0) break;
    ::close(fd);
    fd = -1;
  }
  ::freeaddrinfo(result);
  if (fd < 0) return false;
  int one = 1;
  ::setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
  fd_ = fd;
  return true;
}

void ModbusTcpDeviceDriver::Close() {
  if (fd_ >= 0) {
    ::close(fd_);
    fd_ = -1;
  }
}

// Build the 7-byte MBAP header + PDU, exchange, return the PDU of the response.
bool ModbusTcpDeviceDriver::SendRecv(const std::uint8_t* pdu, std::size_t pdu_len,
                                     std::uint8_t* resp, std::size_t resp_cap,
                                     std::size_t& resp_len) {
  if (fd_ < 0) return false;
  const uint16_t txn = ++txn_;
  std::uint8_t frame[260];
  const std::size_t frame_len = 7 + pdu_len;
  if (frame_len > sizeof(frame)) return false;
  frame[0] = static_cast<std::uint8_t>(txn >> 8);
  frame[1] = static_cast<std::uint8_t>(txn & 0xFF);
  frame[2] = 0;  // protocol id hi
  frame[3] = 0;  // protocol id lo
  const uint16_t len = static_cast<uint16_t>(pdu_len + 1);  // unit id + pdu
  frame[4] = static_cast<std::uint8_t>(len >> 8);
  frame[5] = static_cast<std::uint8_t>(len & 0xFF);
  frame[6] = unit_id_;
  std::memcpy(frame + 7, pdu, pdu_len);

  std::size_t sent = 0;
  while (sent < frame_len) {
    const ssize_t n = ::send(fd_, frame + sent, frame_len - sent, MSG_NOSIGNAL);
    if (n <= 0) {
      if (n < 0 && errno == EINTR) continue;
      return false;
    }
    sent += static_cast<std::size_t>(n);
  }

  // Read the MBAP header (7 bytes), then the PDU it announces.
  std::uint8_t hdr[7];
  std::size_t got = 0;
  while (got < sizeof(hdr)) {
    pollfd pfd{fd_, POLLIN, 0};
    if (::poll(&pfd, 1, read_timeout_ms_) <= 0) return false;
    const ssize_t n = ::recv(fd_, hdr + got, sizeof(hdr) - got, 0);
    if (n <= 0) return false;
    got += static_cast<std::size_t>(n);
  }
  const std::size_t remaining = static_cast<std::size_t>((hdr[4] << 8) | hdr[5]);
  if (remaining == 0) return false;
  const std::size_t body = remaining - 1;  // minus unit id (already in hdr[6])
  if (body > resp_cap) return false;
  got = 0;
  while (got < body) {
    pollfd pfd{fd_, POLLIN, 0};
    if (::poll(&pfd, 1, read_timeout_ms_) <= 0) return false;
    const ssize_t n = ::recv(fd_, resp + got, body - got, 0);
    if (n <= 0) return false;
    got += static_cast<std::size_t>(n);
  }
  resp_len = body;
  return true;
}

bool ModbusTcpDeviceDriver::Write(const std::string& channel, double value) {
  auto it = regs_.write.find(channel);
  if (it == regs_.write.end() || fd_ < 0) return false;
  const uint16_t reg = static_cast<uint16_t>(
      std::lround(value / (it->second.scale == 0.0 ? 1.0 : it->second.scale)));
  // FC 0x06: write single register — [func, addr_hi, addr_lo, val_hi, val_lo]
  std::uint8_t pdu[5] = {
      0x06,
      static_cast<std::uint8_t>(it->second.addr >> 8),
      static_cast<std::uint8_t>(it->second.addr & 0xFF),
      static_cast<std::uint8_t>(reg >> 8),
      static_cast<std::uint8_t>(reg & 0xFF),
  };
  std::uint8_t resp[8];
  std::size_t rlen = 0;
  if (!SendRecv(pdu, sizeof(pdu), resp, sizeof(resp), rlen)) return false;
  return rlen >= 1 && resp[0] == 0x06;  // not an exception (0x86)
}

bool ModbusTcpDeviceDriver::Read(const std::string& channel, double& out) {
  auto it = regs_.read.find(channel);
  if (it == regs_.read.end() || fd_ < 0) return false;
  // FC 0x03: read holding registers — [func, addr_hi, addr_lo, cnt_hi, cnt_lo]
  std::uint8_t pdu[5] = {
      0x03,
      static_cast<std::uint8_t>(it->second.addr >> 8),
      static_cast<std::uint8_t>(it->second.addr & 0xFF),
      0x00, 0x01,
  };
  std::uint8_t resp[8];
  std::size_t rlen = 0;
  if (!SendRecv(pdu, sizeof(pdu), resp, sizeof(resp), rlen)) return false;
  // response: [func, byte_count, reg_hi, reg_lo]
  if (rlen < 4 || resp[0] != 0x03 || resp[1] < 2) return false;
  const uint16_t reg = static_cast<uint16_t>((resp[2] << 8) | resp[3]);
  out = reg * it->second.scale;
  return true;
}

}  // namespace boat::hil
