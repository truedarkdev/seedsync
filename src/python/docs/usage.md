# Usage

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
