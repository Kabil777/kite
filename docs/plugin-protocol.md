# Plugins in any language

KITE's Python/Qt frontend renders results, forms and actions. External backends may be Python, Rust, Go, JavaScript or any executable implementing the API-v2 worker protocol below. Tool plugins and replaceable root-ranking plugins use **the same runtime, package manager, permissions and IPC**, not separate language-specific integrations.

Existing Python packages keep working unchanged. They export `search(query, host)` and optional `handle_event(event, host)`; KITE's Python worker adapts public DTOs to this protocol. Python ranking packages instead export `rank(params, host)`.

## Package manifest

For an executable backend, replace `entry` with `command`:

```json
{
  "id": "node-tool",
  "name": "Node greeting",
  "api_version": 2,
  "command": ["node", "backend.mjs"]
}
```

`command` is an argv array, never a shell string (1–32 nonempty arguments, each at most 4,096 characters and 16 KiB total UTF-8). It is mutually exclusive with `entry`. A bare executable name is found on PATH. A package-relative executable such as `./backend` must stay within the package; absolute interpreter paths also work. Executable bits must be set when publishing a native binary. Working directory is the resolved package root. No Python `__init__.py` is required for command packages.

`role` defaults to `tool`. Set `role: ranking` for a root-ranking replacement. Optional `execution: child` is accepted only for executable ranking packages; it selects a bounded, single-process/single-threaded direct child. Omission or `execution: systemd` keeps the existing supervised path. The Rust example opts into `child`; Python adapters and Node examples retain systemd. Existing glyph/configuration fields remain available; no language-specific frontend code is needed. Keep the package-directory basename equal to its manifest ID.

Examples:

- [`examples/node-tool`](../examples/node-tool/): search results → a native Python/Qt form → a toast; requires Node.js.
- [`examples/node-ranking`](../examples/node-ranking/): a replacement matching/filtering/ordering policy; requires Node.js.
- [`examples/rust-ranking`](../examples/rust-ranking/HOWTO.md): licensed Flea matching plus KITE ranking in one Rust RPC executable; no JavaScript. Build with Cargo, then select `ranking.engine: rust-ranking`.

For local development, copy or link each example directory under `${XDG_CONFIG_HOME:-$HOME/.config}/kite/plugin/` with its original basename. Restart KITE after manual changes. Published packages use the existing [reviewed marketplace](../catalog/README.md); list required runtimes in the catalog's `commands` and manual requirements. KITE does not install runtimes or compile code for users.

Discovery, staging, installation and enablement never execute plugin code. Effective permission remains YAML enabled **and** manager allowed. Tool backends start when opened; ranking backends start when selected and root ranking is needed. Closing the tool/root session retires the worker, so backend memory is not retained indefinitely. GC belongs to the backend's own runtime; the core manages process lifetime, resource limits and confirmed descendant cleanup, not foreign heaps.

## Framing and initialization

Use UTF-8 newline-delimited JSON-RPC 2.0. Stdout is reserved for protocol messages; diagnostics belong on stderr. Flush each response. Messages are bounded to 4 MiB (excluding newline), depth 64, and at most 32 messages per read batch. Duplicate keys, nonfinite numbers, unknown methods/fields and invalid IDs are rejected. IDs are nonempty strings up to 96 characters or integers in `[0, 2^53-1]`. Echo the **exact request ID**, without interpreting its generation/revision components.

KITE sends `initialize` before other requests:

```json
{
  "jsonrpc": "2.0",
  "id": "session:init",
  "method": "initialize",
  "params": {
    "api_version": 2,
    "wire_version": 1,
    "plugin_id": "node-tool",
    "directory": "/absolute/package/path/node-tool",
    "config": {},
    "environment": {}
  }
}
```

The actual config and desktop environment are provided, as with Python plugins; never log them. Use your language's process-environment API if you need the supplied environment for child commands. Reply with exactly:

```json
{
  "jsonrpc": "2.0",
  "id": "session:init",
  "result": {
    "api_version": 2,
    "wire_version": 1,
    "plugin_id": "node-tool",
    "has_handler": true
  }
}
```

`has_handler` is a boolean indicating event-handler support; ranking-only backends may return false. Incompatible versions fail closed. Package API `2`, wire framing `1`, ranking contract `1` or `2`, and JSON-RPC's `2.0` marker are separate versions; unknown majors are not guessed or adapted automatically.

## Tool requests and declarative frontend

