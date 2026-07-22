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
* **Large libraries, your way** - show every download at once or choose 25, 50, 100, or 1,000 per page, with faster list updates, search, filters, sorting, and bulk actions for big queues.

When v0.8.6 configuration needs migration, SeedSync exposes only a read-only migration checkpoint. Direct/package launches bind that checkpoint to `127.0.0.1` unless `--web-bind-host` is explicitly supplied; Docker keeps its explicit `SEEDSYNC_WEB_BIND_HOST` setting. The checkpoint reuses a valid legacy `[Web] port` value from `settings.cfg` (range 1-65535) and otherwise falls back to port 8800; Docker deployments with a custom legacy port must publish that same container port. Migration start remains unavailable until retained backup and restore support is implemented and validated.

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
