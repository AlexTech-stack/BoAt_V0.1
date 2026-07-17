#include <catch2/catch_test_macros.hpp>

#include <filesystem>
#include <memory>
#include <thread>

#include <grpcpp/grpcpp.h>

#include "boat/v1/replay.grpc.pb.h"
#include "can_bus_registry.h"
#include "ethernet_bus_registry.h"
#include "event_store/event_store.h"
#include "gateway/grpc_gateway/frame_sink.h"
#include "gateway/grpc_gateway/gateway_context.h"
#include "gateway/grpc_gateway/replay_service_impl.h"
#include "gateway/grpc_gateway/rpc_audit_log.h"
#include "core/plugin/plugin_manager.h"
#include "replay_engine/replay_engine.h"
#include "scenario/scenario_loader.h"
#include "simulation/simulation_context.h"
#include "trace_store/trace_store.h"

namespace {

std::vector<std::uint8_t> BuildTestTraceData() {
  std::vector<std::uint8_t> data;
  constexpr std::uint32_t magic = 0xB0A7B0A7;
  for (std::uint64_t tick = 100; tick <= 200; tick += 10) {
    std::uint32_t event_type = 100;
    std::uint32_t payload_size = 2;
    std::int64_t wall = static_cast<std::int64_t>(tick * 1'000'000ULL);

    auto append = [&](const void* src, size_t n) {
      const auto* p = static_cast<const std::uint8_t*>(src);
      data.insert(data.end(), p, p + n);
    };

    append(&magic, 4);
    append(&event_type, 4);
    append(&tick, 8);
    append(&wall, 8);
    append(&payload_size, 4);
    std::uint8_t val = static_cast<std::uint8_t>(tick & 0xFF);
    append(&val, 1);
    append(&val, 1);
  }
  return data;
}

struct IntegrationFixture {
  std::string event_db;
  std::string trace_db;
  std::string trace_file;

  boat::core::SimulationContext sim{777, 2};
  boat::core::SignalBus signal_bus;
  boat::core::ScenarioLoader scenario_loader;
  boat::store::SqliteEventStore event_store;
  boat::store::FlatFileTraceStore trace_store;
  boat::replay::ReplayController replay_controller{trace_store, event_store, sim.event_bus()};
  boat::hil::CanBusRegistry can_registry;
  boat::hil::EthernetBusRegistry eth_registry;
  boat::core::PluginManager plugin_manager;
  boat::gateway::FrameSink frame_sink{can_registry, eth_registry};
  boat::gateway::RpcAuditLog audit_log;
  boat::gateway::GatewayContext ctx{
      .sim = sim,
      .signal_bus = signal_bus,
      .scenario_loader = scenario_loader,
      .event_store = event_store,
      .trace_store = trace_store,
      .replay_controller = replay_controller,
      .can_bus_registry = can_registry,
      .ethernet_bus_registry = eth_registry,
      .plugin_manager = plugin_manager,
      .frame_sink = frame_sink,
      .audit_log = audit_log,
  };
  boat::gateway::ReplayServiceImpl replay_service{ctx};

  IntegrationFixture(const std::string& prefix)
      : event_db((std::filesystem::temp_directory_path() / (prefix + "_events.db")).string()),
        trace_db((std::filesystem::temp_directory_path() / (prefix + "_traces.db")).string()),
        trace_file((std::filesystem::temp_directory_path() / (prefix + "_trace.bin")).string()),
        event_store(event_db),
        trace_store(trace_db) {
    std::filesystem::remove(event_db);
    std::filesystem::remove(trace_db);
    auto trace_data = BuildTestTraceData();
    boat::store::TraceRecord meta;
    meta.id = prefix;
    meta.storage_path = trace_file;
    meta.format = boat::store::TraceRecord::Format::BINARY;
    trace_store.WriteTrace(meta, std::span<const std::uint8_t>(trace_data));
  }

  ~IntegrationFixture() {
    replay_controller.Stop();
    sim.scheduler().Stop();
    std::filesystem::remove(event_db);
    std::filesystem::remove(trace_db);
    std::filesystem::remove(trace_file);
  }
};

}  // namespace

