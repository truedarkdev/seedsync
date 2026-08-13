#!/usr/bin/env node
'use strict';

/*
 * Sanitized browser timeline probe for the synthetic performance lab.
 * Playwright is intentionally required only after --self-test handling so the
 * pure statistics/schema checks work in a minimal worker environment.
 */

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

const MAX_EVENTS = 256;
const MAX_MUTATIONS = 1024;
const MAX_ERRORS = 64;
const DEFAULT_TIMEOUT_MS = 60_000;
const MAX_READINESS_STEPS = 4;
const MAX_PROGRESS_GAP_MS = 1_250;
const ACTION_RENDER_LIMITS_MS = Object.freeze({
  queue: 250,
  stop: 250,
  delete_local: 1_200,
  requeue: 250,
});
const QUEUEABLE_STATES = new Set(['default', 'default-remote', 'stopped', 'deleted', 'corrupt']);
// Deleting an incomplete first download returns the target to its ordinary
// remote-only state. A target with completed-download history is instead
// rendered as deleted. Both prove that local content is absent and Queue is
// available; requiring only the historical state turns a successful delete
// into a multi-minute false timeout on a freshly seeded fixture.
const LOCAL_ABSENT_STATES = ['deleted', 'default-remote', 'local-absent'];

function boundedPush(array, value, limit) {
  if (array.length < limit) array.push(value);
}

function stableDigest(value) {
  if (value == null || value === '') return null;
  return crypto.createHash('sha256').update(String(value), 'utf8').digest('hex').slice(0, 16);
}

function normalizeAppPath(pathname) {
  if (pathname == null || pathname === '') return null;
  let value = String(pathname);
  try { value = new URL(value, 'http://seedsync.invalid').pathname; } catch (_) { /* keep the input */ }
  if (value === '/server/model/v1/summary' || value === '/server/model/v1/summary/stream') return value;
  const match = value.match(/^\/server\/model\/v1\/pairs\/([^/]+)\/(roots|children|stream)$/);
  if (match) {
    let scope = match[1];
    try { scope = decodeURIComponent(scope); } catch (_) { /* retain encoded scope for hashing */ }
    return `/server/model/v1/pairs/<scope-digest:${stableDigest(scope)}>/${match[2]}`;
  }
  if (value.startsWith('/server/model/v1/')) return '/server/model/v1/<route>';
  return null;
}

function finiteNumbers(values) {
  return (Array.isArray(values) ? values : [])
    .map(Number)
    .filter(value => Number.isFinite(value) && value >= 0);
}

function percentile(values, percentileRank = 0.95) {
  const numbers = finiteNumbers(values).sort((a, b) => a - b);
  if (!numbers.length) return null;
  const rank = Math.max(0, Math.min(numbers.length - 1, Math.ceil(numbers.length * percentileRank) - 1));
  return Number(numbers[rank].toFixed(3));
}

function maximum(values) {
  const numbers = finiteNumbers(values);
  return numbers.length ? Number(Math.max(...numbers).toFixed(3)) : null;
}

function cadence(samples) {
  const times = (Array.isArray(samples) ? samples : [])
    .map(sample => Number(sample && sample.t_ms))
    .filter(value => Number.isFinite(value) && value >= 0)
    .sort((a, b) => a - b);
  const gaps = [];
  for (let index = 1; index < times.length; index += 1) {
    gaps.push(times[index] - times[index - 1]);
  }
  return {count: times.length, p95_ms: percentile(gaps), max_ms: maximum(gaps), gaps_ms: gaps};
}

function progressGap(samples) {
  const gaps = [];
  let previousActive = null;
  let activeSampleCount = 0;
  for (const sample of (Array.isArray(samples) ? samples : [])
    .slice().sort((a, b) => Number(a?.t_ms) - Number(b?.t_ms))) {
    const progress = Number(sample && sample.progress);
    const status = String(sample && sample.status || '').toLowerCase();
    const active = Number.isFinite(progress) && progress > 0 && progress < 100
      && status !== 'stopped' && status !== 'downloaded'
      && Number.isFinite(Number(sample && sample.t_ms));
    if (!active) {
      previousActive = null;
      continue;
    }
    activeSampleCount += 1;
    if (previousActive != null) gaps.push(Number(sample.t_ms) - previousActive);
    previousActive = Number(sample.t_ms);
  }
  return {max_ms: maximum(gaps), sample_count: activeSampleCount};
}

function latencyStats(samples, field) {
  return {
    count: (Array.isArray(samples) ? samples : []).filter(sample => Number.isFinite(Number(sample && sample[field]))).length,
    p95_ms: percentile((samples || []).map(sample => sample && sample[field])),
    max_ms: maximum((samples || []).map(sample => sample && sample[field])),
  };
}

function latestRelevantApply(applies, atMs, scopedPath) {
  return (Array.isArray(applies) ? applies : [])
    .filter(item => item && Number(item.t_ms) <= Number(atMs)
      && String(item.pathname || '') === String(scopedPath || '')
      && ['model-init', 'model-added', 'model-updated', 'model-removed',
        'model-page', 'model-invalidate', 'model-patch', 'model-reset'].includes(String(item.event_type)))
    .sort((a, b) => Number(a.t_ms) - Number(b.t_ms)).slice(-1)[0] || null;
}

function actionStats(actions, field) {
  const grouped = {};
  for (const action of Array.isArray(actions) ? actions : []) {
    if (!action || action.measured === false || !Number.isFinite(Number(action[field]))) continue;
    const name = String(action.name || 'unknown');
    if (!grouped[name]) grouped[name] = [];
    grouped[name].push(Number(action[field]));
  }
  return Object.fromEntries(Object.entries(grouped).map(([name, values]) => [name, {
    count: values.length,
    p95_ms: percentile(values),
    max_ms: maximum(values),
  }]));
}

function cleanupPass(record) {
  if (!record || record.required !== true) return true;
  return record.attempted === true
    && Array.isArray(record.errors) && record.errors.length === 0
    && record.residual_local_absent === true;
}

function safeText(value) {
  let text = String(value || '')
    .replace(/\/server\/model\/v1\/pairs\/([^\s/?]+)\/(roots|children|stream)/g,
      (_match, scope, action) => {
        try { scope = decodeURIComponent(scope); } catch (_) { /* retain encoded scope */ }
        return `/server/model/v1/pairs/<scope-digest:${stableDigest(scope)}>/${action}`;
      })
    .replace(/\/server\/model\/v1\/summary(?:\/stream)?/g, match => match);
  return text
    .replace(/https?:\/\/[^\s]+/gi, '<url>')
    .replace(/file:\/\/[^\s]+/gi, '<file>')
    .replace(/[A-Za-z]:\\[^\s]+/g, '<path>')
    .replace(/(?:^|\s)\/(?!server\/model\/v1(?:\/|$))(?:[^\s/]+\/)+[^\s]*/g, ' <path>')
    .replace(/([?&](?:token|api[_-]?key|secret|password|credential)=)[^&\s]*/gi, '$1<redacted>')
    .slice(0, 240);
}

function errorRecord(kind, error) {
  return {kind, message: safeText(error && (error.message || error.errorText || error))};
}

function sanitizeTimelinePaths(items) {
  return (Array.isArray(items) ? items : []).map(item => ({
    ...item,
    pathname: normalizeAppPath(item && item.pathname) || '/server/<route>',
  }));
}

