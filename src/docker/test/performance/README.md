# SeedSync performance lab

This lab is a tracked, synthetic-only Docker harness for reproducing scan and
model-update cost. It creates six enabled path pairs with 32,000 tiny files
per pair on each side. Shared branches produce about 214,974 expected merged
model-tree nodes. The fixture is stored in retained Docker named volumes and
is marked with a topology fingerprint; changing the requested topology on a
retained volume fails instead of regenerating it.

Set `PERF_PROFILE=mixed` for the split profile: pair 01 is a small ordinary
active pair with `auto_queue=false` and a deterministic remote-only
`remote-only-target.bin` at the pair root; pair 02 is a
200,000-files-per-side high-cardinality-idle pair. `PERF_HIGH_CARD_ENABLED=off`
disables only that pair in seeded config for an A/B run while retaining the
same filesystem and `fixture_fingerprint`; roles, counts, enabled state, and
the separate `config_fingerprint` are recorded in the manifest and
`path_pairs.json`. The default profile and six-pair behavior are unchanged.
The mixed profile seeds `Lftp.rate_limit` at the synthetic-only
2,000,000-byte/s default so Queue/Stop progress remains observable. Uniform
keeps the historical `rate_limit = 0`; the effective value is recorded in
`settings.cfg`, `path_pairs.json`, `run-manifest.json`, and fixture evidence.
Manifest topology records physical fixture expectations separately from
`enabled_expected_merged_model_tree_nodes` and
`enabled_expected_model_tree_file_count`; the former gates the >=200k fixture
requirement while the latter drives the enabled experiment's model target.

The lab must be given the exact app image under test:

~~~sh
export PERF_IMAGE=seedsync:local
export PERF_PROFILE=mixed
export PERF_RUN_ID=baseline-$(date -u +%Y%m%dt%H%M%Sz)
src/docker/test/performance/lab.sh prepare
src/docker/test/performance/lab.sh start
src/docker/test/performance/lab.sh status
src/docker/test/performance/lab.sh measure baseline
src/docker/test/performance/lab.sh stop
~~~

Worker self-check (implementation lane): run the focused
`src/docker/test/performance/tests/test_performance_lab.py` pytest file and
`git diff --check`. Verifier/final validation (acceptance lane) must run the
Docker-served app and Playwright against the exact image; worker self-checks
are not final verification.

For a browser timeline against the already-started Docker app, use the same
`PERF_PROFILE=mixed` setting through `prepare`, `start`, and `browser`. Set the
API key only in the environment and run the lab command below. The probe discovers the
`ordinary-active` pair and its `remote_only_targets` from the current manifest;
it does not contain fixture names or credentials.

~~~sh
export PERF_API_TOKEN=the-local-lab-key
export PERF_PROFILE=mixed
src/docker/test/performance/lab.sh prepare
src/docker/test/performance/lab.sh start
export PERF_PLAYWRIGHT_MODULE=playwright       # optional module name
export PERF_NODE_PATH=/path/to/node_modules    # optional NODE_PATH
export PERF_BROWSER_DESTRUCTIVE_APPROVED=on    # only after approving displayed exact targets
src/docker/test/performance/lab.sh browser candidate
~~~

The browser lane performs `Delete Local` twice on the manifest's synthetic
remote-only target. It first prints the exact local Docker-volume path and
refuses to proceed unless `PERF_BROWSER_DESTRUCTIVE_APPROVED=on`; set that
opt-in only after the displayed target set has received explicit approval.
The remote fixture remains intact.

The artifact is `tmp/pytest/performance-lab/<run-id>/candidate/browser-timeline.json`.
`PERF_NODE_BINARY` (default `node`) and `PERF_BROWSER_TIMEOUT_MS` (5,000 to
300,000 ms) are configurable. The probe uses the remembered-browser API-key
flow and never clicks the first-run claim. On failure it writes a generic
`browser-failure.png` beside the JSON artifact; screenshots are not retained
for passing runs.