TEST_CASE("ReplayService StartReplay accepts valid trace and rejects empty", "[integration][replay]") {
  IntegrationFixture f("r1");

  grpc::ServerBuilder builder;
  builder.AddListeningPort("127.0.0.1:0", grpc::InsecureServerCredentials());
  builder.RegisterService(&f.replay_service);
  std::unique_ptr<grpc::Server> server = builder.BuildAndStart();
  REQUIRE(server != nullptr);
  auto channel = server->InProcessChannel({});
  auto stub = boat::v1::ReplayService::NewStub(channel);

  grpc::ClientContext start_ctx;
  boat::v1::StartReplayRequest start_req;
  start_req.set_trace_id("r1");
  start_req.set_speed(boat::v1::REPLAY_SPEED_ACCELERATED);
  start_req.set_speed_multiplier(100.0);
  boat::v1::ReplayControlResponse start_resp;
  auto status = stub->StartReplay(&start_ctx, start_req, &start_resp);
  REQUIRE(status.ok());
  REQUIRE(start_resp.accepted());
  REQUIRE_FALSE(start_resp.replay_id().empty());

  grpc::ClientContext empty_ctx;
  boat::v1::StartReplayRequest empty_req;
  empty_req.set_trace_id("");
  boat::v1::ReplayControlResponse empty_resp;
  auto empty_status = stub->StartReplay(&empty_ctx, empty_req, &empty_resp);
  REQUIRE(empty_status.error_code() == grpc::StatusCode::INVALID_ARGUMENT);

  grpc::ClientContext stop_ctx;
  boat::v1::StopReplayRequest stop_req;
  stop_req.set_replay_id(start_resp.replay_id());
  boat::v1::ReplayControlResponse stop_resp;
  auto stop_status = stub->StopReplay(&stop_ctx, stop_req, &stop_resp);
  REQUIRE(stop_status.ok());

  server->Shutdown();
}

TEST_CASE("ReplayService controls return NOT_FOUND for unknown replay", "[integration][replay]") {
  IntegrationFixture f("r2");

  grpc::ServerBuilder builder;
  builder.AddListeningPort("127.0.0.1:0", grpc::InsecureServerCredentials());
  builder.RegisterService(&f.replay_service);
  std::unique_ptr<grpc::Server> server = builder.BuildAndStart();
  REQUIRE(server != nullptr);
  auto channel = server->InProcessChannel({});
  auto stub = boat::v1::ReplayService::NewStub(channel);

  grpc::ClientContext pause_ctx;
  boat::v1::PauseReplayRequest pause_req;
  pause_req.set_replay_id("unknown");
  boat::v1::ReplayControlResponse pause_resp;
  auto pause_status = stub->PauseReplay(&pause_ctx, pause_req, &pause_resp);
  REQUIRE(pause_status.error_code() == grpc::StatusCode::NOT_FOUND);

  grpc::ClientContext resume_ctx;
  boat::v1::ResumeReplayRequest resume_req;
  resume_req.set_replay_id("unknown");
  boat::v1::ReplayControlResponse resume_resp;
  auto resume_status = stub->ResumeReplay(&resume_ctx, resume_req, &resume_resp);
  REQUIRE(resume_status.error_code() == grpc::StatusCode::NOT_FOUND);

  grpc::ClientContext stop_ctx;
  boat::v1::StopReplayRequest stop_req;
  stop_req.set_replay_id("unknown");
  boat::v1::ReplayControlResponse stop_resp;
  auto stop_status = stub->StopReplay(&stop_ctx, stop_req, &stop_resp);
  REQUIRE(stop_status.error_code() == grpc::StatusCode::NOT_FOUND);

  grpc::ClientContext seek_ctx;
  boat::v1::SeekReplayRequest seek_req;
  seek_req.set_replay_id("unknown");
  seek_req.set_tick(100);
  boat::v1::ReplayControlResponse seek_resp;
  auto seek_status = stub->SeekReplay(&seek_ctx, seek_req, &seek_resp);
  REQUIRE(seek_status.error_code() == grpc::StatusCode::NOT_FOUND);

  grpc::ClientContext stream_ctx;
  boat::v1::StreamReplayRequest stream_req;
  stream_req.set_replay_id("unknown");
  auto reader = stub->StreamReplay(&stream_ctx, stream_req);
  auto stream_status = reader->Finish();
  REQUIRE(stream_status.error_code() == grpc::StatusCode::NOT_FOUND);

  server->Shutdown();
}

