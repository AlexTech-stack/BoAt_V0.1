#include "device/gpio_relay_driver.h"

#include <fcntl.h>
#include <linux/gpio.h>
#include <sys/ioctl.h>
#include <unistd.h>

#include <cstring>
#include <utility>

namespace boat::hil {

GpioRelayDriver::GpioRelayDriver(std::string chip, unsigned int line,
                                 bool active_low)
    : chip_(std::move(chip)), line_(line), active_low_(active_low) {}

GpioRelayDriver::~GpioRelayDriver() { Close(); }

bool GpioRelayDriver::Open() {
  if (req_fd_ >= 0) return true;
  const int chip_fd = ::open(chip_.c_str(), O_RDONLY | O_CLOEXEC);
  if (chip_fd < 0) return false;

  gpio_v2_line_request req{};
  req.offsets[0] = line_;
  req.num_lines = 1;
  std::strncpy(req.consumer, "boat-relay", sizeof(req.consumer) - 1);
  req.config.flags = GPIO_V2_LINE_FLAG_OUTPUT;
  if (active_low_) req.config.flags |= GPIO_V2_LINE_FLAG_ACTIVE_LOW;

  const int rc = ::ioctl(chip_fd, GPIO_V2_GET_LINE_IOCTL, &req);
  ::close(chip_fd);
  if (rc < 0 || req.fd < 0) return false;
  req_fd_ = req.fd;
  return true;
}

void GpioRelayDriver::Close() {
  if (req_fd_ >= 0) {
    ::close(req_fd_);
    req_fd_ = -1;
  }
}

bool GpioRelayDriver::Write(const std::string& channel, double value) {
  if (channel != "state" || req_fd_ < 0) return false;
  gpio_v2_line_values values{};
  values.mask = 1;
  values.bits = (value != 0.0) ? 1 : 0;
  if (::ioctl(req_fd_, GPIO_V2_LINE_SET_VALUES_IOCTL, &values) < 0) return false;
  last_closed_ = (value != 0.0);
  return true;
}

bool GpioRelayDriver::Read(const std::string& channel, double& out) {
  if (channel != "state" || req_fd_ < 0) return false;
  gpio_v2_line_values values{};
  values.mask = 1;
  if (::ioctl(req_fd_, GPIO_V2_LINE_GET_VALUES_IOCTL, &values) < 0) {
    out = last_closed_ ? 1.0 : 0.0;  // fall back to last commanded state
    return true;
  }
  out = (values.bits & 1) ? 1.0 : 0.0;
  return true;
}

}  // namespace boat::hil
