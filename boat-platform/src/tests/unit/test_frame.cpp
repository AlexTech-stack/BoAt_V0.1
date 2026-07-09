#include "core/frame.h"

#include <catch2/catch_test_macros.hpp>

using boat::core::Frame;

namespace {

template <typename T, typename U>
static bool arrays_equal(const T& a, const U& b, size_t n) {
  for (size_t i = 0; i < n; ++i) {
    if (static_cast<uint8_t>(a[i]) != static_cast<uint8_t>(b[i])) return false;
  }
  return true;
}

}  // namespace

TEST_CASE("Frame default construction", "[frame]") {
  Frame f;
  CHECK(f.bus_type() == Frame::BusType::kUnspecified);
  CHECK(f.iface().empty());
  CHECK(f.timestamp_ns() == 0);
  CHECK(f.payload().empty());
}

TEST_CASE("Frame FromCan factory", "[frame]") {
  std::vector<uint8_t> data = {0x01, 0x02, 0x03, 0x04};
  auto f = Frame::FromCan("vcan0", 0x123, 4, 0x04, data);

  CHECK(f.bus_type() == Frame::BusType::kCan);
  CHECK(f.iface() == "vcan0");
  CHECK(f.can_meta().can_id == 0x123);
  CHECK(f.can_meta().dlc == 4);
  CHECK(f.can_meta().flags == 0x04);
  CHECK(f.payload() == data);
}

TEST_CASE("Frame FromCan FD variant", "[frame]") {
  std::vector<uint8_t> data(64, 0xFF);
  auto f = Frame::FromCan("can0", 0x7FF, 15, 0x01, data, /*is_fd=*/true);

  CHECK(f.bus_type() == Frame::BusType::kCanFd);
  CHECK(f.can_meta().can_id == 0x7FF);
  CHECK(f.can_meta().dlc == 15);
  CHECK(f.can_meta().flags == 0x01);
  CHECK(f.payload().size() == 64);
}

TEST_CASE("Frame FromEthernet factory", "[frame]") {
  uint8_t dst_mac[6] = {0x00, 0x11, 0x22, 0x33, 0x44, 0x55};
  uint8_t src_mac[6] = {0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF};
  uint8_t src_ip[4]   = {10, 0, 0, 1};
  uint8_t dst_ip[4]   = {10, 0, 0, 2};
  std::vector<uint8_t> payload = {0xDE, 0xAD, 0xBE, 0xEF};

  auto f = Frame::FromEthernet("eth0", dst_mac, src_mac, 0x0800, 100,
                                src_ip, 4, dst_ip, payload);

  CHECK(f.bus_type() == Frame::BusType::kEthernet);
  CHECK(f.iface() == "eth0");
  CHECK(f.eth_meta().ethertype == 0x0800);
  CHECK(f.eth_meta().vlan_id == 100);
  CHECK(f.eth_meta().ip_version == 4);
  CHECK(arrays_equal(f.eth_meta().dst_mac, dst_mac, 6));
  CHECK(arrays_equal(f.eth_meta().src_mac, src_mac, 6));
  CHECK(f.payload() == payload);
}

TEST_CASE("Frame FromTcp factory", "[frame]") {
  uint8_t src_ip[4] = {192, 168, 1, 100};
  uint8_t dst_ip[4] = {10, 0, 0, 1};
  std::vector<uint8_t> payload = {'H', 'e', 'l', 'l', 'o'};

  auto f = Frame::FromTcp("eth0", src_ip, 4, dst_ip, 12345, 8080,
                           -1, payload);

  CHECK(f.bus_type() == Frame::BusType::kTcp);
  CHECK(f.tcp_meta().src_port == 12345);
  CHECK(f.tcp_meta().dst_port == 8080);
  CHECK(f.tcp_meta().conn_id == -1);
  CHECK(f.tcp_meta().ip_version == 4);
  CHECK(f.payload() == payload);
}

TEST_CASE("Frame FromPdu factory", "[frame]") {
  std::vector<uint8_t> data = {0xAA, 0xBB};
  auto f = Frame::FromPdu("", 0x300, data);

  CHECK(f.bus_type() == Frame::BusType::kPdu);
  CHECK(f.pdu_meta().pdu_id == 0x300);
  CHECK(f.payload() == data);
}

