# Prebuilt application releases

Users install a release or run `bash install.sh`; they do **not** need Cargo or a Rust compiler. Releases contain KITE's Python/Qt application source and a ready-built native search executable. This is not a self-contained Python/Qt bundle: the usual Python, uv, Gio and display-runtime requirements still apply.

## What is provided

Release builds run on native GitHub-hosted Linux x86-64 and ARM64 runners. Each records its GNU target, build host's minimum glibc requirement and compiler version. The installer refuses an incompatible CPU/libc rather than trying the wrong executable. Python-only installation remains available with `bash install.sh --python-only`.

Assets include:

- `kite-v<version>-linux-<architecture>.tar.gz`: tracked application source plus the corresponding embedded executable/notices.
- Separate native executables and third-party notices for source-checkout installation.
- `native-assets.json`: version, Rust source digest, source commit, supported platforms, file names/sizes and actual SHA-256 values.
- `SHA256SUMS`: checksums for the published assets.

Rust/crate/Flea notices are included. Build outputs, virtual environments, installed tool packages, settings and private data are not taken from a developer's working directory: the application source comes from `git archive`.

## Installer behavior

The trusted **core installer**, not a marketplace package hook, performs native setup:

1. Read an embedded descriptor or fetch the exact version's public descriptor from `Kabil777/kite` Releases.
2. Require the release version and Rust source digest to match the checkout.
3. Check the CPU, glibc requirement, allowed asset names, bounded sizes, SHA-256 and executable architecture.
4. Install the executable/notices and first-party manifest under `kite/native/rust-ranking`.
5. Register it only if no user-authored or installed `rust-ranking` override already exists.

Installation does not execute the binary, select an engine or change YAML/manager permissions. Existing preferences and custom packages win. Native execution still uses the bounded persistent direct-child path and Python remains the default/fallback.

Old installer-owned native versions are backed up under `.native-backups`. Edited files, extra files, symlinks, unconfirmed ownership and a live package-use lease prevent replacement rather than discarding data. Close KITE before updates. This component does not replace the plugin manager's ownership/recovery rules.

`--python-only` skips native fetching/updating; it does not delete an existing engine or rewrite your chosen engine. Source changes that have not been released yet may need this flag during development.

Checksums detect corrupted/wrong bytes, not a malicious publisher. Trust the source/release owner; metadata from a release is not an independent audit of that owner.

## Publish a release (maintainers only)

1. Update `project.version` in `pyproject.toml` and commit the tested source. Rust changes require a new release; do not replace an old release's assets.
2. Push the commit and a matching version tag, for example:

   ```sh
   git push origin main
   git tag v0.1.1
   git push origin v0.1.1
   ```

3. GitHub Actions builds both architectures, exercises a synthetic catalog/query exchange, collects license notices and packages the application.
4. The publish job merges the build descriptors, writes checksums and creates the release. It has scoped release-write permission; build jobs have read-only repository access. Action versions are pinned to reviewed commits.
5. Download the real assets and verify the descriptor/checksums and installation before announcing the release. No package is advertised as published just because its workflow exists.

The tag must equal `v` plus the project's version. Existing releases are not overwritten automatically. To correct published bytes, publish a new version.

## Local verification

```sh
uv run --locked --no-sync python -m tests.check_native_release
```

This uses mocked downloads and a deliberately non-runnable ELF fixture. It checks integrity/source/platform validation, no compilation or executable startup during installation, registration, overrides, permission flags, edit/extra preservation, embedded offline assets and use leases. The isolated installer case proves that default installation needs no Rust toolchain and still does not install Gopass.

Actual build/release and native runtime tests are separate from those fixtures. Only the CI packager in `tools/build_release.py` invokes Cargo; the end-user hook in `main/native_release.py` never does.
