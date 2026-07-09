# Database Design

## Storage Backends

| Store | Backend | Use Case |
|---|---|---|
| Event Store | SQLite (embedded) / TimescaleDB (distributed) | Queryable event history |
| Trace Store | Binary flat files + SQLite index | High-throughput raw traces |
| Config Store | SQLite + TOML files | Scenario and plugin configuration |
| Metrics Store | In-memory ring buffer + optional Prometheus remote write | Live metrics |

## SQLite Schema (Event Store)

### `events` table

- `id` TEXT PRIMARY KEY (UUID)
- `simulation_id` TEXT NOT NULL
- `tick` INTEGER NOT NULL
- `wall_time_ns` INTEGER NOT NULL
- `signal_id` TEXT NOT NULL
- `value_type` INTEGER NOT NULL
- `value_blob` BLOB NOT NULL
- `tags` TEXT (JSON)
- Indexes:
  - `(simulation_id, tick)`
  - `(signal_id, tick)`
  - `(wall_time_ns)`

### `simulations` table

- `id` TEXT PRIMARY KEY
- `scenario_id` TEXT NOT NULL
- `state` INTEGER NOT NULL
- `created_at_ns` INTEGER NOT NULL
- `config_json` TEXT NOT NULL

### `scenarios` table

- `id` TEXT PRIMARY KEY
- `name` TEXT NOT NULL
- `version` TEXT NOT NULL
- `definition_json` TEXT NOT NULL
- `created_at_ns` INTEGER NOT NULL

### `traces` table

- `id` TEXT PRIMARY KEY
- `simulation_id` TEXT NOT NULL
- `start_tick` INTEGER NOT NULL
- `end_tick` INTEGER NOT NULL
- `format` INTEGER NOT NULL
- `storage_path` TEXT NOT NULL
- `size_bytes` INTEGER NOT NULL

## Performance Considerations

- Enable WAL mode for concurrent readers and a continuous writer.
- Insert events in transactions of 1000 rows for throughput.
- Replay reads traces via memory-mapped I/O.
- TimescaleDB deployments use hypertables partitioned by `wall_time_ns` in 1-hour chunks.
- Write path remains asynchronous using a dedicated writer thread and bounded queue.

