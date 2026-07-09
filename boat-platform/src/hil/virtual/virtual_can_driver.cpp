#include "virtual/virtual_can_driver.h"

#include <utility>

namespace boat::hil {

VirtualCanDriver::VirtualCanDriver(std::string iface)
    : iface_(std::move(iface)), driver_(iface_) {}

bool VirtualCanDriver::Open() { return driver_.Open(); }

bool VirtualCanDriver::ReadFrame(CanFrame& out_frame) { return driver_.ReadFrame(out_frame); }

bool VirtualCanDriver::WriteFrame(const CanFrame& frame) { return driver_.WriteFrame(frame); }

void VirtualCanDriver::Close() { driver_.Close(); }

CanInterfaceInfo VirtualCanDriver::GetInfo() const {
  auto info = driver_.GetInfo();
  info.driver_name = "vcan";
  return info;
}

}  // namespace boat::hil
