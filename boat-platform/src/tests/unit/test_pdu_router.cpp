#include <catch2/catch_test_macros.hpp>

#include <chrono>
#include <memory>
#include <string>
#include <thread>
#include <vector>

#include "core/event/event_bus.h"
#include "can_bus_registry.h"
#include "ethernet_bus_registry.h"
#include "hal/hal_driver.h"
#include "ethernet/ethernet_frame.h"
#include "pdu/ipdumcontainer.h"
#include "pdu/pdu_router.h"
#include "pdu/pdu_types.h"

using namespace boat::hil;

// ── Mock drivers ──────────────────────────────────────────────────────────────

class MockCanDriver : public IHalDriver {
 public:
  bool Open()  override { return true; }
  void Close() override {}
  bool ReadFrame(CanFrame&) override {
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    return false;
  }
  bool WriteFrame(const CanFrame& f) override {
    written.push_back(f);
    return true;
  }
  CanInterfaceInfo GetInfo() const override {
    return {};
  }
  std::vector<CanFrame> written;
};

class MockEthernetDriver : public IEthernetDriver {
 public:
  bool Open()  override { return true; }
  void Close() override {}
  bool ReadFrame(EthernetFrame&) override {
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    return false;
  }
  bool WriteFrame(const EthernetFrame& f) override {
    written.push_back(f);
    return true;
  }
  std::vector<EthernetFrame> written;
};

// ── Fixtures ──────────────────────────────────────────────────────────────────

struct Fixture {
  MockCanDriver*      mock_can  = nullptr;
  MockEthernetDriver* mock_eth  = nullptr;
  boat::core::EventBus event_bus;
  CanBusRegistry      can_reg;
  EthernetBusRegistry eth_reg;
  PduRouter           router{can_reg, eth_reg};

  Fixture() {
    auto can_drv = std::make_shared<MockCanDriver>();
    mock_can = can_drv.get();
    can_reg.Add("vcan0", std::move(can_drv), event_bus);

    auto eth_drv = std::make_unique<MockEthernetDriver>();
    mock_eth = eth_drv.get();
    eth_reg.Add("veth0", std::move(eth_drv));
  }
};

// ── Tests ─────────────────────────────────────────────────────────────────────

TEST_CASE("PduRouter SendPdu over CAN writes correct frame", "[unit][pdu]") {
  Fixture f;
  PduRoute route;
  route.pdu_id    = 0x100;
  route.transport = PduTransport::kCan;
  route.iface     = "vcan0";
  f.router.AddRoute(route);

  const std::vector<uint8_t> payload = {0x01, 0x02, 0x03};
  REQUIRE(f.router.SendPdu(0x100, payload));
  REQUIRE(f.mock_can->written.size() == 1);
  const auto& fr = f.mock_can->written[0];
  REQUIRE(fr.can_id == 0x100);
  REQUIRE(fr.dlc    == 3);
  REQUIRE(fr.data[0] == 0x01);
  REQUIRE(fr.data[2] == 0x03);
}

TEST_CASE("PduRouter SendPdu over CAN uses explicit can_id", "[unit][pdu]") {
  Fixture f;
  PduRoute route;
  route.pdu_id    = 0x200;
  route.transport = PduTransport::kCan;
  route.iface     = "vcan0";
  route.can_id    = 0x7FF;
  f.router.AddRoute(route);

  REQUIRE(f.router.SendPdu(0x200, {0xAB}));
  REQUIRE(f.mock_can->written[0].can_id == 0x7FF);
}

TEST_CASE("PduRouter SendPdu returns false for unknown pdu_id", "[unit][pdu]") {
  Fixture f;
  REQUIRE_FALSE(f.router.SendPdu(0x999, {0x00}));
}

