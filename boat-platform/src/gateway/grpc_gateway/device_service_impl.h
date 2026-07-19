#pragma once

#include <grpcpp/grpcpp.h>

#include "boat/v1/device.grpc.pb.h"
#include "core/device_manager_interface.h"
#include "gateway_context.h"

namespace boat::gateway {

/* DeviceService gRPC facade. Holds no device logic itself — it looks up the
   device_manager plugin's IDeviceManager via PluginManager::FindService and
   delegates every call, mirroring PduServiceImpl -> pdu_router. */
class DeviceServiceImpl final : public boat::v1::DeviceService::Service {
 public:
  explicit DeviceServiceImpl(GatewayContext& ctx);

  grpc::Status ListDevices(grpc::ServerContext* context,
                           const boat::v1::ListDevicesRequest* request,
                           boat::v1::ListDevicesResponse* response) override;

  grpc::Status SetControl(grpc::ServerContext* context,
                          const boat::v1::SetControlRequest* request,
                          boat::v1::SetControlResponse* response) override;

  grpc::Status ReadState(grpc::ServerContext* context,
                         const boat::v1::ReadStateRequest* request,
                         boat::v1::ReadStateResponse* response) override;

  grpc::Status StreamState(
      grpc::ServerContext* context,
      const boat::v1::StreamStateRequest* request,
      grpc::ServerWriter<boat::v1::DeviceStateUpdate>* writer) override;

 private:
  boat::core::IDeviceManager* GetManager();
  GatewayContext& ctx_;
};

}  // namespace boat::gateway