function parseOption(argv, name, fallback = null) {
  const index = argv.indexOf(name);
  return index >= 0 && index + 1 < argv.length ? argv[index + 1] : fallback;
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function targetIdentityMatches(identity, targetId, targetName) {
  if (!identity) return false;
  if (targetId) return String(identity.file_id || '') === String(targetId);
  return String(identity.name || '') === String(targetName || '');
}

function targetIdentityMode(targetId) {
  return targetId ? 'file-id' : 'target-name';
}

function targetIdentityMismatchError(identity, targetId, targetName) {
  const expected = targetId ? `file id ${stableDigest(targetId)}` : `target name ${safeText(targetName)}`;
  const actual = identity?.file_id ? `file id ${stableDigest(identity.file_id)}` : `target name ${safeText(identity?.name)}`;
  return invalidPrecondition(`target identity mismatch: expected ${expected}, observed ${actual}`);
}

function runSelfTest() {
  assert(percentile([10, 20, 30, 40]) === 40, 'p95 nearest-rank statistic failed');
  assert(maximum([1, 8, 3]) === 8, 'max statistic failed');
  const c = cadence([{t_ms: 0}, {t_ms: 100}, {t_ms: 250}]);
  assert(c.p95_ms === 150 && c.max_ms === 150, 'cadence statistic failed');
  const gap = progressGap([
    {t_ms: 0, status: 'queued', progress: 0},
    {t_ms: 500, status: 'local-only', progress: 1},
    {t_ms: 1200, status: 'downloading', progress: 2},
    {t_ms: 1300, status: 'stopped', progress: 2},
    {t_ms: 5000, status: 'downloading', progress: 3},
    {t_ms: 5700, status: 'downloading', progress: 4},
  ]);
  assert(gap.max_ms === 700 && gap.sample_count === 4, 'progress gap statistic failed');
  assert(progressGap([{t_ms: 1, status: 'local only', progress: 12.5}]).sample_count === 1,
    'non-downloading finite in-flight progress was not counted');
  const latency = latencyStats([{receive_to_dom_ms: 35}, {receive_to_dom_ms: 120}], 'receive_to_dom_ms');
  assert(latency.p95_ms === 120 && latency.max_ms === 120, 'receive-to-dom latency statistic failed');
  assert(latestRelevantApply([
    {t_ms: 10, pathname: '/server/stream', event_type: 'message'},
    {t_ms: 20, pathname: '/server/model/v1/pairs/x/stream', event_type: 'model-updated'},
  ], 30, '/server/model/v1/pairs/x/stream').t_ms === 20, 'unrelated stream correlation filter failed');
  assert(latestRelevantApply([
    {t_ms: 10, pathname: '/server/model/v1/pairs/x/stream', event_type: 'model-page'},
    {t_ms: 20, pathname: '/server/model/v1/pairs/x/stream', event_type: 'model-invalidate'},
    {t_ms: 30, pathname: '/server/model/v1/pairs/x/stream', event_type: 'model-patch'},
    {t_ms: 40, pathname: '/server/model/v1/pairs/x/stream', event_type: 'model-reset'},
    {t_ms: 45, pathname: '/server/model/v1/summary/stream', event_type: 'model-reset'},
  ], 50, '/server/model/v1/pairs/x/stream').event_type === 'model-reset', 'v1 model event names are not accepted');
  assert(latestRelevantApply([
    {t_ms: 10, pathname: '/server/model/v1/pairs/other/stream', event_type: 'model-updated'},
    {t_ms: 20, pathname: '/server/model/v1/pairs/x/stream', event_type: 'message'},
  ], 30, '/server/model/v1/pairs/x/stream') === null, 'generic or other-pair events were correlated');
  const target = discoverTarget({
    synthetic_only: true,
    path_pairs: [{id: 'pair-01', name: 'Performance Pair 01', directory: 'path-pair-01', role: 'ordinary-active',
      remote_only_targets: [{relative_path: ['path-pair-01', 'remote-only', 'target.bin'].join('/')}]}],
  });
  assert(target.pair_id === 'pair-01', 'target pair identity was not retained internally');
  assert(!JSON.stringify(target).includes('pair-01'), 'target pair identity leaked into serialized evidence');
  assert(normalizeAppPath('/server/model/v1/pairs/private-scope/stream') ===
    '/server/model/v1/pairs/<scope-digest:7bc278faa0682944>/stream', 'scoped route normalization failed');
  assert(safeText('/server/model/v1/pairs/private-scope/stream').includes('/server/model/v1/pairs/<scope-digest:'),
    'normalized scoped route was redacted as a generic path');
  assert(cleanupPass({required: true, attempted: true, errors: [], restored_state: 'stopped',
    residual_state: 'stopped', residual_local_absent: true}),
    'successful cleanup was not accepted');
  assert(!cleanupPass({required: true, attempted: true, errors: [{kind: 'cleanup-stop'}],
    restored_state: 'deleted', residual_state: 'deleted', residual_local_absent: true}),
  'cleanup failure was silently accepted');
  assert(readinessIsQueueable({status: 'default-remote', controls: {
    Queue: {enabled: true}, Stop: {enabled: false}, 'Delete Local': {enabled: false},
  }}), 'remote-only default target was not recognized as queueable');
  assert(readinessIsQueueable({status: 'stopped', controls: {
    Queue: {enabled: true}, Stop: {enabled: false}, 'Delete Local': {enabled: false},
  }}), 'stopped remote target was not recognized as queueable');
  assert(!readinessIsQueueable({status: 'downloaded', controls: {
    Queue: {enabled: false}, Stop: {enabled: false}, 'Delete Local': {enabled: true},
  }}), 'local terminal target was incorrectly recognized as queueable');
  assert(targetIdentityMatches({file_id: 'stable-id', name: 'reordered-title'}, 'stable-id', 'target.bin'),
    'file-id identity did not take precedence over reordered title');
  assert(!targetIdentityMatches({file_id: 'other-id', name: 'target.bin'}, 'stable-id', 'target.bin'),
    'wrong file-id identity was accepted despite matching title');
  assert(targetIdentityMatches({file_id: null, name: 'target.bin'}, null, 'target.bin'),
    'exact target-name fallback identity was rejected');
  assert(!targetIdentityMatches({file_id: null, name: 'target.bin.bak'}, null, 'target.bin'),
    'non-exact target-name fallback identity was accepted');
  assert(measuredTargetMutations([{t_ms: 1}, {t_ms: 10}, {t_ms: 11}], 10).length === 2,
    'pre-queue DOM samples were not excluded from measurement');
  const output = {
    schema: 'seedsync.performance-lab.browser-self-test.v1',
    statistics: {p95_ms: percentile([1, 2, 3, 4]), max_ms: maximum([1, 2, 3, 4])},
    thresholds: {
      target_dom_p95_ms: 200,
      target_dom_max_ms: 500,
      max_progress_gap_ms: MAX_PROGRESS_GAP_MS,
      action_render_limits_ms: ACTION_RENDER_LIMITS_MS,
    },
    checks: {readiness_matrix: true, stable_identity_reorder: true, measurement_epoch: true},
    pass: true,
  };
  process.stdout.write(`${JSON.stringify(output)}\n`);
}

if (process.argv.includes('--self-test')) {
  try {
    runSelfTest();
    process.exitCode = 0;
  } catch (error) {
    process.stderr.write(`browser probe self-test failed: ${safeText(error)}\n`);
    process.exitCode = 1;
  }
} else {
  main().catch(error => {
    process.stderr.write(`browser probe failed: ${safeText(error)}\n`);
    process.exitCode = 1;
  });
}

async function main() {
  const argv = process.argv.slice(2);
  const label = parseOption(argv, '--label');
  const baseUrl = parseOption(argv, '--base-url');
  const manifestPath = parseOption(argv, '--manifest');
  const runManifestPath = parseOption(argv, '--run-manifest');
  const bindingPath = parseOption(argv, '--binding');
  const imageIdentityDigest = parseOption(argv, '--image-identity-digest');
  const outputPath = parseOption(argv, '--output');
  if (!label || !baseUrl || !manifestPath || !outputPath) {
    throw new Error('usage: browser_probe.js --label <label> --base-url <url> --manifest <path> --output <path>');
  }
  const apiToken = process.env.PERF_API_TOKEN;
  if (typeof apiToken !== 'string' || !apiToken.trim()) {
    throw new Error('PERF_API_TOKEN must be set in the environment');
  }
  const timeoutMs = Number(process.env.PERF_BROWSER_TIMEOUT_MS || DEFAULT_TIMEOUT_MS);
  if (!Number.isFinite(timeoutMs) || timeoutMs < 5_000 || timeoutMs > 300_000) {
    throw new Error('PERF_BROWSER_TIMEOUT_MS must be between 5000 and 300000');
  }

  const outputFile = path.resolve(outputPath);
  fs.mkdirSync(path.dirname(outputFile), {recursive: true});
  let manifest;
  let runManifest = {};
  let binding = null;
  try {
    manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
    if (runManifestPath && fs.existsSync(runManifestPath)) {
      runManifest = JSON.parse(fs.readFileSync(runManifestPath, 'utf8'));
    }
    if (bindingPath) binding = JSON.parse(fs.readFileSync(bindingPath, 'utf8'));
  } catch (error) {
    const failed = baseEvidence(label, runManifest, null, errorRecord('manifest', error));
    writeEvidence(outputFile, failed);
    throw error;
  }

  const target = discoverTarget(manifest);
  const evidence = baseEvidence(label, runManifest, target, null);
  evidence.identities.live_binding_digest = binding ? stableDigest(JSON.stringify(binding)) : null;
  evidence.identities.image_identity_digest = imageIdentityDigest || null;
  evidence.identities.fixture_fingerprint = manifest.fixture_fingerprint || null;
  evidence.identities.config_fingerprint = manifest.config_fingerprint || null;
  evidence.identities.manifest_schema = manifest.schema || null;
  if (process.env.PERF_BROWSER_DESTRUCTIVE_APPROVED !== 'on') {
    const error = new Error('PERF_BROWSER_DESTRUCTIVE_APPROVED=on is required after explicit approval of the displayed synthetic Delete Local target');
    evidence.failure_classification = 'destructive-approval';
    boundedPush(evidence.errors, errorRecord(evidence.failure_classification, error), MAX_ERRORS);
    writeEvidence(outputFile, evidence);
    throw error;
  }
  const expectedTargetPath = path.posix.join('/mounts', target.pair_directory, ...target.path_segments);
  if (!binding || binding.validated !== true || binding.service !== 'app'
      || binding.volume?.type !== 'volume' || binding.volume?.target !== '/mounts'
      || typeof binding.volume?.source_name_digest !== 'string'
      || typeof binding.fixture?.fixture_fingerprint !== 'string'
      || binding.fixture.fixture_fingerprint !== manifest.fixture_fingerprint
      || binding.target_path !== expectedTargetPath) {
    const error = new Error('validated live app/project/service/volume/fixture binding is required before Delete Local');
    evidence.failure_classification = 'live-binding';
    boundedPush(evidence.errors, errorRecord(evidence.failure_classification, error), MAX_ERRORS);
    writeEvidence(outputFile, evidence);
    throw error;
  }
  if (!imageIdentityDigest || !/^[0-9a-f]{64}$/i.test(imageIdentityDigest)) {
    const error = new Error('immutable app image identity digest is required for browser evidence');
    evidence.failure_classification = 'image-identity';
    boundedPush(evidence.errors, errorRecord(evidence.failure_classification, error), MAX_ERRORS);
    writeEvidence(outputFile, evidence);
    throw error;
  }
  let browser = null;
  let context = null;
  let page = null;
  try {
    // Lazy module resolution is deliberately after argument/token validation and
    // self-test handling.  The lab shell controls NODE_PATH and module name.
    const moduleName = process.env.PERF_PLAYWRIGHT_MODULE
      || process.env.SEEDSYNC_PLAYWRIGHT_MODULE || 'playwright';
    const playwright = require(moduleName);
    if (!playwright || !playwright.chromium) throw new Error('Playwright chromium is unavailable');
    browser = await playwright.chromium.launch({headless: true});
    context = await browser.newContext();
    page = await context.newPage();
    installPageDiagnostics(page, evidence);
    await page.goto(new URL('/bootstrap', baseUrl).href, {waitUntil: 'domcontentloaded', timeout: timeoutMs});
    await rememberBrowser(page, baseUrl, apiToken, timeoutMs, evidence);
    await page.goto(new URL('/dashboard', baseUrl).href, {waitUntil: 'domcontentloaded', timeout: timeoutMs});
    await waitForDashboardShell(page, timeoutMs);
    await selectPairFromSidebar(page, target.pair_name, timeoutMs);
    await page.locator('#file-list').waitFor({state: 'visible', timeout: timeoutMs});
    const modelStreamPath = await waitForScopedModelPath(page, target.pair_id, timeoutMs);
    evidence.identities.model_stream_path = normalizeAppPath(modelStreamPath) || '/server/model/v1/<route>';
    await traverseTargetRows(page, target, timeoutMs);
    const targetId = await readTargetId(page, target.name, timeoutMs);
    target.file_id_present = Boolean(targetId);
    await exerciseActions(page, target, targetId, timeoutMs, evidence, modelStreamPath);
    const timeline = await page.evaluate(() => window.__seedSyncPerfTimeline?.snapshot?.() || {});
    evidence.samples.event_source_receive = sanitizeTimelinePaths(timeline.eventSourceReceive);
    evidence.samples.event_source_apply = sanitizeTimelinePaths(timeline.eventSourceApply);
    evidence.samples.target_dom_mutations = Array.isArray(timeline.targetDomMutations) ? timeline.targetDomMutations : [];
    evidence.measurement.epoch_t_ms = timeline.measurementEpochMs ?? evidence.measurement.epoch_t_ms;
    evidence.measurement.measured_queue_t_ms = timeline.measuredQueueMs ?? evidence.measurement.measured_queue_t_ms;
    finalizeEvidence(evidence);
    writeEvidence(outputFile, evidence);
    if (!evidence.pass) throw new Error('browser timeline thresholds failed');
  } catch (error) {
    evidence.pass = false;
    evidence.failure_classification = classifyFailure(error);
    if (error && error.precondition) {
      boundedPush(evidence.preconditions, {
        action: error.action || null,
        pre_action: error.precondition,
      }, MAX_EVENTS);
    }
    boundedPush(evidence.errors, errorRecord(evidence.failure_classification, error), MAX_ERRORS);
    if (page) {
      try {
        const screenshot = path.join(path.dirname(outputFile), 'browser-failure.png');
        await page.screenshot({path: screenshot, fullPage: false});
        evidence.failure_screenshot = path.basename(screenshot);
      } catch (screenshotError) {
        boundedPush(evidence.errors, errorRecord('failure-screenshot', screenshotError), MAX_ERRORS);
      }
    }
    try {
      if (page) {
        const timeline = await page.evaluate(() => window.__seedSyncPerfTimeline?.snapshot?.() || {});
        evidence.samples.event_source_receive = sanitizeTimelinePaths(timeline.eventSourceReceive);
        evidence.samples.event_source_apply = sanitizeTimelinePaths(timeline.eventSourceApply);
        evidence.samples.target_dom_mutations = Array.isArray(timeline.targetDomMutations) ? timeline.targetDomMutations : [];
        evidence.measurement.epoch_t_ms = timeline.measurementEpochMs ?? evidence.measurement.epoch_t_ms;
        evidence.measurement.measured_queue_t_ms = timeline.measuredQueueMs ?? evidence.measurement.measured_queue_t_ms;
      }
    } catch (_) {
      // Preserve the primary failure classification while still writing schema.
    }
    finalizeEvidence(evidence);
    writeEvidence(outputFile, evidence);
    throw error;
  } finally {
    await closeQuietly(context);
    await closeQuietly(browser);
  }
}

function baseEvidence(label, runManifest, target, initialError) {
  const identities = {
    run_id_digest: runManifest && runManifest.run_id_digest
      ? runManifest.run_id_digest : stableDigest(process.env.PERF_RUN_ID),
    image_tag_digest: runManifest && runManifest.image_tag_digest
      ? runManifest.image_tag_digest : stableDigest(runManifest && runManifest.image),
    image_identity_digest: null,
    project_digest: runManifest && runManifest.project_digest
      ? runManifest.project_digest : stableDigest(runManifest && runManifest.project),
    image_present: Boolean(runManifest && (runManifest.image_tag_digest || runManifest.image)),
    project_present: Boolean(runManifest && (runManifest.project_digest || runManifest.project)),
    profile: runManifest && runManifest.profile || null,
    high_card_enabled: runManifest && typeof runManifest.high_card_enabled === 'boolean'
      ? runManifest.high_card_enabled : null,
    fixture_fingerprint: null,
    config_fingerprint: null,
    manifest_schema: null,
  };
  return {
    schema: 'seedsync.performance-lab.browser-timeline.v1',
    label,
    pass: false,
    failure_classification: initialError ? initialError.kind : 'not-run',
    bootstrap: {mode: 'remembered-api-key', first_run_claim_clicked: false},
    identities,
    target: target || null,
    thresholds: {
      target_dom_p95_ms: {limit_ms: 200, observed_ms: null, pass: false},
      target_dom_max_ms: {limit_ms: 500, observed_ms: null, pass: false},
      max_progress_gap_ms: {limit_ms: MAX_PROGRESS_GAP_MS, observed_ms: null, pass: false},
      action_rendered_state_p95_ms: {
        limit_ms_by_action: ACTION_RENDER_LIMITS_MS, observed: {}, pass: false,
      },
      action_http_response_reported: {required: true, pass: false},
      browser_errors: {limit: 0, observed: 0, pass: false},
    },
    samples: {event_source_receive: [], event_source_apply: [], target_dom_mutations: [], target_dom_cadence: null, progress: []},
    measurement: {
      required: true, epoch_t_ms: null, measured_queue_t_ms: null,
      post_readiness: false, sample_epoch_source: 'post-measured-queue',
    },
    actions: [],
    cycles: [],
    max_progress_gap_ms: null,
    statistics: {action_http_response: {}, action_rendered_state: {}, progress_gap: null},
    expected_request_aborts: [],
    preconditions: [],
    readiness: {
      required: true, attempted: false, steps: [],
      initial_precondition: null, final_precondition: null,
      normalized: false, pass: false, failure_classification: null,
    },
    cleanup: {
      required: false, attempted: false, steps: [], errors: [],
      restored_state: null, residual_state: null, residual_local_absent: false, pass: false,
    },
    errors: initialError ? [initialError] : [],
  };
}

function discoverTarget(manifest) {
  if (!manifest || manifest.synthetic_only !== true || !Array.isArray(manifest.path_pairs)) {
    throw new Error('fixture manifest is not a synthetic path-pair manifest');
  }
  const pair = manifest.path_pairs.find(entry => entry && entry.role === 'ordinary-active');
  if (!pair || !Array.isArray(pair.remote_only_targets) || !pair.remote_only_targets.length) {
    throw new Error('ordinary-active pair has no remote_only_targets');
  }
  const remoteTarget = pair.remote_only_targets[0];
  const relativePath = String(remoteTarget.relative_path || '');
  const parts = relativePath.split('/').filter(Boolean);
  if (parts.length < 2) throw new Error('remote_only target path is not nested');
  const name = parts[parts.length - 1];
  const target = {
    pair_name: pair.name || `Performance Pair ${String(pair.id || '').slice(-2)}`,
    pair_role: pair.role,
    pair_directory: pair.directory || parts[0],
    relative_path: relativePath,
    path_segments: parts[0] === pair.directory ? parts.slice(1) : parts,
    name,
    remote_only: true,
    file_id_present: false,
  };
  Object.defineProperty(target, 'pair_id', {value: pair.id || null, enumerable: false});
  return target;
}

function installPageDiagnostics(page, evidence) {
  page.on('pageerror', error => boundedPush(evidence.errors, errorRecord('pageerror', error), MAX_ERRORS));
  page.on('console', message => {
    if (message.type() === 'error') boundedPush(evidence.errors, {kind: 'console-error', message: safeText(message.text())}, MAX_ERRORS);
  });
  page.on('requestfailed', request => {
    try {
      const url = new URL(request.url());
      const failureText = request.failure()?.errorText || '';
      const navigationAbort = typeof request.isNavigationRequest === 'function' && request.isNavigationRequest();
      const eventSourceAbort = typeof request.resourceType === 'function' && request.resourceType() === 'eventsource';
      if (failureText === 'net::ERR_ABORTED' && (navigationAbort || eventSourceAbort)) {
        boundedPush(evidence.expected_request_aborts, {
          pathname: normalizeAppPath(url.pathname) || '/server/<route>',
          resource_type: eventSourceAbort ? 'eventsource' : 'navigation',
          reason: eventSourceAbort ? 'stream-replacement' : 'page-navigation',
        }, MAX_ERRORS);
        return;
      }
      boundedPush(evidence.errors, {
         kind: 'request-failed', method: request.method(), pathname: normalizeAppPath(url.pathname) || '/server/<route>',
        message: safeText(failureText || 'request failed'),
      }, MAX_ERRORS);
    } catch (_) {
      boundedPush(evidence.errors, {kind: 'request-failed', message: 'request failed'}, MAX_ERRORS);
    }
  });
  page.addInitScript(() => {
    const limit = 256;
    const eventSourceReceive = [];
    const eventSourceApply = [];
    const eventSourcePaths = [];
    const targetDomMutations = [];
    const started = performance.now();
    let measurementEpochMs = null;
    let measuredQueueMs = null;
    const add = (array, value) => { if (array.length < limit) array.push(value); };
    const relative = () => Number((performance.now() - started).toFixed(3));
    const paths = new WeakMap();
    let seenEvents = new WeakSet();
    let eventRecords = new WeakMap();
    const eventPath = source => {
      try { return new URL(paths.get(source) || '', location.href).pathname; } catch (_) { return null; }
    };
    const recordEvent = (source, event) => {
      if (!event) return null;
      if (typeof event === 'object' && seenEvents.has(event)) return eventRecords.get(event) || null;
      if (event && typeof event === 'object') seenEvents.add(event);
      const receivedAt = relative();
      const item = {t_ms: receivedAt, event_type: String(event.type || 'message'), pathname: eventPath(source)};
      add(eventSourceReceive, item);
      if (event && typeof event === 'object') eventRecords.set(event, item);
      return item;
    };
    const recordApply = (source, event, received) => {
      add(eventSourceApply, {
        t_ms: relative(), event_type: String(event?.type || 'message'), pathname: eventPath(source),
        receive_t_ms: received?.t_ms ?? null,
      });
    };
    const OriginalEventSource = window.EventSource;
    if (OriginalEventSource) {
      const proto = OriginalEventSource.prototype;
      const originalDispatch = proto.dispatchEvent;
      if (typeof originalDispatch === 'function') {
        proto.dispatchEvent = function dispatchEvent(event) {
          recordEvent(this, event);
          return originalDispatch.call(this, event);
        };
      }
      const originalAdd = proto.addEventListener;
      if (typeof originalAdd === 'function') {
        proto.addEventListener = function addEventListener(type, listener, options) {
          const wrapped = typeof listener === 'function' ? function wrappedEventListener(event) {
            const received = recordEvent(this, event || {type});
            try { return listener.call(this, event); }
            finally { recordApply(this, event || {type}, received); }
          } : listener;
          return originalAdd.call(this, type, wrapped, options);
        };
      }
      function WrappedEventSource(url, options) {
        const source = new OriginalEventSource(url, options);
        try {
          const pathname = new URL(String(url), location.href).pathname;
          paths.set(source, pathname);
          add(eventSourcePaths, {pathname});
        } catch (_) { paths.set(source, ''); }
        return source;
      }
      WrappedEventSource.prototype = proto;
      try { Object.setPrototypeOf(WrappedEventSource, OriginalEventSource); } catch (_) { /* old browser */ }
      window.EventSource = WrappedEventSource;
    }
    const scopedModelEventTypes = ['model-init', 'model-added', 'model-updated', 'model-removed',
      'model-page', 'model-invalidate', 'model-patch', 'model-reset'];
    const latestScopedApply = (applies, atMs, scopedPath) => (applies || [])
      .filter(item => item && Number(item.t_ms) <= Number(atMs)
        && (measurementEpochMs == null || Number(item.t_ms) >= measurementEpochMs)
        && String(item.pathname || '') === String(scopedPath || '')
        && scopedModelEventTypes.includes(String(item.event_type)))
      .sort((a, b) => Number(a.t_ms) - Number(b.t_ms)).slice(-1)[0] || null;
    const pathForPair = pairId => {
      const expected = String(pairId || '');
      if (!expected) return null;
      return eventSourcePaths.map(item => item.pathname).find(pathname => {
        if (!String(pathname || '').startsWith('/server/model/v1/')) return false;
        return String(pathname).split('/').filter(Boolean).some(segment => {
          try { return decodeURIComponent(segment) === expected; } catch (_) { return segment === expected; }
        });
      }) || null;
    };
    window.__seedSyncPerfTimeline = {
      eventSourceReceive,
      eventSourceApply,
      eventSourcePaths,
      targetDomMutations,
      findScopedModelPath: pathForPair,
      beginMeasurement() {
        eventSourceReceive.length = 0;
        eventSourceApply.length = 0;
        targetDomMutations.length = 0;
        seenEvents = new WeakSet();
        eventRecords = new WeakMap();
        measurementEpochMs = relative();
        measuredQueueMs = null;
        return {epoch_t_ms: measurementEpochMs};
      },
      markMeasuredQueue() {
        measuredQueueMs = relative();
        return measuredQueueMs;
      },
      attachTarget(targetId, targetName, scopedPath) {
        const root = document.querySelector('#file-list') || document.body;
        const find = () => Array.from(document.querySelectorAll('#file-list .file')).find(row => {
          const id = row.getAttribute('data-file-id');
          const title = row.querySelector('.name .title')?.textContent?.trim();
          return targetId ? id === targetId : title === targetName;
        });
        let lastSignature = null;
        const capture = () => {
          const row = find();
          if (!row) return;
          const statusText = row.querySelector('.status .text')?.textContent?.trim();
          const icon = row.querySelector('.status img[id]')?.id || null;
          const status = statusText ? statusText.toLowerCase() : (icon === 'default-remote' ? 'default-remote' : icon);
          const progressValue = Number(row.querySelector('.progress-bar')?.getAttribute('aria-valuenow'));
          const signature = `${status || ''}|${Number.isFinite(progressValue) ? progressValue : ''}`;
          if (signature === lastSignature) return;
          lastSignature = signature;
          const tMs = relative();
          const apply = latestScopedApply(eventSourceApply, tMs, scopedPath);
          add(targetDomMutations, {
            t_ms: tMs, status: status || null,
            progress: Number.isFinite(progressValue) ? progressValue : null,
            receive_t_ms: apply?.receive_t_ms ?? null,
            apply_t_ms: apply?.t_ms ?? null,
            receive_to_apply_ms: apply && apply.receive_t_ms != null ? Number((apply.t_ms - apply.receive_t_ms).toFixed(3)) : null,
            apply_to_dom_ms: apply ? Number((tMs - apply.t_ms).toFixed(3)) : null,
            receive_to_dom_ms: apply && apply.receive_t_ms != null ? Number((tMs - apply.receive_t_ms).toFixed(3)) : null,
          });
        };
        const observer = new MutationObserver(capture);
        observer.observe(root, {subtree: true, childList: true, attributes: true, characterData: true,
          attributeFilter: ['aria-valuenow', 'class', 'style']});
        window.__seedSyncPerfTimeline.snapshot = () => ({
          eventSourceReceive: eventSourceReceive.slice(), eventSourceApply: eventSourceApply.slice(),
          eventSourcePaths: eventSourcePaths.slice(), targetDomMutations: targetDomMutations.slice(),
          measurementEpochMs, measuredQueueMs,
        });
      },
      snapshot() { return {eventSourceReceive: eventSourceReceive.slice(), eventSourceApply: eventSourceApply.slice(),
        eventSourcePaths: eventSourcePaths.slice(), targetDomMutations: targetDomMutations.slice(),
        measurementEpochMs, measuredQueueMs}; },
    };
  });
}

async function rememberBrowser(page, baseUrl, apiToken, timeoutMs, evidence) {
  const body = (await page.locator('body').innerText().catch(() => '')).toLowerCase();
  const rememberButton = page.getByRole('button', {name: 'Remember browser', exact: true});
  const secretInput = page.locator('#browser-secret');
  if (!body.includes('remembered browser') || await rememberButton.count() !== 1 || await secretInput.count() !== 1) {
    if (body.includes('claim session') || body.includes('first-run browser access')) {
      throw new Error('first-run claim flow is not allowed for performance browser evidence');
    }
    throw new Error('remembered-browser bootstrap form was not available');
  }
  await secretInput.fill(apiToken);
  const responsePromise = page.waitForResponse(response => {
      try { return new URL(response.url()).pathname === '/server/browser/v1/remember'; } catch (_) { return false; }
  }, {timeout: timeoutMs}).catch(error => ({status: () => 0, error}));
  await rememberButton.click();
  const response = await responsePromise;
  const status = typeof response.status === 'function' ? response.status() : 0;
  evidence.bootstrap.http_status = status;
  evidence.bootstrap.response_reported = status > 0;
  if (status < 200 || status >= 300) throw new Error(`remember browser HTTP ${status}`);
  await page.waitForURL(url => new URL(url).pathname !== '/bootstrap', {timeout: timeoutMs});
  evidence.bootstrap.final_pathname = new URL(page.url()).pathname;
  evidence.bootstrap.base_url = new URL(baseUrl).origin;
}

async function traverseTargetRows(page, target, timeoutMs) {
  // Every path segment is located through visible rows.  Parent directories
  // are selected before the final target; no private fixture names are used.
  for (const segment of target.path_segments) {
    const row = await visibleRowByName(page, segment, timeoutMs);
    await row.scrollIntoViewIfNeeded();
    if (segment !== target.name) {
      await row.click();
      await page.waitForTimeout(25);
    }
  }
}

async function selectPairFromSidebar(page, pairName, timeoutMs) {
  const modeHandle = await page.waitForFunction(({name}) => {
    const fileList = document.querySelector('#file-list');
    if (fileList) {
      const style = window.getComputedStyle(fileList);
      if (style.display !== 'none' && style.visibility !== 'hidden' && fileList.getBoundingClientRect().width > 0) return 'direct';
    }
    const link = Array.from(document.querySelectorAll('#sidebar a.button')).find(item => item.textContent?.includes(name));
    return link ? 'sidebar' : false;
  }, {name: pairName}, {timeout: timeoutMs});
  const mode = await modeHandle.jsonValue();
  await modeHandle.dispose();
  if (mode === 'direct') return;
  const link = page.locator('#sidebar a.button').filter({hasText: pairName}).first();
  await link.scrollIntoViewIfNeeded();
  await link.click();
  await page.locator('#file-list').waitFor({state: 'visible', timeout: timeoutMs});
}

async function waitForDashboardShell(page, timeoutMs) {
  await page.waitForFunction(() => Boolean(
    document.querySelector('#sidebar')
      || document.querySelector('#dashboard')
      || document.querySelector('main')
      || document.querySelector('#file-list')
  ), undefined, {timeout: timeoutMs});
}

async function waitForScopedModelPath(page, pairId, timeoutMs) {
  const handle = await page.waitForFunction(({id}) => window.__seedSyncPerfTimeline?.findScopedModelPath?.(id) || false,
    {id: pairId}, {timeout: timeoutMs});
  const pathname = await handle.jsonValue();
  await handle.dispose();
  return pathname;
}

async function visibleRowByName(page, name, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  const filter = page.locator('#filter-search input[type="search"]');
  while (Date.now() < deadline) {
    const rows = page.locator('#file-list .file');
    const count = await rows.count();
    for (let index = 0; index < count; index += 1) {
      const row = rows.nth(index);
      const title = (await row.locator('.name .title').innerText().catch(() => '')).trim();
      if (title === name) return row;
    }
    if (await filter.count()) {
      await filter.fill(name);
      await page.waitForTimeout(25);
      const filteredRows = page.locator('#file-list .file');
      const filteredCount = await filteredRows.count();
      for (let index = 0; index < filteredCount; index += 1) {
        const row = filteredRows.nth(index);
        const title = (await row.locator('.name .title').innerText().catch(() => '')).trim();
        if (title === name) {
          await filter.fill('');
          await page.waitForTimeout(25);
          const candidates = page.locator('#file-list .file').filter({hasText: name});
          const candidateCount = await candidates.count();
          for (let candidateIndex = 0; candidateIndex < candidateCount; candidateIndex += 1) {
            const candidate = candidates.nth(candidateIndex);
            const candidateTitle = (await candidate.locator('.name .title').innerText().catch(() => '')).trim();
            if (candidateTitle === name) return candidate;
          }
        }
      }
      await filter.fill('');
    }
    await page.mouse.wheel(0, 800);
    await page.waitForTimeout(50);
  }
  throw new Error(`visible target row not found for manifest segment ${safeText(name)}`);
}

async function readTargetId(page, name, timeoutMs) {
  const row = await visibleRowByName(page, name, timeoutMs);
  return row.getAttribute('data-file-id');
}

function cssAttributeValue(value) {
  return String(value)
    .replace(/\\/g, '\\\\')
    .replace(/"/g, '\\"')
    .replace(/\r/g, '\\r')
    .replace(/\n/g, '\\n');
}

function targetRowLocator(page, targetId, targetName) {
  if (targetId) {
    return page.locator(`#file-list .file[data-file-id="${cssAttributeValue(targetId)}"]`);
  }
  return page.getByText(targetName, {exact: true})
    .locator('xpath=ancestor::*[contains(concat(" ", normalize-space(@class), " "), " file ")][1]');
}

async function rowIdentity(row) {
  return row.evaluate(node => ({
    file_id: node.getAttribute('data-file-id'),
    name: node.querySelector('.name .title')?.textContent?.trim() || null,
  }));
}

async function verifyTargetIdentity(row, targetId, targetName) {
  const identity = await rowIdentity(row);
  const matched = targetIdentityMatches(identity, targetId, targetName);
  if (!matched) {
    const error = targetIdentityMismatchError(identity, targetId, targetName);
    error.precondition = {
      identity,
      identity_mode: targetIdentityMode(targetId),
      identity_match: false,
    };
    throw error;
  }
  return {
    identity,
    identity_mode: targetIdentityMode(targetId),
    identity_match: true,
  };
}

async function reacquireTargetRow(page, targetId, targetName, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  const rows = targetRowLocator(page, targetId, targetName);
  while (Date.now() < deadline) {
    const count = await rows.count();
    for (let index = 0; index < count; index += 1) {
      const row = rows.nth(index);
      try {
        await verifyTargetIdentity(row, targetId, targetName);
        return row;
      } catch (_) {
        // A virtualized/sorted list can remount between count and identity
        // read. Continue looking for the stable identity until the deadline.
      }
    }
    await page.mouse.wheel(0, 600);
    await page.waitForTimeout(50);
  }
  throw new Error('target row was not reacquired after DOM remount');
}

async function readTargetState(page, targetId, targetName, timeoutMs) {
  const row = await reacquireTargetRow(page, targetId, targetName, timeoutMs);
  return row.evaluate(node => {
    const text = node.querySelector('.status .text')?.textContent?.trim().toLowerCase();
    const icon = node.querySelector('.status img[id]')?.id;
    const progress = Number(node.querySelector('.progress-bar')?.getAttribute('aria-valuenow'));
    const controls = {};
    for (const action of ['Queue', 'Stop', 'Delete Local']) {
      const button = Array.from(node.querySelectorAll('.actions .button')).find(item =>
        item.textContent?.trim().includes(action));
      const ariaDisabled = button?.getAttribute('aria-disabled');
      controls[action] = {
        present: Boolean(button),
        enabled: Boolean(button && !button.disabled && ariaDisabled !== 'true'),
        disabled: Boolean(!button || button.disabled || ariaDisabled === 'true'),
      };
    }
    return {status: text || (icon === 'default-remote' ? 'default-remote' : icon) || null,
      progress: Number.isFinite(progress) ? progress : null,
      controls,
      identity: {
        file_id: node.getAttribute('data-file-id'),
        name: node.querySelector('.name .title')?.textContent?.trim() || null,
      }};
  });
}

async function readActionPrecondition(page, targetId, targetName, timeoutMs) {
  const row = await selectTarget(page, targetId, targetName, timeoutMs);
  await row.locator('.actions').waitFor({state: 'visible', timeout: timeoutMs});
  return captureActionPrecondition(row, targetId, targetName);
}

async function captureActionPrecondition(row, targetId, targetName) {
  const precondition = await row.evaluate(node => {
    const statusText = node.querySelector('.status .text')?.textContent?.trim().toLowerCase() || null;
    const statusIcon = node.querySelector('.status img[id]')?.id || null;
    const progressAttribute = node.querySelector('.progress-bar')?.getAttribute('aria-valuenow');
    const progress = Number(progressAttribute);
    const status = statusText || (statusIcon === 'default-remote' ? 'default-remote' : statusIcon);
    const controls = {};
    for (const action of ['Queue', 'Stop', 'Delete Local']) {
      const button = Array.from(node.querySelectorAll('.actions .button')).find(item =>
        item.textContent?.trim().includes(action));
      const ariaDisabled = button?.getAttribute('aria-disabled');
      controls[action] = {
        present: Boolean(button),
        enabled: Boolean(button && !button.disabled && ariaDisabled !== 'true'),
        disabled: Boolean(!button || button.disabled || ariaDisabled === 'true'),
      };
    }
    return {
      dom: {
        row_present: true,
        selected: node.classList.contains('selected'),
        status_text: statusText,
        status_icon: statusIcon,
        progress_attribute: progressAttribute,
      },
      status: status || null,
      progress: Number.isFinite(progress) ? progress : null,
      controls,
      identity: {
        file_id: node.getAttribute('data-file-id'),
        name: node.querySelector('.name .title')?.textContent?.trim() || null,
      },
    };
  });
  precondition.identity_mode = targetIdentityMode(targetId);
  precondition.identity_match = targetIdentityMatches(precondition.identity, targetId, targetName);
  return precondition;
}

function preconditionControl(precondition, name) {
  return precondition?.controls?.[name] || {present: false, enabled: false, disabled: true};
}

async function attachTargetObserver(page, targetId, targetName, scopedPath) {
  await page.evaluate(({targetId: id, targetName: name, scopedPath: pathName}) => {
    window.__seedSyncPerfTimeline?.attachTarget?.(id, name, pathName);
  }, {targetId, targetName, scopedPath});
}

async function selectTarget(page, targetId, targetName, timeoutMs) {
  const row = await reacquireTargetRow(page, targetId, targetName, timeoutMs);
  const selected = await row.evaluate(node => node.classList.contains('selected')).catch(() => false);
  if (!selected) {
    await verifyTargetIdentity(row, targetId, targetName);
    await row.click();
  }
  return reacquireTargetRow(page, targetId, targetName, timeoutMs);
}

function responseMatcher(action) {
  return response => {
    try {
      const url = new URL(response.url());
      return url.pathname.startsWith(`/server/command/${action}/`);
    } catch (_) { return false; }
  };
}

async function waitForState(page, targetId, targetName, allowed, timeoutMs) {
  await page.waitForFunction(({id, name, states}) => {
    const rows = Array.from(document.querySelectorAll('#file-list .file'));
    const row = rows.find(item => id
      ? item.getAttribute('data-file-id') === id
      : item.querySelector('.name .title')?.textContent?.trim() === name);
    if (!row) return false;
    const text = row.querySelector('.status .text')?.textContent?.trim().toLowerCase();
    const icon = row.querySelector('.status img[id]')?.id;
    const status = text || (icon === 'default-remote' ? 'default-remote' : icon);
    const buttons = Array.from(row.querySelectorAll('.actions .button'));
    const enabled = action => {
      const button = buttons.find(item => item.textContent?.trim().includes(action));
      return Boolean(button && !button.disabled && button.getAttribute('aria-disabled') !== 'true');
    };
    const localAbsent = enabled('Queue') && !enabled('Delete Local');
    return states.includes(status) || (states.includes('local-absent') && localAbsent);
  }, {id: targetId, name: targetName, states: allowed}, {timeout: timeoutMs});
  const row = await reacquireTargetRow(page, targetId, targetName, timeoutMs);
  return row.evaluate(node => {
    const text = node.querySelector('.status .text')?.textContent?.trim().toLowerCase();
    const icon = node.querySelector('.status img[id]')?.id;
    return text || (icon === 'default-remote' ? 'default-remote' : icon) || null;
  });
}

async function waitForActiveMaterialization(page, targetId, targetName, timeoutMs) {
  await page.waitForFunction(({id, name}) => {
    const row = Array.from(document.querySelectorAll('#file-list .file')).find(item => id
      ? item.getAttribute('data-file-id') === id
      : item.querySelector('.name .title')?.textContent?.trim() === name);
    if (!row) return false;
    const status = row.querySelector('.status .text')?.textContent?.trim().toLowerCase()
      || row.querySelector('.status img[id]')?.id;
    const progress = Number(row.querySelector('.progress-bar')?.getAttribute('aria-valuenow'));
    // During an active transfer the current dashboard can render the source
    // presence label (for example "Local only") while the progress bar is
    // advancing. The bounded in-flight progress value is the stable signal;
    // requiring presentation copy here can miss the entire stop window.
    return Number.isFinite(progress) && progress > 0 && progress < 100
      && status !== 'stopped' && status !== 'downloaded';
  }, {id: targetId, name: targetName}, {timeout: timeoutMs});
}

async function waitForEnabledAction(page, targetId, targetName, actionName, timeoutMs) {
  const startedAt = Date.now();
  await page.waitForFunction(({id, name, action}) => {
    const row = Array.from(document.querySelectorAll('#file-list .file')).find(item => id
      ? item.getAttribute('data-file-id') === id
      : item.querySelector('.name .title')?.textContent?.trim() === name);
    const button = row && Array.from(row.querySelectorAll('.actions .button')).find(item =>
      item.textContent?.trim().includes(action));
    return Boolean(button && !button.disabled && button.getAttribute('aria-disabled') !== 'true');
  }, {id: targetId, name: targetName, action: actionName}, {timeout: timeoutMs});
  return Date.now() - startedAt;
}

async function clickAction(page, target, targetId, name, endpointAction, allowedStates, timeoutMs, confirm, recordName = null) {
  const deadline = Date.now() + timeoutMs;
  let row = null;
  let button = null;
  let preAction = null;
  let control = null;
  while (Date.now() < deadline) {
    row = await selectTarget(page, targetId, target.name, timeoutMs);
    button = row.locator('.actions .button').filter({hasText: name}).first();
    await button.waitFor({state: 'visible', timeout: timeoutMs});
    preAction = await captureActionPrecondition(row, targetId, target.name);
    control = preconditionControl(preAction, name);
    // Angular can remount the row between the enabled-control wait and the
    // Playwright click. Reacquire and recheck within the same bounded action
    // window so a transient disabled snapshot is not misreported as a failed
    // measured action.
    if (preAction.identity_match && control.enabled && !(await button.isDisabled().catch(() => true))) break;
    await page.waitForTimeout(25);
  }
  if (!preAction?.identity_match || !control?.enabled || !button || await button.isDisabled().catch(() => true)) {
    const error = new Error(`${name} action is disabled in precondition state ${preAction?.status || 'unknown'}`);
    error.failure_classification = 'invalid-precondition';
    error.action = name;
    error.precondition = preAction;
    throw error;
  }
  let confirmationOpenedAt = null;
  if (confirm) {
    await button.click();
    confirmationOpenedAt = await page.evaluate(() => performance.now());
    const dialog = page.locator('.modal-overlay');
    await dialog.waitFor({state: 'visible', timeout: timeoutMs});
    const confirmButton = dialog.getByRole('button', {name: 'Delete', exact: true});
    await confirmButton.waitFor({state: 'visible', timeout: timeoutMs});
    const confirmationRow = await reacquireTargetRow(page, targetId, target.name, timeoutMs);
    const confirmationIdentity = await verifyTargetIdentity(confirmationRow, targetId, target.name);
    const clickAt = await page.evaluate(() => performance.now());
    const responsePromise = page.waitForResponse(responseMatcher(endpointAction), {timeout: timeoutMs});
    await confirmButton.click();
    const response = await responsePromise;
    const responseAt = await page.evaluate(() => performance.now());
    const renderedState = await waitForState(page, targetId, target.name, allowedStates, timeoutMs);
    const renderedAt = await page.evaluate(() => performance.now());
    return {
      name: recordName || (name === 'Delete Local' ? 'delete_local' : name.toLowerCase()), measured: true,
      pre_action: preAction,
      endpoint_action: endpointAction, http_status: response.status(), response_reported: true,
      confirmation_identity_match: confirmationIdentity.identity_match,
      rendered_state: renderedState, click_to_http_response_ms: Number((responseAt - clickAt).toFixed(3)),
      click_to_rendered_state_ms: Number((renderedAt - clickAt).toFixed(3)),
      confirmation_open_to_click_ms: Number((clickAt - confirmationOpenedAt).toFixed(3)),
      confirmation_click_to_http_response_ms: Number((responseAt - clickAt).toFixed(3)),
      confirmation_click_to_rendered_state_ms: Number((renderedAt - clickAt).toFixed(3)),
    };
  }
  const clickAt = await page.evaluate(() => performance.now());
  const clickIdentity = await verifyTargetIdentity(row, targetId, target.name);
  const responsePromise = page.waitForResponse(responseMatcher(endpointAction), {timeout: timeoutMs});
  await button.click();
  const response = await responsePromise;
  const responseAt = await page.evaluate(() => performance.now());
  const renderedState = await waitForState(page, targetId, target.name, allowedStates, timeoutMs);
  const renderedAt = await page.evaluate(() => performance.now());
  return {
    name: recordName || (name === 'Queue' ? 'queue' : name.toLowerCase()), measured: true,
    pre_action: preAction,
    click_identity_match: clickIdentity.identity_match,
    endpoint_action: endpointAction, http_status: response.status(), response_reported: true,
    rendered_state: renderedState, click_to_http_response_ms: Number((responseAt - clickAt).toFixed(3)),
    click_to_rendered_state_ms: Number((renderedAt - clickAt).toFixed(3)),
  };
}

function invalidPrecondition(message, precondition = null, action = null) {
  const error = new Error(message);
  error.failure_classification = 'invalid-precondition';
  if (action) error.action = action;
  if (precondition) error.precondition = precondition;
  return error;
}

function readinessIsQueueable(precondition) {
  if (!precondition || !QUEUEABLE_STATES.has(String(precondition.status || ''))) return false;
  // Queue must be enabled and Delete Local must be disabled. The latter is
  // the DOM-visible proof that no local copy remains; Queue alone is also
  // enabled for local-only/default states with retained local content.
  return precondition.controls?.Queue?.enabled === true
    && precondition.controls?.['Delete Local']?.enabled !== true;
}

async function waitForNormalizationPrecondition(page, targetId, targetName, timeoutMs, initial = null) {
  const startedAt = Date.now();
  let precondition = initial || await readActionPrecondition(page, targetId, targetName, timeoutMs);
  while (Date.now() - startedAt < timeoutMs) {
    if (readinessIsQueueable(precondition)
        || precondition.controls?.Stop?.enabled === true
        || precondition.controls?.['Delete Local']?.enabled === true) {
      return precondition;
    }
    await page.waitForTimeout(100);
    precondition = await readActionPrecondition(page, targetId, targetName, timeoutMs);
  }
  throw invalidPrecondition(
    `synthetic browser target controls did not become ready in state ${precondition.status || 'unknown'}`,
    precondition,
  );
}

async function normalizeTarget(page, target, targetId, timeoutMs, evidence) {
  const readiness = evidence.readiness;
  readiness.attempted = true;
  let precondition = null;
  try {
    precondition = await waitForNormalizationPrecondition(
      page, targetId, target.name, timeoutMs,
    );
    readiness.initial_precondition = precondition;
    for (let index = 0; index <= MAX_READINESS_STEPS; index += 1) {
      if (readinessIsQueueable(precondition)) {
        readiness.final_precondition = precondition;
        readiness.normalized = true;
        readiness.pass = true;
        return;
      }
      if (index === MAX_READINESS_STEPS) {
        throw invalidPrecondition('synthetic browser target did not reach remote-only queueable readiness',
          precondition);
      }

      let actionName = null;
      let endpointAction = null;
      let allowedStates = null;
      let confirm = false;
      if (precondition.controls?.Stop?.enabled === true) {
        actionName = 'Stop';
        endpointAction = 'stop';
        allowedStates = ['stopped'];
      } else if (precondition.controls?.['Delete Local']?.enabled === true) {
        actionName = 'Delete Local';
        endpointAction = 'delete_local';
        allowedStates = [...QUEUEABLE_STATES, 'default-local', 'local only', 'downloaded',
          'extracting', 'extracted', 'validating', 'validated', 'move_failed', 'move-succeeded'];
        confirm = true;
      } else {
        throw invalidPrecondition(
          `synthetic browser target has no enabled normalization control in state ${precondition.status || 'unknown'}`,
          precondition,
        );
      }

      const result = await clickAction(
        page, target, targetId, actionName, endpointAction, allowedStates, timeoutMs, confirm,
        `normalize_${endpointAction}`,
      );
      result.measured = false;
      readiness.steps.push(result);
      precondition = await waitForNormalizationPrecondition(
        page, targetId, target.name, timeoutMs,
      );
    }
  } catch (error) {
    readiness.final_precondition = precondition || error.precondition || null;
    readiness.failure_classification = error.failure_classification || 'probe';
    readiness.pass = false;
    throw error;
  }
}

function recordCleanupError(evidence, record, step, error) {
  const item = {step, ...errorRecord(`cleanup-${step}`, error)};
  boundedPush(record.errors, item, MAX_ERRORS);
  boundedPush(evidence.errors, item, MAX_ERRORS);
}

async function cleanupTarget(page, target, targetId, timeoutMs, evidence, record) {
  record.attempted = true;
  let observedState = null;
  try { observedState = await readTargetState(page, targetId, target.name, timeoutMs); }
  catch (error) {
    record.observed_state_error = errorRecord('cleanup-observe', error);
    recordCleanupError(evidence, record, 'observe', error);
  }
  record.observed_state = observedState?.status || null;
  record.observed_identity = observedState?.identity || null;

  const stopRequired = !observedState || Boolean(
    ['queued', 'downloading', 'extracting'].includes(observedState.status)
    || (Number.isFinite(observedState.progress) && observedState.progress > 0 && observedState.progress < 100)
  );
  if (stopRequired) {
    const stopStep = {name: 'cleanup_stop', measured: false, attempted: true};
    try {
      const waitMs = await waitForEnabledAction(page, targetId, target.name, 'Stop', timeoutMs);
      const result = await clickAction(page, target, targetId, 'Stop', 'stop', ['stopped'], timeoutMs, false);
      result.name = 'cleanup_stop'; result.measured = false; result.control_enabled_wait_ms = waitMs;
      Object.assign(stopStep, result, {completed: true});
    } catch (error) {
      stopStep.completed = false;
      stopStep.error = errorRecord('cleanup-stop', error);
      recordCleanupError(evidence, record, 'stop', error);
    }
    record.steps.push(stopStep);
  } else {
    record.steps.push({name: 'cleanup_stop', measured: false, attempted: false,
      skipped: true, reason: `target state was ${observedState?.status || 'unknown'}`});
  }

  let deleteCompleted = readinessIsQueueable(observedState);
  if (!deleteCompleted) {
    const deleteStep = {name: 'cleanup_delete_local', measured: false, attempted: true};
    try {
      await waitForEnabledAction(page, targetId, target.name, 'Delete Local', timeoutMs);
      const result = await clickAction(
        page, target, targetId, 'Delete Local', 'delete_local', LOCAL_ABSENT_STATES, timeoutMs, true,
      );
      result.name = 'cleanup_delete_local'; result.measured = false;
      Object.assign(deleteStep, result, {completed: true});
      deleteCompleted = true;
    } catch (error) {
      deleteStep.completed = false;
      deleteStep.error = errorRecord('cleanup-delete-local', error);
      recordCleanupError(evidence, record, 'delete_local', error);
    }
    record.steps.push(deleteStep);
  } else {
    record.steps.push({name: 'cleanup_delete_local', measured: false, attempted: false,
      skipped: true, reason: 'target was already deleted'});
  }

  try {
    const residual = await readTargetState(page, targetId, target.name, timeoutMs);
    record.residual_state = residual?.status || null;
    record.residual_identity = residual?.identity || null;
    record.residual_local_absent = readinessIsQueueable(residual);
  }
  catch (error) {
    record.residual_state = null;
    record.residual_local_absent = false;
    recordCleanupError(evidence, record, 'residual-state', error);
  }
  if (deleteCompleted && record.residual_local_absent) {
    record.restored_state = record.residual_state;
  } else if (!record.errors.length && !record.residual_local_absent) {
    recordCleanupError(evidence, record, 'restore', new Error('target was not restored to a local-absent state'));
  }
  record.pass = cleanupPass(record);
}

async function exerciseActions(page, target, targetId, timeoutMs, evidence, scopedPath) {
  const actions = evidence.actions;
  const cleanupRecord = {name: 'cleanup', measured: false, required: false, attempted: false,
    steps: [], errors: [], observed_state: null, restored_state: null, residual_state: null,
    residual_local_absent: false, pass: false};
  try {
    // Set the obligation before the first Queue click. If that click or its
    // response is interrupted after mutating server state, the finally block
    // still performs best-effort Stop then Delete Local recovery.
    cleanupRecord.required = true;
    evidence.cleanup.required = true;
    await normalizeTarget(page, target, targetId, timeoutMs, evidence);
    // Start target mutation sampling only after readiness normalization. The
    // normalization actions are evidence, but are not part of measurement.
    await attachTargetObserver(page, targetId, target.name, scopedPath);
    const measurementEpoch = await page.evaluate(() =>
      window.__seedSyncPerfTimeline?.beginMeasurement?.() || null);
    evidence.measurement.epoch_t_ms = measurementEpoch?.epoch_t_ms ?? null;
    evidence.measurement.post_readiness = true;
    const queueAction = await clickAction(
      page, target, targetId, 'Queue', 'queue', ['queued', 'downloading'], timeoutMs, false, 'queue',
    );
    actions.push(queueAction);
    evidence.measurement.measured_queue_t_ms = await page.evaluate(() =>
      window.__seedSyncPerfTimeline?.markMeasuredQueue?.() || null);
    await waitForActiveMaterialization(page, targetId, target.name, timeoutMs);
    const stopControlWaitMs = await waitForEnabledAction(page, targetId, target.name, 'Stop', timeoutMs);
    const stopAction = await clickAction(page, target, targetId, 'Stop', 'stop', ['stopped'], timeoutMs, false);
    stopAction.control_enabled_wait_ms = stopControlWaitMs;
    actions.push(stopAction);
    await waitForEnabledAction(page, targetId, target.name, 'Delete Local', timeoutMs);
    actions.push(await clickAction(
      page, target, targetId, 'Delete Local', 'delete_local', LOCAL_ABSENT_STATES, timeoutMs, true,
    ));
    actions.push(await clickAction(page, target, targetId, 'Queue', 'queue', ['queued', 'downloading'], timeoutMs, false, 'requeue'));
  } finally {
    if (cleanupRecord.required) {
      await cleanupTarget(page, target, targetId, timeoutMs, evidence, cleanupRecord);
      evidence.cleanup = cleanupRecord;
      actions.push(cleanupRecord);
    }
  }
}

function finalizeEvidence(evidence) {
  const measuredQueueTMs = Number(evidence.measurement?.measured_queue_t_ms);
  const allTargetMutations = Array.isArray(evidence.samples.target_dom_mutations)
    ? evidence.samples.target_dom_mutations : [];
  const targetMutations = measuredTargetMutations(allTargetMutations, measuredQueueTMs);
  evidence.samples.target_dom_mutations = targetMutations.slice(0, MAX_MUTATIONS);
  evidence.measurement.sample_count = evidence.samples.target_dom_mutations.length;
  const cadenceSummary = cadence(evidence.samples.target_dom_mutations);
  const progressSummary = progressGap(evidence.samples.target_dom_mutations);
  const receiveToDom = latencyStats(evidence.samples.target_dom_mutations, 'receive_to_dom_ms');
  evidence.samples.target_dom_cadence = {
    count: cadenceSummary.count, p95_ms: cadenceSummary.p95_ms, max_ms: cadenceSummary.max_ms,
  };
  evidence.samples.target_dom_latency = {
    count: receiveToDom.count, p95_ms: receiveToDom.p95_ms, max_ms: receiveToDom.max_ms,
  };
  evidence.samples.progress = evidence.samples.target_dom_mutations
    .filter(sample => Number.isFinite(Number(sample.progress)))
    .slice(0, MAX_MUTATIONS)
    .map(sample => ({t_ms: sample.t_ms, status: sample.status, progress: sample.progress}));
  evidence.statistics.action_http_response = actionStats(evidence.actions, 'click_to_http_response_ms');
  evidence.statistics.action_rendered_state = actionStats(evidence.actions, 'click_to_rendered_state_ms');
  evidence.statistics.progress_gap = progressSummary;
  evidence.max_progress_gap_ms = progressSummary.max_ms;
  evidence.cycles = evidence.actions;
  evidence.thresholds.target_dom_p95_ms.observed_ms = receiveToDom.p95_ms;
  evidence.thresholds.target_dom_p95_ms.pass = receiveToDom.p95_ms != null && receiveToDom.p95_ms <= 200;
  evidence.thresholds.target_dom_max_ms.observed_ms = receiveToDom.max_ms;
  evidence.thresholds.target_dom_max_ms.pass = receiveToDom.max_ms != null && receiveToDom.max_ms <= 500;
  evidence.thresholds.max_progress_gap_ms.observed_ms = progressSummary.max_ms;
  evidence.thresholds.max_progress_gap_ms.pass = progressSummary.sample_count <= 1
    || (progressSummary.max_ms != null && progressSummary.max_ms <= MAX_PROGRESS_GAP_MS);
  const requiredActions = ['queue', 'stop', 'delete_local'];
  const rendered = evidence.statistics.action_rendered_state;
  const requeue = rendered.requeue || null;
  evidence.thresholds.action_rendered_state_p95_ms.observed = {
    queue: rendered.queue || null, stop: rendered.stop || null, delete_local: rendered.delete_local || null,
    requeue: requeue,
  };
  evidence.thresholds.action_rendered_state_p95_ms.pass = requiredActions.every(action => {
    const stat = rendered[action];
    return stat && stat.p95_ms != null && stat.p95_ms <= ACTION_RENDER_LIMITS_MS[action];
  }) && Boolean(requeue) && requeue.p95_ms <= ACTION_RENDER_LIMITS_MS.requeue;
  const measured = evidence.actions.filter(action => action && action.measured !== false && !action.steps);
  evidence.thresholds.action_http_response_reported.pass = measured.length >= 4
    && measured.every(action => action.response_reported === true && Number(action.http_status) >= 200 && Number(action.http_status) < 300);
  evidence.thresholds.browser_errors.observed = evidence.errors.length;
  evidence.thresholds.browser_errors.pass = evidence.errors.length === 0;
  evidence.cleanup.pass = cleanupPass(evidence.cleanup);
  evidence.pass = evidence.thresholds.target_dom_p95_ms.pass
    && evidence.thresholds.target_dom_max_ms.pass
    && evidence.thresholds.max_progress_gap_ms.pass
    && evidence.thresholds.action_rendered_state_p95_ms.pass
    && evidence.thresholds.action_http_response_reported.pass
    && evidence.readiness.pass
    && evidence.cleanup.pass
    && evidence.thresholds.browser_errors.pass;
  if (evidence.pass) evidence.failure_classification = 'none';
}

function measuredTargetMutations(samples, measuredQueueTMs) {
  const values = Array.isArray(samples) ? samples : [];
  return Number.isFinite(Number(measuredQueueTMs))
    ? values.filter(sample => Number(sample?.t_ms) >= Number(measuredQueueTMs))
    : values;
}

function classifyFailure(error) {
  if (error && error.failure_classification) return error.failure_classification;
  const message = String(error && error.message || error || '').toLowerCase();
  if (message.includes('bootstrap')) return 'bootstrap';
  if (message.includes('manifest') || message.includes('target')) return 'fixture-target';
  if (message.includes('timeout')) return 'timeout';
  if (message.includes('playwright') || message.includes('chromium')) return 'browser-runtime';
  if (message.includes('threshold')) return 'threshold';
  return 'probe';
}

function writeEvidence(outputFile, evidence) {
  const sanitized = JSON.parse(JSON.stringify(evidence, (_key, value) => {
    if (typeof value === 'string') return safeText(value);
    return value;
  }));
  fs.writeFileSync(outputFile, `${JSON.stringify(sanitized, null, 2)}\n`, {encoding: 'utf8', mode: 0o600});
  try { fs.chmodSync(outputFile, 0o600); } catch (_) { /* best effort on non-POSIX filesystems */ }
}

async function closeQuietly(resource) {
  if (!resource || typeof resource.close !== 'function') return;
  try { await Promise.race([resource.close(), new Promise(resolve => setTimeout(resolve, 5_000))]); } catch (_) { /* preserve probe result */ }
}