TEST_CASE("PduRouter SendPdu over Ethernet frames pdu_id as 4-byte big-endian header",
          "[unit][pdu]") {
  Fixture f;
  PduRoute route;
  route.pdu_id    = 0x00AA0001;
  route.transport = PduTransport::kEthernet;
  route.iface     = "veth0";
  route.ethertype = 0x88B5;
  route.vlan_id   = 0;
  f.router.AddRoute(route);

  const std::vector<uint8_t> payload = {0xDE, 0xAD};
  REQUIRE(f.router.SendPdu(0x00AA0001, payload));
  REQUIRE(f.mock_eth->written.size() == 1);
  const auto& fr = f.mock_eth->written[0];
  REQUIRE(fr.ethertype == 0x88B5);
  REQUIRE(fr.payload.size() == 6);  // 4-byte header + 2-byte payload
  REQUIRE(fr.payload[0] == 0x00);
  REQUIRE(fr.payload[1] == 0xAA);
  REQUIRE(fr.payload[2] == 0x00);
  REQUIRE(fr.payload[3] == 0x01);
  REQUIRE(fr.payload[4] == 0xDE);
  REQUIRE(fr.payload[5] == 0xAD);
}

TEST_CASE("PduRouter AddRoute defaults ethertype to 0x88B5", "[unit][pdu]") {
  Fixture f;
  PduRoute route;
  route.pdu_id    = 0x300;
  route.transport = PduTransport::kEthernet;
  route.iface     = "veth0";
  route.ethertype = 0;  // leave unset
  f.router.AddRoute(route);

  const auto routes = f.router.ListRoutes();
  REQUIRE(routes.size() == 1);
  REQUIRE(routes[0].ethertype == 0x88B5);
}

TEST_CASE("PduRouter Subscribe receives CAN PDU", "[unit][pdu]") {
  Fixture f;
  PduRoute route;
  route.pdu_id    = 0x100;
  route.transport = PduTransport::kCan;
  route.iface     = "vcan0";
  f.router.AddRoute(route);

  std::vector<PduFrame> received;
  f.router.Subscribe({0x100}, [&](const PduFrame& pdu) {
    received.push_back(pdu);
  });

  // Inject a CAN frame directly via the registry.
  CanFrame cf{};
  cf.can_id = 0x100;
  cf.dlc    = 2;
  cf.data[0] = 0xBE;
  cf.data[1] = 0xEF;
  f.can_reg.SendFrame("vcan0", cf);

  REQUIRE(received.size() == 1);
  REQUIRE(received[0].pdu_id == 0x100);
  REQUIRE(received[0].payload.size() == 2);
  REQUIRE(received[0].payload[0] == 0xBE);
  REQUIRE(received[0].source == PduTransport::kCan);
}

TEST_CASE("PduRouter Subscribe receives Ethernet PDU", "[unit][pdu]") {
  Fixture f;
  PduRoute route;
  route.pdu_id    = 0x00AA0001;
  route.transport = PduTransport::kEthernet;
  route.iface     = "";           // accept any iface
  route.ethertype = 0x88B5;
  route.vlan_id   = 0;
  f.router.AddRoute(route);

  std::vector<PduFrame> received;
  f.router.Subscribe({0x00AA0001}, [&](const PduFrame& pdu) {
    received.push_back(pdu);
  });

  EthernetFrame ef;
  ef.ethertype = 0x88B5;
  ef.vlan_id   = 0;
  ef.payload   = {0x00, 0xAA, 0x00, 0x01, 0xCA, 0xFE};  // pdu_id header + data
  f.eth_reg.SendFrame("veth0", ef);

  REQUIRE(received.size() == 1);
  REQUIRE(received[0].pdu_id == 0x00AA0001);
  REQUIRE(received[0].payload.size() == 2);
  REQUIRE(received[0].payload[0] == 0xCA);
  REQUIRE(received[0].payload[1] == 0xFE);
  REQUIRE(received[0].source == PduTransport::kEthernet);
}

TEST_CASE("PduRouter wildcard subscriber receives all PDUs", "[unit][pdu]") {
  Fixture f;
  PduRoute r1; r1.pdu_id = 0x01; r1.transport = PduTransport::kCan; r1.iface = "vcan0";
  PduRoute r2; r2.pdu_id = 0x02; r2.transport = PduTransport::kCan; r2.iface = "vcan0";
  f.router.AddRoute(r1);
  f.router.AddRoute(r2);

  int count = 0;
  f.router.Subscribe({}, [&](const PduFrame&) { ++count; });

  CanFrame cf1{}; cf1.can_id = 0x01; cf1.dlc = 1; f.can_reg.SendFrame("vcan0", cf1);
  CanFrame cf2{}; cf2.can_id = 0x02; cf2.dlc = 1; f.can_reg.SendFrame("vcan0", cf2);
  REQUIRE(count == 2);
}

