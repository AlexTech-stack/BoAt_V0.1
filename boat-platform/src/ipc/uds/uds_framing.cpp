#include "ipc/uds/uds_framing.h"

#include <arpa/inet.h>
#include <sys/socket.h>
#include <unistd.h>

#include <cerrno>
#include <cstdint>

namespace boat::ipc {

namespace {

bool WriteAll(int fd, const void* data, std::size_t size) {
  const auto* ptr = static_cast<const std::uint8_t*>(data);
  std::size_t written = 0;
  while (written < size) {
    const ssize_t rc = ::send(fd, ptr + written, size - written, 0);
    if (rc <= 0) {
      return false;
    }
    written += static_cast<std::size_t>(rc);
  }
  return true;
}

bool ReadAll(int fd, void* data, std::size_t size) {
  auto* ptr = static_cast<std::uint8_t*>(data);
  std::size_t read_count = 0;
  while (read_count < size) {
    const ssize_t rc = ::recv(fd, ptr + read_count, size - read_count, 0);
    if (rc <= 0) {
      return false;
    }
    read_count += static_cast<std::size_t>(rc);
  }
  return true;
}

}  // namespace

bool WriteFrame(int fd, const std::string& bytes) {
  const std::uint32_t len = htonl(static_cast<std::uint32_t>(bytes.size()));
  if (!WriteAll(fd, &len, sizeof(len))) {
    return false;
  }
  if (bytes.empty()) {
    return true;
  }
  return WriteAll(fd, bytes.data(), bytes.size());
}

bool ReadFrame(int fd, std::string& out) {
  std::uint32_t len_be = 0;
  if (!ReadAll(fd, &len_be, sizeof(len_be))) {
    return false;
  }

  const std::uint32_t len = ntohl(len_be);
  out.resize(len);
  if (len == 0) {
    return true;
  }
  return ReadAll(fd, out.data(), len);
}

}  // namespace boat::ipc
