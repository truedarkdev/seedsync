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
make upgrade-v086-restart [RUN_ID=lab-a]
make upgrade-v086-build-transient [RUN_ID=transient-a]
make upgrade-v086-start-transient [RUN_ID=transient-a]
make upgrade-v086-transient [RUN_ID=transient-a]
make upgrade-v086-stop [RUN_ID=lab-a]
```

## v0.8.6 upgrade ship-readiness lane

The retained legacy lab is also the fixture source for the tracked upgrade
ship-readiness verifier.  It is a final-validation lane, not a replacement for
focused Python/Angular migration tests:

```text
# worker self-check only: parser/comparator/harness syntax; creates no run
make upgrade-v086-ship-readiness-self-check

# verifier/final validation: choose a never-before-used RUN_ID and two free,
# explicit loopback ports.  This normally takes 15-35 minutes on a warm Docker
# cache, longer when the current image or Angular bundle must be built.
RUN_ID=ship-20260726a HOST_PORT=18806 CURRENT_PORT=18816 \
  make upgrade-v086-ship-readiness
```

The final lane creates a unique retained run at
`tmp/upgrade-v086/runs/<run-id>/`; it does not delete or reuse a previous run.
It creates uniquely named legacy/current/restore containers and retains them
for post-failure inspection.  The only published listeners are the explicit
loopback ports.  The required current-image build, pinned legacy boot,
Playwright browser capture, full remote scan, migration/reboot, and offline
restore mean this is **verifier/final validation**.  The self-check is a
**worker self-check** only and is not closure evidence.

Each run also creates labelled, retained Docker named volumes:
`seedsync-upgrade-v086-config-<run-id>` is the only live `/config` storage for
the pinned legacy boot, current migration, offline restore, and pinned reboot;
`seedsync-upgrade-v086-protected-<run-id>` is POSIX-backed, owner-only archive
storage. It is intentionally never auto-deleted. The lane preflights its
owner-only mode and exclusive-create lock behavior, records hash/mode inventory
from Linux inside the config volume, and writes full config snapshots only to
the protected volume. A non-root, networkless snapshotter reads `/config` and
writes `/protected`; the validator mounts both read-only and postvalidates the
exact tar without extraction against the matching config inventory (member set,
file hashes/sizes, and modes). Before offline restore consumes a snapshot, the
validator recomputes its digest and repeats that exact binding. Host evidence
contains only a safe storage manifest (identity, digest, owner, and modes),
never archive bytes. Those
archives can contain synthetic credentials and remain
`protected-synthetic-secret`, while the final audit scans every publishable
evidence, log, and screenshot artifact.

After the initial downloads inventory and legacy/proxy shutdown, the lane also
archives the exact `/downloads` baseline as `before-downloads.tar` in that same
protected volume. A dedicated, run-labelled download snapshotter has only the
exact run downloads bind mounted read-only plus the protected volume writable;
the separate restorer reverses those access modes. Both are networkless,
non-root, read-only-rootfs containers with all capabilities dropped and
no-new-privileges. Immediately before the offline restore can reboot legacy,
the lane revalidates the archive digest and inventory binding, snapshots the
post-current downloads tree for recovery, extracts only to a private staging
directory under the exact downloads mount, compares staging to the original
inventory, then replaces the downloads contents. A final exact inventory
comparison blocks the pinned reboot on any mismatch. Archive bytes never enter
the run tree or evidence; manifests retain only safe identity, mode, digest,
and inventory metadata.
Manually remove retained containers, networks, volumes, and run directories
only after the investigation is no longer needed.

The final verifier writes its durable front page to
`evidence/ship-readiness/progress.json`, append history to `progress.tsv`, and
a versioned row-by-row matrix to `matrix.json`.  Every matrix row names its
before/expected/after/restore contract and evidence artifact; pending or failed
rows fail the lane rather than being silently skipped.  Supporting artifacts
include redacted settings, exact inventory/hash/mode JSON for config/download
and remote roots, migration status/security responses, image build output,
browser PNGs/console result, and offline-restore comparison output.  Evidence
records full local paths where needed for machine replay; this README keeps
paths portable.

The verifier runs each retained-lab command through its tracked absolute script
path in a fresh shell rooted at the repository path. This is deliberate: a WSL
cleanup can invalidate a prior working directory, so the next
startup/status/restart command must never reuse that cwd. Direct relative-path
invocation from an already-deleted cwd is unsupported because the shell cannot
open that script; use the Make targets or absolute wrapper invocation. On any
post-run-directory failure it writes `progress.json`,
`summary.json`, and `failures.json`; a non-`passed` row, including
`not-exercised`, makes the final lane fail.  The before contract normalizes the
pinned model, non-secret settings, controller/AutoQueue persistence and fixture
expectations, then compares the same observations after offline restore and a
pinned v0.8.6 reboot.  Runtime-restart evidence includes controller/scan log
health, expected visible file rows, persisted controller/AutoQueue state, and
the remembered browser session.

Browser evidence is always dispatched as `node ship_readiness_browser.mjs`,
never through Python.  On this WSL workstation it defaults
`NODE_PATH` to `/mnt/c/Users/johan/AppData/Local/Temp/codex-playwright-tools/node_modules`
and resolves the newest available WSL NVM Node binary.  Override those with
`SEEDSYNC_PLAYWRIGHT_NODE_PATH` and `SEEDSYNC_NODE_BIN` when needed.  The worker
self-check executes the same Node/MJS dispatch in a no-browser `--dispatch-check`
mode, so a wrong interpreter or module-resolution failure is caught before a
full retained run starts. Browser navigation waits for DOM content, then proves
the expected route plus the relevant app root, fixture rows, or authenticated
API response; it does not wait for an idle network because the app keeps live
connections. On browser failure it retains a redacted body snippet, final URL
and response status, console/page errors, and a screenshot before the shell
records the failed matrix summary.

After migration and after the current-runtime restart, the harness also probes
`/server/status` without browser credentials. A healthy pre-claim runtime must
return SeedSync's HTML `401` **Missing API token** challenge; connection
failures, `5xx`, unrelated `401` pages, and unexpected success responses fail
the readiness gate. This is deliberately distinct from migration readiness:
the public migration endpoint is asserted `required` before apply and
`complete/succeeded` afterward, while the Playwright browser flow separately
proves the claimed and remembered-browser authenticated `200` API requests.
The probe parses raw headers and curl stderr only in an isolated `/tmp`
workspace, removes that workspace on success, retry exhaustion, and handled
interrupt signals, and retains only a fixed JSON allowlist. Timeout diagnostics
contain only phase, attempt count, curl exit code, and HTTP status.

Validator helper JSON is likewise captured outside retained evidence, parsed,
and atomically published only when it is non-empty valid JSON. A failed helper
leaves no final result JSON; it records a bounded, redacted stderr excerpt and
a failed progress phase instead. This keeps a failed redirect from masquerading
as an empty successful contract artifact.

The post-apply contract requires the current producer's initialized auth-store
schema when the store is materialized (`version: 3`, exact known keys, empty
API keys/sessions, and an unclaimed handover). It also validates the retained
backup manifest and data tree exactly: normalized unique file/directory
entries, types, member set, hashes, sizes, and non-broadened modes. Runtime
permission hardening is recorded when it only reduces access, such as a legacy
`0644` settings copy becoming `0600`.
The lane captures two distinct auth-phase artifacts. Before applying, the
legacy inventory must contain neither `api-keys.json` nor its history file;
the inventory walker rejects every symlink, so a dangling or indirect legacy
auth path cannot be hidden as optional absence. At the deterministic
migration-apply boundary, the harness binds that baseline to the successful
coordinator transaction status (`complete` plus operation `succeeded`). The
coordinator validates the transaction end state with neither `api-keys.json`
nor `api-keys.history.jsonl`, including no dangling symlink, and the migration
web app records that status before scheduling normal startup. This avoids
treating a delayed live-volume read as a historical migration-time proof.

After normal startup, but before the lane's explicit current-container
restart, the read-only validator directly observes the config volume. The
auth store may remain lazily absent only when it is neither present nor a
symlink; otherwise it must be the exact empty version-3 producer store. The
version is an integer `3` (not `3.0`), and the store rejects keys, sessions,
claims, dangling links, or schema drift. The apply-boundary and post-start
artifacts are separately linked from the migration-transform matrix row.
Failure excerpts redact request and response credentials, including ordinary or
quoted `Cookie` headers, authorization headers, quoted JSON values, and URL
query credentials.

The current-runtime bridge is separate from the legacy proxy: the current app
and SSH fixture remain on the internal lab network, while a read-only,
no-new-privileges current proxy is the only container that joins both the lab
and browser networks and publishes the current loopback port. The final lane
asserts that topology and records `current-topology.json` plus
`current-runtime-provenance.json` (image/container binding, build-input hashes,
Git head, and a dirty-worktree fingerprint). Long lab/test commands are bounded
and heartbeat their phase; a timeout writes retained command diagnostics rather
than leaving the lane opaque.

The inventory comparator excludes only migration-owned root infrastructure
(`migration-backups`, lock/state/journal files) when evaluating the restored
legacy configuration.  The retained backup itself is intentionally preserved;
all legacy user configuration files must match their before hashes, sizes and
modes exactly.  Settings evidence redacts password/secret/token/API-key-like
values and the harness never writes the synthetic remote password to its
evidence artifacts.

The final lane covers the pinned before state, migration request guards and
single-flight response, retained backup and deterministic Default path-pair
transform, first claim/browser handoff, current runtime/browser/API evidence,
offline restore and pinned legacy reboot.  It also records the focused
tamper/partial-backup, active-runtime, interrupted/retry, and malformed
Host/Origin/body probes that are already strongly exercised in the migration
unit/live suites; it links their saved outputs instead of duplicating their
implementation in a second broad test suite.

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

The tracked `fixture-manifest.json` is the source of truth for the stable
legacy model matrix: backend/UI state, AutoQueue expectation, persist markers,
and the future migration invariant are validated before materialization. The
manifest validator rejects unsafe/non-NFC/ambiguous paths, case-fold
collisions, and oversized generated/text payloads; model validation checks the
complete nested child tree rather than roots only. The
restart waits for a fresh scan/model settlement and checks exact historical
`controller.persist` keys and marker sets. Stable status/restart commands refuse
transient-mode runs. Use `upgrade-v086-build-transient`,
`upgrade-v086-start-transient`, then `upgrade-v086-transient` on a dedicated
transient-mode run to exercise real historical
lftp and extraction behavior; it records bounded JSON evidence for QUEUED,
DOWNLOADING, and EXTRACTING and records states that are too brief to observe
instead of fabricating them. That command recreates the run with a temporary
historical lftp rc (`net:limit-rate=256K`, queue/file parallelism 1) so two
transient downloads can expose queueing without an unbounded transfer or
timing-sensitive large fixture. The dedicated run also overrides the pinned
historical config's parallel-job/file and connection defaults to one, because
the legacy controller reapplies those settings after loading the rc file. A
stable retained run cannot be converted in place; this prevents transient
controls or outputs from contaminating the stable migration oracle.

`fixture-evidence.json` is materialized beside `fixture-expected.json` in every
run. It declares all seven backend states, the frontend-derived `stopped`
state, stable versus transient classifications, the minimal stable topology
coverage (root file, root directory, nested directory, and child file), derived
and source-validated from each fixture's actual remote/local shape, the
AutoQueue positive/negative mapping, transient probe targets, and intentional
exclusions. It is a generated evidence contract, not an assertion that timing-
dependent states were stable. `transient-state.json` records each bounded
observation's target, observed flag, timestamps, timeout, current state, and
states seen; `transient-summary.json` is the same JSON echoed for shell logs.
An unobserved transient is reported as a bounded limitation and is never
converted into a hard stable expectation.

The transient command requires both the canonical pinned manifest and the
generated fixture evidence; it rebuilds and compares the complete contract
before issuing any queue or extract action, failing closed on stale or altered
artifacts.

The directory fixtures intentionally exercise aggregate remote/local sizes:
`root-directory-default` has no local aggregate, while
`root-directory-stopped` has one partial child plus one complete nested child;
`nested-specials` adds distinct duplicate basenames in different directories.
This is the representative directory behavior, not a Cartesian copy of every
state across every topology.

Stable browser evidence checklist (after `upgrade-v086-status`): capture the
dashboard with the complete stable state set and representative topologies,
then inspect AutoQueue settings and the status filter/sort controls supported
by the pinned UI. Capture a restart recovery view after
`upgrade-v086-restart`, and record any console/page errors. On a dedicated
transient run, capture only states actually reported in `transient-state.json`;
do not label a screenshot as queued, downloading, or extracting when the probe
timed out or ended in another current state.

The Compose service uses the retained named-volume `/config` plus bind-mounted `/downloads`, `/mounts`, and
`/logs` contract are deliberately stable for this disposable legacy fixture.
This foundation slice does not accept arbitrary image overrides or perform
image migration/UI changes; future migration work must first create a verified
snapshot/clone contract around this retained fixture.