TEST_CASE("Frame ToAbi / FromAbi CAN round-trip", "[frame][abi]") {
  std::vector<uint8_t> data = {0x01, 0x02, 0x03};
  auto orig = Frame::FromCan("vcan0", 0x456, 3, 0, data);

  BoatFrame abi{};
  orig.ToAbi(&abi);

  auto restored = Frame::FromAbi(abi);

  CHECK(restored.bus_type() == orig.bus_type());
  CHECK(restored.iface() == orig.iface());
  CHECK(restored.timestamp_ns() == orig.timestamp_ns());
  CHECK(restored.can_meta().can_id == orig.can_meta().can_id);
  CHECK(restored.can_meta().dlc == orig.can_meta().dlc);
  CHECK(restored.can_meta().flags == orig.can_meta().flags);
  CHECK(restored.payload() == orig.payload());
}

TEST_CASE("Frame ToAbi / FromAbi Ethernet round-trip", "[frame][abi]") {
  uint8_t dm[6] = {1, 2, 3, 4, 5, 6};
  uint8_t sm[6] = {7, 8, 9, 10, 11, 12};
  uint8_t sip[4] = {10, 0, 0, 1};
  uint8_t dip[4] = {10, 0, 0, 2};
  std::vector<uint8_t> pld = {0xAA};

  auto orig = Frame::FromEthernet("veth0", dm, sm, 0x0800, 42,
                                   sip, 4, dip, pld);

  BoatFrame abi{};
  orig.ToAbi(&abi);

  auto restored = Frame::FromAbi(abi);

  CHECK(restored.bus_type() == orig.bus_type());
  CHECK(restored.iface() == orig.iface());
  CHECK(restored.eth_meta().ethertype == orig.eth_meta().ethertype);
  CHECK(restored.eth_meta().vlan_id == orig.eth_meta().vlan_id);
  CHECK(arrays_equal(restored.eth_meta().dst_mac, orig.eth_meta().dst_mac, 6));
  CHECK(arrays_equal(restored.eth_meta().src_mac, orig.eth_meta().src_mac, 6));
  CHECK(restored.payload() == orig.payload());
}

TEST_CASE("Frame ToAbi / FromAbi PDU round-trip", "[frame][abi]") {
  std::vector<uint8_t> data = {0xFF, 0xEE};
  auto orig = Frame::FromPdu("", 0x700, data);

  BoatFrame abi{};
  orig.ToAbi(&abi);

  auto restored = Frame::FromAbi(abi);

  CHECK(restored.bus_type() == orig.bus_type());
  CHECK(restored.pdu_meta().pdu_id == orig.pdu_meta().pdu_id);
  CHECK(restored.payload() == data);
}

TEST_CASE("Frame ToAbi null iface", "[frame][abi]") {
  auto f = Frame::FromCan("", 0x100, 3, 0, {1, 2, 3});

  BoatFrame abi{};
  f.ToAbi(&abi);

  CHECK(abi.iface == nullptr);
  CHECK(abi.payload_len == 3);
}

TEST_CASE("Frame ToAbi empty payload", "[frame][abi]") {
  auto f = Frame::FromCan("vcan0", 0x100, 0, 0, {});

  BoatFrame abi{};
  f.ToAbi(&abi);

  CHECK(abi.payload == nullptr);
  CHECK(abi.payload_len == 0);
}

TEST_CASE("Frame move semantics", "[frame]") {
  auto f1 = Frame::FromCan("vcan0", 0x100, 8, 0, {1, 2, 3, 4, 5, 6, 7, 8});
  Frame f2 = std::move(f1);

  CHECK(f2.iface() == "vcan0");
  CHECK(f2.can_meta().can_id == 0x100);
  CHECK(f2.payload().size() == 8);
}

TEST_CASE("Frame timestamp propagation", "[frame]") {
  BoatFrame abi{};
  abi.bus_type     = BOAT_BUS_CAN;
  abi.iface        = "can0";
  abi.timestamp_ns = 1234567890123ULL;
  abi.meta.can.can_id = 0x200;
  uint8_t buf[]     = {0x01};
  abi.payload       = buf;
  abi.payload_len   = 1;

  auto f = Frame::FromAbi(abi);
  CHECK(f.timestamp_ns() == 1234567890123ULL);
}
