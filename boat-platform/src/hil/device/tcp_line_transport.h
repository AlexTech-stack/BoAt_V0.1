#pragma once

#include <cstdint>
#include <string>

#include "device/line_transport.h"

namespace boat::hil {

/* Newline-delimited transport over a TCP socket — the usual way to reach a
   bench instrument's SCPI raw-socket/LXI port (commonly 5025). Blocking I/O
   with a per-read timeout. */
class TcpLineTransport final : public ILineTransport {
 public:
  TcpLineTransport(std::string host, uint16_t port);
  ~TcpLineTransport() override;

  bool Open() override;
  void Close() override;
  bool IsOpen() const override { return fd_ >= 0; }

  bool WriteLine(const std::string& line) override;
  bool ReadLine(std::string& out, int timeout_ms) override;

 private:
  std::string host_;
  uint16_t port_;
  int fd_ = -1;
  std::string rx_buffer_;  // holds bytes read past a line terminator
};

}  // namespace boat::hil