TEST_CASE("PduRouter Unsubscribe stops delivery", "[unit][pdu]") {
  Fixture f;
  PduRoute route;
  route.pdu_id    = 0x100;
  route.transport = PduTransport::kCan;
  route.iface     = "vcan0";
  f.router.AddRoute(route);

  int count = 0;
  const auto sid = f.router.Subscribe({0x100}, [&](const PduFrame&) { ++count; });

  CanFrame cf{}; cf.can_id = 0x100; cf.dlc = 1;
  f.can_reg.SendFrame("vcan0", cf);
  REQUIRE(count == 1);

  f.router.Unsubscribe(sid);
  f.can_reg.SendFrame("vcan0", cf);
  REQUIRE(count == 1);  // no second delivery
}

TEST_CASE("PduRouter ListRoutes returns all configured routes", "[unit][pdu]") {
  Fixture f;
  PduRoute r1; r1.pdu_id = 0x10; r1.transport = PduTransport::kCan;      r1.iface = "vcan0";
  PduRoute r2; r2.pdu_id = 0x20; r2.transport = PduTransport::kEthernet; r2.iface = "veth0";
  f.router.AddRoute(r1);
  f.router.AddRoute(r2);

  const auto routes = f.router.ListRoutes();
  REQUIRE(routes.size() == 2);
}

// ── IpduM container unit tests ────────────────────────────────────────────────

TEST_CASE("IpduMSerialize / IpduMDeserialize round-trip single PDU", "[unit][ipdumcontainer]") {
  const IpduMEntry entry{0x00AA0001, {0x01, 0x02, 0x03}};
  const auto buf = IpduMSerialize({entry});

  // Header: 4 bytes ID + 4 bytes DLC = 8 bytes; payload: 3 bytes
  REQUIRE(buf.size() == 11);
  // PDU ID big-endian
  REQUIRE(buf[0] == 0x00);
  REQUIRE(buf[1] == 0xAA);
  REQUIRE(buf[2] == 0x00);
  REQUIRE(buf[3] == 0x01);
  // DLC big-endian
  REQUIRE(buf[4] == 0x00);
  REQUIRE(buf[5] == 0x00);
  REQUIRE(buf[6] == 0x00);
  REQUIRE(buf[7] == 0x03);

  std::vector<IpduMEntry> out;
  REQUIRE(IpduMDeserialize(buf.data(), buf.size(), out));
  REQUIRE(out.size() == 1);
  REQUIRE(out[0].pdu_id == 0x00AA0001);
  REQUIRE(out[0].payload == std::vector<uint8_t>({0x01, 0x02, 0x03}));
}

TEST_CASE("IpduMSerialize / IpduMDeserialize round-trip multiple PDUs", "[unit][ipdumcontainer]") {
  const std::vector<IpduMEntry> entries = {
      {0x00000001, {0xAA, 0xBB}},
      {0x00000002, {0xCC}},
      {0x00000003, {0xDE, 0xAD, 0xBE, 0xEF}},
  };
  const auto buf = IpduMSerialize(entries);

  std::vector<IpduMEntry> out;
  REQUIRE(IpduMDeserialize(buf.data(), buf.size(), out));
  REQUIRE(out.size() == 3);
  REQUIRE(out[0].pdu_id == 0x00000001);
  REQUIRE(out[1].pdu_id == 0x00000002);
  REQUIRE(out[2].payload == std::vector<uint8_t>({0xDE, 0xAD, 0xBE, 0xEF}));
}

TEST_CASE("IpduMDeserialize rejects truncated header", "[unit][ipdumcontainer]") {
  // Only 5 bytes — not enough for an 8-byte header
  const uint8_t buf[] = {0x00, 0x00, 0x00, 0x01, 0x00};
  std::vector<IpduMEntry> out;
  REQUIRE_FALSE(IpduMDeserialize(buf, sizeof(buf), out));
}

