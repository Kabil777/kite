# Rust Flea + KITE ranking plugin

Standalone native backend for KITE's existing API-v2 ranking role. The licensed Flea matcher is vendored unchanged; KITE's own ordering policy is implemented in Rust. There is no JavaScript dependency or JavaScript ranking-code port. The backend advertises ranking contract 2: validated revisioned catalogs and normalized fields are cached, and subsequent queries carry only query/options. Legacy full contract-1 requests are also accepted. Restart KITE after updating the host/backend.

- [Build, install, select and verify](HOWTO.md)
- [Source provenance and license boundary](PROVENANCE.md)
- [KITE's shared worker protocol](../../docs/plugin-protocol.md)

```sh
cargo build --release --locked
```

For a reviewed release ZIP, include only the `rust-ranking/` package tree: `plugin.json`, `target/release/kite-flea-ranking` (executable), `HOWTO.md`, `README.md`, `PROVENANCE.md`, `FLEA-PROVENANCE.md`, and `LICENSE-FLEA`. Do not archive the complete Cargo `target/` directory, caches, local state or credentials. Include source/license information for other distributed dependencies as required by their licenses. Release packaging/publication remains a separate maintainer action; no catalog entry or downloadable release is claimed here.

The checked-in `Cargo.lock` pins build dependencies. Normalization uses `unicode-normalization`; JSON uses `serde`/`serde_json`. Runtime needs neither Node nor the Rust toolchain. The manifest selects `execution: child`: KITE starts the compiled executable once as a direct child, without a Python adapter, Rust binding or systemd unit. Linux `setpriv`/`flock`/`prlimit`/`sh` exec into that same child PID to enforce parent-death cleanup, package leases, a 768-MiB virtual-address-space limit and no forks/threads. The process is reused across queries. Other plugins keep their existing supervision; see [execution limits](../../docs/plugin-protocol.md#cancellation-lifecycle-and-trust).
