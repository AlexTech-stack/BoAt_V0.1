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

#pragma once

#include <memory>
#include <string>

#include "can/socket_can_driver.h"
#include "hal/hal_driver.h"

namespace boat::hil {

class PhysicalCanDriver : public IHalDriver {
 public:
  explicit PhysicalCanDriver(std::string iface);

  bool Open() override;
  bool ReadFrame(CanFrame& out_frame) override;
  bool WriteFrame(const CanFrame& frame) override;
  void Close() override;
  CanInterfaceInfo GetInfo() const override;

 private:
  static bool IsPhysicalInterface(const std::string& iface);
  bool ReadInterfaceInfo();

  std::string iface_;
  SocketCanDriver driver_;
  CanInterfaceInfo info_{};
};

}  // namespace boat::hil