TEST_CASE("IpduMDeserialize rejects truncated payload", "[unit][ipdumcontainer]") {
  // Header claims DLC=10 but only 2 payload bytes follow
  const uint8_t buf[] = {
      0x00, 0x00, 0x00, 0x01,  // PDU ID
      0x00, 0x00, 0x00, 0x0A,  // DLC = 10
      0xAA, 0xBB               // only 2 bytes
  };
  std::vector<IpduMEntry> out;
  REQUIRE_FALSE(IpduMDeserialize(buf, sizeof(buf), out));
}

// ── IP/UDP/IpduM send path tests ──────────────────────────────────────────────

TEST_CASE("PduRouter SendPdu over IPv4/UDP builds correct Ethernet frame", "[unit][pdu][ipdumcontainer]") {
  Fixture f;
  PduRoute route;
  route.pdu_id    = 0x00AA0001;
  route.transport = PduTransport::kEthernet;
  route.iface     = "veth0";
  route.src_ip    = {10, 0, 0, 1};
  route.dst_ip    = {10, 0, 0, 2};
  route.src_port  = 1234;
  route.dst_port  = 5678;
  route.ttl       = 64;
  f.router.AddRoute(route);

  REQUIRE(f.router.SendPdu(0x00AA0001, {0xDE, 0xAD}));
  REQUIRE(f.mock_eth->written.size() == 1);

  const auto& fr = f.mock_eth->written[0];
  REQUIRE(fr.ethertype == 0x0800);  // IPv4

  // Parse the IP/UDP/IpduM content
  uint16_t sp = 0, dp = 0;
  std::vector<IpduMEntry> entries;
  REQUIRE(ParseUdpIpPacket(fr.payload.data(), fr.payload.size(), &sp, &dp, entries));
  REQUIRE(sp == 1234);
  REQUIRE(dp == 5678);
  REQUIRE(entries.size() == 1);
  REQUIRE(entries[0].pdu_id == 0x00AA0001);
  REQUIRE(entries[0].payload == std::vector<uint8_t>({0xDE, 0xAD}));
}

TEST_CASE("PduRouter SendPdu over IPv6/UDP builds correct Ethernet frame", "[unit][pdu][ipdumcontainer]") {
  Fixture f;
  PduRoute route;
  route.pdu_id    = 0x00BB0001;
  route.transport = PduTransport::kEthernet;
  route.iface     = "veth0";
  // ::1 → ::2
  route.src_ip = {0,0,0,0, 0,0,0,0, 0,0,0,0, 0,0,0,1};
  route.dst_ip = {0,0,0,0, 0,0,0,0, 0,0,0,0, 0,0,0,2};
  route.src_port = 4000;
  route.dst_port = 5000;
  route.ttl      = 128;
  f.router.AddRoute(route);

  REQUIRE(f.router.SendPdu(0x00BB0001, {0xCA, 0xFE}));
  REQUIRE(f.mock_eth->written.size() == 1);

  const auto& fr = f.mock_eth->written[0];
  REQUIRE(fr.ethertype == 0x86DD);  // IPv6

  uint16_t sp = 0, dp = 0;
  std::vector<IpduMEntry> entries;
  REQUIRE(ParseUdpIpPacket(fr.payload.data(), fr.payload.size(), &sp, &dp, entries));
  REQUIRE(sp == 4000);
  REQUIRE(dp == 5000);
  REQUIRE(entries.size() == 1);
  REQUIRE(entries[0].pdu_id == 0x00BB0001);
  REQUIRE(entries[0].payload == std::vector<uint8_t>({0xCA, 0xFE}));
}

