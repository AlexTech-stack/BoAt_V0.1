#include "can/socket_can_driver.h"

#ifdef __linux__

#include <cerrno>
#include <cstring>
#include <ctime>
#include <fstream>
#include <string>
#include <utility>

#include <linux/can.h>
#include <linux/can/raw.h>
#include <linux/can/error.h>
#include <net/if.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <unistd.h>

namespace boat::hil {

SocketCanDriver::SocketCanDriver(std::string interface_name) : iface_(std::move(interface_name)) {}

bool SocketCanDriver::Open() {
  Close();

  socket_fd_ = socket(AF_CAN, SOCK_RAW, CAN_RAW);
  if (socket_fd_ < 0) {
    return false;
  }

  const unsigned int if_index = if_nametoindex(iface_.c_str());
  if (if_index == 0) {
    Close();
    return false;
  }

  // Enable CAN FD frames on this socket. Falls back gracefully on classic-only interfaces.
  const int enable_fd = 1;
  (void)setsockopt(socket_fd_, SOL_CAN_RAW, CAN_RAW_FD_FRAMES, &enable_fd, sizeof(enable_fd));

  // Do NOT set CAN_RAW_RECV_OWN_MSGS: the gateway dispatches sent frames directly to
  // subscribers via CanBusRegistry::DispatchRx, so socket loopback is not needed and
  // would cause every sent frame to be double-delivered.

  struct timeval timeout {};
  timeout.tv_sec = 0;
  timeout.tv_usec = 100000;
  if (setsockopt(socket_fd_, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)) < 0) {
    Close();
    return false;
  }

  struct sockaddr_can addr {};
  addr.can_family = AF_CAN;
  addr.can_ifindex = static_cast<int>(if_index);
  if (bind(socket_fd_, reinterpret_cast<struct sockaddr*>(&addr), sizeof(addr)) < 0) {
    Close();
    return false;
  }

  return true;
}

bool SocketCanDriver::ReadFrame(CanFrame& out_frame) {
  if (socket_fd_ < 0) {
    return false;
  }

  // canfd_frame is large enough to hold both classic and FD frames.
  struct canfd_frame raw {};
  const ssize_t bytes = read(socket_fd_, &raw, sizeof(raw));
  if (bytes < 0) {
    if (errno == EAGAIN || errno == EWOULDBLOCK) {
      return false;
    }
    return false;
  }
  // Accept either a classic frame (sizeof(can_frame)) or an FD frame.
  if (bytes != static_cast<ssize_t>(sizeof(struct can_frame)) &&
      bytes != static_cast<ssize_t>(sizeof(struct canfd_frame))) {
    return false;
  }

  const bool is_fd = (bytes == static_cast<ssize_t>(sizeof(struct canfd_frame)));
  const std::uint8_t data_len = (raw.len <= CANFD_MAX_DLEN) ? raw.len : CANFD_MAX_DLEN;

  out_frame.can_id = raw.can_id;
  out_frame.dlc    = data_len;
  out_frame.flags  = is_fd ? raw.flags : 0;
  std::memset(out_frame.data, 0, sizeof(out_frame.data));
  std::memcpy(out_frame.data, raw.data, data_len);

  struct timespec ts {};
  if (clock_gettime(CLOCK_REALTIME, &ts) == 0) {
    out_frame.timestamp_ns =
        static_cast<std::uint64_t>(ts.tv_sec) * 1000000000ULL + static_cast<std::uint64_t>(ts.tv_nsec);
  } else {
    out_frame.timestamp_ns = 0;
  }

  return true;
}

bool SocketCanDriver::WriteFrame(const CanFrame& frame) {
  if (socket_fd_ < 0) {
    return false;
  }

  const std::uint32_t ext_flag = (frame.can_id > 0x7FF) ? CAN_EFF_FLAG : 0U;
  if (frame.flags & kCanFdFlagFdf) {
    // CAN FD frame
    struct canfd_frame raw {};
    raw.can_id = frame.can_id | ext_flag;
    raw.len    = frame.dlc <= CANFD_MAX_DLEN ? frame.dlc : CANFD_MAX_DLEN;
    raw.flags  = frame.flags;
    std::memcpy(raw.data, frame.data, raw.len);
    const ssize_t bytes = write(socket_fd_, &raw, sizeof(raw));
    return bytes == static_cast<ssize_t>(sizeof(raw));
  } else {
    // Classic CAN frame
    struct can_frame raw {};
    raw.can_id  = frame.can_id | ext_flag;
    raw.can_dlc = frame.dlc <= CAN_MAX_DLEN ? frame.dlc : CAN_MAX_DLEN;
    std::memcpy(raw.data, frame.data, raw.can_dlc);
    const ssize_t bytes = write(socket_fd_, &raw, sizeof(raw));
    return bytes == static_cast<ssize_t>(sizeof(raw));
  }
}

void SocketCanDriver::Close() {
  if (socket_fd_ >= 0) {
    close(socket_fd_);
    socket_fd_ = -1;
  }
}

CanInterfaceInfo SocketCanDriver::GetInfo() const {
  CanInterfaceInfo info{};
  info.driver_name = "socketcan";
  info.bitrate = 0;

  const std::string base = "/sys/class/net/" + iface_ + "/";

  // MTU determines CAN FD capability (72 = FD, 16 = classic).
  std::ifstream mtu_file(base + "mtu");
  unsigned mtu = 0;
  if (mtu_file >> mtu) {
    info.fd_support = (mtu >= 72);
  }

  // Operational state: "up", "down", "unknown".
  std::ifstream state_file(base + "operstate");
  std::string state;
  if (state_file >> state) {
    info.state = state;
  } else {
    info.state = "unknown";
  }

  // Attempt to read the kernel driver name from the device's driver symlink.
  // Virtual interfaces (vcan) do not have a device/ subdirectory.
  std::string driver_link = base + "device/driver";
  std::ifstream driver_file(driver_link);
  if (driver_file) {
    // The symlink target looks like "../../../../../../bus/usb/drivers/peak_usb"
    // so we resolve it with readlink.
    char linkbuf[256] = {};
    const ssize_t len = readlink(driver_link.c_str(), linkbuf, sizeof(linkbuf) - 1);
    if (len > 0) {
      linkbuf[len] = '\0';
      std::string resolved(linkbuf);
      // Extract the last component (the driver name).
      const auto pos = resolved.rfind('/');
      if (pos != std::string::npos && pos + 1 < resolved.size()) {
        info.driver_name = resolved.substr(pos + 1);
      }
    }
  }

  return info;
}

}  // namespace boat::hil

#endif
