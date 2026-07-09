#include <catch2/catch_test_macros.hpp>

#include <filesystem>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

#include "determinism/determinism_engine.h"
#include "scenario/scenario_loader.h"

namespace {

std::string RunScenarioAndPersistTrace(const std::string& scenario_json, const std::filesystem::path& output_path) {
  const boat::core::ScenarioDef scenario = boat::core::ScenarioLoader::LoadFromJson(scenario_json);
  boat::core::DeterminismEngine engine(scenario.seed);

  std::vector<std::uint8_t> trace_bytes;
  trace_bytes.reserve(static_cast<std::size_t>(scenario.duration_ticks) * 24U);
  for (std::uint64_t tick = 1; tick <= scenario.duration_ticks; ++tick) {
    engine.BeforeTick(tick);
    const std::uint64_t sample = engine.NextRandom();
    for (std::size_t i = 0; i < sizeof(tick); ++i) {
      trace_bytes.push_back(static_cast<std::uint8_t>((tick >> (i * 8U)) & 0xFFU));
    }
    for (std::size_t i = 0; i < sizeof(sample); ++i) {
      trace_bytes.push_back(static_cast<std::uint8_t>((sample >> (i * 8U)) & 0xFFU));
    }
  }

  std::filesystem::create_directories(output_path.parent_path());
  std::ofstream out(output_path, std::ios::binary);
  out.write(reinterpret_cast<const char*>(trace_bytes.data()), static_cast<std::streamsize>(trace_bytes.size()));
  return output_path.string();
}

std::vector<std::uint8_t> ReadBytes(const std::filesystem::path& path) {
  std::ifstream in(path, std::ios::binary);
  return {std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>()};
}

}  // namespace

TEST_CASE("Deterministic seeds reproduce replay sequence", "[determinism][seed]") {
  const std::string scenario_json =
      R"({"id":"seed-determinism","name":"Determinism","version":"1.0","duration_ticks":1000,"seed":1234,"plugins":[],"signals":[],"faults":[]})";
  const auto out_dir = std::filesystem::temp_directory_path() / "boat_determinism_outputs";
  const auto run_a = out_dir / "trace_run_a.bin";
  const auto run_b = out_dir / "trace_run_b.bin";

  RunScenarioAndPersistTrace(scenario_json, run_a);
  RunScenarioAndPersistTrace(scenario_json, run_b);

  const auto bytes_a = ReadBytes(run_a);
  const auto bytes_b = ReadBytes(run_b);

  INFO("trace_a=" << run_a.string());
  INFO("trace_b=" << run_b.string());
  INFO("size_a=" << bytes_a.size() << ", size_b=" << bytes_b.size());
  REQUIRE(bytes_a.size() == bytes_b.size());
  REQUIRE(bytes_a == bytes_b);
}
