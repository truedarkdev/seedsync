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

To relocate an enabled local path without losing its path-pair identity or history, first expose the same
data under both the old and new container paths (for example, keep `/downloads/root-a` and add
`/data/root-a` as aliases for the same host directory). Wait until the pair has no queued or active
work, then change the local path in Settings. SeedSync checks that both paths exist and resolve to the
same directory; it does not copy or move data. A new container without the retained `/config` directory
also loses `path_pairs.json`, settings, controller and AutoQueue persistence, and API-key/browser-claim
state: reconfigure the instance and reclaim the browser before use. Historical timestamps and completion
markers are only part of that lost state. The new instance then performs normal fresh reconciliation. During
that reconciliation, a complete size-matched local file (including a zero-byte file) is recognized and is
not downloaded or deleted. Remote-only content queues only when that path pair has `Auto-queue` enabled;
otherwise it remains unqueued for manual action. Local-only content is left untouched, and partial or
conflicting local content is never silently overwritten.

Container-path prefixes do not change these rules. SeedSync requires write access only to roots currently
used by enabled path pairs and active staging/extraction settings. A disabled historical pair does not make
its old root mandatory, and `/downloads` remains the backward-compatible default rather than a universal
requirement.

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
The same section controls its byte budget and optional entry cap. The byte budget is for retained,
sanitized evidence; child-process ingress has a separate small admission limit.

When you need the recent failure context, read the breadcrumb diagnostics endpoint with an authenticated admin session or an admin-scoped API key:

- `GET /server/breadcrumbs/get`
- add `since_version=<n>` to read only entries newer than a previous snapshot
- optionally add `limit`, `corr_id`, `flow_id`, `stage`, `event_type`, `path_pair_id`, `file_id`, and `order=asc|desc` for server-side filtering and bounded retrieval

Use breadcrumbs for the short sequence of state changes, retries, queue decisions, transfer steps, and extraction transitions around the failure.
Use normal logs for long-lived operational history and broad troubleshooting context.

Breadcrumb entries are intentionally bounded and designed to redact common sensitive values.
They are meant to explain what happened right before a failure, not to act as full command or payload logging or exhaustive secret scrubbing.

### Breadcrumb Trace API v1

The versioned API is admin-only and returns JSON with bounded request and response sizes. The page endpoint caps `limit` at 256 entries and the export endpoint caps it at 2048 entries, regardless of the configured in-memory retention depth. Both preserve the collector's version, reset, truncation, and gap metadata so clients can detect that a cursor must be restarted.

- `GET /server/breadcrumbs/v1/capabilities` describes supported filters and policy operations.
- `GET /server/breadcrumbs/v1/events` returns a bounded page. Filters include `category`, `category_prefix`, `level`, `correlation_id` (or legacy `corr_id`), `flow_id`, `source`, `stage`, `event_type`, `since_version`, `until_version`, `version`, `start_time_ms`, `end_time_ms`, `order`, and `limit`.
- `GET /server/breadcrumbs/v1/export` returns a bounded JSON export using the same filters. Add `format=jsonl` for bounded sanitized JSON Lines.
- `GET /server/breadcrumbs/v1/policy` reads the active retention policy and its persistence/worker-propagation status. Submit a policy object to `POST /server/breadcrumbs/v1/policy/validate`; submit it to `POST /server/breadcrumbs/v1/policy/apply` with optional `expected_revision` and `persist=true` for compare-and-swap updates. `POST /server/breadcrumbs/v1/policy/reset` restores the default policy and accepts the same controls.
- `POST /server/breadcrumbs/v1/clear` clears retained breadcrumbs. `GET /server/breadcrumbs/v1/stream` emits one bounded snapshot by default; add `follow=true` for bounded replay polling and heartbeat comments. `Last-Event-ID` or `since_version` resumes from a version when it is still available.

Only `/server/breadcrumbs/get` and `/server/breadcrumbs/reset` are compatibility routes. The newer diagnostics surface is v1-only.

## Performance diagnostics

Performance diagnostics collect a fixed, numeric, bounded resource window locally when enabled with `general.performance_diagnostics_enabled=true`. Disabled mode performs no procfs/cgroup reads or sample retention; enabled collection is bounded by the configured interval and retention depth. The detailed duration aggregates use only fixed in-code metric names. Local scanner attribution adds `local_scan_filesystem_traversal`, `local_scan_managed_extract`, `local_scan_staging_merge`, `local_scan_aggregation`, and `local_scan_progress_publication`; model-update attribution adds `model_update_state_preparation`, `model_update_scan_intake`, `model_update_status_ingestion`, `model_update_builder_sync`, `model_update_lifecycle_maintenance`, and `model_update_build_finalization`. Each reports bounded count, wall time, and (for completed spans) same-thread CPU time. Snapshots also expose numeric `active_stage_counts` and bounded `active_stages` wall-time attribution, plus `active_stage` (global longest stage) and `active_scanner_stage` (scanner-only longest stage). Active CPU fields are intentionally `null` because snapshots can run on a different thread than the scanner. The global in-flight span table is bounded; excess begins are dropped and counted in `duration_spans_dropped` without fairness guarantees across metric families.

Disabling diagnostics stops new collection but deliberately leaves prior numeric history available until reset or restart. Linux container metrics use cgroup v2 files; non-Linux and unavailable procfs/cgroup fields are reported as unknown. Breadcrumb diagnostics remain always-auth admin routes even when `general.disable_browser_auth=true`.

Admin sessions or admin-scoped API keys can read `GET /server/admin/performance-diagnostics/v1`, reset its retained window with `POST /server/admin/performance-diagnostics/v1/reset`, or request the bounded support form at `GET /server/admin/performance-diagnostics/v1/export`. The stable schema is `seedsync.performance-diagnostics.v1`; it contains no application, file, or path-pair identifiers, paths, commands, payloads, or credentials. Session and sequence values are bounded diagnostic cursors. `limit` and `since_sequence` only bound a retained numeric sample window. When diagnostics are enabled, `GET /server/admin/performance-diagnostics/v1/ownership` performs one bounded main-process ownership census; it reports only fixed owner labels and numeric object/byte totals, never file names, paths, keys, values, or credentials. Its detailed object totals are globally deduplicated and capped. Per-owner `graph_node_count` and `graph_shallow_bytes` separately count only `ModelFile`/`SystemFile` nodes through fixed child links, deduplicate within each owner, and may intentionally overlap between owners; `graph_truncated` reports their independent bounds.
