#pragma once

#include <map>
#include <memory>
#include <string>

#include "device/device_driver.h"
#include "device/line_transport.h"

namespace boat::hil {

/* A SCPI instrument as an IDeviceDriver. Channels map to SCPI command
   templates, so the same driver serves any SCPI-speaking supply / e-load by
   swapping the command map. Templates use "{v}" for the numeric value and
   "{ONOFF}" for a boolean rendered as ON/OFF, e.g.:
     write  "voltage" -> "VOLT {v}"      read "voltage" -> "MEAS:VOLT?"
     write  "enable"  -> "OUTP {ONOFF}"  read "current" -> "MEAS:CURR?"

   The transport is injected, so the driver is fully testable against an
   in-process mock instrument (no hardware required). */
class ScpiDeviceDriver final : public IDeviceDriver {
 public:
  struct CommandMap {
    std::map<std::string, std::string> write;  // channel -> set command
    std::map<std::string, std::string> read;   // channel -> query command
  };

  /* Default SCPI command map for a programmable DC power supply. */
  static CommandMap PowerSupplyDefaults();

  ScpiDeviceDriver(std::unique_ptr<ILineTransport> transport,
                   CommandMap commands, int read_timeout_ms = 1000);

  bool Open() override;
  void Close() override;
  bool IsOpen() const override;
  bool Write(const std::string& channel, double value) override;
  bool Read(const std::string& channel, double& out) override;

  /* Device identity from *IDN? (empty until a successful Open() queried it). */
  const std::string& identity() const { return identity_; }

 private:
  std::unique_ptr<ILineTransport> transport_;
  CommandMap commands_;
  int read_timeout_ms_;
  std::string identity_;
};

}  // namespace boat::hil