TEST_CASE("ReplayService Pause/Resume/Stop lifecycle via gRPC", "[integration][replay]") {
  IntegrationFixture f("r3");

  grpc::ServerBuilder builder;
  builder.AddListeningPort("127.0.0.1:0", grpc::InsecureServerCredentials());
  builder.RegisterService(&f.replay_service);
  std::unique_ptr<grpc::Server> server = builder.BuildAndStart();
  REQUIRE(server != nullptr);
  auto channel = server->InProcessChannel({});
  auto stub = boat::v1::ReplayService::NewStub(channel);

  grpc::ClientContext start_ctx;
  boat::v1::StartReplayRequest start_req;
  start_req.set_trace_id("r3");
  start_req.set_speed(boat::v1::REPLAY_SPEED_ACCELERATED);
  start_req.set_speed_multiplier(10.0);
  boat::v1::ReplayControlResponse start_resp;
  auto start_status = stub->StartReplay(&start_ctx, start_req, &start_resp);
  REQUIRE(start_status.ok());
  const std::string replay_id = start_resp.replay_id();
  REQUIRE_FALSE(replay_id.empty());

  std::this_thread::sleep_for(std::chrono::milliseconds(30));

  grpc::ClientContext pause_ctx;
  boat::v1::PauseReplayRequest pause_req;
  pause_req.set_replay_id(replay_id);
  boat::v1::ReplayControlResponse pause_resp;
  auto pause_status = stub->PauseReplay(&pause_ctx, pause_req, &pause_resp);
  REQUIRE(pause_status.ok());
  REQUIRE(pause_resp.accepted());

  grpc::ClientContext resume_ctx;
  boat::v1::ResumeReplayRequest resume_req;
  resume_req.set_replay_id(replay_id);
  boat::v1::ReplayControlResponse resume_resp;
  auto resume_status = stub->ResumeReplay(&resume_ctx, resume_req, &resume_resp);
  REQUIRE(resume_status.ok());
  REQUIRE(resume_resp.accepted());

  std::this_thread::sleep_for(std::chrono::milliseconds(30));

  grpc::ClientContext stop_ctx;
  boat::v1::StopReplayRequest stop_req;
  stop_req.set_replay_id(replay_id);
  boat::v1::ReplayControlResponse stop_resp;
  auto stop_status = stub->StopReplay(&stop_ctx, stop_req, &stop_resp);
  REQUIRE(stop_status.ok());
  REQUIRE(stop_resp.accepted());

  grpc::ClientContext stop2_ctx;
  boat::v1::ReplayControlResponse stop2_resp;
  auto stop2_status = stub->StopReplay(&stop2_ctx, stop_req, &stop2_resp);
  REQUIRE(stop2_status.error_code() == grpc::StatusCode::NOT_FOUND);

  server->Shutdown();
}

