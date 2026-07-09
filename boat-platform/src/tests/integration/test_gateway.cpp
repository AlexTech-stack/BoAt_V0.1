#include <catch2/catch_test_macros.hpp>

#include <filesystem>
#include <memory>
#include <span>
#include <vector>

#include <grpcpp/grpcpp.h>

#include "boat/v1/pdu.grpc.pb.h"
#include "boat/v1/scenario.grpc.pb.h"
#include "boat/v1/signal.grpc.pb.h"
#include "boat/v1/simulation.grpc.pb.h"
#include "event_store/event_store.h"
#include "gateway/grpc_gateway/frame_sink.h"
#include "gateway/grpc_gateway/gateway_context.h"
#include "gateway/grpc_gateway/pdu_service_impl.h"
#include "gateway/grpc_gateway/scenario_service_impl.h"
#include "gateway/grpc_gateway/signal_service_impl.h"
#include "gateway/grpc_gateway/simulation_service_impl.h"
#include "can_bus_registry.h"
#include "ethernet_bus_registry.h"
#include "pdu/pdu_router.h"
#include "core/plugin/plugin_manager.h"
#include "gateway/grpc_gateway/rpc_audit_log.h"
#include "replay_engine/replay_engine.h"
#include "scenario/scenario_loader.h"
#include "simulation/simulation_context.h"
#include "trace_store/trace_store.h"

