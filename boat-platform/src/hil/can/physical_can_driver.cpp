// Copyright 2026 Alexander Günther
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#include "can/physical_can_driver.h"

#include <cstdio>
#include <fstream>
#include <string>
#include <utility>

#include <unistd.h>

namespace boat::hil {

PhysicalCanDriver::PhysicalCanDriver(std::string iface)
    : iface_(std::move(iface)), driver_(iface_) {
  // Read sysfs metadata eagerly so GetInfo() works even before Open().
  ReadInterfaceInfo();
}

bool PhysicalCanDriver::Open() {
  Close();
  return driver_.Open();
}

bool PhysicalCanDriver::ReadFrame(CanFrame& out_frame) {
  return driver_.ReadFrame(out_frame);
}

bool PhysicalCanDriver::WriteFrame(const CanFrame& frame) {
  return driver_.WriteFrame(frame);
}

void PhysicalCanDriver::Close() {
  driver_.Close();
}

CanInterfaceInfo PhysicalCanDriver::GetInfo() const {
  return info_;
}

bool PhysicalCanDriver::ReadInterfaceInfo() {
  info_ = {};
  info_.bitrate = 0;
  info_.state = "unknown";

  const std::string base = "/sys/class/net/" + iface_ + "/";

  // MTU determines FD capability.
  std::ifstream mtu_file(base + "mtu");
  unsigned mtu = 0;
  if (!(mtu_file >> mtu)) {
    return false;
  }
  info_.fd_support = (mtu >= 72);

  // Operational state.
  std::ifstream state_file(base + "operstate");
  std::string state;
  if (state_file >> state) {
    info_.state = state;
  }

  // Determine driver name from the device's driver symlink.
  // Physical interfaces have a device/ subdirectory pointing to the USB/PCI driver.
  std::string driver_link = base + "device/driver";
  std::ifstream driver_file(driver_link);
  if (driver_file) {
    char linkbuf[256] = {};
    const ssize_t len = readlink(driver_link.c_str(), linkbuf, sizeof(linkbuf) - 1);
    if (len > 0) {
      linkbuf[len] = '\0';
      std::string resolved(linkbuf);
      auto pos = resolved.rfind('/');
      if (pos != std::string::npos && pos + 1 < resolved.size()) {
        info_.driver_name = resolved.substr(pos + 1);
      }
    }
  }

  // If we have a driver name, we successfully probed the interface.
  return !info_.driver_name.empty();
}

bool PhysicalCanDriver::IsPhysicalInterface(const std::string& iface) {
  // vcan interfaces are always virtual.
  if (iface.size() >= 4 && iface.compare(0, 4, "vcan") == 0) {
    return false;
  }
  // Check for the device/ subdirectory which only exists for physical interfaces.
  std::string check = "/sys/class/net/" + iface + "/device";
  std::ifstream f(check);
  return f.good();
}

}  // namespace boat::hil
