#pragma once

#include <memory>
#include <string>

#include "can/socket_can_driver.h"
#include "hal/hal_driver.h"

namespace boat::hil {

class VirtualCanDriver : public IHalDriver {
 public:
  explicit VirtualCanDriver(std::string iface = "vcan0");

  bool Open() override;
  bool ReadFrame(CanFrame& out_frame) override;
  bool WriteFrame(const CanFrame& frame) override;
  void Close() override;
  CanInterfaceInfo GetInfo() const override;

 private:
  std::string iface_;
  SocketCanDriver driver_;
};

}  // namespace boat::hil
