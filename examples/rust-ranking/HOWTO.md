# Use Rust Flea + KITE ranking

An optional search plugin: native Flea matching with KITE's ordering/history policy. Python/Qt still renders and performs actions. **No JavaScript, Node, Quickshell or oma-cast installation is required.** Your existing Python fuzzy policy stays available as the default and fallback.

## Build

Install a Rust toolchain first. From your KITE checkout root:

```sh
cd examples/rust-ranking
cargo build --release --locked
```

Keep this terminal in `examples/rust-ranking` for the link command below.

The command may download the locked crates on its first build. It creates `target/release/kite-flea-ranking`, which the manifest runs directly. The built executable does not need Cargo or Rust installed to run. Builds are target-specific; use a matching Linux architecture/libc. Do not assume the locally tested binary runs on every distribution.

## Install and select

Close KITE first. Copy this package (including the built executable), or create an explicit development link under:

```text
${XDG_CONFIG_HOME:-$HOME/.config}/kite/plugin/rust-ranking/
```

For a local checkout, make the link from the package directory where you just built it:

```sh
mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}/kite/plugin"
ln -sT -- "$PWD" "${XDG_CONFIG_HOME:-$HOME/.config}/kite/plugin/rust-ranking"
```

Keep the checkout in place while using that link. If the destination already exists, stop and inspect it instead of forcing replacement.

Keep the package basename `rust-ranking`. Do not overwrite an existing package/link. If the marketplace owns that ID, use its normal uninstall/update workflow instead.

In your normal `config.yaml`:

```yaml
ranking:
  engine: rust-ranking
plugins:
  rust-ranking:
    enabled: true
```

The manager must also allow the plugin in KITE. A YAML disable always wins. Restart KITE after changing settings or manually rebuilding/replacing code. Nothing is installed, selected or enabled automatically by these instructions.

To return to the unchanged default:

```yaml
ranking:
  engine: builtin
```

Missing/disabled plugins and worker failures use the built-in policy. Runtime failure disables the external ranker for that launcher session; refresh the package or restart to retry. If cleanup is unconfirmed, use the marketplace's normal cleanup/recovery flow; do not delete ownership records.

## Behavior

- Exact/prefix/word/subsequence matching plus bounded native typo correction; for example `firefoc` can find Firefox. High sensitivity removes typo-only hits.
- Accent and selected-letter folding, aliases, learned-query boosts, decaying usage, stable kind/title/ID tie breaks, and empty-query favorites.
- Empty queries prioritize Favorites → commands/plugins → Applications **before** applying the global result limit, so a large app catalog cannot crowd enabled plugins out of their section. Typed-query matching and ordering are unchanged.
- Keywords admit a candidate but cannot lift it above a stronger visible-title match.
- This is **not** oma-cast's complete ranking policy. Only its explicitly MIT-licensed matcher is reused; the JavaScript policy was not ported. Some ordering/matching differs from both original oma-cast and KITE's Python scorer.
- KITE supplies root app/command candidates only. Clipboard, secret-store entries, provider results and action payloads are not put into this ranking catalog. As with every plugin, initialization provides copied configuration/environment: only run code you trust.

The process is reused across root queries, caches normalized candidate text, and speaks [KITE's plugin RPC](../../docs/plugin-protocol.md) directly. The host sends a validated catalog once per revision/process, followed by small query/options requests. The compiled binary is a persistent direct child (`execution: child`), not a systemd unit or Rust binding; exec-only Linux launch utilities leave no helper process behind. Work is synchronous: cancellation notifications are consumed between requests, while KITE rejects stale responses and enforces cancellation/time/resource limits. No usage is written by the plugin.

## Verify

From KITE's project root after building:

```sh
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 uv run --locked --no-sync python -m tests.check_rust_ranking
```

This uses synthetic rows and temporary config/state, checks shared KITE ordering cases, native typo/folding behavior, malformed/oversized RPC recovery, catalog reloads, and a persistent contained worker with cleanup. It does not change your settings or activate real applications. The Qt integration needs an unprivileged Linux user and `setpriv`, `flock`, `prlimit`, and `sh`, not a systemd ranking unit. Direct execution forbids threads/subprocesses and bounds virtual address space; other plugin types remain systemd-supervised. Run `uv run --locked --no-sync python -m tests.check_direct_ranking` to check limits, owner-death/lease handling and hung-child cleanup. Add `--skip-qt` for direct RPC/policy checks only.

These are correctness checks, not a measured speedup claim. Real-desktop keystroke-to-paint latency has not been verified; synthetic host-path benchmark results must not be presented as UI timings.
