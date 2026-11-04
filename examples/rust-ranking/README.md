# Rust Flea + KITE search engine

**Users:** follow [HOWTO.md](HOWTO.md). Supported KITE releases provide a compiled executable and the installer registers it automatically. You do not need to build or link a plugin yourself.

This directory is the **developer source** for that first-party native engine. Python/Qt still renders the UI and performs actions; Rust returns ordered candidate IDs over KITE's persistent shared RPC. The selected engine uses cached, revisioned catalogs and small per-query requests. The built-in Python engine remains available as default/fallback.

- [Build and test as a contributor](../../docs/development.md#build-and-test-the-optional-rust-engine)
- [Release build/installer verification](../../docs/releases.md)
- [Worker protocol](../../docs/plugin-protocol.md)
- [Flea source provenance](PROVENANCE.md) and [license](LICENSE-FLEA)

Release builds happen in GitHub Actions, separately for Linux x86-64 and ARM64. Artifacts record the compiler, target, minimum glibc, source digest, sizes and SHA-256 checksums. Runtime/locked-crate notices accompany the binary; no JavaScript, Node or Rust toolchain is needed by users. The Python/Qt environment is still installed normally with uv.

The source manifest's `target/release` command is for development. The installer creates its own verified package under KITE's `native/rust-ranking` directory with a direct executable command, preserving existing user or marketplace overrides. Do not archive an entire Cargo target directory or install over another package by hand.
