#pragma once

#include <cstdint>
#include <map>
#include <string>

#include "device/device_driver.h"

namespace boat::hil {

/* A Modbus-TCP instrument (power supply / e-load) as an IDeviceDriver. Channels
   map to 16-bit holding registers with a scale factor: writes use FC 0x06
   (write single register), reads use FC 0x03 (read holding registers). The
   value <-> register conversion is `reg = round(value / scale)` /
   `value = reg * scale`, so e.g. a 0.01 scale stores 24.00 V as register 2400.

   Minimal blocking TCP client; testable against an in-process mock server. */
class ModbusTcpDeviceDriver final : public IDeviceDriver {
 public:
  struct Reg {
    std::uint16_t addr;
    double scale;
  };
  struct RegisterMap {
    std::map<std::string, Reg> write;  // channel -> holding register (FC06)
    std::map<std::string, Reg> read;   // channel -> holding register (FC03)
  };

  /* Default register map for a programmable DC power supply (0.01 scale). */
  static RegisterMap PowerSupplyDefaults();

  ModbusTcpDeviceDriver(std::string host, uint16_t port, RegisterMap regs,
                        uint8_t unit_id = 1, int read_timeout_ms = 1000);
  ~ModbusTcpDeviceDriver() override;

  bool Open() override;
  void Close() override;
  bool IsOpen() const override { return fd_ >= 0; }
  bool Write(const std::string& channel, double value) override;
  bool Read(const std::string& channel, double& out) override;

 private:
  bool SendRecv(const std::uint8_t* req, std::size_t req_len,
                std::uint8_t* resp, std::size_t resp_cap, std::size_t& resp_len);

  std::string host_;
  uint16_t port_;
  RegisterMap regs_;
  uint8_t unit_id_;
  int read_timeout_ms_;
  int fd_ = -1;
  uint16_t txn_ = 0;
};

}  // namespace boat::hil
