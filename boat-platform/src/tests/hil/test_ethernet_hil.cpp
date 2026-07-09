#include <catch2/catch_test_macros.hpp>

#include <atomic>
#include <chrono>
#include <cstdlib>
#include <thread>

#include "ethernet/ethernet_frame.h"
#include "ethernet/virtual_ethernet_driver.h"
#include "ethernet_bus_registry.h"

using namespace boat::hil;

TEST_CASE("Virtual Ethernet HIL: frame send and receive via UDP multicast", "[hil][ethernet]") {
  const char* enabled = std::getenv("BOAT_HIL_ENABLED");
  if (enabled == nullptr || *enabled == '\0') {
    SKIP("BOAT_HIL_ENABLED not set");
  }

  // Two separate driver instances on the same virtual interface (index 7).
  // rx opens first so its socket has joined the multicast group before tx sends.
  auto rx = VirtualEthernetDriver::FromIndex("veth_test", 7);
  auto tx = VirtualEthernetDriver::FromIndex("veth_test", 7);

  REQUIRE(rx->Open());
  REQUIRE(tx->Open());

  EthernetFrame sent;
  sent.src_mac[0] = 0xAA; sent.src_mac[1] = 0xBB;
  sent.dst_mac[0] = 0xFF; sent.dst_mac[1] = 0xFF;
  sent.ethertype  = 0x88B5;  // custom test ethertype
  sent.payload    = {0xDE, 0xAD, 0xBE, 0xEF};

  REQUIRE(tx->WriteFrame(sent));

  EthernetFrame received;
  REQUIRE(rx->ReadFrame(received));
  REQUIRE(received.ethertype    == 0x88B5);
  REQUIRE(received.payload      == sent.payload);
  REQUIRE(received.src_mac[0]   == 0xAA);
  REQUIRE(received.src_mac[1]   == 0xBB);
  REQUIRE(received.timestamp_ns  > 0);

  tx->Close();
  rx->Close();
}

TEST_CASE("Virtual Ethernet HIL: registry dispatches RX frame to subscriber", "[hil][ethernet]") {
  const char* enabled = std::getenv("BOAT_HIL_ENABLED");
  if (enabled == nullptr || *enabled == '\0') {
    SKIP("BOAT_HIL_ENABLED not set");
  }

  EthernetBusRegistry registry;
  REQUIRE(registry.Add("veth_reg",
    VirtualEthernetDriver::FromIndex("veth_reg", 8)));

  std::atomic<int>  hits{0};
  EthernetFrame     last_rx;

  registry.Subscribe("veth_reg", 0,
    [&](const EthernetFrame& f, const std::string&) {
      last_rx = f;
      ++hits;
    });

  // SendFrame delivers via DispatchRx directly (no multicast loopback needed).
  EthernetFrame f;
  f.ethertype = 0x0800;
  f.payload   = {0x01, 0x02, 0x03};
  REQUIRE(registry.SendFrame("veth_reg", f));

  REQUIRE(hits == 1);
  REQUIRE(last_rx.ethertype == 0x0800);
  REQUIRE(last_rx.payload   == f.payload);
}

TEST_CASE("Virtual Ethernet HIL: ethertype filter in registry", "[hil][ethernet]") {
  const char* enabled = std::getenv("BOAT_HIL_ENABLED");
  if (enabled == nullptr || *enabled == '\0') {
    SKIP("BOAT_HIL_ENABLED not set");
  }

  EthernetBusRegistry registry;
  REQUIRE(registry.Add("veth_filt",
    VirtualEthernetDriver::FromIndex("veth_filt", 9)));

  int ipv4_hits = 0, all_hits = 0;
  registry.Subscribe("", 0x0800, [&](const EthernetFrame&, const std::string&) { ++ipv4_hits; });
  registry.Subscribe("", 0,      [&](const EthernetFrame&, const std::string&) { ++all_hits; });

  EthernetFrame f;
  f.ethertype = 0x0800;
  registry.SendFrame("veth_filt", f);

  f.ethertype = 0x0806;
  registry.SendFrame("veth_filt", f);

  REQUIRE(ipv4_hits == 1);
  REQUIRE(all_hits  == 2);
}
