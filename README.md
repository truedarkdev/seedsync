<p align="center">
    <img src="https://user-images.githubusercontent.com/12875506/85908858-c637a100-b7cb-11ea-8ab3-75c0c0ddf756.png" alt="SeedSync" />
</p>

> This repository is a maintained integration fork of [ipsingh06/seedsync](https://github.com/ipsingh06/seedsync).

<p align="center">
  <a href="https://github.com/truedarkdev/seedsync/actions/workflows/master.yml">
    <img src="https://img.shields.io/github/actions/workflow/status/truedarkdev/seedsync/master.yml?branch=master" alt="CI">
  </a>
  <a href="https://github.com/truedarkdev/seedsync">
    <img src="https://img.shields.io/github/stars/truedarkdev/seedsync" alt="Stars">
  </a>
  <a href="https://github.com/truedarkdev/seedsync/releases/latest">
    <img src="https://img.shields.io/github/v/release/truedarkdev/seedsync" alt="Release">
  </a>
  <a href="https://github.com/truedarkdev/seedsync/blob/master/src/angular/package.json">
    <img src="https://img.shields.io/badge/Angular-21-DD0031?logo=angular&logoColor=white" alt="Angular 21">
  </a>
  <a href="https://github.com/truedarkdev/seedsync/blob/master/src/python/pyproject.toml">
    <img src="https://img.shields.io/badge/Python-3.11%20%7C%203.12-3776AB?logo=python&logoColor=white" alt="Python 3.11 | 3.12">
  </a>
  <a href="https://github.com/truedarkdev/seedsync/blob/master/LICENSE.txt">
    <img src="https://img.shields.io/github/license/truedarkdev/seedsync" alt="License">
  </a>
</p>

SeedSync is a tool to sync the files on a remote Linux server (like your seedbox, for example).
It uses LFTP to transfer files fast!

## Features

* Built on top of [LFTP](http://lftp.tech/), the fastest file transfer program ever
* Web UI - track and control your transfers from anywhere
* Multiple path pairs - sync multiple remote/local directory combinations in one SeedSync instance
* Automatically extract your files after sync
* Auto-Queue - only sync the files you want based on pattern matching
* Delete local and remote files easily
* Staging directory - land downloads on fast storage before moving them to the final location
* API keys - require web UI/API authentication when configured
* Fully open source!

### What's new in v0.9.0

* **Sync more than one folder** with path pairs for separate remote and local libraries in one SeedSync instance.
* **Progress you can trust** with smoother live byte updates that stay closer to active, stopped, resumed, and completed transfers.
* **Safer local access** by claiming a trusted browser, remembering approved sessions, and rotating or revoking scoped API keys.
* **Notifications where you want them** for selected download, extraction, and remote-delete events through a webhook or Apprise.
* **Choose your transfer engine** between LFTP with SFTP or FTPS and rclone with SFTP.
* **Find problems faster** by searching retained logs by text, severity, logger, or time range while sensitive values stay redacted.
* **Large libraries, your way** - choose 25, 50, 100, 1,000, or All and keep that preference. All preserves the full filtered list; large lists render a measured window around what you can see (up to 80 rows at once), keeping big libraries responsive without forcing pagination.

When v0.8.6 configuration needs migration, SeedSync exposes only a restricted migration checkpoint; normal APIs, transfers, queueing, settings mutations, and workers remain unavailable. Direct/package launches bind that checkpoint to `127.0.0.1` unless `--web-bind-host` is explicitly supplied; Docker keeps its explicit `SEEDSYNC_WEB_BIND_HOST` setting. The checkpoint reuses a valid legacy `[Web] port` value from `settings.cfg` (range 1-65535) and otherwise falls back to port 8800; Docker deployments with a custom legacy port must publish that same container port. Migration pages and status are available through every syntactically valid direct hostname or IP authority that can reach the configured bind address; malformed authorities are refused. SeedSync deliberately ignores `X-Forwarded-*`, and mutation requests require the browser `Origin` to exactly match the direct WSGI scheme, host, and effective port. Start also requires an explicit confirmation and a per-process CSRF proof. The migration then runs in the background with single-flight locking and creates and validates the retained backup before any migration mutation. After success, the completed checkpoint remains visible and normal startup stays paused until the user selects **Continue to SeedSync**; the browser then waits for ordinary startup before opening the fresh first-browser claim. This migration checkpoint is deliberately unauthenticated: its controls prevent ordinary cross-site browser submission but do not restrict named authorities or claim to prevent DNS rebinding. Any client that can establish and read a reachable checkpoint origin can request migration, so do not expose it to an untrusted network.

Each confirmed migration first retains a private, recursively verified snapshot under `migration-backups`. The snapshot covers regular files and directories inside the exact configuration root, including unknown and hidden entries. It deliberately does not copy runtime-home/SSH state, downloads, mounts, or transfer staging because the migration does not mutate those locations, and it refuses links, reparse points, nested mounts, hardlinks, and special files rather than following them. Backup creation and restore take the same cross-process root lease that participating current SeedSync processes hold for their full runtime. Legacy v0.8.6 and other nonparticipating processes do not hold this lease and therefore cannot be detected by it.

After a successful upgrade and first-browser claim, an administrator can open `/migration/recovery` directly to undo that migration and try again. The recovery page is intentionally absent from ordinary navigation and Settings. It exposes only the one backup bound to the validated completion receipt, explains that downloads, mounts, and transfer staging are unchanged, and requires typing `RESTORE` plus an attestation that no other SeedSync instance is using the configuration directory. SeedSync then stops normal services, performs the existing offline restore before rebuilding the runtime, and returns the browser to `/migration` for another upgrade attempt. To return fully to v0.8.6, restore the configuration here first, then change the deployed SeedSync image back to the previous v0.8.6 image or tag; the recovery page gives the corresponding Unraid steps. The browser cannot select a backup or filesystem path.

If normal startup is unavailable after a migration attempt, stop every SeedSync process using that configuration root and run the executable in offline restore mode:

```text
seedsync -c <config-root> --restore-migration-backup <backup-id-or-absolute-path> --confirm-restore --confirm-stopped
```

Both confirmations are mandatory. `--confirm-stopped` is the operator's required attestation that every SeedSync process using the configuration root—including legacy v0.8.6 processes that the new lease cannot observe—has been stopped. The backup must be one directly retained beneath that configuration root's `migration-backups` directory. Restore verifies the complete manifest, owner, private permissions, and content before changing destination files, removes post-backup configuration entries under that exact root, retains the backup itself, and exits without constructing the web or controller runtime. Backups are intentionally restorable only by the same supported runtime principal that created them; copying them between users or relaxing their permissions is unsupported and refused. The SeedSync OS account is part of this trust boundary: owner-private files and the cross-process lease separate SeedSync from other accounts and participating current SeedSync processes, but they cannot protect against malicious software already running as the same OS account. The command remains the break-glass path when normal authenticated startup is unavailable; browser recovery is limited to the sole receipt-bound backup from the completed migration.

## How it works

Install SeedSync on a local machine.
SeedSync will connect to your remote server and sync files to the local machine as
they become available.

You don't need to install anything on the remote server.
All you need are the SSH credentials for the remote server. Bulk transfers use SFTP by default.
FTPS is available as an explicit opt-in transfer protocol in Settings; file discovery still uses SSH, and FTPS certificate verification is enabled by default.

## Recommended Workflow

The recommended setup is to use hard links with a dedicated completion directory. Point SeedSync at that directory, enable `Auto-queue`, and turn on `Delete remote files after download` so SeedSync syncs each file once while your seeding originals stay intact.

See the [Recommended Setup](src/python/docs/usage.md#recommended-setup) guide for directory layout examples, torrent-client setup notes, and the same-filesystem requirement.

## Supported Platforms

* Linux
* Raspberry Pi (v2, v3 and v4)
* Windows (via Docker)
* macOS (via Docker)


## Installation and Usage

Please refer to the documentation in this repository:

* [Installation Guide](src/python/docs/install.md)
* [Usage Guide](src/python/docs/usage.md)
* [FAQ](src/python/docs/faq.md)
* [Latest Releases](https://github.com/truedarkdev/seedsync/releases/latest)

If the remote scanner cannot copy to the configured `Server Script Path`, or the path collides with an existing `scanfs` directory, see the FAQ troubleshooting notes for the fallback and cleanup steps.

If you enable the Staging Directory, mount `/staging` in Docker and set `lftp.staging_path` in Settings so in-progress downloads land on fast storage before moving to the final location.

If you run SeedSync in Docker and need a different default permission mask, set `UMASK` to an octal value such as `002` before starting the container. Invalid values abort startup. The container runs with the configured primary `PUID`/`PGID` only, so it does not retain supplementary groups; mounted paths should be writable by that UID/GID.

The configuration root mounted at `/config` is a security boundary. New deployments should start the entrypoint as root, set `PUID`/`PGID`, and use a real POSIX directory or named volume that can reach owner-private mode `0700`. That strict path admits only a root-owned root directory or one already owned by the configured `PUID`, rejects initial group/other write access, validates the complete tree without following symlinks, hard-linked regular files, or nested mounts, and limits filesystem types to `ext2`, `ext3`, `ext4`, `xfs`, `btrfs`, `zfs`, `tmpfs`, or `overlay`. Existing Unraid-style containers that set Docker `--user` are handled by a narrower compatibility path: the entrypoint derives that current UID/GID, accepts `fuse.shfs` only when `/config` and every entry already have exactly the same owner, rejects conflicting `PUID`/`PGID`, and clamps the root to mode `0700`. This allows an image-only upgrade without a preparatory host `chmod` or `chown`; owner mismatches and unsafe tree entries still fail closed. Other shared, network, and FUSE filesystems remain unsupported. Safe startup assumes one exclusive SeedSync startup with no concurrent same-UID writer or retained write-capable descriptor. Windows shared folders and WSL DrvFS cannot provide this contract; use `compose.windows.yml` or another Docker named volume. Docker administrators and processes that control the Docker daemon remain trusted. This constraint applies only to `/config`, not `/downloads` or `/mounts`.

To use FTPS for bulk transfers, keep your SSH settings configured for file discovery, then choose `FTPS` under Settings > Transfer Protocol and set the remote FTP port. Leave certificate verification enabled unless you need the insecure compatibility mode for a self-signed or legacy server.

## Report an Issue

Please report any issues on the [issues](../../issues) page.
Please post the logs as well. The logs are available at:
* Deb install: `<user home directory>/.seedsync/log/seedsync.log`
* Docker: Run `docker logs <container id>`


## Contribute

Contributions to SeedSync are welcome!
Please take a look at the [Developer Readme](doc/DeveloperReadme.md) for instructions
on environment setup and the build process.


## License

SeedSync is distributed under Apache License Version 2.0.
See [License.txt](https://github.com/truedarkdev/seedsync/blob/master/LICENSE.txt) for more information.



![SeedSync Dashboard](https://user-images.githubusercontent.com/12875506/37031587-3a5df834-20f4-11e8-98a0-e42ee764f2ea.png)
