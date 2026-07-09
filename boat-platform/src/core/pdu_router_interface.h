#pragma once

#include <cstdint>
#include <functional>
#include <string>
#include <vector>

#include "pdu/pdu_types.h"

namespace boat::core {

/* Interface that the PduRouter plugin exposes to gRPC PduService.
   The plugin registers itself via PluginManager::RegisterService("pdu_router", this)
   during initialize(). PduServiceImpl looks it up and delegates all calls. */
class IPduRouter {
 public:
  virtual ~IPduRouter() = default;

  using SubId     = std::size_t;
  using RxCallback = std::function<void(const hil::PduFrame&)>;

  virtual bool SendPdu(uint32_t pdu_id, const std::vector<uint8_t>& payload) = 0;
  virtual SubId Subscribe(std::vector<uint32_t> pdu_ids, RxCallback cb) = 0;
  virtual void Unsubscribe(SubId id) = 0;

  virtual void AddRoute(const hil::PduRoute& route) = 0;
  virtual void RemoveRoute(uint32_t pdu_id) = 0;
  virtual std::vector<hil::PduRoute> ListRoutes() const = 0;

  virtual void AddContainer(const hil::PduContainerDef& def) = 0;

  virtual void AddGroup(const hil::PduGroup& group) = 0;
  virtual void EnableGroup(uint32_t group_id) = 0;
  virtual void DisableGroup(uint32_t group_id) = 0;
  virtual std::vector<hil::PduGroup> ListGroups() const = 0;

  virtual void ConfigureDeadline(uint32_t pdu_id,
                                 const hil::PduDeadlineConfig& cfg) = 0;
};

}  // namespace boat::core
