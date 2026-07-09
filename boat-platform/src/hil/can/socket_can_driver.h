#pragma once

#include <string>

#include "hal/hal_driver.h"

namespace boat::hil {

class SocketCanDriver : public IHalDriver {
 public:
  explicit SocketCanDriver(std::string interface_name = "can0");

  bool Open() override;
  bool ReadFrame(CanFrame& out_frame) override;
  bool WriteFrame(const CanFrame& frame) override;
  void Close() override;
  CanInterfaceInfo GetInfo() const override;

 private:
  int socket_fd_{-1};
  std::string iface_;
};

}  // namespace boat::hil
