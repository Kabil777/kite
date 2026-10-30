# Work on KITE

If you only want to use KITE, follow the [installation guide](../README.md#setup-with-uv). This page is for running, changing and testing the source.

## Get a working checkout

```sh
git clone https://github.com/Kabil777/kite.git
cd kite
uv venv --python /usr/bin/python3 --system-site-packages .venv
uv sync --locked
uv run --locked --no-sync python main.py
```

Your system Python must be 3.14 or newer and able to import `gi.repository.Gio`. Use its actual path if your distribution does not provide `/usr/bin/python3`. The environment needs system site-packages for the matching Gio bindings. Do not copy another Python version's `gi` directory into it.

Close an installed KITE window before launching a development window: normal invocations use the same per-user socket. `uv.lock` pins Python dependencies; keep it in version control. The virtual environment is local and must not be committed.

## Find the code you need

| You want to change… | Start here |
| --- | --- |
| Main input, navigation or result sections | `main/launcher.py` |
| Application caching and change notifications | `main/applications.py` |
| Native providers and application actions | `main/core.py` |
| Default Python matching | `main/ranking.py` |
| Select an external ranking engine | `main/root_ranking.py` |
| External worker connections and lifecycle | `main/plugin_runtime.py`, `main/plugin_worker.py` |
| Shared controls and declarative UI | `main/frontend/` |
| Plugin installation and recovery | `main/plugin_manager.py` |

Read [Core](core.md) for the complete flow and [Frontend](frontend.md) before adding a new widget. Reuse existing components instead of giving each backend its own interface.

## Run focused checks

These commands use the project's existing environment:

```sh
QT_QPA_PLATFORM=offscreen uv run --locked --no-sync python -m tests.check_frontend
QT_QPA_PLATFORM=offscreen uv run --locked --no-sync python -m tests.check_applications
uv run --locked --no-sync python -m tests.check_app_launch
uv run --locked --no-sync python -m tests.check_ranking
uv run --locked --no-sync python -m tests.check_plugin_manager
uv run --locked --no-sync python -m tests.check_docs
```

The app-launch check starts only a short-lived synthetic desktop entry. Cache checks use temporary XDG directories and a synthetic Gio application database. Neither check needs your password store or clipboard.

For supervised plugin lifecycle work, use:

```sh
QT_QPA_PLATFORM=offscreen uv run --locked --no-sync python -m tests.check_plugin_runtime
QT_QPA_PLATFORM=offscreen uv run --locked --no-sync python -m tests.check_multilang_plugins
uv run --locked --no-sync python -m tests.check_direct_ranking
```

Supervised-worker checks require a working systemd user manager and cgroup v2; multilingual execution also needs Node.js. The direct-child check needs the Linux launch utilities described in the [protocol guide](plugin-protocol.md#cancellation-lifecycle-and-trust). A skipped or unavailable environment is not a passing lifecycle test.

## Build and test the optional Rust engine

From the KITE checkout root:

```sh
cargo build --release --locked --manifest-path examples/rust-ranking/Cargo.toml
QT_QPA_PLATFORM=offscreen uv run --locked --no-sync python -m tests.check_rust_ranking
```

The test does not select Rust in your real settings. To use it yourself, follow [the Rust setup guide](../examples/rust-ranking/HOWTO.md). Keep the unchanged matcher sources and their [license/provenance notices](../examples/rust-ranking/PROVENANCE.md) together. Do not commit `target/` or claim every architecture is supported by one local binary.

## Known verification gaps

Do not assume that every historical check is green:

- The broad `tests.check` script currently stops at an older Gopass icon/glyph expectation. Focused application, ranking and plugin checks are separate from that failure.
- `tests.check_plugin_manager_ui` has an unresolved marketplace navigation/refresh timeout.
- Headless checks are not proof of desktop window-switcher compatibility or actual keystroke-to-painted-results latency.

If you fix one of these, include a focused regression check and update this list rather than hiding the failure.

## Work on the registry

The application and registry are separate repositories:

```sh
# From the directory containing your kite checkout:
git clone https://github.com/Kabil777/kite_plugins.git
```

Change application code and core examples here. Gopass source is in `kite_plugins/plugins/gopass`; optional plugin packages, their READMEs and catalog entries belong in that registry repository. Cloning it does not install its plugins. Follow its [contribution guide](https://github.com/Kabil777/kite_plugins/blob/main/CONTRIBUTING.md); neither repository should contain build outputs, personal settings or secret data.

Gopass-specific integration checks use the sibling checkout above, or an explicit `KITE_GOPASS_SOURCE` pointing to its `plugins/gopass` directory. For example, from this KITE checkout:

```sh
uv run --locked --no-sync python -m tests.check_gopass_sync
```

The baseline check reports its optional Gopass case as skipped if that source is absent; ordinary core checks do not require installing the plugin. The legacy broad script and Gopass-specific UI checks need the optional source checkout as well.

## Before you send a change

Run the checks for the code you touched. Keep configuration, caches, installed `plugins/`, `plugin-packages/`, local backups and credentials out of commits. Preserve third-party license notices. Describe what you verified, what needs manual setup and what remains unverified.
