#include <atomic>
#include <chrono>
#include <csignal>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <memory>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#include <arpa/inet.h>

#include <grpcpp/grpcpp.h>

#include "bus_service_impl.h"
#include "can_bus_registry.h"
#include "can_service_impl.h"
#include "debug_service_impl.h"
#include "ethernet_bus_registry.h"
#include "ethernet_service_impl.h"
#include "device_service_impl.h"
#include "frame_service_impl.h"
#include "rpc_audit_interceptor.h"
#include "rpc_audit_log.h"
#include "fault_service_impl.h"
#include "frame_sink.h"
#include "gateway_context.h"
#include "hil/virtual/virtual_can_driver.h"
#include "hil/can/physical_can_driver.h"
#include "hil/ethernet/virtual_ethernet_driver.h"
#include "hil/ethernet/raw_socket_ethernet_driver.h"
#include "pdu/pdu_types.h"
#include "pdu/tick_timer.h"
#include "pdu_service_impl.h"
#include "metrics_service_impl.h"
#include "plugin/plugin_manager.h"
#include "plugin_service_impl.h"
#include "bus_signal_recorder.h"
#include "replay_engine/replay_engine.h"
#include "replay_service_impl.h"
#include "scenario/scenario_loader.h"
#include "scenario_service_impl.h"
#include "simulation/simulation_context.h"
#include "signal/signal_bus.h"
#include "signal_service_impl.h"
#include "simulation_service_impl.h"
#include "event_store/event_store.h"
#include "trace_store/trace_store.h"
#include "trace_service_impl.h"

namespace {
std::shared_ptr<grpc::Server> g_server;
boat::core::TickScheduler* g_scheduler = nullptr;
std::atomic<bool> g_node_tick_running{false};
std::atomic<bool> g_shutdown_requested{false};
constexpr std::uint64_t kGatewayDeterminismSeed = 777;

std::array<std::uint8_t, 6> ReadInterfaceMac(const std::string& iface) {
  std::array<std::uint8_t, 6> mac{};
  std::ifstream f("/sys/class/net/" + iface + "/address");
  if (!f.is_open()) {
    mac[5] = 0x01;
    return mac;
  }
  std::string line;
  std::getline(f, line);
  if (line.size() < 17) {
    mac[5] = 0x01;
    return mac;
  }
  for (int i = 0; i < 6; ++i) {
    mac[i] = static_cast<std::uint8_t>(
        std::stoul(line.substr(i * 3, 2), nullptr, 16));
  }
  return mac;
}

void HandleSignal(int) {
  // Signal handlers may only call async-signal-safe functions.
  // grpc::Server::Shutdown() acquires internal Abseil mutexes and is not
  // signal-safe -- calling it directly here can land while the main thread
  // is inside Server::Wait() holding that same lock, which Abseil's
  // deadlock detector then aborts on. Just flip a flag; the actual
  // Shutdown()/Stop() calls happen on the dedicated watcher thread in
  // main() below.
  g_shutdown_requested.store(true, std::memory_order_relaxed);
}
}  // namespace

