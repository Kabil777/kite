# Plugin catalog reference

**Want to install a plugin?** Start with the [registry's getting-started guide](https://github.com/Kabil777/kite_plugins). This page is the technical reference for package authors and maintainers.

The live catalog belongs to **[Kabil777/kite_plugins](https://github.com/Kabil777/kite_plugins)**. The `index.json` beside this document is an empty development fixture, not the published registry. The live catalog is also currently empty: entries need real reviewed Release archives and their actual checksums.

## Hosting and review

[Kabil777/kite](https://github.com/Kabil777/kite) contains the application, examples and technical documentation. [Kabil777/kite_plugins](https://github.com/Kabil777/kite_plugins) contains the catalog, contribution guides and optional plugin packages under `plugins/`, each with its own README. Gopass is maintained there and is not installed by KITE's default installer. External authors can also keep their source/releases in their own public GitHub repositories and submit catalog changes by pull request. The catalog is reviewed; it is not an arbitrary-repository installer.

Configure the genuine published **raw.githubusercontent.com JSON URL** in Plugin marketplace → the **Configure** dropdown view → **Save source**, or, with KITE closed:

```sh
kite plugins source https://raw.githubusercontent.com/Kabil777/kite_plugins/main/index.json
kite plugins search
```

The source address is an explicit user choice; setting it does not install or run any plugin. Catalog fetches happen on request, not app startup. A source-bound cache supports clearly labeled offline browsing; pinned artifacts must still be available to install. No tokens, accounts, repository cloning or Git subprocesses are used by the manager. Only public HTTPS GitHub resources and redirects to approved GitHub asset hosts are accepted.

Maintainers should review source, package contents, permissions warning, licensing, manual requirements and the exact artifact hash before merging. SHA-256 identifies bytes; it does **not** certify safety. A plugin has the user's permissions, including filesystem and network access. Systemd contains faults, not permissions.

## Schema v1

Top level: exactly `schema: 1` and `plugins: []`. Each plugin has:

| Field | Contract |
| --- | --- |
| `id` | Manifest ID; lowercase letter followed by lowercase letters, digits, `_` or `-`, at most 64 characters; native/reserved IDs rejected |
| `name` | Must match `plugin.json`; nonblank, at most 128 printable characters |
| `summary` | Nonblank plain text, at most 512 printable characters |
| `author` | Nonblank display name, at most 128 printable characters |
| `repository` | Public HTTPS `github.com` owner/repository URL, without credentials/query/fragment |
| `latest` | Explicit stable `major.minor.patch` version from `releases`; no leading zeroes/prereleases |
| `releases` | 1–100 unique release records |
| `requirements` | Optional list of up to 20 manual setup instructions, each at most 200 printable characters; never executed |
| `commands` | Optional list of up to 20 executable basenames to check on PATH; never run or installed during inventory |

Each release has exactly `version`, integer `api_version`, public GitHub **Release asset ZIP** `url`, and lowercase 64-hex `sha256`. The asset must belong to the declared source repository. The URL path is owner/repository/releases/download/tag/asset.zip, not a branch/source-code snapshot. An incompatible API is visible but cannot be installed. Version and provenance belong in catalog/manager records, **not** extra manifest fields.

The following is a template, **not a usable catalog**; replace every angle-bracket placeholder with genuine published metadata:

```json
{
  "schema": 1,
  "plugins": [{
    "id": "hello",
    "name": "Hello",
    "summary": "Prepare a greeting to copy",
    "author": "<reviewed author>",
    "repository": "<public GitHub repository URL>",
    "latest": "1.0.0",
    "requirements": ["<manual setup, or omit this field>"],
    "releases": [{
      "version": "1.0.0",
      "api_version": 2,
      "url": "<versioned GitHub Release ZIP asset URL>",
      "sha256": "<actual lowercase SHA-256 of the uploaded ZIP>"
    }]
  }]
}
```

Unknown fields, duplicate JSON keys, duplicate IDs/versions and malformed URLs/hashes are rejected. Bounds are fixed: 2 MiB catalog, 1,000 entries, 20 MiB compressed ZIP, 100 MiB extracted bytes, 2,000 entries (including implicit directories), 100 installed external plugins, and 128 KiB UTF-8 per guide. There is no dependency solver, hook execution, automatic update, prerelease channel or release-version guessing.

## Build a release asset

1. Review the plugin source and API-v2 `plugin.json`; the package directory basename and manifest ID must match. For Python-entry packages keep `__init__.py` at that directory's root. Executable packages instead declare `command` argv and include its backend files; they need no Python entry. Both tool and `role: ranking` packages use this catalog unchanged. See [the language-neutral protocol](../docs/plugin-protocol.md).
2. Include an actionable UTF-8 `HOWTO.md` (preferred) or `README.md`, at most 128 KiB. Explain every required binary/library and how users install/configure it themselves. Do not bundle settings, stores, keys, credentials, caches or virtualenvs.
3. Create a ZIP containing exactly the `<id>/` package tree. For the documented Hello example, from its parent directory:

   ```sh
   zip -r hello-1.0.0.zip hello
   sha256sum hello-1.0.0.zip
   ```

4. Inspect the ZIP contents before uploading. Only ordinary files/directories are allowed: no links/hardlinks, special files, traversal/absolute/drive/backslash paths, duplicate/conflicting paths, encrypted entries or unsupported ZIP metadata. Store/deflate compression is supported; ordinary timestamps/UID fields are accepted, but exotic extra fields may be rejected.
5. Publish that reviewed ZIP as an explicit GitHub Release asset. Keep published bytes immutable: changed bytes require a new version. Recalculate/verify the hash against the uploaded artifact.
6. Submit a pull request updating the catalog with its real repository identity, version, API, Release URL, hash and manual requirements. Retain old release metadata as appropriate; `latest` is explicit.

Browse/install/enable are metadata and file operations only: none imports the entrypoint. Approved installation enables the package unless YAML explicitly disables it, and tool code starts only when opened; a selected ranking plugin starts when root search needs it. `--yes` approves an operation but bypasses no URL, checksum, archive, ownership or cleanup validation.

## Storage, updates and failures

Managed discovery links remain at `$XDG_DATA_HOME/kite/plugins/<id>`, pointing to private versioned payloads in `plugin-packages/<sha256>/<id>`. The manager's registry and durable recovery journal live in `$XDG_STATE_HOME/kite/plugin-manager/`; the source-bound catalog cache lives under `$XDG_CACHE_HOME/kite/plugin-manager/`. Default XDG roots apply when unset.

Updates preserve enablement and YAML. Catalog/publisher identity changes and replacement bytes under an already-installed version are refused, even with `--yes`; review a deliberate uninstall/reinstall to change origin. Authored packages, legacy installs and development links are never silently adopted or removed. Uninstalling managed code preserves YAML and external data. Edited or extra payload files are retained under `plugin-backups/` without opening unknown extras; unchanged obsolete code is cleaned only after commit and confirmed quiescence.

Mutation/use lock files are anchored in the data directory's `plugin-packages/`, so alternate state homes cannot race on the same physical package store. Registry/journal records remain under the selected state home.

Close KITE for CLI mutations and application upgrades. Do not replace a manager-owned link using `--link-example`; uninstall through the manager first. Installer overlays exclude installed links, versioned packages, staging and backups. Keep backups private; they can contain files written by trusted plugin code.

A process lease disappearing does not prove its systemd unit is empty. Updates, uninstall and recovery fail closed if cleanup cannot be confirmed. The manager never stops unknown leftover units automatically: inspect `systemctl --user list-units 'kite-plugin-*.service' --all` yourself and resolve the reported unit/session failure before retrying. A disabled flag remains disabled if cleanup fails.

Interrupted transactions recover under exclusive coordination before discovery. An unfinished intent rolls back; a durable committed transaction finishes housekeeping. Retry **Check cleanup / recover** in-app, or a CLI mutation with KITE closed. A cancellation after commit does not undo the operation. Corrupt registry/journal or changed links are preserved, never reset/adopted: back up the state and inspect the registry's recorded hash and expected link target before manual repair. Do not delete state to “fix” ownership; untracked managed payloads remain blocked.

A crash before journaling can leave an unreferenced payload or private staging directory. An untracked hash payload is not reused merely because its bytes match a release; settle its ownership/recovery first. No broad garbage collector guesses that such directories are safe to delete. Inspect and retain them until ownership/recovery is settled; never remove a payload used by a running GUI, supervisor, authored reference or external data store.

## Isolated verification

```sh
uv run --locked --no-sync python -m tests.check_plugin_manager
```

This one check uses inert ZIPs, temporary XDG roots, mocked downloads/systemd/cleanup and blocked unintended network/process operations. It is not a live desktop, real GitHub publication, native UI interaction or plugin-activation certification. Do not run unrelated suites or touch live stores to verify the package manager.