TEST_CASE("Gateway integration runs lifecycle and queries events via RPC", "[integration][gateway]") {
  std::filesystem::remove("boat_config.db");
  const auto temp_dir = std::filesystem::temp_directory_path();
  const auto event_db_path = temp_dir / "boat_integration_gateway_events.db";
  const auto trace_db_path = temp_dir / "boat_integration_gateway_traces.db";
  std::filesystem::remove(event_db_path);
  std::filesystem::remove(trace_db_path);

  boat::core::SimulationContext sim(777, 2);
  boat::core::SignalBus signal_bus;
  boat::core::ScenarioLoader scenario_loader;
  boat::store::SqliteEventStore event_store(event_db_path.string());
  boat::store::FlatFileTraceStore trace_store(trace_db_path.string());
  boat::replay::ReplayController replay_controller(trace_store, event_store, sim.event_bus());
  boat::hil::CanBusRegistry can_registry;  // no interfaces opened in unit tests
  boat::hil::EthernetBusRegistry eth_registry;
  boat::core::PluginManager plugin_manager;
  boat::gateway::FrameSink frame_sink(can_registry, eth_registry);
  boat::gateway::RpcAuditLog audit_log;
  sim.signal_router().SetFaultInjector(&sim.fault_injector());

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

  boat::gateway::PduServiceImpl pdu_service(ctx);
  boat::gateway::ScenarioServiceImpl scenario_service(ctx);
  boat::gateway::SimulationServiceImpl simulation_service(sim, can_registry, eth_registry);
  boat::gateway::SignalServiceImpl signal_service(ctx);

  grpc::ServerBuilder builder;
  builder.AddListeningPort("127.0.0.1:0", grpc::InsecureServerCredentials());
  builder.RegisterService(&pdu_service);
  builder.RegisterService(&scenario_service);
  builder.RegisterService(&simulation_service);
  builder.RegisterService(&signal_service);
  std::unique_ptr<grpc::Server> server = builder.BuildAndStart();
  REQUIRE(server != nullptr);
  auto channel = server->InProcessChannel({});

  auto scenario_stub = boat::v1::ScenarioService::NewStub(channel);
  auto simulation_stub = boat::v1::SimulationService::NewStub(channel);
  auto signal_stub = boat::v1::SignalService::NewStub(channel);

  boat::v1::CreateScenarioRequest create_scenario_request;
  auto* scenario = create_scenario_request.mutable_scenario();
  scenario->set_scenario_id("scn-int");
  scenario->set_name("Integration Scenario");
  scenario->set_content(
      R"({"id":"scn-int","name":"Integration Scenario","version":"1.0","duration_ticks":1000,"seed":777,"plugins":[],"signals":[],"faults":[]})");
  boat::v1::ScenarioResponse create_scenario_response;
  grpc::ClientContext create_scenario_ctx;
  REQUIRE(scenario_stub->CreateScenario(&create_scenario_ctx, create_scenario_request, &create_scenario_response).ok());

  boat::v1::CreateSimulationRequest create_sim_request;
  create_sim_request.set_scenario_id("scn-int");
  boat::v1::SimulationResponse create_sim_response;
  grpc::ClientContext create_sim_ctx;
  REQUIRE(simulation_stub->CreateSimulation(&create_sim_ctx, create_sim_request, &create_sim_response).ok());
  const std::string simulation_id = create_sim_response.simulation().simulation_id();
  REQUIRE_FALSE(simulation_id.empty());

  boat::v1::StartSimulationRequest start_request;
  start_request.set_simulation_id(simulation_id);
  boat::v1::SimulationResponse start_response;
  grpc::ClientContext start_ctx;
  REQUIRE(simulation_stub->StartSimulation(&start_ctx, start_request, &start_response).ok());

  boat::v1::PauseSimulationRequest pause_request;
  pause_request.set_simulation_id(simulation_id);
  boat::v1::SimulationResponse pause_response;
  grpc::ClientContext pause_ctx;
  REQUIRE(simulation_stub->PauseSimulation(&pause_ctx, pause_request, &pause_response).ok());

  boat::v1::StepSimulationRequest step_request;
  step_request.set_simulation_id(simulation_id);
  step_request.set_ticks(1000);
  boat::v1::SimulationResponse step_response;
  grpc::ClientContext step_ctx;
  REQUIRE(simulation_stub->StepSimulation(&step_ctx, step_request, &step_response).ok());
  REQUIRE(sim.clock().tick() >= 1000);

  boat::v1::StopSimulationRequest stop_request;
  stop_request.set_simulation_id(simulation_id);
  boat::v1::SimulationResponse stop_response;
  grpc::ClientContext stop_ctx;
  REQUIRE(simulation_stub->StopSimulation(&stop_ctx, stop_request, &stop_response).ok());

  const std::vector<boat::store::EventRecord> expected = {
      {.id = "e1",
       .simulation_id = simulation_id,
       .tick = 10,
       .wall_time_ns = 100,
       .signal_id = "speed",
       .value_type = 1,
       .value_blob = {'1', '2'},
       .tags = "integration"},
      {.id = "e2",
       .simulation_id = simulation_id,
       .tick = 20,
       .wall_time_ns = 200,
       .signal_id = "speed",
       .value_type = 1,
       .value_blob = {'3', '4'},
       .tags = "integration"},
  };
  event_store.InsertBatch(std::span<const boat::store::EventRecord>(expected.data(), expected.size()));

  boat::v1::GetSignalHistoryRequest history_request;
  history_request.set_simulation_id(simulation_id);
  history_request.set_name("speed");
  boat::v1::SignalHistoryResponse history_response;
  grpc::ClientContext history_ctx;
  REQUIRE(signal_stub->GetSignalHistory(&history_ctx, history_request, &history_response).ok());
  REQUIRE(history_response.values_size() == static_cast<int>(expected.size()));
  REQUIRE(history_response.values(0).name() == "speed");
  REQUIRE(history_response.values(0).tick() == expected[0].tick);
  REQUIRE(history_response.values(1).tick() == expected[1].tick);

  boat::store::EventFilter filter;
  filter.simulation_id = simulation_id;
  filter.signal_id = "speed";
  const auto db_rows = event_store.Query(filter);
  REQUIRE(db_rows.size() == expected.size());

  // ── PDU Group integration RPC tests ──────────────────────────────────────
  {
    // Create and register a PduRouter for the test
    boat::hil::PduRouter test_pdu_router(can_registry, eth_registry);
    plugin_manager.RegisterService("pdu_router", &test_pdu_router);

    auto pdu_stub = boat::v1::PduService::NewStub(channel);

    // Configure a route (no physical interface needed for config)
    boat::v1::ConfigureRouteRequest route_req;
    route_req.mutable_route()->set_pdu_id(0x100);
    route_req.mutable_route()->set_transport(boat::v1::PDU_TRANSPORT_CAN);
    route_req.mutable_route()->set_iface("vcan0");
    boat::v1::ConfigureRouteResponse route_resp;
    grpc::ClientContext route_ctx;
    REQUIRE(pdu_stub->ConfigureRoute(&route_ctx, route_req, &route_resp).ok());
    REQUIRE(route_resp.ok());

    // Configure a group (disabled)
    boat::v1::ConfigureGroupRequest group_req;
    group_req.mutable_group()->set_group_id(1);
    group_req.mutable_group()->set_name("Safety");
    group_req.mutable_group()->add_pdu_ids(0x100);
    group_req.mutable_group()->set_enabled(false);
    boat::v1::ConfigureGroupResponse group_resp;
    grpc::ClientContext group_ctx;
    REQUIRE(pdu_stub->ConfigureGroup(&group_ctx, group_req, &group_resp).ok());
    REQUIRE(group_resp.ok());

    // Send PDU while group disabled → PduRouter returns false, gRPC returns NOT_FOUND
    boat::v1::SendPduRequest send_req;
    send_req.mutable_pdu()->set_pdu_id(0x100);
    send_req.mutable_pdu()->set_payload(std::string({0x01}));
    boat::v1::SendPduResponse send_resp;
    grpc::ClientContext send_ctx;
    auto send_status = pdu_stub->SendPdu(&send_ctx, send_req, &send_resp);
    REQUIRE(send_status.error_code() == grpc::StatusCode::NOT_FOUND);

    // Enable group via RPC
    boat::v1::EnableGroupRequest enable_req;
    enable_req.set_group_id(1);
    boat::v1::EnableGroupResponse enable_resp;
    grpc::ClientContext enable_ctx;
    REQUIRE(pdu_stub->EnableGroup(&enable_ctx, enable_req, &enable_resp).ok());
    REQUIRE(enable_resp.ok());

    // Verify group is now enabled via IsGroupEnabled (list groups)
    // Disable group via RPC
    boat::v1::DisableGroupRequest disable_req;
    disable_req.set_group_id(1);
    boat::v1::DisableGroupResponse disable_resp;
    grpc::ClientContext disable_ctx;
    REQUIRE(pdu_stub->DisableGroup(&disable_ctx, disable_req, &disable_resp).ok());
    REQUIRE(disable_resp.ok());

    // List groups → verify disabled state
    boat::v1::ListGroupsRequest list_groups_req;
    boat::v1::ListGroupsResponse list_groups_resp;
    grpc::ClientContext list_grp_ctx;
    REQUIRE(pdu_stub->ListGroups(&list_grp_ctx, list_groups_req, &list_groups_resp).ok());
    REQUIRE(list_groups_resp.groups_size() == 1);
    REQUIRE(list_groups_resp.groups(0).group_id() == 1);
    REQUIRE(list_groups_resp.groups(0).name() == "Safety");
    REQUIRE_FALSE(list_groups_resp.groups(0).enabled());

    // List routes → verify configured route
    boat::v1::ListRoutesRequest list_route_req;
    boat::v1::ListRoutesResponse list_route_resp;
    grpc::ClientContext list_route_ctx;
    REQUIRE(pdu_stub->ListRoutes(&list_route_ctx, list_route_req, &list_route_resp).ok());
    bool route_found = false;
    for (const auto& r : list_route_resp.routes()) {
      if (r.pdu_id() == 0x100) route_found = true;
    }
    REQUIRE(route_found);

    // Configure route with schedule
    boat::v1::ConfigureRouteRequest sched_req;
    sched_req.mutable_route()->set_pdu_id(0x200);
    sched_req.mutable_route()->set_transport(boat::v1::PDU_TRANSPORT_CAN);
    sched_req.mutable_route()->set_iface("vcan0");
    sched_req.mutable_route()->mutable_schedule()->set_send_type(boat::v1::SEND_TYPE_CYCLIC);
    sched_req.mutable_route()->mutable_schedule()->set_cycle_ms(100);
    sched_req.mutable_route()->mutable_schedule()->set_fast_ms(10);
    sched_req.mutable_route()->mutable_schedule()->set_repetitions(3);
    boat::v1::ConfigureRouteResponse sched_resp;
    grpc::ClientContext sched_ctx;
    REQUIRE(pdu_stub->ConfigureRoute(&sched_ctx, sched_req, &sched_resp).ok());

    // List routes and verify schedule is preserved through proto round-trip
    boat::v1::ListRoutesRequest list2_req;
    boat::v1::ListRoutesResponse list2_resp;
    grpc::ClientContext list2_ctx;
    REQUIRE(pdu_stub->ListRoutes(&list2_ctx, list2_req, &list2_resp).ok());
    bool sched_ok = false;
    for (const auto& r : list2_resp.routes()) {
      if (r.pdu_id() == 0x200 && r.has_schedule() &&
          r.schedule().send_type() == boat::v1::SEND_TYPE_CYCLIC &&
          r.schedule().cycle_ms() == 100 &&
          r.schedule().fast_ms() == 10 &&
          r.schedule().repetitions() == 3) {
        sched_ok = true;
      }
    }
    REQUIRE(sched_ok);

    // Re-enable group before PduRouter shutdown (cleanup)
    boat::v1::EnableGroupRequest enable_cleanup;
    enable_cleanup.set_group_id(1);
    boat::v1::EnableGroupResponse enable_cleanup_resp;
    grpc::ClientContext enable_cleanup_ctx;
    pdu_stub->EnableGroup(&enable_cleanup_ctx, enable_cleanup, &enable_cleanup_resp);
  }

  server->Shutdown();
  sim.scheduler().Stop();
  std::filesystem::remove("boat_config.db");
  std::filesystem::remove(event_db_path);
  std::filesystem::remove(trace_db_path);
}
