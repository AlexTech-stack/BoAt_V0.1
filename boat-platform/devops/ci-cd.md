# CI/CD Plan

## Build System

- Root `CMakeLists.txt` supports `cmake --preset`.
- Presets:
  - `debug`
  - `release`
  - `asan`
  - `tsan`
  - `coverage`
- `add_boat_plugin()` builds plugins as `MODULE` and installs to `lib/boat/plugins/`.
- `BoAtProto.cmake` wraps protobuf and gRPC code generation for all `.proto` files.
- Dependencies via `FetchContent`:
  - iceoryx2
  - gRPC
  - protobuf
  - spdlog
  - nlohmann-json
  - Catch2
  - pybind11

## GitHub Actions Pipeline

Trigger on push and PR to `main` or `release/*`.

```text
Jobs:
  build-and-test:
    matrix: [ubuntu-22.04, ubuntu-24.04]
    steps:
      - checkout
      - setup-cmake, setup-ninja
      - cmake --preset release
      - cmake --build
      - ctest --preset release (unit + integration)
      - upload test results

  determinism-check:
    steps:
      - run simulation twice with same seed
      - diff trace outputs (must be bit-exact)

  asan-build:
    steps:
      - cmake --preset asan
      - run unit + integration tests

  python-tests:
    steps:
      - setup-python 3.11
      - install boat-py and test deps
      - pytest sdk/python cli boat_ai unit suites (run `boat_ai/tests` when present)

  coverage:
    steps:
      - cmake --preset coverage
      - gcovr report -> upload to Codecov

  hil-smoke:
    steps:
      - provision vcan0
      - set BOAT_HIL_ENABLED=1
      - run tests/hil smoke suite

  docker-build:
    steps:
      - docker buildx build --platform linux/amd64,linux/arm64
      - push to ghcr.io/boat-platform/boat-platform:<tag>

  release:
    trigger: tag v*.*.*
    steps:
      - build release artifacts
      - cpack (DEB + RPM + TGZ)
      - create GitHub Release with artifacts
      - push Docker image with semver tag
```

