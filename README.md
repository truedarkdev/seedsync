<p align="center">
    <img src="https://user-images.githubusercontent.com/12875506/85908858-c637a100-b7cb-11ea-8ab3-75c0c0ddf756.png" alt="SeedSync" />
</p>

> This repository is a maintained integration fork of [ipsingh06/seedsync](https://github.com/ipsingh06/seedsync).

<p align="center">
  <a href="https://github.com/truedarkdev/seedsync">
    <img src="https://img.shields.io/github/stars/truedarkdev/seedsync" alt="Stars">
  </a>
  <a href="https://github.com/truedarkdev/seedsync/releases/latest">
    <img src="https://img.shields.io/github/v/release/truedarkdev/seedsync" alt="Release">
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

## How it works

Install SeedSync on a local machine.
SeedSync will connect to your remote server and sync files to the local machine as
they become available.

You don't need to install anything on the remote server.
All you need are the SSH credentials for the remote server.

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
