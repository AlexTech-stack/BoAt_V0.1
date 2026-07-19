#include "device/tcp_line_transport.h"

#include <arpa/inet.h>
#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>

#include <cerrno>
#include <cstring>

namespace boat::hil {

TcpLineTransport::TcpLineTransport(std::string host, uint16_t port)
    : host_(std::move(host)), port_(port) {}

TcpLineTransport::~TcpLineTransport() { Close(); }

bool TcpLineTransport::Open() {
  if (fd_ >= 0) return true;

  addrinfo hints{};
  hints.ai_family = AF_UNSPEC;
  hints.ai_socktype = SOCK_STREAM;
  addrinfo* result = nullptr;
  const std::string port_str = std::to_string(port_);
  if (::getaddrinfo(host_.c_str(), port_str.c_str(), &hints, &result) != 0) {
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
  rx_buffer_.clear();
  return true;
}

void TcpLineTransport::Close() {
  if (fd_ >= 0) {
    ::close(fd_);
    fd_ = -1;
  }
  rx_buffer_.clear();
}

bool TcpLineTransport::WriteLine(const std::string& line) {
  if (fd_ < 0) return false;
  std::string out = line;
  out.push_back('\n');
  size_t sent = 0;
  while (sent < out.size()) {
    const ssize_t n = ::send(fd_, out.data() + sent, out.size() - sent, MSG_NOSIGNAL);
    if (n <= 0) {
      if (n < 0 && errno == EINTR) continue;
      return false;
    }
    sent += static_cast<size_t>(n);
  }
  return true;
}

bool TcpLineTransport::ReadLine(std::string& out, int timeout_ms) {
  if (fd_ < 0) return false;
  for (;;) {
    // Serve a complete line already buffered from a previous read.
    const auto nl = rx_buffer_.find('\n');
    if (nl != std::string::npos) {
      out = rx_buffer_.substr(0, nl);
      if (!out.empty() && out.back() == '\r') out.pop_back();
      rx_buffer_.erase(0, nl + 1);
      return true;
    }

    pollfd pfd{fd_, POLLIN, 0};
    const int pr = ::poll(&pfd, 1, timeout_ms);
    if (pr <= 0) return false;  // timeout or error

    char buf[512];
    const ssize_t n = ::recv(fd_, buf, sizeof(buf), 0);
    if (n <= 0) {
      if (n < 0 && errno == EINTR) continue;
      return false;  // closed or error
    }
    rx_buffer_.append(buf, static_cast<size_t>(n));
  }
}

}  // namespace boat::hil
