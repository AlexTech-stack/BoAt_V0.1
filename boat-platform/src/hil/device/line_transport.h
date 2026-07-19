#pragma once

#include <string>

namespace boat::hil {

/* A newline-delimited text transport — the wire under a SCPI (or similar)
   instrument link. Implementations: TcpLineTransport (LXI / raw socket, the
   common case for bench PSUs on port 5025) and, in the future, a serial
   (termios) transport. Kept as an interface so the driver is testable against
   an in-process mock instrument without any hardware. */
class ILineTransport {
 public:
  virtual ~ILineTransport() = default;

  virtual bool Open() = 0;
  virtual void Close() = 0;
  virtual bool IsOpen() const = 0;

  /* Send one command. The transport appends the line terminator ('\n'). */
  virtual bool WriteLine(const std::string& line) = 0;

  /* Read one response line (without the terminator). Returns false on timeout
     or error. */
  virtual bool ReadLine(std::string& out, int timeout_ms) = 0;
};

}  // namespace boat::hil