TEST_CASE("PduRouter receives PDU from IPv4/UDP/IpduM Ethernet frame", "[unit][pdu][ipdumcontainer]") {
  Fixture f;
  PduRoute route;
  route.pdu_id    = 0x00AA0001;
  route.transport = PduTransport::kEthernet;
  route.iface     = "";
  route.dst_port  = 5678;
  route.src_ip    = {10, 0, 0, 1};
  route.dst_ip    = {10, 0, 0, 2};
  f.router.AddRoute(route);

  std::vector<PduFrame> received;
  f.router.Subscribe({0x00AA0001}, [&](const PduFrame& p) { received.push_back(p); });

  // Build a real IP/UDP/IpduM frame and inject it
  const auto container = IpduMSerialize({{0x00AA0001, {0x11, 0x22}}});
  const uint8_t src4[4] = {10, 0, 0, 1};
  const uint8_t dst4[4] = {10, 0, 0, 2};
  const auto ip_pkt = BuildUdpIpv4(src4, dst4, 1234, 5678, 64, container);

  EthernetFrame ef;
  ef.ethertype = 0x0800;
  ef.payload   = ip_pkt;
  f.eth_reg.SendFrame("veth0", ef);

  REQUIRE(received.size() == 1);
  REQUIRE(received[0].pdu_id == 0x00AA0001);
  REQUIRE(received[0].payload == std::vector<uint8_t>({0x11, 0x22}));
}

TEST_CASE("PduRouter receives multiple PDUs from one IpduM container", "[unit][pdu][ipdumcontainer]") {
  Fixture f;
  PduRoute r1; r1.pdu_id = 0x01; r1.transport = PduTransport::kEthernet;
               r1.dst_ip = {10,0,0,2}; r1.dst_port = 9000;
  PduRoute r2; r2.pdu_id = 0x02; r2.transport = PduTransport::kEthernet;
               r2.dst_ip = {10,0,0,2}; r2.dst_port = 9000;
  f.router.AddRoute(r1);
  f.router.AddRoute(r2);

  std::vector<uint32_t> ids;
  f.router.Subscribe({}, [&](const PduFrame& p) { ids.push_back(p.pdu_id); });

  // Pack two PDUs into one container
  const auto container = IpduMSerialize({{0x01, {0xAA}}, {0x02, {0xBB}}});
  const uint8_t src4[4] = {10,0,0,1};
  const uint8_t dst4[4] = {10,0,0,2};
  const auto ip_pkt = BuildUdpIpv4(src4, dst4, 0, 9000, 64, container);

  EthernetFrame ef;
  ef.ethertype = 0x0800;
  ef.payload   = ip_pkt;
  f.eth_reg.SendFrame("veth0", ef);

  REQUIRE(ids.size() == 2);
  REQUIRE((ids[0] == 0x01 || ids[0] == 0x02));
  REQUIRE((ids[1] == 0x01 || ids[1] == 0x02));
  REQUIRE(ids[0] != ids[1]);
}

// ── I-PDU Group tests ──────────────────────────────────────────────────────────

TEST_CASE("PduRouter AddGroup stores and ListGroups returns groups", "[unit][pdu][group]") {
  Fixture f;
  PduGroup g;
  g.group_id = 1;
  g.name     = "TestGroup";
  g.pdu_ids  = {0x100, 0x200};
  g.enabled  = true;
  f.router.AddGroup(g);

  const auto groups = f.router.ListGroups();
  REQUIRE(groups.size() == 1);
  REQUIRE(groups[0].group_id == 1);
  REQUIRE(groups[0].name == "TestGroup");
  REQUIRE(groups[0].enabled);
}

TEST_CASE("PduRouter AddGroup replaces existing group with same ID", "[unit][pdu][group]") {
  Fixture f;
  PduGroup g1; g1.group_id = 1; g1.pdu_ids = {0x100};
  PduGroup g2; g2.group_id = 1; g2.pdu_ids = {0x200};
  f.router.AddGroup(g1);
  f.router.AddGroup(g2);

  const auto groups = f.router.ListGroups();
  REQUIRE(groups.size() == 1);
  REQUIRE(groups[0].pdu_ids.size() == 1);
  REQUIRE(groups[0].pdu_ids[0] == 0x200);
}

TEST_CASE("PduRouter EnableGroup / DisableGroup toggles group", "[unit][pdu][group]") {
  Fixture f;
  PduGroup g; g.group_id = 1; g.pdu_ids = {0x100}; g.enabled = false;
  f.router.AddGroup(g);
  REQUIRE_FALSE(f.router.IsGroupEnabled(1));

  f.router.EnableGroup(1);
  REQUIRE(f.router.IsGroupEnabled(1));

  f.router.DisableGroup(1);
  REQUIRE_FALSE(f.router.IsGroupEnabled(1));
}