Worker self-check (implementation lane; not verification):

~~~sh
node src/docker/test/performance/browser_probe.js --self-test
pytest -q src/docker/test/performance/tests/test_performance_lab.py \
  --junitxml=tmp/pytest/performance-diagnostics-instrumentation/worker-browser-harness/lab.xml
python -m py_compile src/docker/test/performance/seed_config.py
node --check src/docker/test/performance/browser_probe.js
git diff --check
~~~

Verifier/final validation (acceptance lane) must run `lab.sh browser <label>`
against the live Docker-served app with Playwright. The JSON reports relative
EventSource receive events, target-row DOM mutations/cadence, progress gaps,
EventSource apply timestamps and bounded receive-to-apply/apply-to-DOM/
receive-to-DOM correlations,
per-action click-to-response and click-to-rendered-state timings, browser
errors, identities, thresholds, and pass/fail. Worker self-checks do not count
as verifier/final validation.

`PERF_DIAGNOSTICS_MODE=on|off` controls the seeded performance diagnostics
recorder (default `on`) and is recorded in run/config evidence. For a same-image
diagnostics A/B, keep the retained fixture and image unchanged, run one label
with `PERF_DIAGNOSTICS_MODE=on PERF_BREADCRUMB_MODE=on`, then stop/reseed the
config and run the paired label with both modes `off`. The browser timeline
requires diagnostics on; use `measure` for the overhead A/B and reserve browser
actions for the diagnostics-enabled acceptance run.
The comparison records a one-way digest of Docker's immutable image ID and
fails unless both measurements use that same image identity as well as the
same fixture fingerprint; matching a mutable image tag alone is insufficient.
When diagnostics are off, `measure` uses the authenticated
`/server/model/v1/summary` endpoint for compact root cardinality and stable
model-version readiness, while sampling sanitized external Docker CPU/memory
stats on every observation for both modes into separate `app` and
`remote-helper` series. Off-mode readiness requires the expected root count and
model version to remain stable for two successful summaries plus three
consecutive app-container CPU samples at or below the hard `1.0%` gate; a
cardinality or version change resets that boundary. The readiness condition,
zero-based successful-summary target index, and required observation duration
are retained in the timing artifacts. The off-mode summary keeps the same
150-second post-target observation and timing/resource schema, but deliberately
has no stage attribution; diagnostics-on retains the full stage windows and
breadcrumb evidence.
Acceptance requires valid app and remote-helper samples in both the complete
and post-target windows; missing or malformed remote-helper data fails the
candidate/baseline result.
The acceptance gate is the settled app-container CPU average, with an effective
default threshold of `1.0` percent of one core; the peak remains secondary
evidence. The threshold is recorded in each metrics summary and may be changed
for a worker check with `PERF_SETTLED_IDLE_CPU_PERCENT`, but final acceptance
must use the default.

By default, Compose creates an isolated project-scoped bridge network for the
lab. On a host whose Docker bridge address pools are exhausted, set
`PERF_EXTERNAL_NETWORK` to an existing test-only bridge network. The lab then
attaches its uniquely named containers to that network instead of creating a
new one; it never removes or modifies the external network. Also set
`PERF_REMOTE_ADDRESS` to that run's exact remote container name when the
external network can contain other Compose projects; this prevents the generic
`remote` service alias from resolving another retained fixture.

Run the candidate against the same retained fixture with a different run ID:

~~~sh
export PERF_RUN_ID=candidate-$(date -u +%Y%m%dt%H%M%Sz)
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

The observation loop requests only the latest retained diagnostics sample.
It fetches the full retained history and breadcrumb snapshot once after the
timed window, so evidence collection does not become the recurring idle load
being measured. After the model target, it checks that compact view every ten
seconds; the app's own five-second samples remain the source for final CPU
classification.

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
