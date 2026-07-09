#include <catch2/catch_test_macros.hpp>

#include <filesystem>
#include <span>
#include <vector>

#include "event_store/event_store.h"

TEST_CASE("SqliteEventStore InsertBatch and Query", "[unit][sqlite_event_store]") {
  const auto db_path = std::filesystem::temp_directory_path() / "boat_unit_event_store.db";
  std::filesystem::remove(db_path);

  boat::store::SqliteEventStore store(db_path.string());

  const std::vector<boat::store::EventRecord> events = {
      {.id = "e1",
       .simulation_id = "sim-a",
       .tick = 10,
       .wall_time_ns = 1000,
       .signal_id = "sig-1",
       .value_type = 1,
       .value_blob = {0x01, 0x02},
       .tags = "alpha"},
      {.id = "e2",
       .simulation_id = "sim-a",
       .tick = 20,
       .wall_time_ns = 2000,
       .signal_id = "sig-2",
       .value_type = 2,
       .value_blob = {0x03},
       .tags = "beta"},
      {.id = "e3",
       .simulation_id = "sim-b",
       .tick = 30,
       .wall_time_ns = 3000,
       .signal_id = "sig-1",
       .value_type = 3,
       .value_blob = {0x04},
       .tags = "gamma"},
  };

  store.InsertBatch(std::span<const boat::store::EventRecord>(events.data(), events.size()));

  SECTION("Round-trip query by simulation id") {
    boat::store::EventFilter filter;
    filter.simulation_id = "sim-a";
    const auto result = store.Query(filter);
    REQUIRE(result.size() == 2);
    REQUIRE(result[0].simulation_id == "sim-a");
    REQUIRE(result[1].simulation_id == "sim-a");
  }

  SECTION("Query by tick range") {
    boat::store::EventFilter filter;
    filter.tick_min = 15;
    filter.tick_max = 25;
    const auto result = store.Query(filter);
    REQUIRE(result.size() == 1);
    REQUIRE(result[0].id == "e2");
  }

  SECTION("No match yields empty result") {
    boat::store::EventFilter filter;
    filter.simulation_id = "sim-x";
    REQUIRE(store.Query(filter).empty());
  }
}
