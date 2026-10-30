# Prefix-based extensions

Extensions are optional trusted executables, separate from API-v2 plugin packages. A configured prefix starts a one-shot command that accepts JSON on stdin and returns JSON on stdout. Logs belong on stderr. There is no shell interpolation or permission sandbox.

For the shipped uppercase example, add this to `config.yaml`:

```yaml
extensions:
  - name: Uppercase
    prefix: 'upper '
    command:
      - /usr/bin/python3
      - /absolute/path/to/kite/examples/uppercase.py
```

Replace the path with your installation/checkout path. Literal `~` and `$HOME` are not expanded inside argument lists. Restart KITE, then type `upper hello`.

The [example executable](../examples/uppercase.py) receives:

```json
{"version": 1, "query": "hello"}
```

It returns:

```json
{
  "results": [{
    "title": "HELLO",
    "subtitle": "Enter to copy",
    "icon": "edit-copy",
    "action": {"type": "copy", "value": "HELLO"}
  }]
}
```

Supported actions are `copy` (string), `open` (HTTP(S) URL or local path), and `exec` (argument list). Only run extensions you trust, especially ones returning executable actions.

Each request has a 2.5-second timeout, 256-KiB output budget and 100-result limit. Obsolete jobs are cancelled when the query changes; generation checks prevent stale results from replacing current ones. Extensions cannot register inline Python callbacks inside YAML.

For richer tool views, forms or persistent executable backends, use [the plugin API](plugins.md) instead.