- `search`: `params: {"query":"text"}`. Return a `view` envelope.
- `handle_event`: `params: {"event":{"node_id":"id","name":"activate","payload":{}}}`. Other event names are `submit`, `change`, `filter`; payload includes the current query and applicable form inputs. Return a view, or a toast to preserve the screen.

A result response:

```json
{
  "jsonrpc": "2.0",
  "id": "session:1",
  "result": {
    "kind": "view",
    "data": {
      "results": [{
        "id": "hello",
        "title": "Copy greeting",
        "keywords": "greeting",
        "action": {"type":"copy", "value":"Hello"}
      }]
    }
  }
}
```

JSON objects use the same field names as the [public response objects](plugins.md#the-response-objects); tuple fields become arrays. Omitted fields get public DTO defaults. Views can contain `nodes`, `results`, `catalog`, `actions`, `toast`, `title`, `error`. A node has `type`, `id`, `props`, `children`, `events`, optional `accessibility_label`; see [Frontend](frontend.md) for supported components and allowed properties. A full-width form must be the sole root node, with no result rows. KITE validates all nested DTOs before rendering and executes only supported, validated actions after user interaction. Tool result IDs are namespaced by plugin ID.

Toast response: `{"kind":"toast","data":{"title":"Done","message":"Optional detail","tone":"success"}}`. Error response, without raw sensitive backend output: `{"jsonrpc":"2.0","id":"session:1","error":{"code":-32000,"message":"Setup required"}}` (message at most 512 characters).

### Host services

During an active tool request, a backend may send an ID-bearing `host.call` request:

```json
{
  "jsonrpc":"2.0", "id":"service:1", "method":"host.call",
  "params":{"parent":"session:1","name":"math.calculate","args":{"query":"2*3"}}
}
```

KITE replies with that service ID and a raw `result` (or an error), then waits for the original plugin response. `parent` must exactly identify the still-current tool request. Process responses while waiting; a loop that blocks stdin while awaiting a service will deadlock. Only one service per request may be outstanding. Host operations and argument restrictions are the same as the [Python host services](plugins.md#host-services). Ranking requests have no host-service access.

## Replace root search as a plugin

The bundled [`main/bundled_plugins/ranking`](../main/bundled_plugins/ranking/) is the default policy, reusing KITE's existing fuzzy matcher/frecency code. The default `ranking.engine: builtin` preserves current behavior, including Favorites → Plugins → Applications for an empty query.

Install a permitted package with `role: ranking` and select its ID:

```yaml
ranking:
  engine: node-ranking
plugins:
  node-ranking:
    enabled: true
```

Manager **Allow in KITE** must also permit the package; an explicit YAML disable wins. Ranking plugins appear in package inventory, not as tool commands. Missing/disabled engines fall back to the default. The [official registry](https://github.com/Kabil777/kite_plugins) currently has no release entries. Follow an example's local setup guide until reviewed release archives are published.

For legacy ranking contract **1**, KITE sends `rank` with exactly these parameters:

| Field | Value |
| --- | --- |
| `protocol_version` | Integer `1` |
| `catalog_revision` | Host-assigned snapshot revision, echoed in the result |
| `query` | Current root query, up to 4,096 characters |
| `candidates` | Up to 10,000 `{id,title,subtitle,keywords,kind}` objects |
| `usage` | Candidate-ID-keyed `count`, `last`, `anchor`, optional `terms` |
| `aliases` | Candidate-ID-keyed alias strings |
| `favorites` | Ordered known candidate IDs |
| `limit` | Maximum returned IDs, 1–200 |
| `sensitivity` | `low`, `medium` or `high` |
| `learn_queries` | Boolean; learned terms are omitted when false |

### Cached catalog contract 2

A ranking backend may add `"ranking_version":2` to its initialization result. Omission selects legacy contract 1; unsupported versions fail closed. Python ranking packages are adapted automatically: their `rank(params, host)` still receives full contract-1 parameters. The Rust example implements contract 2 directly; existing Node examples remain compatible using contract 1.

For contract 2, the host sends `catalog` once per revision and worker process, with exactly `protocol_version:2`, `catalog_revision`, `candidates`, `usage`, `aliases`, and `favorites`. Validate the complete catalog before replacing the previous one; acknowledge `{"catalog_revision":7,"count":420}`. Cache candidate preprocessing here, not on each query. A restarted worker always receives a fresh catalog.

After acknowledgment, each `rank` contains only `protocol_version:2`, `catalog_revision`, `query`, `limit`, `sensitivity`, and `learn_queries`. Reject unloaded revisions. The result has the same revision/ordered-ID shape as contract 1. Query changes do not resend the catalog; candidates, usage, aliases, favorites or learned-term inclusion changes do. Catalog/query messages retain the same framing limits, cancellation, permission checks and result-ID validation. Catalog acknowledgments must match both revision and candidate count.

The plugin controls **matching, filtering and ordering**. KITE does not prefilter its catalog using the built-in matcher. Typed queries display returned IDs in plugin order. For an empty query, the frontend restores the standard Favorites → Plugins → Applications section headings, preserving plugin order within each non-favorite section and the configured favorite order; it does not insert candidates omitted by the backend. Provider-owned math/web/extension results remain separate, as they are today; this contract replaces root app/command ranking, not provider execution.

Return `{"catalog_revision":7,"ordered_ids":["app:example","cmd:clipboard"]}` as the JSON-RPC result. Omit nonmatches. Only unique IDs from that request are accepted, at most `limit`; KITE resolves them to its own rows/actions. A backend cannot manufacture actions through ranking replies.

The host coalesces superseded work, tags requests/snapshots, ignores cancelled replies, and validates IDs/revisions before use. While a new root query is pending, previous ranked rows remain visible but cannot be activated; the Qt list reuses row items rather than clearing and rebuilding them. Warm ranking alone does not show a loader. Initial app indexing/catalog loading uses one consistent “Loading applications…” message without a second progress indicator; unrelated provider work retains its existing busy indication. This does not claim a live-desktop visual verification. Changes to queries, catalog, aliases, favorites, usage or ranking options invalidate the cached response. Crash, timeout, malformed response or oversized catalog/output switches to the built-in policy for that launcher session. Explicit package refresh or restart allows a retry. There is no automatic replay of a failed write.

## Cancellation, lifecycle and trust

KITE sends ID-less `cancel` with `params: {"id":"old-request-id"}` for obsolete work. Stop it cooperatively where possible and reply with its original ID; the host discards cancelled values. Only one active request plus the latest queued replacement is retained. Cancellation gets 300 ms before termination. Systemd workers are retired through their supervisor; direct children receive SIGTERM, followed by SIGKILL after another 300 ms if needed. `shutdown` is an ID-less notification: exit promptly. Initialization/search have five-second deadlines, ranking one second, and tool events 120 seconds.

By default, external languages run in the same systemd user containment: 768 MiB memory, 256 tasks, 100% CPU quota, cgroup-wide stop, owner-pidfd monitoring, and package-store use leases through cleanup. Unconfirmed cleanup blocks further external execution and package mutation until recovery is confirmed. There is no automatic weaker-execution fallback.

An explicit `execution: child` ranker instead runs as one direct QProcess child of KITE. Linux `setpriv`, `sh`, `flock` and `prlimit` execute in that same PID before replacing themselves with the backend; no Python adapter, shell or supervisor remains alongside it. A kernel parent-death SIGKILL plus an owner-PID check covers launcher death, including startup races. The executable independently holds a shared package lease until exit. Hard limits are 768 MiB **virtual address space** (not the systemd resident-memory limit), zero new processes/threads, 64 open descriptors and no core dumps. Single-thread execution cannot consume more than one CPU core; request deadlines and output bounds remain unchanged. This mode requires an unprivileged Linux user with no effective capabilities and the listed utilities; missing requirements fail closed to built-in ranking. It is unsuitable for Node, Python thread-based adapters or backends needing threads/subprocesses: leave those supervised. GUI cleanup is confirmed by the child's actual exit before reuse or package mutation.

**This is fault containment, not a permission sandbox.** Plugins retain your user privileges. Root queries, app/command metadata, aliases and usage are supplied to the selected ranker; initialization also supplies copied configuration/environment. Only choose reviewed code you trust. No clipboard contents, secret-store entries or tool-result/action payloads are included in the ranking catalog. Network behavior inside trusted third-party code is not controlled by KITE's web-suggestions switch.

## Checks

```sh
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 uv run --locked --no-sync python -m tests.check_multilang_plugins
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 uv run --locked --no-sync python -m tests.check_plugin_runtime
```

`uv run --locked --no-sync python -m tests.check_direct_ranking` checks direct-child memory/fork/thread bounds, owner death, package leases and crash/hang cleanup without systemd. The Rust Qt integration also verifies that the actual compiled executable is KITE's persistent direct child.

The multilingual check uses temporary XDG roots, mocked catalog downloads and synthetic Node workers. It covers managed non-Python packages, shared Qt forms, stale requests, ranking fallback and cleanup. Node execution requires Node.js; contained-worker integration requires a working systemd user manager/cgroup v2. These checks are not real GitHub publication or live user-store verification.