TEST_CASE("ReplayService StartReplay invalidates the previous replay_id", "[integration][replay]") {
  // Only one boat::replay::ReplayController is ever shared across all
  // replays, so starting a second replay silently stops whatever was
  // playing under the first replay_id. active_replays_ must reflect that --
  // otherwise a stale replay_id from a superseded StartReplay call would
  // still pass the PauseReplay/ResumeReplay/StopReplay/SeekReplay lookup
  // and end up controlling the *new* replay under the old name.
  IntegrationFixture f("r5a");
  boat::store::TraceRecord meta_b;
  meta_b.id = "r5b";
  meta_b.storage_path = f.trace_file + "_b";
  meta_b.format = boat::store::TraceRecord::Format::BINARY;
  f.trace_store.WriteTrace(meta_b, std::span<const std::uint8_t>(BuildTestTraceData()));

  grpc::ServerBuilder builder;
  builder.AddListeningPort("127.0.0.1:0", grpc::InsecureServerCredentials());
  builder.RegisterService(&f.replay_service);
  std::unique_ptr<grpc::Server> server = builder.BuildAndStart();
  REQUIRE(server != nullptr);
  auto channel = server->InProcessChannel({});
  auto stub = boat::v1::ReplayService::NewStub(channel);

  grpc::ClientContext start_a_ctx;
  boat::v1::StartReplayRequest start_a_req;
  start_a_req.set_trace_id("r5a");
  start_a_req.set_speed(boat::v1::REPLAY_SPEED_ACCELERATED);
  start_a_req.set_speed_multiplier(100.0);
  boat::v1::ReplayControlResponse start_a_resp;
  REQUIRE(stub->StartReplay(&start_a_ctx, start_a_req, &start_a_resp).ok());
  const std::string replay_id_a = start_a_resp.replay_id();
  REQUIRE_FALSE(replay_id_a.empty());

  grpc::ClientContext start_b_ctx;
  boat::v1::StartReplayRequest start_b_req;
  start_b_req.set_trace_id("r5b");
  start_b_req.set_speed(boat::v1::REPLAY_SPEED_ACCELERATED);
  start_b_req.set_speed_multiplier(100.0);
  boat::v1::ReplayControlResponse start_b_resp;
  REQUIRE(stub->StartReplay(&start_b_ctx, start_b_req, &start_b_resp).ok());
  const std::string replay_id_b = start_b_resp.replay_id();
  REQUIRE_FALSE(replay_id_b.empty());
  REQUIRE(replay_id_a != replay_id_b);

  // The old replay_id must no longer be controllable -- it was superseded,
  // not merely one of several concurrently active replays.
  grpc::ClientContext pause_a_ctx;
  boat::v1::PauseReplayRequest pause_a_req;
  pause_a_req.set_replay_id(replay_id_a);
  boat::v1::ReplayControlResponse pause_a_resp;
  auto pause_a_status = stub->PauseReplay(&pause_a_ctx, pause_a_req, &pause_a_resp);
  REQUIRE(pause_a_status.error_code() == grpc::StatusCode::NOT_FOUND);

  // The current replay_id must still work.
  grpc::ClientContext stop_b_ctx;
  boat::v1::StopReplayRequest stop_b_req;
  stop_b_req.set_replay_id(replay_id_b);
  boat::v1::ReplayControlResponse stop_b_resp;
  auto stop_b_status = stub->StopReplay(&stop_b_ctx, stop_b_req, &stop_b_resp);
  REQUIRE(stop_b_status.ok());
  REQUIRE(stop_b_resp.accepted());

  server->Shutdown();
}

TEST_CASE("ReplayService speed proto mapping works for all modes", "[integration][replay]") {
  IntegrationFixture f("r4");

  grpc::ServerBuilder builder;
  builder.AddListeningPort("127.0.0.1:0", grpc::InsecureServerCredentials());
  builder.RegisterService(&f.replay_service);
  std::unique_ptr<grpc::Server> server = builder.BuildAndStart();
  REQUIRE(server != nullptr);
  auto channel = server->InProcessChannel({});
  auto stub = boat::v1::ReplayService::NewStub(channel);

  std::vector<boat::v1::ReplaySpeed> test_speeds = {
      boat::v1::REPLAY_SPEED_REAL_TIME,
      boat::v1::REPLAY_SPEED_ACCELERATED,
      boat::v1::REPLAY_SPEED_STEP_BY_STEP,
  };

  for (auto speed : test_speeds) {
    grpc::ClientContext start_ctx;
    boat::v1::StartReplayRequest start_req;
    start_req.set_trace_id("r4");
    start_req.set_speed(speed);
    start_req.set_speed_multiplier(500.0);
    boat::v1::ReplayControlResponse start_resp;
    auto status = stub->StartReplay(&start_ctx, start_req, &start_resp);
    REQUIRE(status.ok());
    REQUIRE(start_resp.accepted());
    std::this_thread::sleep_for(std::chrono::milliseconds(30));

    grpc::ClientContext stop_ctx;
    boat::v1::StopReplayRequest stop_req;
    stop_req.set_replay_id(start_resp.replay_id());
    boat::v1::ReplayControlResponse stop_resp;
    stub->StopReplay(&stop_ctx, stop_req, &stop_resp);
  }

  server->Shutdown();
}
