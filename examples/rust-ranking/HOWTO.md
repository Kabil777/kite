# Use native Rust search

KITE releases include a **precompiled Rust search engine** for supported Linux x86-64 and ARM64 systems. You do not need Cargo, a Rust toolchain, manual compilation or a development link. Python/Qt still owns the interface and actions.

## Install

Use the normal KITE installer:

```sh
bash install.sh
```

From a release archive, extract it first and run that command inside its `kite-<version>` directory. The archive contains the compiled helper. From a Git checkout, the installer downloads the matching release helper automatically, checks its source identity, architecture, size and SHA-256, and installs its license notices.

The installer registers the engine for KITE but does not run it, change your engine preference, or override plugin permissions. Existing configuration and custom/marketplace packages are preserved.

## Select Rust

Close KITE. In `~/.config/kite/config.yaml`, merge:

```yaml
ranking:
  engine: rust-ranking
```

Reopen KITE. If you previously disabled that package, use **Plugin marketplace → Installed → Options → Allow in KITE**; an explicit YAML disable still wins. No compile command is needed.

To return to Python search:

```yaml
ranking:
  engine: builtin
```

Python remains the default and runtime fallback. Missing, disabled or failed native engines fall back to it; refresh/restart after fixing setup to retry.

## Existing development or marketplace installs

If an earlier guide had you create your own `rust-ranking` link, the installer leaves it alone rather than taking over your package. That override continues to take precedence.

To adopt the bundled engine, close KITE and remove only your own old development link after verifying its target, then rerun the installer. For a manager-owned package, use its normal uninstall/update flow instead. Never force-replace an unknown link or delete ownership records.

## Requirements and behavior

- Use the release for your CPU architecture. The installer checks the recorded glibc requirement; unsupported systems can install Python-only with `bash install.sh --python-only`.
- Native execution uses Linux `setpriv`, `flock`, `prlimit` and `sh`, with a persistent direct child—not a systemd ranking unit or Rust binding.
- Flea matching supports typos and accent folding. KITE adds aliases, learned-query boosts, usage decay, favorites and stable ordering. Empty-query commands/plugins remain ahead of apps before the result limit.
- This is not a port of oma-cast's unlicensed JavaScript policy. [Matcher license/provenance](PROVENANCE.md) is preserved, and third-party runtime notices accompany the binary.
- Only trusted code should be selected: initialization supplies configuration/environment, and root ranking supplies app/command metadata and usage. Tool contents and secret-store entries are not ranking candidates.

## Developers

Only maintainers/contributors need to build Rust. See [development setup](../../docs/development.md#build-and-test-the-optional-rust-engine) and [release publishing](../../docs/releases.md). The public executable uses KITE's [shared protocol](../../docs/plugin-protocol.md), including cached catalogs and query-only updates.
