# Media Servers Setup

This page explains how to connect Emby and Plex media servers so that the
ADR-033 **consumption pass** can pull watch/rating evidence and write
`ConsumptionSignal` rows.

## Overview

The consumption pass is part of the media closed-loop reconciliation
(`apps.cli.ops.reconcile --pass consumption`). It polls each configured media
server instance, resolves each library item's title to a `video_code` via the
high/medium/low confidence join-key ladder, and writes:

- **`ConsumptionSignal`** — one row per `(video_code, instance, library)`
  for items that resolved successfully.
- **`UnresolvedMediaItem`** — one row per item whose `video_code` could not be
  resolved; never silently dropped.

`--pass all` (the default) includes the consumption pass after the acquisition
and ownership passes.

## Configuration shape

`MEDIA_SERVERS` in `config.py` is a list of dicts. The full set of supported
fields is documented in [`config.py.example`](../../../../config.py.example)
(search for `MEDIA_SERVERS`). Field reference:

| Field | Required | Description |
|-------|----------|-------------|
| `type` | Yes | Media server type. Accepted values: `'emby'`, `'plex'`. |
| `instance` | Yes | A unique connection identifier; flows into `ConsumptionSignal.instance`. Must be unique across all entries. |
| `base_url` | Yes | Base URL of the server, e.g. `'http://192.168.1.50:8096'`. No trailing slash needed. |
| `token` | Yes | API token (see sections below). Masked in all log output. |
| `libraries` | No | List of library **names** to scan. Omit (or set to `[]`) to scan all libraries on that server. |

Tokens are stored inline in `config.py`, which is encrypted at rest (stored as
`config.py.enc`) and never committed to the repository.

## Obtaining credentials

### Emby API key

1. Open the Emby dashboard and navigate to **Administration → API Keys**.
2. Click **New API Key**, enter a description (e.g. `javdb-autospider`), and
   confirm.
3. Copy the generated key. Use it as the `token` value.
4. The key is sent as the `X-Emby-Token` request header by the adapter.

### Plex token

1. Sign in to Plex Web at `https://app.plex.tv`.
2. Open any media item, click the **⋮** (more) menu, and choose **Get Info**.
3. Click **View XML** at the bottom of the info panel.
4. In the URL that opens, copy the value of the `X-Plex-Token` query parameter.

Alternatively, follow the official guide at
`https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/`.

The token is sent as the `X-Plex-Token` query parameter by the adapter.

## Example configurations

### Single Emby instance, one library

```python
MEDIA_SERVERS = [
    {
        'type': 'emby',
        'instance': 'emby-nas',
        'base_url': 'http://192.168.1.50:8096',
        'token': 'your_emby_api_key',
        'libraries': ['JAV'],  # scan only the "JAV" library
    },
]
```

### Single Plex instance, all libraries

```python
MEDIA_SERVERS = [
    {
        'type': 'plex',
        'instance': 'plex-home',
        'base_url': 'http://192.168.1.51:32400',
        'token': 'your_plex_token',
        # 'libraries' omitted -> scan all libraries
    },
]
```

### Multiple instances

```python
MEDIA_SERVERS = [
    {
        'type': 'emby',
        'instance': 'emby-nas',
        'base_url': 'http://192.168.1.50:8096',
        'token': 'your_emby_api_key',
        'libraries': ['JAV'],
    },
    {
        'type': 'plex',
        'instance': 'plex-home',
        'base_url': 'http://192.168.1.51:32400',
        'token': 'your_plex_token',
    },
]
```

`instance` values must be unique. The consumption pass runs each instance
independently; a failure on one instance is logged and skipped without aborting
the others.

## Library name resolution

When `libraries` is set, the adapter fetches the full library list from the
server and matches by exact name. If a listed name does not match any library on
that server, it is skipped and a warning is logged — the run does not fail.

When `libraries` is omitted, all libraries on that server are scanned.

## Running the consumption pass

### Locally

```bash
STORAGE_BACKEND=d1 python3 -m apps.cli.ops.reconcile --pass consumption --json
```

If `MEDIA_SERVERS` is empty, this is a no-op. If `MEDIA_SERVERS` is malformed,
the CLI prints an error to stderr and exits with code `1`.

### Via GitHub Actions

`ReconcileLibrary.yml` runs `--pass all` on an hourly cron, which includes the
consumption pass. The `MEDIA_SERVERS_JSON` secret supplies the server list at
runtime (see [GitHub Actions Setup](github-actions-setup.md#media-servers-secret)).

## See also

- [CLI Reference — Acquisition Reconcile CLI](../developer/cli-reference.md#acquisition-reconcile-cli)
- [GitHub Actions Setup](github-actions-setup.md)
- [`config.py.example`](../../../../config.py.example) — full `MEDIA_SERVERS` block with comments
