# SeedSync performance lab

This lab is a tracked, synthetic-only Docker harness for reproducing scan and
model-update cost. It creates six enabled path pairs with 32,000 tiny files
per pair on each side. Shared branches produce about 214,974 expected merged
model-tree nodes. The fixture is stored in retained Docker named volumes and
is marked with a topology fingerprint; changing the requested topology on a
retained volume fails instead of regenerating it.

The lab must be given the exact app image under test:

~~~sh
export PERF_IMAGE=seedsync:local
export PERF_RUN_ID=baseline-$(date -u +%Y%m%dT%H%M%SZ)
src/docker/test/performance/lab.sh prepare
src/docker/test/performance/lab.sh start
src/docker/test/performance/lab.sh status
src/docker/test/performance/lab.sh measure baseline
src/docker/test/performance/lab.sh stop
~~~

By default, Compose creates an isolated project-scoped bridge network for the
lab. On a host whose Docker bridge address pools are exhausted, set
`PERF_EXTERNAL_NETWORK` to an existing test-only bridge network. The lab then
attaches its uniquely named containers to that network instead of creating a
new one; it never removes or modifies the external network.

Run the candidate against the same retained fixture with a different run ID:

~~~sh
export PERF_RUN_ID=candidate-$(date -u +%Y%m%dT%H%M%SZ)
src/docker/test/performance/lab.sh start
src/docker/test/performance/lab.sh measure candidate
src/docker/test/performance/lab.sh stop
~~~

PERF_BREADCRUMB_MODE=on is the default and uses the bounded 128-entry
recorder. To perform the otherwise-identical breadcrumb A/B, stop the app,
set PERF_BREADCRUMB_MODE=off, run prepare (the fixture marker prevents
topology changes), then start and measure another run. The metric summary and
comparison include breadcrumb mode, retention, entry count, diagnostics stage
windows, process/cgroup CPU and memory, and Docker process/container/image
records.

Remote transport timing includes SSH setup, capability probing, helper
installation, and scan reads. Remote stages can overlap or nest while those
operations run, so their percentages are attribution signals and must not be
added as if they were disjoint shares of elapsed time.

`PERF_MOVE_FAILURE_MODE=stale` is the production-shaped default and seeds one
durable retry marker. Use `PERF_MOVE_FAILURE_MODE=none` only for the paired
trigger-isolation run against the same retained fixture; the run manifest
records which mode was used.

Each run is retained under
tmp/pytest/performance-lab/<run-id>/. The baseline summary rejects a
fixture unless the expected merged cardinality is present, the model file
count is within 15% of target, several consecutive samples are materially
busy, CPU is attributed to a fixed scanner/model-update stage, no duration
spans were dropped, and breadcrumb mode is recorded. It separately reports
cold start/full-scan timing and post-scan settled-idle windows, so a quiet
post-scan process is not confused with persistent lifecycle churn. The default
waits for six real diagnostics samples (about 30 seconds) after reaching the
model target before judging steady idle against the one-core CPU target. When a
target sequence is available, a baseline also needs a consecutive high-CPU,
fixed-stage post-target window to count as a persistent saturation result;
ordinary expensive scans followed by quiet idle are recorded but rejected.

The target boundary requires both the expected full model cardinality and a
complete diagnostics window with zero model builds, so a long startup build
cannot open the idle window while catch-up builds remain queued. The default
post-target observation lasts 150 seconds as well as at least six diagnostics
samples. This deliberately crosses the mirrored 120-second remote refresh
boundary; a short quiet gap before the next scan cannot satisfy the idle CPU
contract. `diagnostics-at-target.json` preserves the exact counter
baseline for refresh-delta analysis. `PERF_POST_TARGET_OBSERVATION_SECONDS`
may be shortened only for diagnostic worker self-checks, never final
performance acceptance.

After the final diagnostics window and final container resource snapshot,
`measure` requests the admin ownership census once and writes the sanitized
`ownership-final.json` artifact. The retained file contains only its fixed
numeric owner schema. Detailed totals are globally deduplicated; structural
file-graph totals are independently deduplicated per owner and can overlap
between the live, local, and remote roots. Both traversal kinds expose their
own truncation state. Request, schema, or validation failures produce an
explicit `capture_status` of `unavailable` without retaining the raw response.

The local fixture uses Linux Docker volumes and does not reproduce Unraid's
btrfs/FUSE mount mix. Run metadata records this limitation; never point this
harness at Unraid or production data. stop only stops containers and never
removes volumes or artifacts.
