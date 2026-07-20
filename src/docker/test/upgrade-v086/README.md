# Reproducible v0.8.6 upgrade lab

This lab derives the legacy source tree from the pinned v0.8.6 commit
`ff2a1039935beccbbf7ec76134b41d2e91137742` with `git archive`; it never switches,
resets, or overwrites the active worktree. Docker build layers and the extracted
source are cached under the ignored `tmp/upgrade-v086/` tree.

From the repository root:

```text
make upgrade-v086-preflight
make upgrade-v086-build [RUN_ID=lab-a]
make upgrade-v086-start [RUN_ID=lab-a]
make upgrade-v086-status [RUN_ID=lab-a]
make upgrade-v086-stop [RUN_ID=lab-a]
```

`build` refuses to reuse a run directory. Each run gets synthetic config,
download, and SSH-remote fixtures plus redacted manifest/log/evidence files at
`tmp/upgrade-v086/runs/<run-id>/`. The app is exposed only at
`http://127.0.0.1:18806/` by default; the remote SSH fixture is internal to the
Compose network. Set `HOST_PORT` to choose another loopback port. Run IDs are
strictly validated and each run receives a distinct labelled internal network.
Only the hardened browser proxy joins a second per-run browser network and
publishes the loopback port; the legacy app and SSH fixture remain on the
internal network only, so `upgrade_remote` DNS cannot cross between runs.

The image builds the real Angular frontend from the pinned historical source;
it does not serve a placeholder page. A lab-owned npm v6 lockfile is installed
with `npm ci`; evidence records its digest and the resolved npm, pip, and dpkg
inventories alongside source, base-image, helper-file, and runtime image
digests. This gives repeatable local builds and provenance for the executed
image, but is not a fully air-gapped or bit-for-bit dependency guarantee: npm,
apt, and pip package archives are still fetched unless Docker has them cached.

All fixtures are synthetic and disposable. Do not point this lab at real user
configuration, downloads, remote hosts, credentials, or state. Run artifacts
use a restrictive umask and permissions where the filesystem supports them, but
DrvFS mode bits are not confidentiality enforcement; keep sensitive material
outside this lab and rely on host ACLs for access control.

The Compose service and bind-mounted `/config`, `/downloads`, `/mounts`, and
`/logs` contract are deliberately stable for this disposable legacy fixture.
This foundation slice does not accept arbitrary image overrides or perform
image migration/UI changes; future migration work must first create a verified
snapshot/clone contract around this retained fixture.
