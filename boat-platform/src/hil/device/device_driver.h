#pragma once

#include <string>

namespace boat::hil {

/* Backend abstraction for an electrical device (power supply, e-load, relay,
   generator). Setpoints go in and measurements come out, addressed by channel
   name ("voltage", "current", "enable", "state", ...). This is the seam that
   lets a device plugin swap a deterministic virtual model for real bench
   hardware (SCPI/GPIO/Modbus) without changing its signal-bus contract.

   Physical implementations are inherently live-only: they perform real I/O, so
   they are excluded from the determinism seed test and are never the target of
   a replay (a replay reconstitutes recorded state into a *virtual* model). */
class IDeviceDriver {
 public:
  virtual ~IDeviceDriver() = default;

  /* Establish the connection to the device. Returns false on failure. */
  virtual bool Open() = 0;

  /* Release the connection. Safe to call when not open. */
  virtual void Close() = 0;

  /* True once Open() has succeeded and the link is usable. */
  virtual bool IsOpen() const = 0;

  /* Apply a setpoint / command to a channel. Returns false on I/O failure. */
  virtual bool Write(const std::string& channel, double value) = 0;

  /* Read a measurement from a channel into out. Returns false if the channel
     is not readable or the read failed. */
  virtual bool Read(const std::string& channel, double& out) = 0;
};

}  // namespace boat::hil
