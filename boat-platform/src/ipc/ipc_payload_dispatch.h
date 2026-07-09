#pragma once

#include <functional>
#include <string>

#include "boat/v1/control.pb.h"
#include "ipc/shm/shm_payload_sample.h"
#include "ipc/shm/shm_publisher.h"

namespace boat::ipc {

// Resolved iceoryx2 service name for large UDS control payloads for a given bound UDS socket path.
[[nodiscard]] std::string LargeControlPayloadShmTopicForSocket(const std::string& resolved_socket_path);

// If payload_bytes is non-empty and SelectChannel chooses SHM, publishes the bytes and sets shm_payload_topic
// and shm_payload_token. Otherwise leaves the message unchanged. When the SHM path is taken, publisher must be
// non-null and Open; shm_payload_topic must match the publisher's topic.
[[nodiscard]] bool PrepareOutboundUdsControlPayload(boat::v1::UdsControlMessage* message,
                                                    ShmPublisher<ShmPayloadSample>* publisher,
                                                    const std::string& shm_payload_topic);

// If shm_payload_topic is set, restores payload_bytes using receive_shm_payload for the message token.
// Fails on topic mismatch, zero token, or unknown/stale SHM reference.
[[nodiscard]] bool ResolveInboundUdsControlPayload(
    boat::v1::UdsControlMessage* message, const std::string& expected_shm_payload_topic,
    const std::function<bool(std::uint64_t payload_token, std::string& out)>& receive_shm_payload);

}  // namespace boat::ipc