int main() {
  boat::gateway::RpcAuditLog audit_log;
  // Gateway bootstrap uses a fixed seed so startup behavior is deterministic across environments.
  boat::core::SimulationContext sim(kGatewayDeterminismSeed);
  boat::core::SignalBus signal_bus;
  boat::store::SqliteEventStore event_store("boat_events.db");
  boat::store::FlatFileTraceStore trace_store("boat_traces.db");
  boat::replay::ReplayController replay_controller(trace_store, event_store, sim.event_bus());
  boat::core::ScenarioLoader scenario_loader;

  // Build Ethernet bus registry from BOAT_ETH_INTERFACES (comma-separated).
  // Each entry may be:
  //   name                        → auto-assign multicast addr/port by index
  //   name:mcast_addr:port        → explicit e.g. "veth0:239.255.0.1:51000"
  boat::hil::EthernetBusRegistry eth_registry;
  {
    const char* env = std::getenv("BOAT_ETH_INTERFACES");
    if (env != nullptr) {
      std::istringstream ss(env);
      std::string entry;
      std::size_t index = 0;
      while (std::getline(ss, entry, ',')) {
        if (entry.empty()) continue;
        // Parse "name" or "name:mcast_addr:port"
        // "raw:<ifname>" → physical NIC via AF_PACKET; else virtual multicast.
        if (entry.rfind("raw:", 0) == 0) {
          const std::string name = entry.substr(4);
          auto driver = std::make_unique<boat::hil::RawSocketEthernetDriver>(name);
          if (!eth_registry.Add(name, std::move(driver))) {
            std::fprintf(stderr, "[Gateway] Failed to open raw Ethernet interface '%s' "
                         "(check permissions / interface name)\n", name.c_str());
          } else {
            std::fprintf(stderr, "[Gateway] Registered raw Ethernet interface '%s'\n",
                         name.c_str());
          }
        } else {
          std::istringstream es(entry);
          std::string name, mcast, port_str;
          std::getline(es, name, ':');
          std::getline(es, mcast, ':');
          std::getline(es, port_str);
          if (mcast.empty() || port_str.empty()) {
            auto driver = boat::hil::VirtualEthernetDriver::FromIndex(name, index);
            eth_registry.Add(name, std::move(driver));
          } else {
            const auto port = static_cast<std::uint16_t>(std::stoul(port_str));
            auto driver = std::make_unique<boat::hil::VirtualEthernetDriver>(
                name, mcast, port);
            eth_registry.Add(name, std::move(driver));
          }
        }
        ++index;
      }
    }
  }

  // Build CAN bus registry from BOAT_CAN_INTERFACES (comma-separated, default "vcan0").
  // Interfaces named "vcan*" use VirtualCanDriver; all others use PhysicalCanDriver
  // (which reads sysfs for driver metadata and works with any SocketCAN-compatible
  // hardware, including PEAK PCAN, Kvaser, gs_usb, etc.).
  boat::hil::CanBusRegistry can_registry;
  {
    const char* env = std::getenv("BOAT_CAN_INTERFACES");
    const std::string ifaces_str = env ? env : "vcan0";
    std::istringstream ss(ifaces_str);
    std::string iface;
    while (std::getline(ss, iface, ',')) {
      if (iface.empty()) continue;

      std::shared_ptr<boat::hil::IHalDriver> driver;
      if (iface.size() >= 4 && iface.compare(0, 4, "vcan") == 0) {
        driver = std::make_shared<boat::hil::VirtualCanDriver>(iface);
      } else {
        driver = std::make_shared<boat::hil::PhysicalCanDriver>(iface);
      }

      // Capture info before driver is moved into the registry.
      auto info = driver->GetInfo();
      if (can_registry.Add(iface, std::move(driver), sim.event_bus())) {
        std::fprintf(stderr, "[Gateway] Registered CAN interface '%s' "
                     "(driver=%s, fd=%s, state=%s)\n",
                     iface.c_str(),
                     info.driver_name.c_str(),
                     info.fd_support ? "yes" : "no",
                     info.state.c_str());
      } else {
        std::fprintf(stderr, "[Gateway] Failed to open CAN interface '%s' "
                     "(check interface name / permissions)\n", iface.c_str());
      }
    }
  }

  // The single core frame -> wire sink.  Every producer (plugins, replay, gRPC
  // FrameService) transmits through this one path; the registries own loopback
  // tagging and RX dispatch.
  boat::gateway::FrameSink frame_sink(can_registry, eth_registry);

  // Node manager: loads permanent always-on plugins from BOAT_NODE_PLUGINS
  // (comma-separated .so paths). These are wired to the CAN/Ethernet bus at
  // startup and run independently of any simulation lifecycle.
  boat::core::PluginManager node_manager;
  {
    node_manager.SetBusPublisher([&signal_bus](const char* name, double value) {
      signal_bus.Publish(name, value);
    });

    // v8: node plugins transmit frames through the single core sink.
    node_manager.SetFramePublisher([&frame_sink](const BoatFrame& f) {
      frame_sink.Publish(boat::core::Frame::FromAbi(f));
    });

    // v8: Forward CAN/Eth frames to node plugins via unified dispatch.
    can_registry.SubscribeFrame([&node_manager](const boat::core::Frame& f) {
      BoatFrame abi{};
      f.ToAbi(&abi);
      node_manager.DispatchFrame(abi);
    });
    eth_registry.SubscribeFrame([&node_manager](const boat::core::Frame& f) {
      BoatFrame abi{};
      f.ToAbi(&abi);
      node_manager.DispatchFrame(abi);
    });

    // v9: Forward always-on signal-bus values to node plugins that implement
    // on_signal (device setpoints/commands, e.g. "psu.main.voltage.set",
    // "relay.kl15.set"). Numeric/bool signals only; plugins filter by name.
    // Subscribed before any plugin is loaded so no publish races this setup.
    signal_bus.Subscribe({}, [&node_manager](const boat::core::BusSignal& s) {
      double value = 0.0;
      if (const auto* d = std::get_if<double>(&s.value)) {
        value = *d;
      } else if (const auto* i = std::get_if<std::int64_t>(&s.value)) {
        value = static_cast<double>(*i);
      } else if (const auto* b = std::get_if<bool>(&s.value)) {
        value = *b ? 1.0 : 0.0;
      } else {
        return;  // string/bytes are not deliverable as a double
      }
      node_manager.DispatchSignal(s.name.c_str(), value);
    });

    // Load node plugins from BOAT_NODE_PLUGINS env var.
    // Entries are separated by commas.  Each entry may optionally append a JSON
    // config with '?':
    //   ./can_tp.so?{"iface":"can0"},./tcp.so?{"mode":"server","port":8080}
    // The split is brace-aware so commas *inside* a {...} config do not split
    // an entry.
    {
      const char* nodes_env = std::getenv("BOAT_NODE_PLUGINS");
      if (nodes_env != nullptr) {
        std::vector<std::string> entries;
        std::string cur;
        int brace_depth = 0;
        for (const char* c = nodes_env; *c != '\0'; ++c) {
          if (*c == '{') ++brace_depth;
          else if (*c == '}') { if (brace_depth > 0) --brace_depth; }
          if (*c == ',' && brace_depth == 0) {
            entries.push_back(cur);
            cur.clear();
          } else {
            cur.push_back(*c);
          }
        }
        entries.push_back(cur);

        for (const auto& entry : entries) {
          if (entry.empty()) continue;
          auto qpos = entry.find('?');
          std::string so_path  = entry.substr(0, qpos);
          std::string config   = (qpos != std::string::npos)
                                    ? entry.substr(qpos + 1) : "{}";
          try {
            node_manager.Load(so_path, config);
            std::fprintf(stderr, "[Gateway] Loaded plugin '%s'\n",
                         so_path.c_str());
          } catch (const std::exception& ex) {
            std::fprintf(stderr, "[Gateway] Failed to load plugin '%s': %s\n",
                         so_path.c_str(), ex.what());
          }
        }
      }
    }
    }

  // Optional: record always-on bus signals (device measurements, etc.) into the
  // event store so they can be replayed as named signals via
  // `replay from-events --sim-id <tag>`. Off by default — no effect on the
  // determinism seed test. Enable with BOAT_RECORD_BUS_SIGNALS=<sim_id>;
  // narrow with BOAT_RECORD_BUS_PREFIXES=psu.,relay. (comma-separated).
  std::unique_ptr<boat::replay::BusSignalRecorder> bus_recorder;
  {
    const char* rec_env = std::getenv("BOAT_RECORD_BUS_SIGNALS");
    if (rec_env != nullptr && rec_env[0] != '\0') {
      boat::replay::BusSignalRecorder::Config rc;
      rc.simulation_id = rec_env;
      const char* pfx_env = std::getenv("BOAT_RECORD_BUS_PREFIXES");
      if (pfx_env != nullptr && pfx_env[0] != '\0') {
        std::istringstream ps(pfx_env);
        std::string p;
        while (std::getline(ps, p, ',')) {
          if (!p.empty()) rc.prefixes.push_back(p);
        }
      }
      bus_recorder = std::make_unique<boat::replay::BusSignalRecorder>(
          signal_bus, event_store, rc);
      bus_recorder->Start();
      std::fprintf(stderr,
                   "[Gateway] Recording bus signals -> event store "
                   "(sim_id=%s%s)\n",
                   rec_env, rc.prefixes.empty() ? "" : ", filtered");
    }
  }

  // Replay transmits each trace frame straight through the single core sink.
  // The registry's RX dispatch then delivers it to node plugins' on_frame
  // (tagged self-sent), so plugins still observe replayed traffic without a
  // dedicated forwarder plugin in the loop.
  replay_controller.SetEventForwarder(
      [&frame_sink](const boat::core::Frame& core_frame) {
        frame_sink.Publish(core_frame);
      });

  // Event-store (signal-domain) replay re-publishes each recorded event as its
  // original named signal on the always-on signal bus — so a replayed device
  // curve (e.g. psu.main.voltage.meas) is observed by plugins/subscribers
  // exactly as when it was recorded, rather than as synthetic CAN traffic.
  replay_controller.SetSignalForwarder(
      [&signal_bus](const std::string& name, double value) {
        signal_bus.Publish(name, value);
      });


  // Wire the PDU publisher so plugins (e.g. CanTp) can deliver reassembled
  // I-PDUs into the frame bus (handled by PduRouter plugin if loaded).
  node_manager.SetPduPublisher([&node_manager](const BoatPduFrame& f) {
    if (f.payload == nullptr) return;
    BoatFrame bf{};
    bf.bus_type = BOAT_BUS_PDU;
    bf.meta.pdu.pdu_id = f.pdu_id;
    bf.payload = const_cast<uint8_t*>(f.payload);
    bf.payload_len = f.payload_len;
    node_manager.DispatchFrame(bf);
  });

  // Start a background tick thread for node plugins and PDU transmission engine.
  // The tick interval sets the minimum achievable PDU cycle time.
  //   BOAT_NODE_TICK_MS=N   — set tick in ms (default 1)
  //   BOAT_NODE_TICK_US=N   — set tick in μs (overrides MS when set)
  //   Both use TimerfdTickTimer (Linux timerfd, absolute-time scheduling).
  {
    using namespace std::chrono_literals;
    std::chrono::nanoseconds tick_ns = 1ms;  // default

    const char* us_env = std::getenv("BOAT_NODE_TICK_US");
    if (us_env != nullptr) {
      char* end = nullptr;
      auto val = std::strtoul(us_env, &end, 10);
      if (end != us_env && val > 0) {
        tick_ns = std::chrono::microseconds(val);
      }
    } else {
      const char* ms_env = std::getenv("BOAT_NODE_TICK_MS");
      if (ms_env != nullptr) {
        char* end = nullptr;
        auto val = std::strtoul(ms_env, &end, 10);
        if (end != ms_env && val > 0) {
          tick_ns = std::chrono::milliseconds(val);
        }
      }
    }

    auto timer = boat::hil::TickTimer::Create(tick_ns);
    g_node_tick_running.store(true, std::memory_order_release);
    std::thread([&node_manager, timer = std::move(timer)]() {
      std::uint64_t tick = 0;
      while (g_node_tick_running.load(std::memory_order_acquire)) {
        if (!timer->WaitForNextTick()) break;
        node_manager.TickAll(tick++);
        // PduRouter plugin handles its own OnTick via PluginManager::TickAll
      }
    }).detach();
  }

  boat::gateway::GatewayContext ctx{
      .sim = sim,
      .signal_bus = signal_bus,
      .scenario_loader = scenario_loader,
      .event_store = event_store,
      .trace_store = trace_store,
      .replay_controller = replay_controller,
      .can_bus_registry = can_registry,
      .ethernet_bus_registry = eth_registry,
      .plugin_manager = node_manager,
      .frame_sink = frame_sink,
      .audit_log = audit_log,
  };

  boat::gateway::BusServiceImpl      bus_impl(audit_log, signal_bus);
  boat::gateway::EthernetServiceImpl ethernet_impl(ctx);
  boat::gateway::SimulationServiceImpl simulation_impl(sim, can_registry, eth_registry);
  boat::gateway::SignalServiceImpl signal_impl(ctx);
  boat::gateway::ScenarioServiceImpl scenario_impl(ctx);
  boat::gateway::ReplayServiceImpl replay_impl(ctx);
  boat::gateway::PluginServiceImpl plugin_impl(ctx);
  boat::gateway::MetricsServiceImpl metrics_impl(ctx);
  boat::gateway::TraceServiceImpl trace_impl(ctx);
  boat::gateway::FaultServiceImpl fault_impl(ctx);
  boat::gateway::CanServiceImpl can_impl(ctx);
  boat::gateway::PduServiceImpl pdu_impl(ctx);
  boat::gateway::DebugServiceImpl debug_impl(audit_log);
  boat::gateway::FrameServiceImpl frame_impl(ctx);
  boat::gateway::DeviceServiceImpl device_impl(ctx);

  grpc::ServerBuilder builder;
  builder.AddListeningPort("0.0.0.0:50051", grpc::InsecureServerCredentials());

  // Register the audit interceptor — captures every RPC call automatically.
  std::vector<std::unique_ptr<grpc::experimental::ServerInterceptorFactoryInterface>>
      interceptors;
  interceptors.push_back(
      std::make_unique<boat::gateway::RpcAuditInterceptorFactory>(audit_log));
  builder.experimental().SetInterceptorCreators(std::move(interceptors));
  builder.RegisterService(&bus_impl);
  builder.RegisterService(&ethernet_impl);
  builder.RegisterService(&simulation_impl);
  builder.RegisterService(&signal_impl);
  builder.RegisterService(&scenario_impl);
  builder.RegisterService(&replay_impl);
  builder.RegisterService(&plugin_impl);
  builder.RegisterService(&metrics_impl);
  builder.RegisterService(&trace_impl);
  builder.RegisterService(&fault_impl);
  builder.RegisterService(&can_impl);
  builder.RegisterService(&pdu_impl);
  builder.RegisterService(&debug_impl);
  builder.RegisterService(&frame_impl);
  builder.RegisterService(&device_impl);

  g_server = builder.BuildAndStart();
  g_scheduler = &sim.scheduler();
  std::signal(SIGINT, HandleSignal);
  std::signal(SIGTERM, HandleSignal);

  // Shutdown() must not be called from the signal handler itself (see
  // HandleSignal); this thread polls the flag it sets and performs the
  // actual shutdown from a normal thread context instead.
  std::thread shutdown_watcher([] {
    while (!g_shutdown_requested.load(std::memory_order_relaxed)) {
      std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
    if (g_server) {
      g_server->Shutdown();
    }
    if (g_scheduler != nullptr) {
      g_scheduler->Stop();
    }
  });

  if (g_server) {
    g_server->Wait();
  }
  g_shutdown_requested.store(true, std::memory_order_relaxed);
  shutdown_watcher.join();
  sim.scheduler().Stop();
  g_node_tick_running.store(false, std::memory_order_release);
  node_manager.ShutdownAll();
  eth_registry.StopAll();
  return 0;
}
