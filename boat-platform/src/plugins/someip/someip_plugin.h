#pragma once

#include <boat/plugin.h>
#include <boat/someip.h>

#include <cstdint>
#include <functional>
#include <string>
#include <unordered_map>
#include <vector>

/* A registered SOME/IP service. */
struct SomeipService {
  uint16_t service_id;
  uint16_t instance_id;
  uint8_t  major_ver;
  uint32_t minor_ver;
  uint16_t port;           // UDP port
  bool     offered{false}; // currently being offered via SD
};

/* SOME/IP plugin state. */
struct SomeipPlugin {
  BoatFramePublishFn frame_publish_fn{nullptr};
  void*              frame_publisher_ctx{nullptr};

  // Locally-offered services
  std::unordered_map<uint16_t, SomeipService> local_services;

  // Discovered remote services
  std::unordered_map<uint16_t, SomeipService> remote_services;

  uint16_t next_client_id{1};
  uint16_t next_session_id{1};
  uint16_t sd_port{30490};  // SOME/IP-SD well-known port
};

extern "C" BoatPlugin* boat_plugin_create();
extern "C" void boat_plugin_destroy(BoatPlugin* plugin);
extern "C" uint32_t boat_plugin_abi_version();
