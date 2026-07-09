# Deployment Plan

## Docker Strategy

- `Dockerfile.runtime`
  - Minimal runtime image based on Ubuntu 22.04 slim
  - Includes runtime dependencies and BoAt binaries only
- `Dockerfile.dev`
  - Full development image with compiler toolchain, CMake, Ninja, Python dev stack, `gdb`, `valgrind`
- `docker-compose.yml`
  - Services:
    - `boat-gateway`
    - `boat-agent`
    - `boat-store`
    - optional `timescaledb`

## Multi-Architecture Support

- Build targets:
  - `linux/amd64`
  - `linux/arm64`
- Build toolchain: Docker Buildx
- Registry: `ghcr.io/boat-platform/boat-platform`

## Artifact Versioning

- Semantic versioning: `MAJOR.MINOR.PATCH[-PRERELEASE]+BUILD`
- Version source from Git tags
- `cmake/Version.cmake` extracts version via `git describe`
- Plugin ABI tracked separately with `BOAT_PLUGIN_ABI_VERSION`

## Release Flow

1. Tag release as `v*.*.*`
2. CI builds binaries and packages with CPack (DEB, RPM, TGZ)
3. Publish GitHub Release with packaged artifacts
4. Publish multi-arch Docker images using semver tags
5. Notify downstream automation consumers

