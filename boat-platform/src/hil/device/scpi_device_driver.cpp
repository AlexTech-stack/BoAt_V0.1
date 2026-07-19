#include "device/scpi_device_driver.h"

#include <cstdio>
#include <cstdlib>
#include <utility>

namespace boat::hil {

namespace {

std::string ReplaceAll(std::string s, const std::string& from,
                       const std::string& to) {
  if (from.empty()) return s;
  for (std::size_t pos = 0;
       (pos = s.find(from, pos)) != std::string::npos; pos += to.size()) {
    s.replace(pos, from.size(), to);
  }
  return s;
}

// Render a command template: "{v}" -> value (%g), "{ONOFF}" -> ON/OFF.
std::string Render(const std::string& tmpl, double value) {
  char num[32];
  std::snprintf(num, sizeof(num), "%g", value);
  std::string out = ReplaceAll(tmpl, "{v}", num);
  out = ReplaceAll(out, "{ONOFF}", value != 0.0 ? "ON" : "OFF");
  return out;
}

}  // namespace

ScpiDeviceDriver::CommandMap ScpiDeviceDriver::PowerSupplyDefaults() {
  CommandMap m;
  m.write = {
      {"voltage", "VOLT {v}"},
      {"current", "CURR {v}"},   // current *limit*
      {"enable", "OUTP {ONOFF}"},
  };
  m.read = {
      {"voltage", "MEAS:VOLT?"},
      {"current", "MEAS:CURR?"},
  };
  return m;
}

ScpiDeviceDriver::ScpiDeviceDriver(std::unique_ptr<ILineTransport> transport,
                                   CommandMap commands, int read_timeout_ms)
    : transport_(std::move(transport)),
      commands_(std::move(commands)),
      read_timeout_ms_(read_timeout_ms) {}

bool ScpiDeviceDriver::Open() {
  if (transport_ == nullptr) return false;
  if (!transport_->IsOpen() && !transport_->Open()) return false;
  // Best-effort identity handshake; failure does not fail Open().
  if (transport_->WriteLine("*IDN?")) {
    std::string idn;
    if (transport_->ReadLine(idn, read_timeout_ms_)) identity_ = idn;
  }
  return true;
}

void ScpiDeviceDriver::Close() {
  if (transport_ != nullptr) transport_->Close();
}

bool ScpiDeviceDriver::IsOpen() const {
  return transport_ != nullptr && transport_->IsOpen();
}

bool ScpiDeviceDriver::Write(const std::string& channel, double value) {
  auto it = commands_.write.find(channel);
  if (it == commands_.write.end()) return false;
  if (transport_ == nullptr) return false;
  return transport_->WriteLine(Render(it->second, value));
}

bool ScpiDeviceDriver::Read(const std::string& channel, double& out) {
  auto it = commands_.read.find(channel);
  if (it == commands_.read.end()) return false;
  if (transport_ == nullptr) return false;
  if (!transport_->WriteLine(it->second)) return false;
  std::string resp;
  if (!transport_->ReadLine(resp, read_timeout_ms_)) return false;
  char* end = nullptr;
  const double v = std::strtod(resp.c_str(), &end);
  if (end == resp.c_str()) return false;  // no number parsed
  out = v;
  return true;
}

}  // namespace boat::hil