TEST_CASE("PduRouter SendPdu is blocked by disabled group", "[unit][pdu][group]") {
  Fixture f;
  PduRoute route;
  route.pdu_id    = 0x100;
  route.transport = PduTransport::kCan;
  route.iface     = "vcan0";
  f.router.AddRoute(route);

  // Send before group — should work
  REQUIRE(f.router.SendPdu(0x100, {0x01}));

  // Add disabled group containing 0x100
  PduGroup g; g.group_id = 1; g.pdu_ids = {0x100}; g.enabled = false;
  f.router.AddGroup(g);

  // Send while group disabled — should be gated
  REQUIRE_FALSE(f.router.SendPdu(0x100, {0x02}));

  // Re-enable — should work again
  f.router.EnableGroup(1);
  REQUIRE(f.router.SendPdu(0x100, {0x03}));
}

TEST_CASE("PduRouter disabled group blocks receive dispatch", "[unit][pdu][group]") {
  Fixture f;
  PduRoute route;
  route.pdu_id    = 0x100;
  route.transport = PduTransport::kCan;
  route.iface     = "vcan0";
  f.router.AddRoute(route);

  std::vector<PduFrame> received;
  f.router.Subscribe({0x100}, [&](const PduFrame& p) { received.push_back(p); });

  CanFrame cf{}; cf.can_id = 0x100; cf.dlc = 1;
  f.can_reg.SendFrame("vcan0", cf);
  REQUIRE(received.size() == 1);

  // Disable group, inject again
  PduGroup g; g.group_id = 1; g.pdu_ids = {0x100}; g.enabled = false;
  f.router.AddGroup(g);
  f.can_reg.SendFrame("vcan0", cf);
  REQUIRE(received.size() == 1);  // no new delivery
}

TEST_CASE("PduRouter PDUs not in any group are unaffected by group disable", "[unit][pdu][group]") {
  Fixture f;
  PduRoute r1; r1.pdu_id = 0x100; r1.transport = PduTransport::kCan; r1.iface = "vcan0";
  PduRoute r2; r2.pdu_id = 0x200; r2.transport = PduTransport::kCan; r2.iface = "vcan0";
  f.router.AddRoute(r1);
  f.router.AddRoute(r2);

  PduGroup g; g.group_id = 1; g.pdu_ids = {0x100}; g.enabled = false;
  f.router.AddGroup(g);

  // 0x100 is gated, 0x200 is not in any group → should work
  REQUIRE_FALSE(f.router.SendPdu(0x100, {0x01}));
  REQUIRE(f.router.SendPdu(0x200, {0x01}));
}

// ── Transmission Engine tests ──────────────────────────────────────────────────

TEST_CASE("TransmissionEngine schedules cyclic send", "[unit][pdu][txengine]") {
  PduSchedule sched;
  sched.send_type = SendType::kCyclic;
  sched.cycle_ms  = 100;

  int send_count = 0;
  uint32_t last_id = 0;
  TransmissionEngine engine([&](uint32_t pid, const std::vector<uint8_t>&) {
    ++send_count;
    last_id = pid;
    return true;
  });

  engine.ConfigureSchedule(42, sched);
  engine.UpdatePayload(42, {0xAA});

  // No tick yet → nothing sent
  REQUIRE(send_count == 0);

  // Tick at t=100 → initialises schedule without sending
  engine.OnTick(100);
  REQUIRE(send_count == 0);

  // Tick at t=199 → before first period → should not fire
  engine.OnTick(199);
  REQUIRE(send_count == 0);

  // Tick at t=200 → first period → should fire
  engine.OnTick(200);
  REQUIRE(send_count == 1);
  REQUIRE(last_id == 42);

  // Tick at t=299 → before next period → should not fire
  engine.OnTick(299);
  REQUIRE(send_count == 1);

  // Tick at t=300 → next period → should fire
  engine.OnTick(300);
  REQUIRE(send_count == 2);
}

