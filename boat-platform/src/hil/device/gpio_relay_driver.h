#pragma once

#include <cstdint>
#include <string>

#include "device/device_driver.h"

namespace boat::hil {

/* A relay wired to a GPIO line, as an IDeviceDriver, over the Linux GPIO
   character device (/dev/gpiochipN, GPIO uAPI v2). The single channel "state"
   drives the line (Write: 0 = open, non-zero = closed) and reads it back.

   Real hardware I/O — validated only on a board with the relay wired; without
   the chip/line it fails to Open() and stays idle. Live-only: excluded from the
   determinism seed test and never a replay target. `active_low` inverts the
   line for boards where the relay closes on a logic-low. */
class GpioRelayDriver final : public IDeviceDriver {
 public:
  GpioRelayDriver(std::string chip, unsigned int line, bool active_low = false);
  ~GpioRelayDriver() override;

  bool Open() override;
  void Close() override;
  bool IsOpen() const override { return req_fd_ >= 0; }
  bool Write(const std::string& channel, double value) override;
  bool Read(const std::string& channel, double& out) override;

 private:
  std::string chip_;      // e.g. "/dev/gpiochip0"
  unsigned int line_;     // line offset on the chip
  bool active_low_;
  int req_fd_ = -1;       // line-request fd from GPIO_V2_GET_LINE_IOCTL
  bool last_closed_ = false;
};

}  // namespace boat::hil
