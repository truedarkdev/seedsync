# Usage

## Recommended Setup

The best way to use SeedSync is with hard links and a dedicated completion directory. This setup helps avoid duplicate syncing and re-downloading in the intended workflow, including after a container restart.

### How it works

1. Configure your torrent client (qBittorrent, ruTorrent, etc.) to hard link completed downloads into a dedicated directory. For example, if your client downloads to `/downloads/tv`, have it hard link completed files to `/downloads/complete`.
2. Point SeedSync at the completion directory (`/downloads/complete`).
3. Enable `Auto-queue` and turn on `Delete remote files after download` in Settings.

Hard links don't consume extra disk space on the seedbox - they create another reference to the same data on disk. When SeedSync finishes syncing and deletes from `/downloads/complete`, only the hard-link copy is removed. The original file stays intact for seeding.

:::note
Hard links only work when both directories are on the same filesystem or device. If your download and completion directories are on different mounts, use a bind mount to place them on the same filesystem, or use a copy-on-complete script instead (at the cost of extra disk space).
:::

### Setting up hard links

- **qBittorrent**: Use [qbit-hardlinker](https://github.com/gravelfreeman/qbit-hardlinker) to automatically hard link completed downloads to your SeedSync directory.
- **ruTorrent**: Use a post-completion script that creates hard links. Note that the Autotools "Move to" option performs a *move*, not a hard link - this would break seeding since the original file is relocated.

### Example directory layout

```text
/downloads/
├── tv/              ← Sonarr downloads here, torrents continue seeding
├── movies/          ← Radarr downloads here
└── complete/        ← Hard links go here, SeedSync watches this directory
```

:::tip
This setup also solves the common problem of setting up SeedSync on a seedbox that already has many existing files. Since only newly completed downloads get hard-linked into the completion directory, SeedSync won't try to sync your entire library.
:::

## Dashboard

The Dashboard page shows all the files and directories on the remote server and the local machine.
Here you can manually queue files to be transferred, extract archives and delete files.
If you use path pairs, the Dashboard and Files page also show which configured source path a file came from.
This keeps duplicate names distinguishable and lets you see per-path-pair activity at a glance.

When multiple path pairs are enabled, the files list shows a small source label with the path-pair name for each file.
The files page also includes a path-pair stats card so you can compare queued, downloading, completed, and other counts across sources.

## Path Pairs

Path pairs let you sync multiple remote/local directory combinations in one SeedSync instance.
Configure them in `Settings > Path Pairs`.
On the Files page, each file can show a source label with its path-pair name so duplicate filenames stay distinguishable.
When multiple enabled path pairs are active, the Files page also shows a path-pair statistics card for per-source totals and activity.
`Enabled` controls whether a path pair is active, and `Auto-queue` is applied per path pair.

## AutoQueue

AutoQueue queues all newly discovered files on the remote server.
You can also restrict AutoQueue to pattern-based matches (see this option in the Settings page).
When pattern restriction is enabled, the AutoQueue page is where you can add or remove patterns.
Any files or directories on the remote server that match a pattern will be automatically queued for transfer.
With path pairs, auto-queue is evaluated per enabled path pair based on that pair's `Auto-queue` setting.

## Breadcrumb Trace

Breadcrumb trace is a low-overhead, opt-in recent-context recorder for hard-to-diagnose failures.
It keeps a short bounded window of structured breadcrumbs in memory so operators can see the lead-up to a problem without turning on noisy debug logging.

Enable it in `Settings > General` with `Enable breadcrumb trace recorder`.
The same section also lets you tune the breadcrumb retention depth if you want a larger or smaller recent window.

When you need the recent failure context, read the breadcrumb diagnostics endpoint with an authenticated admin session or an admin-scoped API key:

- `GET /server/breadcrumbs/get`
- add `since_version=<n>` to read only entries newer than a previous snapshot
- optionally add `limit`, `corr_id`, `flow_id`, `stage`, `event_type`, `path_pair_id`, `file_id`, and `order=asc|desc` for server-side filtering and bounded retrieval

Use breadcrumbs for the short sequence of state changes, retries, queue decisions, transfer steps, and extraction transitions around the failure.
Use normal logs for long-lived operational history and broad troubleshooting context.

Breadcrumb entries are intentionally bounded and designed to redact common sensitive values.
They are meant to explain what happened right before a failure, not to act as full command or payload logging or exhaustive secret scrubbing.