TEST_CASE("TransmissionEngine OnChange sends on payload change", "[unit][pdu][txengine]") {
  PduSchedule sched;
  sched.send_type = SendType::kOnChange;

  int send_count = 0;
  TransmissionEngine engine([&](uint32_t, const std::vector<uint8_t>&) {
    ++send_count;
    return true;
  });

  engine.ConfigureSchedule(42, sched);
  engine.UpdatePayload(42, {0xAA});  // first set → fires
  REQUIRE(send_count == 1);

  engine.UpdatePayload(42, {0xAA});  // same payload → no fire
  REQUIRE(send_count == 1);

  engine.UpdatePayload(42, {0xBB});  // changed → fires
  REQUIRE(send_count == 2);
}

TEST_CASE("TransmissionEngine OnChange fires n-times repetitions", "[unit][pdu][txengine]") {
  PduSchedule sched;
  sched.send_type = SendType::kOnChange;
  sched.fast_ms    = 10;
  sched.repetitions = 3;

  int send_count = 0;
  TransmissionEngine engine([&](uint32_t, const std::vector<uint8_t>&) {
    ++send_count;
    return true;
  });

  engine.ConfigureSchedule(42, sched);
  engine.UpdatePayload(42, {0xAA});  // immediate send

  // Tick x3 for repetitions
  engine.OnTick(10);  // rep 1
  engine.OnTick(20);  // rep 2
  engine.OnTick(30);  // rep 3

  REQUIRE(send_count == 4);  // 1 immediate + 3 reps
}

TEST_CASE("TransmissionEngine Mixed mode sends cyclic + OnChange", "[unit][pdu][txengine]") {
  PduSchedule sched;
  sched.send_type = SendType::kMixed;
  sched.cycle_ms   = 100;
  sched.fast_ms    = 10;
  sched.repetitions = 2;

  int send_count = 0;
  TransmissionEngine engine([&](uint32_t, const std::vector<uint8_t>&) {
    ++send_count;
    return true;
  });

  engine.ConfigureSchedule(42, sched);
  engine.UpdatePayload(42, {0xAA});  // first set triggers OnChange send, schedules 2 reps

  // Cyclic tick at t=100 initialises the schedule without sending;
  // reps from the first OnChange fire here
  engine.OnTick(100);
  REQUIRE(send_count == 2);  // 1 OnChange + 1 rep

  // Change payload → immediate + re-scheduled reps
  engine.UpdatePayload(42, {0xBB});
  // immediate send, now tick reps
  engine.OnTick(110);
  engine.OnTick(120);
  // count: 1(first OnChange) + 1(rep) + 1(second OnChange) + 2(reps) = 5
  REQUIRE(send_count == 5);
}

TEST_CASE("TransmissionEngine RemoveSchedule stops sending", "[unit][pdu][txengine]") {
  PduSchedule sched;
  sched.send_type = SendType::kCyclic;
  sched.cycle_ms  = 50;

  int send_count = 0;
  TransmissionEngine engine([&](uint32_t, const std::vector<uint8_t>&) {
    ++send_count;
    return true;
  });

  engine.ConfigureSchedule(42, sched);
  engine.UpdatePayload(42, {0x01});
  engine.RemoveSchedule(42);

  engine.OnTick(50);
  REQUIRE(send_count == 0);
}

TEST_CASE("PduRouter OnTick triggers cyclic send from configured route", "[unit][pdu][txengine]") {
  Fixture f;
  PduRoute route;
  route.pdu_id     = 0x100;
  route.transport  = PduTransport::kCan;
  route.iface      = "vcan0";
  route.schedule.send_type = SendType::kCyclic;
  route.schedule.cycle_ms  = 50;
  f.router.AddRoute(route);

  // Manually send to set the payload for the engine
  REQUIRE(f.router.SendPdu(0x100, {0xAA}));

  // First OnTick initialises the schedule without sending
  f.router.OnTick(50);
  REQUIRE(f.mock_can->written.size() == 1);  // only the manual send

  // Second OnTick at (50+50)=100 → first cycle fires
  f.router.OnTick(100);
  REQUIRE(f.mock_can->written.size() >= 2);  // manual send + tick
}
