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
const MIN_ACTIVE_PROGRESS_SAMPLES = 21;
const MIN_PROGRESS_GAPS = MIN_ACTIVE_PROGRESS_SAMPLES - 1;
const PROGRESS_GAP_P50_LIMIT_MS = 150;
const PROGRESS_GAP_P95_LIMIT_MS = 200;
const MAX_PROGRESS_GAP_MS = 1_000;
const MAIN_THREAD_HEARTBEAT_INTERVAL_MS = 100;
const MIN_MAIN_THREAD_HEARTBEATS = 5;
const MAIN_THREAD_DRIFT_LIMIT_MS = 250;
const MAX_MAIN_THREAD_HEARTBEATS = 256;
const TERMINAL_PROGRESS_STATUSES = new Set(['stopped', 'downloaded', 'complete', 'completed']);
const RECONCILIATION_POLL_INTERVAL_MS = 100;
const MAX_RECONCILIATION_OBSERVATIONS = 64;
const ACTION_RENDER_LIMITS_MS = Object.freeze({
  queue: 250,
  stop: 250,
  delete_local: 1_200,
  requeue: 250,
});
const QUEUEABLE_STATES = new Set(['default', 'default-remote', 'stopped', 'deleted', 'corrupt']);
const LOCAL_ABSENT_STATES = ['deleted', 'default-remote', 'local-absent'];
const FRESH_REMOTE_ONLY_STATE = 'default-remote';
const MAX_READINESS_STEPS = 4;

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
  return progressGapForField(samples, 'progress');
}

function progressGapForField(samples, field) {
  const gaps = [];
  let previousActive = null;
  let activeSampleCount = 0;
  let forwardProgressSampleCount = 0;
  let regressionCount = 0;
  for (const sample of (Array.isArray(samples) ? samples : [])
    .slice().sort((a, b) => Number(a?.t_ms) - Number(b?.t_ms))) {
    const rawValue = sample && sample[field];
    const progress = rawValue == null ? Number.NaN : Number(rawValue);
    const status = String(sample && sample.status || '').toLowerCase();
    const terminal = TERMINAL_PROGRESS_STATUSES.has(status);
    const validTransferProgress = Number.isFinite(progress) && progress >= 0
      && (field === 'transferred_size' ? progress < Number.MAX_SAFE_INTEGER : progress < 100)
      && Number.isFinite(Number(sample && sample.t_ms));
    if (terminal || !validTransferProgress) {
      previousActive = null;
      continue;
    }
    if (progress === 0) {
      if (previousActive != null) regressionCount += 1;
      previousActive = null;
      continue;
    }
    activeSampleCount += 1;
    const tMs = Number(sample.t_ms);
    if (previousActive == null) {
      previousActive = {t_ms: tMs, progress};
      forwardProgressSampleCount += 1;
      continue;
    }
    if (progress < previousActive.progress) {
      regressionCount += 1;
      previousActive = null;
      continue;
    }
    if (progress === previousActive.progress) continue;
    gaps.push(tMs - previousActive.t_ms);
    forwardProgressSampleCount += 1;
    previousActive = {t_ms: tMs, progress};
  }
  const result = {
    p50_ms: percentile(gaps, 0.5),
    p95_ms: percentile(gaps),
    max_ms: maximum(gaps),
    sample_count: forwardProgressSampleCount,
    active_sample_count: activeSampleCount,
    gap_count: gaps.length,
    gaps_ms: gaps,
    monotonic: regressionCount === 0,
    regression_count: regressionCount,
  };
  if (field !== 'progress') result.value_field = field;
  return result;
}

function visibleSizeBytes(value) {
  const match = String(value || '').trim().match(
    /^([0-9]+(?:[.,][0-9]+)?)\s*(B|KB|MB|GB|TB|PB)\b/i,
  );
  if (!match) return null;
  const amount = Number(match[1].replace(',', '.'));
  const units = {B: 0, KB: 1, MB: 2, GB: 3, TB: 4, PB: 5};
  const unit = units[String(match[2]).toUpperCase()];
  if (!Number.isFinite(amount) || unit == null) return null;
  return amount * (1024 ** unit);
}

function visibleSizeGap(samples) {
  const gaps = [];
  let previousActive = null;
  let activeSampleCount = 0;
  let forwardProgressSampleCount = 0;
  let regressionCount = 0;
  for (const sample of (Array.isArray(samples) ? samples : [])
    .slice().sort((a, b) => Number(a?.t_ms) - Number(b?.t_ms))) {
    const size = visibleSizeBytes(sample && sample.size_info);
    const status = String(sample && sample.status || '').toLowerCase();
    const terminal = TERMINAL_PROGRESS_STATUSES.has(status);
    if (terminal) {
      previousActive = null;
      continue;
    }
    // A status-only DOM mutation has no visible size sample.  Ignore it so
    // the next rendered size change is measured from the last real value.
    if (size == null || !Number.isFinite(Number(sample && sample.t_ms))) continue;
    if (size === 0) {
      if (previousActive != null) regressionCount += 1;
      previousActive = null;
      continue;
    }
    activeSampleCount += 1;
    const tMs = Number(sample.t_ms);
    if (previousActive == null) {
      previousActive = {t_ms: tMs, size};
      forwardProgressSampleCount += 1;
      continue;
    }
    if (size < previousActive.size) {
      regressionCount += 1;
      previousActive = null;
      continue;
    }
    if (size === previousActive.size) continue;
    gaps.push(tMs - previousActive.t_ms);
    forwardProgressSampleCount += 1;
    previousActive = {t_ms: tMs, size};
  }
  return {
    p50_ms: percentile(gaps, 0.5),
    p95_ms: percentile(gaps),
    max_ms: maximum(gaps),
    sample_count: forwardProgressSampleCount,
    active_sample_count: activeSampleCount,
    gap_count: gaps.length,
    gaps_ms: gaps,
    monotonic: regressionCount === 0,
    regression_count: regressionCount,
    value_field: 'size_info',
  };
}

function advancingRawTransferredBytes(samples) {
  let previous = null;
  for (const sample of (Array.isArray(samples) ? samples : [])
    .slice().sort((a, b) => Number(a?.t_ms) - Number(b?.t_ms))) {
    const value = sample?.transferred_size == null ? Number.NaN : Number(sample.transferred_size);
    if (!Number.isFinite(value) || value < 0 || value >= Number.MAX_SAFE_INTEGER) continue;
    if (previous != null && value > previous) return true;
    previous = value;
  }
  return false;
}

function mainThreadResponsiveness(samples) {
  const drift = (Array.isArray(samples) ? samples : [])
    .map(sample => Number(sample && sample.drift_ms))
    .filter(value => Number.isFinite(value) && value >= 0);
  return {
    interval_ms: MAIN_THREAD_HEARTBEAT_INTERVAL_MS,
    count: drift.length,
    p95_drift_ms: percentile(drift),
    max_drift_ms: maximum(drift),
  };
}

function forwardProgressReady(
  samples, queueMarker, minimumSamples = MIN_ACTIVE_PROGRESS_SAMPLES, rawBytes = false
) {
  if (!finiteMeasurementMarker(queueMarker)) return false;
  let previousActive = null;
  let genuineProgressSamples = 0;
  let regression = false;
  for (const sample of (Array.isArray(samples) ? samples : [])
    .filter(item => Number.isFinite(Number(item?.t_ms)) && Number(item.t_ms) >= queueMarker)
    .sort((a, b) => Number(a.t_ms) - Number(b.t_ms))) {
    const progress = Number(sample && sample.progress);
    const status = String(sample && sample.status || '').toLowerCase();
    const terminal = TERMINAL_PROGRESS_STATUSES.has(status);
    const validTransferProgress = Number.isFinite(progress) && progress >= 0
      && (rawBytes ? progress < Number.MAX_SAFE_INTEGER : progress < 100);
    if (terminal || !validTransferProgress) {
      previousActive = null;
      continue;
    }
    if (progress === 0) {
      if (previousActive != null) regression = true;
      previousActive = null;
      continue;
    }
    if (previousActive == null) {
      previousActive = progress;
      genuineProgressSamples += 1;
    } else if (progress > previousActive) {
      previousActive = progress;
      genuineProgressSamples += 1;
    } else if (progress < previousActive) {
      regression = true;
      previousActive = null;
    }
  }
  return !regression && genuineProgressSamples >= minimumSamples;
}

function progressGapAcceptance(summary) {
  const gapCount = Number(summary && summary.gap_count);
  const p50 = Number(summary && summary.p50_ms);
  const p95 = Number(summary && summary.p95_ms);
  const max = Number(summary && summary.max_ms);
  return {
    minimum_gap_count: Number.isFinite(gapCount) && gapCount >= MIN_PROGRESS_GAPS,
    p50: summary?.p50_ms != null && Number.isFinite(p50) && p50 <= PROGRESS_GAP_P50_LIMIT_MS,
    p95: summary?.p95_ms != null && Number.isFinite(p95) && p95 <= PROGRESS_GAP_P95_LIMIT_MS,
    max: summary?.max_ms != null && Number.isFinite(max) && max <= MAX_PROGRESS_GAP_MS,
    monotonic: Number(summary?.sample_count) > 0 && summary?.monotonic === true
      && summary?.regression_count != null
      && Number.isFinite(Number(summary.regression_count)) && Number(summary.regression_count) === 0,
  };
}

function finiteMeasurementMarker(value) {
  return typeof value === 'number' && Number.isFinite(value);
}

function measurementBoundaryValid(measurement) {
  return Boolean(measurement)
    && finiteMeasurementMarker(measurement.epoch_t_ms)
    && finiteMeasurementMarker(measurement.measured_queue_t_ms)
    && measurement.post_readiness === true;
}

function latencyStats(samples, field) {
  return {
    count: (Array.isArray(samples) ? samples : []).filter(sample => Number.isFinite(Number(sample && sample[field]))).length,
    p95_ms: percentile((samples || []).map(sample => sample && sample[field])),
    max_ms: maximum((samples || []).map(sample => sample && sample[field])),
  };
}

function latestRelevantApply(applies, atMs, scopedPath, measuredQueueMs = null) {
  return (Array.isArray(applies) ? applies : [])
    .filter(item => item && Number(item.t_ms) <= Number(atMs)
      && (measuredQueueMs == null || Number(item.t_ms) >= Number(measuredQueueMs))
      && String(item.pathname || '') === String(scopedPath || '')
      && item.target_bearing === true
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
  if (record.profile === 'legacy-file') {
    return record.attempted === true
      && Array.isArray(record.errors) && record.errors.length === 0
      && record.residual_local_absent === true;
  }
  const actionAccepted = record.queue_accepted === true || record.mutation_accepted === true;
  if (!actionAccepted) {
    return record.attempted === true
      && Array.isArray(record.errors) && record.errors.length === 0
      && record.residual_remote_only_queueable === true
      && record.transfer_quiescent === true;
  }
  return record.attempted === true
    && Array.isArray(record.errors) && record.errors.length === 0
    && record.residual_state === 'stopped'
    && record.transfer_quiescent === true;
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
  const record = {kind, message: safeText(error && (error.message || error.errorText || error))};
  if (error?.action) record.action = String(error.action);
  if (error?.endpoint_action) record.endpoint_action = String(error.endpoint_action);
  if (error?.endpoint) record.endpoint = safeText(error.endpoint);
  if (error?.schema_error) record.schema_error = safeText(error.schema_error);
  if (error?.transport_error) record.transport_error = safeText(error.transport_error);
  if (Number.isFinite(Number(error?.http_status))) record.http_status = Number(error.http_status);
  if (error?.response_reported != null) record.response_reported = error.response_reported === true;
  return record;
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

function summaryPathPairReconciliation(summary, targetPairId) {
  const pairs = Array.isArray(summary?.path_pairs) ? summary.path_pairs : [];
  const matches = typeof targetPairId === 'string' && targetPairId.trim().length > 0
    ? pairs.filter(pair => typeof pair?.path_pair_id === 'string' && pair.path_pair_id === targetPairId)
    : [];
  if (matches.length !== 1) {
    return {
      path_pair_id_match: false,
      duplicate_match: matches.length > 1,
      fields_valid: false,
      reconciled_local: false,
      reconciled_remote: false,
    };
  }
  const pair = matches[0];
  return {
    path_pair_id_match: true,
    duplicate_match: false,
    fields_valid: typeof pair.reconciled_local === 'boolean'
      && typeof pair.reconciled_remote === 'boolean',
    reconciled_local: pair.reconciled_local === true,
    reconciled_remote: pair.reconciled_remote === true,
  };
}

function pathPairReconciled(summary, targetPairId) {
  const pair = summaryPathPairReconciliation(summary, targetPairId);
  return pair.path_pair_id_match && pair.reconciled_local === true && pair.reconciled_remote === true;
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
  assert(gap.p50_ms === 700 && gap.p95_ms === 700 && gap.max_ms === 700
    && gap.sample_count === 4 && gap.active_sample_count === 4 && gap.gap_count === 2
    && gap.monotonic === true && gap.regression_count === 0
    && JSON.stringify(gap.gaps_ms) === JSON.stringify([700, 700]),
  'progress gap statistic failed');
  assert(progressGap([{t_ms: 1, status: 'local only', progress: 12.5}]).sample_count === 1,
    'non-downloading finite in-flight progress was not counted');
  const resetGap = progressGap([
    {t_ms: 100, status: 'downloading', progress: 1},
    {t_ms: 200, status: 'downloading', progress: 2},
    {t_ms: 300, status: 'stopped', progress: 2},
    {t_ms: 1_100, status: 'downloading', progress: 3},
    {t_ms: 1_200, status: 'downloaded', progress: 100},
    {t_ms: 1_300, status: 'downloading', progress: 4},
  ]);
  assert(JSON.stringify(resetGap.gaps_ms) === JSON.stringify([100]),
    'inactive and stopped progress segments were not reset');
  const duplicateProgress = progressGap([
    {t_ms: 100, status: 'downloading', progress: 1},
    {t_ms: 150, status: 'downloading', progress: 1},
    {t_ms: 180, status: 'local only', progress: 1},
    {t_ms: 200, status: 'downloading', progress: 2},
    {t_ms: 300, status: 'downloading', progress: 2},
    {t_ms: 400, status: 'downloading', progress: 3},
  ]);
  assert(duplicateProgress.sample_count === 3 && duplicateProgress.active_sample_count === 6
    && duplicateProgress.gap_count === 2
    && JSON.stringify(duplicateProgress.gaps_ms) === JSON.stringify([100, 200]),
  'equal-progress and status-only mutations were counted as forward progress');
  const rawDuplicateProgress = progressGapForField([
    {t_ms: 100, status: 'downloading', transferred_size: 100},
    {t_ms: 150, status: 'downloading', transferred_size: 100},
    {t_ms: 200, status: 'downloading', transferred_size: 200},
    {t_ms: 300, status: 'downloading', transferred_size: 200},
    {t_ms: 400, status: 'downloading', transferred_size: 300},
  ], 'transferred_size');
  assert(rawDuplicateProgress.sample_count === 3 && rawDuplicateProgress.gap_count === 2
    && JSON.stringify(rawDuplicateProgress.gaps_ms) === JSON.stringify([100, 200]),
  'equal raw transferred bytes were counted as forward progress');
  const rawRegression = progressGapForField([
    {t_ms: 100, status: 'downloading', transferred_size: 100},
    {t_ms: 200, status: 'downloading', transferred_size: 200},
    {t_ms: 300, status: 'downloading', transferred_size: 150},
    {t_ms: 400, status: 'downloading', transferred_size: 250},
  ], 'transferred_size');
  assert(rawRegression.monotonic === false && rawRegression.regression_count === 1
    && rawRegression.gap_count === 1,
  'decreasing raw transferred bytes were accepted');
  const heartbeatSummary = mainThreadResponsiveness([
    {drift_ms: 1}, {drift_ms: 3}, {drift_ms: 2},
  ]);
  assert(heartbeatSummary.count === 3 && heartbeatSummary.max_drift_ms === 3,
    'browser main-thread heartbeat statistic failed');
  const rawNullOnly = progressGapForField([
    {t_ms: 100, status: 'downloading', transferred_size: null},
    {t_ms: 200, status: 'downloading', download_progress: 1},
  ], 'transferred_size');
  assert(rawNullOnly.sample_count === 0 && rawNullOnly.gap_count === 0,
    'null raw transferred bytes activated byte-progress acceptance');
  const unchangedSizeInfo = visibleSizeGap([
    {t_ms: 100, status: 'downloading', size_info: '0 B of 4 GB'},
    {t_ms: 200, status: 'downloading', size_info: '64 KB of 4 GB'},
    {t_ms: 300, status: 'downloading', size_info: '64 KB of 4 GB'},
  ]);
  assert(unchangedSizeInfo.sample_count === 1 && unchangedSizeInfo.gap_count === 0,
    'unchanged visible size_info was counted as forward progress');
  const visibleSizeProgress = visibleSizeGap([
    {t_ms: 100, status: 'downloading', size_info: '0 B of 4 GB'},
    {t_ms: 200, status: 'downloading', size_info: '64 KB of 4 GB'},
    {t_ms: 300, status: 'downloading', size_info: '128 KB of 4 GB'},
    {t_ms: 400, status: 'downloading', size_info: '128 KB of 4 GB'},
  ]);
  assert(visibleSizeProgress.sample_count === 2 && visibleSizeProgress.gap_count === 1
    && visibleSizeProgress.gaps_ms[0] === 100,
  'visible size_info forward cadence was not measured');
  const visibleZeroRegression = visibleSizeGap([
    {t_ms: 100, status: 'downloading', size_info: '64 KB of 4 GB'},
    {t_ms: 200, status: 'downloading', size_info: '0 B of 4 GB'},
    {t_ms: 300, status: 'downloading', size_info: '64 KB of 4 GB'},
  ]);
  assert(visibleZeroRegression.monotonic === false && visibleZeroRegression.regression_count === 1
    && visibleZeroRegression.gap_count === 0,
  'active zero visible size_info reset was accepted as a clean segment');
  const decreasingProgress = progressGap([
    {t_ms: 100, status: 'downloading', progress: 1},
    {t_ms: 200, status: 'downloading', progress: 2},
    {t_ms: 300, status: 'downloading', progress: 1.5},
    {t_ms: 400, status: 'downloading', progress: 2.5},
  ]);
  assert(decreasingProgress.monotonic === false && decreasingProgress.regression_count === 1
    && decreasingProgress.gap_count === 1
    && JSON.stringify(decreasingProgress.gaps_ms) === JSON.stringify([100])
    && !progressGapAcceptance(decreasingProgress).monotonic,
  'decreasing progress was not rejected');
  const zeroRegression = progressGap([
    {t_ms: 100, status: 'downloading', progress: 2},
    {t_ms: 200, status: 'downloading', progress: 0},
    {t_ms: 300, status: 'downloading', progress: 3},
  ]);
  assert(zeroRegression.monotonic === false && zeroRegression.regression_count === 1
    && !progressGapAcceptance(zeroRegression).monotonic,
  'zero progress regression was treated as an inactive reset');
  assert(!forwardProgressReady([
    {t_ms: 0, status: 'downloading', progress: 2},
    {t_ms: 100, status: 'downloading', progress: 0},
    ...new Array(MIN_ACTIVE_PROGRESS_SAMPLES).fill(null).map((_value, index) =>
      ({t_ms: 200 + index * 100, status: 'downloading', progress: index + 3})),
  ], 0), 'wait readiness accepted a zero-progress regression');
  const rawByteSamples = new Array(MIN_ACTIVE_PROGRESS_SAMPLES).fill(null).map((_value, index) => ({
    t_ms: index * 100, status: 'downloading', progress: 0, transferred_size: (index + 1) * 256,
  }));
  const rawByteProgressSamples = rawByteSamples.map(sample => ({...sample, progress: sample.transferred_size}));
  assert(forwardProgressReady(rawByteProgressSamples, 0, MIN_ACTIVE_PROGRESS_SAMPLES, true)
    && !forwardProgressReady(rawByteProgressSamples, 0, MIN_ACTIVE_PROGRESS_SAMPLES, false)
    && advancingRawTransferredBytes(rawByteSamples)
    && rawByteSamples.every(sample => sample.progress === 0),
  'raw bytes above 100 did not establish activity while visible percent remained zero');
  const terminalZeroReset = progressGap([
    {t_ms: 100, status: 'downloading', progress: 2},
    {t_ms: 200, status: 'complete', progress: 0},
    {t_ms: 300, status: 'downloading', progress: 3},
  ]);
  assert(terminalZeroReset.monotonic === true && terminalZeroReset.regression_count === 0,
    'terminal zero progress was incorrectly treated as a regression');
  const samplesForGaps = gaps => {
    const samples = [{t_ms: 0, status: 'downloading', progress: 1}];
    let tMs = 0;
    gaps.forEach((gapMs, index) => {
      tMs += gapMs;
      samples.push({t_ms: tMs, status: 'downloading', progress: (index % 99) + 2});
    });
    return samples;
  };
  const insufficient = progressGap(samplesForGaps(new Array(19).fill(100)));
  assert(insufficient.sample_count === 20 && insufficient.gap_count === 19
    && !progressGapAcceptance(insufficient).minimum_gap_count,
  'insufficient active progress samples were accepted');
  const p50Failure = progressGap(samplesForGaps([...new Array(9).fill(100), ...new Array(11).fill(151)]));
  assert(!progressGapAcceptance(p50Failure).p50 && progressGapAcceptance(p50Failure).p95,
    'p50 progress-gap threshold failure was not detected');
  const p95Failure = progressGap(samplesForGaps([...new Array(18).fill(100), 201, 201]));
  assert(progressGapAcceptance(p95Failure).p50 && !progressGapAcceptance(p95Failure).p95,
    'p95 progress-gap threshold failure was not detected');
  const maxFailure = progressGap(samplesForGaps([...new Array(19).fill(100), 1_001]));
  assert(progressGapAcceptance(maxFailure).p95 && !progressGapAcceptance(maxFailure).max,
    'maximum progress-gap threshold failure was not detected');
  const passingOutlier = progressGap(samplesForGaps([...new Array(19).fill(100), 900]));
  const passingAcceptance = progressGapAcceptance(passingOutlier);
  assert(passingOutlier.gap_count === MIN_PROGRESS_GAPS && passingOutlier.p95_ms === 100
    && passingOutlier.max_ms === 900 && Object.values(passingAcceptance).every(Boolean),
  'allowed sub-1000ms progress-gap outlier was rejected');
  const visibleSamplesForGaps = gaps => {
    const samples = [{t_ms: 0, status: 'downloading', size_info: '64 KB of 4 GB'}];
    let tMs = 0;
    gaps.forEach((gapMs, index) => {
      tMs += gapMs;
      samples.push({t_ms: tMs, status: 'downloading', size_info: `${65 + index} KB of 4 GB`});
    });
    return samples;
  };
  const visiblePassingOutlier = visibleSizeGap(visibleSamplesForGaps([...new Array(19).fill(100), 900]));
  assert(visiblePassingOutlier.gap_count === MIN_PROGRESS_GAPS
    && Object.values(progressGapAcceptance(visiblePassingOutlier)).every(Boolean),
  'visible size_info progress-gap acceptance was rejected');
  const latency = latencyStats([{receive_to_dom_ms: 35}, {receive_to_dom_ms: 120}], 'receive_to_dom_ms');
  assert(latency.p95_ms === 120 && latency.max_ms === 120, 'receive-to-dom latency statistic failed');
  assert(latestRelevantApply([
    {t_ms: 10, pathname: '/server/stream', event_type: 'message', target_bearing: false},
    {t_ms: 20, pathname: '/server/model/v1/pairs/x/stream', event_type: 'model-updated', target_bearing: true},
  ], 30, '/server/model/v1/pairs/x/stream').t_ms === 20, 'unrelated stream correlation filter failed');
  assert(latestRelevantApply([
    {t_ms: 10, pathname: '/server/model/v1/pairs/x/stream', event_type: 'model-page', target_bearing: true},
    {t_ms: 20, pathname: '/server/model/v1/pairs/x/stream', event_type: 'model-invalidate', target_bearing: true},
    {t_ms: 30, pathname: '/server/model/v1/pairs/x/stream', event_type: 'model-patch', target_bearing: true},
    {t_ms: 40, pathname: '/server/model/v1/pairs/x/stream', event_type: 'model-reset', target_bearing: true},
    {t_ms: 45, pathname: '/server/model/v1/summary/stream', event_type: 'model-reset', target_bearing: true},
  ], 50, '/server/model/v1/pairs/x/stream').event_type === 'model-reset', 'v1 model event names are not accepted');
  assert(latestRelevantApply([
    {t_ms: 10, pathname: '/server/model/v1/pairs/other/stream', event_type: 'model-updated', target_bearing: true},
    {t_ms: 20, pathname: '/server/model/v1/pairs/x/stream', event_type: 'model-message', target_bearing: false},
  ], 30, '/server/model/v1/pairs/x/stream') === null, 'generic or other-pair events were correlated');
  assert(latestRelevantApply([
    {t_ms: 10, pathname: '/server/model/v1/pairs/x/stream', event_type: 'model-patch', target_bearing: true},
    {t_ms: 20, pathname: '/server/model/v1/pairs/x/stream', event_type: 'model-patch', target_bearing: false},
  ], 30, '/server/model/v1/pairs/x/stream').t_ms === 10,
  'unrelated same-scope apply replaced target attribution');
  const targetCausalApply = latestRelevantApply([
    {t_ms: 100, receive_t_ms: 80, pathname: '/server/model/v1/pairs/x/stream',
      event_type: 'model-patch', target_bearing: true},
    {t_ms: 110, receive_t_ms: 105, pathname: '/server/model/v1/pairs/x/stream',
      event_type: 'model-patch', target_bearing: false},
  ], 120, '/server/model/v1/pairs/x/stream');
  assert(targetCausalApply.t_ms === 100 && targetCausalApply.receive_t_ms === 80,
    'unrelated same-scope apply changed target receive/apply latency attribution');
  const postQueueApply = latestRelevantApply([
    {t_ms: 90, pathname: '/server/model/v1/pairs/x/stream', event_type: 'model-patch', target_bearing: true},
    {t_ms: 110, pathname: '/server/model/v1/pairs/x/stream', event_type: 'model-patch', target_bearing: true},
  ], 120, '/server/model/v1/pairs/x/stream', 100);
  assert(postQueueApply?.t_ms === 110,
    'pre-Queue target apply was used for post-Queue DOM attribution');
  const target = discoverTarget({
    synthetic_only: true,
    path_pairs: [{id: 'pair-01', name: 'Performance Pair 01', directory: 'path-pair-01', role: 'ordinary-active',
      remote_only_targets: [{kind: 'directory', relative_path: ['path-pair-01', 'remote-only', 'target'].join('/'),
        size_bytes: 1024, storage_mode: 'real-bytes-hardlink-deduplicated', storage_size_bytes: 128,
        file_count: 20, directory_count: 3, max_depth: 2}]}],
  });
  assert(target.pair_id === 'pair-01', 'target pair identity was not retained internally');
  assert(target.kind === 'directory' && target.file_count === 20 && target.directory_count === 3
    && target.max_depth === 2, 'directory target descriptor was not retained internally');
  const fileManifestForPairId = pairId => ({
    synthetic_only: true,
    path_pairs: [{id: pairId, name: 'Performance Pair File', directory: 'path-pair-01', role: 'ordinary-active',
      remote_only_targets: [{relative_path: 'path-pair-01/remote-only/target.bin'}]}],
  });
  let missingPairIdRejected = false;
  try { discoverTarget(fileManifestForPairId(undefined)); } catch (_) { missingPairIdRejected = true; }
  let blankPairIdRejected = false;
  try { discoverTarget(fileManifestForPairId('   ')); } catch (_) { blankPairIdRejected = true; }
  assert(missingPairIdRejected && blankPairIdRejected,
    'missing or blank target pair ids were accepted');
  const unreconciledSummary = {path_pairs: [{path_pair_id: 'pair-01', reconciled_local: false, reconciled_remote: true}]};
  assert(!pathPairReconciled(unreconciledSummary, target.pair_id)
    && !summaryPathPairReconciliation(unreconciledSummary, 'other-pair').path_pair_id_match,
  'summary reconciliation readiness accepted the wrong or partially reconciled pair');
  assert(pathPairReconciled({path_pairs: [
    {path_pair_id: 'pair-01', reconciled_local: true, reconciled_remote: true},
  ]}, target.pair_id), 'summary reconciliation readiness rejected the exact reconciled pair');
  assert(!pathPairReconciled({path_pairs: [
    {path_pair_id: 'pair-01', reconciled_local: true, reconciled_remote: true},
    {path_pair_id: 'pair-01', reconciled_local: true, reconciled_remote: true},
  ]}, target.pair_id), 'duplicate summary path-pair identities were accepted');
  assert(!summaryPathPairReconciliation({path_pairs: [
    {path_pair_id: '', reconciled_local: true, reconciled_remote: true},
  ]}, '').path_pair_id_match, 'empty summary pair identity was accepted');
  assert(!summaryPathPairReconciliation({path_pairs: [
    {path_pair_id: 'pair-01', reconciled_local: 'true', reconciled_remote: true},
  ]}, target.pair_id).fields_valid, 'non-boolean reconciliation flags were accepted');
  const legacyTarget = discoverTarget({
    synthetic_only: true,
    path_pairs: [{id: 'pair-file', name: 'Performance Pair File', directory: 'path-pair-01', role: 'ordinary-active',
      remote_only_targets: [{relative_path: 'path-pair-01/remote-only/target.bin'}]}],
  });
  assert(legacyTarget.kind === 'file' && !Object.prototype.hasOwnProperty.call(legacyTarget, 'file_count'),
    'committed file target descriptor was not retained as a legacy file');
  assert(!JSON.stringify(target).includes('"pair_id"'), 'target pair identity leaked into serialized evidence');
  assert(normalizeAppPath('/server/model/v1/pairs/private-scope/stream') ===
    '/server/model/v1/pairs/<scope-digest:7bc278faa0682944>/stream', 'scoped route normalization failed');
  assert(safeText('/server/model/v1/pairs/private-scope/stream').includes('/server/model/v1/pairs/<scope-digest:'),
    'normalized scoped route was redacted as a generic path');
  assert(cleanupPass({required: true, profile: 'cadence-directory', attempted: true,
    queue_accepted: true, mutation_accepted: true, errors: [], residual_state: 'stopped',
    transfer_quiescent: true}),
    'successful cleanup was not accepted');
  assert(!cleanupPass({required: true, attempted: true, errors: [{kind: 'cleanup-stop'}],
    residual_state: 'stopped', transfer_quiescent: true}),
  'cleanup failure was silently accepted');
  assert(cleanupPass({required: true, profile: 'legacy-file', attempted: true, errors: [],
    residual_local_absent: true}), 'legacy local-absent cleanup was not accepted');
  assert(cleanupPass({required: true, profile: 'cadence-directory', attempted: true,
    queue_accepted: false, mutation_accepted: false, errors: [],
    residual_remote_only_queueable: true, transfer_quiescent: true}),
  'rejected Queue remote-only cleanup was not accepted as a quiescent no-op');
  assert(!cleanupPass({required: true, profile: 'cadence-directory', attempted: true,
    queue_accepted: false, mutation_accepted: false, errors: [],
    residual_remote_only_queueable: false, transfer_quiescent: false}),
  'rejected Queue cleanup accepted a non-quiescent target');
  assert(cleanupPass({required: true, profile: 'cadence-directory', attempted: true,
    queue_accepted: true, mutation_accepted: true, errors: [], residual_state: 'stopped',
    transfer_quiescent: true}), 'accepted Queue cleanup did not retain Stop completion requirements');
  assert(readinessIsQueueable({status: 'default-remote', controls: {
    Queue: {enabled: true}, Stop: {enabled: false},
  }}), 'remote-only default target was not recognized as queueable');
  assert(readinessIsQueueable({status: 'stopped', controls: {
    Queue: {enabled: true}, Stop: {enabled: false},
  }}), 'legacy stopped remote target was not recognized as queueable');
  assert(localAbsentControlsAreReady({controls: {
    Queue: {enabled: true}, 'Delete Local': {enabled: false},
  }}), 'legacy local-absent Queue fallback was not recognized');
  assert(!localAbsentControlsAreReady({controls: {
    Queue: {enabled: true}, 'Delete Local': {enabled: true},
  }}), 'legacy local-absent Queue fallback ignored Delete Local');
  assert(!directoryReadinessIsFresh({status: 'stopped', controls: {
    Queue: {enabled: true}, Stop: {enabled: false},
  }}), 'stopped directory target was incorrectly recognized as fresh queueable');
  assert(!readinessIsQueueable({status: 'default-remote', controls: {
    Queue: {enabled: true}, Stop: {enabled: true}, 'Delete Local': {enabled: true},
  }}), 'active remote target was incorrectly recognized as fresh queueable');
  assert(directoryReadinessIsFresh({status: 'default-remote', controls: {
    Queue: {enabled: true}, Stop: {enabled: false}, 'Delete Local': {enabled: true},
  }}), 'directory freshness incorrectly depended on destructive control state');
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
  assert(measuredTargetMutations([{t_ms: 0}, {t_ms: 10}], 0).length === 2,
    'zero measured-queue boundary was not preserved');
  assert(measuredTargetMutations([{t_ms: 1}, {t_ms: 10}], null).length === 0
    && measuredTargetMutations([{t_ms: 1}, {t_ms: 10}], Number.NaN).length === 0,
  'missing measured-queue boundary was accepted');
  assert(measurementBoundaryValid({epoch_t_ms: 0, measured_queue_t_ms: 0, post_readiness: true}),
    'zero measurement markers were rejected');
  assert(!measurementBoundaryValid({epoch_t_ms: 0, measured_queue_t_ms: null, post_readiness: true})
    && !measurementBoundaryValid({epoch_t_ms: Number.NaN, measured_queue_t_ms: 0, post_readiness: true})
    && !measurementBoundaryValid({epoch_t_ms: 0, measured_queue_t_ms: 0, post_readiness: false}),
  'invalid measurement boundary was accepted');
  const output = {
    schema: 'seedsync.performance-lab.browser-self-test.v1',
    statistics: {
      p95_ms: percentile([1, 2, 3, 4]), max_ms: maximum([1, 2, 3, 4]),
      progress_gap: visiblePassingOutlier,
      visible_size_info_gap: visiblePassingOutlier,
      raw_progress_gap: passingOutlier,
    },
    thresholds: {
      target_dom_p95_ms: 200,
      target_dom_max_ms: 500,
      minimum_progress_gap_count: MIN_PROGRESS_GAPS,
      progress_gap_p50_ms: PROGRESS_GAP_P50_LIMIT_MS,
      progress_gap_p95_ms: PROGRESS_GAP_P95_LIMIT_MS,
      max_progress_gap_ms: MAX_PROGRESS_GAP_MS,
      progress_monotonic: true,
      raw_progress_monotonic: true,
      main_thread_heartbeat_interval_ms: MAIN_THREAD_HEARTBEAT_INTERVAL_MS,
      main_thread_drift_limit_ms: MAIN_THREAD_DRIFT_LIMIT_MS,
      measurement_boundary: true,
      action_render_limits_ms: ACTION_RENDER_LIMITS_MS,
    },
    checks: {
      readiness_matrix: true, stable_identity_reorder: true, measurement_epoch: true,
      progress_gap_statistics: true, progress_gap_inactive_reset: true,
      progress_gap_duplicate_equal: true, progress_gap_status_only: true,
      raw_progress_gap_duplicate_equal: true, raw_progress_gap_regression: true,
      raw_progress_gap_null_ignored: true,
      visible_size_info_duplicate_equal: true,
      visible_size_info_zero_regression: true,
      progress_gap_decreasing: true, progress_gap_zero_regression: true,
      progress_gap_insufficient_samples: true, progress_gap_p50_threshold: true,
      progress_gap_p95_threshold: true, progress_gap_max_threshold: true,
      progress_gap_allowed_outlier: true, progress_monotonic: true,
      progress_monotonic_pass_fail: true, progress_wait_zero_regression: true,
      raw_progress_wait_byte_values: true, raw_progress_activity_above_percent_zero: true,
      target_apply_causal_attribution: true,
      main_thread_responsiveness: true,
      measurement_boundary: true,
      reconciliation_summary_readiness: true,
    },
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
  const breadcrumbsBeforeCleanupPath = parseOption(argv, '--breadcrumbs-before-cleanup-output');
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
  if (target.kind !== 'directory' && process.env.PERF_BROWSER_DESTRUCTIVE_APPROVED !== 'on') {
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
    const error = new Error('validated live app/project/service/volume/fixture binding is required before browser evidence');
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
    const targetId = await readTargetId(page, target.name, timeoutMs, target);
    target.file_id_present = Boolean(targetId);
    await exerciseActions(page, target, targetId, timeoutMs, evidence, modelStreamPath, async () => {
      await captureBreadcrumbsBeforeCleanup(
        baseUrl, apiToken, breadcrumbsBeforeCleanupPath, timeoutMs,
      );
    });
    const timeline = await page.evaluate(() => window.__seedSyncPerfTimeline?.snapshot?.() || {});
    evidence.samples.event_source_receive = sanitizeTimelinePaths(timeline.eventSourceReceive);
    evidence.samples.event_source_apply = sanitizeTimelinePaths(timeline.eventSourceApply);
    evidence.samples.target_raw_progress = Array.isArray(timeline.targetRawProgress) ? timeline.targetRawProgress : [];
    evidence.samples.target_dom_mutations = Array.isArray(timeline.targetDomMutations) ? timeline.targetDomMutations : [];
    evidence.samples.main_thread_responsiveness = Array.isArray(timeline.mainThreadResponsiveness)
      ? timeline.mainThreadResponsiveness : [];
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
        evidence.samples.target_raw_progress = Array.isArray(timeline.targetRawProgress) ? timeline.targetRawProgress : [];
        evidence.samples.target_dom_mutations = Array.isArray(timeline.targetDomMutations) ? timeline.targetDomMutations : [];
        evidence.samples.main_thread_responsiveness = Array.isArray(timeline.mainThreadResponsiveness)
          ? timeline.mainThreadResponsiveness : [];
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
      minimum_progress_gap_count: {limit: MIN_PROGRESS_GAPS, observed: null, pass: false},
      progress_gap_p50_ms: {limit_ms: PROGRESS_GAP_P50_LIMIT_MS, observed_ms: null, pass: false},
      progress_gap_p95_ms: {limit_ms: PROGRESS_GAP_P95_LIMIT_MS, observed_ms: null, pass: false},
      max_progress_gap_ms: {limit_ms: MAX_PROGRESS_GAP_MS, observed_ms: null, pass: false},
      progress_monotonic: {required: true, observed: null, pass: false},
      raw_progress_monotonic: {required: true, observed: null, pass: false},
      main_thread_responsiveness: {
        minimum_samples: MIN_MAIN_THREAD_HEARTBEATS,
        max_drift_ms: MAIN_THREAD_DRIFT_LIMIT_MS,
        observed: null, pass: false,
      },
      measurement_boundary: {required: true, observed: null, pass: false},
      action_rendered_state_p95_ms: {
        limit_ms_by_action: ACTION_RENDER_LIMITS_MS, observed: {}, pass: false,
      },
      action_http_response_reported: {required: true, pass: false},
      browser_errors: {limit: 0, observed: 0, pass: false},
    },
    samples: {
      event_source_receive: [], event_source_apply: [], target_raw_progress: [],
      target_dom_mutations: [], target_dom_cadence: null, progress: [],
      visible_progress: [], visible_size_info: [], main_thread_responsiveness: [],
    },
    measurement: {
      required: true, epoch_t_ms: null, measured_queue_t_ms: null,
      post_readiness: false, sample_epoch_source: 'post-measured-queue',
    },
    actions: [],
    cycles: [],
    max_progress_gap_ms: null,
    statistics: {
      action_http_response: {}, action_rendered_state: {}, progress_gap: null,
      visible_size_info_gap: null, raw_progress_gap: null, visible_progress_gap: null,
      browser_main_thread_responsiveness: null,
    },
    expected_request_aborts: [],
    preconditions: [],
    readiness: {
      required: true, attempted: false,
      initial_precondition: null, final_precondition: null,
      reconciliation: {
        required: target?.kind === 'directory', attempted: false, endpoint: '/server/model/v1/summary',
        target_path_pair_id_digest: target?.pair_id ? stableDigest(target.pair_id) : null,
        poll_count: 0, observations: [], last: null, elapsed_ms: null,
        pass: target?.kind !== 'directory', failure_classification: null,
      },
      pass: false, failure_classification: null,
    },
    cleanup: {
      required: false, attempted: false, steps: [], errors: [],
      profile: target?.kind === 'directory' ? 'cadence-directory' : 'legacy-file',
      observed_state: null, restored_state: null, residual_state: null,
      residual_local_absent: false, residual_remote_only_queueable: false, transfer_quiescent: false,
      queue_accepted: false, mutation_accepted: false, noop: false,
      pass: false,
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
  if (typeof pair.id !== 'string' || pair.id.trim().length === 0) {
    throw new Error('ordinary-active pair must have a non-empty string id');
  }
  const remoteTarget = pair.remote_only_targets[0];
  const kind = remoteTarget.kind || 'file';
  if (!['file', 'directory'].includes(kind)) {
    throw new Error('ordinary-active browser target kind is unsupported');
  }
  if (kind === 'directory' && (
      !Number.isInteger(remoteTarget.size_bytes) || remoteTarget.size_bytes <= 0
      || !Number.isInteger(remoteTarget.file_count) || remoteTarget.file_count < 1
      || !Number.isInteger(remoteTarget.directory_count) || remoteTarget.directory_count < 1
      || !Number.isInteger(remoteTarget.max_depth) || remoteTarget.max_depth < 1
      || remoteTarget.storage_mode !== 'real-bytes-hardlink-deduplicated'
      || !Number.isInteger(remoteTarget.storage_size_bytes)
      || remoteTarget.storage_size_bytes <= 0
      || remoteTarget.storage_size_bytes > remoteTarget.size_bytes)) {
    throw new Error('ordinary-active browser directory target must be a described aggregate');
  }
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
    kind,
    remote_only: true,
    file_id_present: false,
  };
  if (kind === 'directory') {
    Object.assign(target, {
      aggregate_size_bytes: remoteTarget.size_bytes,
      storage_mode: remoteTarget.storage_mode,
      storage_size_bytes: remoteTarget.storage_size_bytes,
      file_count: remoteTarget.file_count,
      directory_count: remoteTarget.directory_count,
      max_depth: remoteTarget.max_depth,
    });
  }
  Object.defineProperty(target, 'pair_id', {value: pair.id, enumerable: false});
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
    const targetRawProgress = [];
    const targetDomMutations = [];
    const mainThreadResponsiveness = [];
    const heartbeatIntervalMs = 100;
    const started = performance.now();
    let measurementEpochMs = null;
    let measuredQueueMs = null;
    let transferActive = false;
    let targetSelector = null;
    let heartbeatLastMs = null;
    let heartbeatTimer = null;
    const add = (array, value) => { if (array.length < limit) array.push(value); };
    const relative = () => Number((performance.now() - started).toFixed(3));
    const paths = new WeakMap();
    let seenEvents = new WeakSet();
    let eventRecords = new WeakMap();
    let eventTargetRecords = new WeakMap();
    const eventPath = source => {
      try { return new URL(paths.get(source) || '', location.href).pathname; } catch (_) { return null; }
    };
    const targetRecordMatches = record => {
      if (!targetSelector || !record) return false;
      const recordId = record.file_id == null ? '' : String(record.file_id);
      const recordName = record.name == null ? '' : String(record.name);
      return targetSelector.id ? recordId === targetSelector.id : recordName === targetSelector.name;
    };
    const recordTargetProgress = (source, event, received) => {
      if (measurementEpochMs == null || !event || !event.data || !targetSelector) return;
      const eventType = String(event.type || 'message');
      if (!['model-page', 'model-invalidate', 'model-patch', 'model-added', 'model-updated'].includes(eventType)) return;
      let parsed;
      try { parsed = JSON.parse(String(event.data)); } catch (_) { return; }
      const records = Array.isArray(parsed) ? parsed
        : Array.isArray(parsed.records) ? parsed.records
        : [parsed.new_file, parsed.old_file].filter(Boolean);
      const targetSamples = [];
      for (const record of records) {
        if (!targetRecordMatches(record)) continue;
        const transferred = record.transferred_size == null ? null : Number(record.transferred_size);
        const progress = record.download_progress == null ? null : Number(record.download_progress);
        const targetSample = {
          t_ms: relative(), event_type: eventType,
          pathname: eventPath(source),
          receive_t_ms: received?.t_ms ?? null,
          apply_t_ms: null,
          receive_to_apply_ms: null,
          is_dir: record.is_dir === true,
          status: String(record.state || '').toLowerCase() || null,
          transferred_size: Number.isFinite(transferred) ? transferred : null,
          download_progress: Number.isFinite(progress) ? progress : null,
        };
        add(targetRawProgress, targetSample);
        targetSamples.push(targetSample);
      }
      if (event && typeof event === 'object' && targetSamples.length) eventTargetRecords.set(event, targetSamples);
    };
    const recordEvent = (source, event) => {
      if (!event) return null;
      if (typeof event === 'object' && seenEvents.has(event)) return eventRecords.get(event) || null;
      if (event && typeof event === 'object') seenEvents.add(event);
      const receivedAt = relative();
      const item = {t_ms: receivedAt, event_type: String(event.type || 'message'), pathname: eventPath(source)};
      add(eventSourceReceive, item);
      recordTargetProgress(source, event, item);
      if (event && typeof event === 'object') eventRecords.set(event, item);
      return item;
    };
    const recordApply = (source, event, received) => {
      const apply = {
        t_ms: relative(), event_type: String(event?.type || 'message'), pathname: eventPath(source),
        receive_t_ms: received?.t_ms ?? null,
        target_bearing: Boolean(event && eventTargetRecords.get(event)?.length),
      };
      add(eventSourceApply, apply);
      for (const sample of (event && eventTargetRecords.get(event) || [])) {
        sample.apply_t_ms = apply.t_ms;
        sample.receive_to_apply_ms = sample.receive_t_ms == null
          ? null : Number((apply.t_ms - sample.receive_t_ms).toFixed(3));
      }
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
        && (measuredQueueMs == null || Number(item.t_ms) >= measuredQueueMs)
        && String(item.pathname || '') === String(scopedPath || '')
        && item.target_bearing === true
        && scopedModelEventTypes.includes(String(item.event_type)))
      .sort((a, b) => Number(a.t_ms) - Number(b.t_ms)).slice(-1)[0] || null;
    const startHeartbeat = () => {
      if (heartbeatTimer != null) clearInterval(heartbeatTimer);
      heartbeatTimer = setInterval(() => {
        if (!transferActive) return;
        const observedMs = relative();
        const expectedMs = heartbeatLastMs == null
          ? observedMs : heartbeatLastMs + heartbeatIntervalMs;
        heartbeatLastMs = observedMs;
        add(mainThreadResponsiveness, {
          t_ms: observedMs,
          expected_t_ms: Number(expectedMs.toFixed(3)),
          observed_t_ms: observedMs,
          drift_ms: Number(Math.max(0, observedMs - expectedMs).toFixed(3)),
        });
      }, heartbeatIntervalMs);
    };
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
      targetRawProgress,
      targetDomMutations,
      mainThreadResponsiveness,
      findScopedModelPath: pathForPair,
      beginMeasurement() {
        eventSourceReceive.length = 0;
        eventSourceApply.length = 0;
        targetRawProgress.length = 0;
        targetDomMutations.length = 0;
        mainThreadResponsiveness.length = 0;
        seenEvents = new WeakSet();
        eventRecords = new WeakMap();
        eventTargetRecords = new WeakMap();
        measurementEpochMs = relative();
        measuredQueueMs = null;
        transferActive = false;
        heartbeatLastMs = null;
        return {epoch_t_ms: measurementEpochMs};
      },
      markMeasuredQueue() {
        measuredQueueMs = relative();
        transferActive = true;
        heartbeatLastMs = measuredQueueMs;
        return measuredQueueMs;
      },
      markTransferEnded() { transferActive = false; },
      attachTarget(targetId, targetName, scopedPath) {
        targetSelector = {id: targetId ? String(targetId) : '', name: String(targetName || '')};
        startHeartbeat();
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
          const progressBar = row.querySelector('.progress-bar');
          const progressAttribute = progressBar?.getAttribute('aria-valuenow');
          const progressValue = Number(progressAttribute);
          const sizeInfo = row.querySelector('.size_info')?.textContent?.replace(/\s+/g, ' ').trim() || null;
          const isDirectory = Boolean(row.querySelector('.name img[src*="directory"]'));
          const signature = `${status || ''}|${progressAttribute || ''}|${sizeInfo || ''}`;
          if (signature === lastSignature) return;
          lastSignature = signature;
          const tMs = relative();
          const apply = latestScopedApply(eventSourceApply, tMs, scopedPath);
          add(targetDomMutations, {
            t_ms: tMs, status: status || null,
            progress: Number.isFinite(progressValue) ? progressValue : null,
            aria_progress: progressAttribute || null,
            size_info: sizeInfo,
            is_dir: isDirectory,
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
          eventSourcePaths: eventSourcePaths.slice(), targetRawProgress: targetRawProgress.slice(),
          targetDomMutations: targetDomMutations.slice(), mainThreadResponsiveness: mainThreadResponsiveness.slice(),
          measurementEpochMs, measuredQueueMs,
        });
      },
      snapshot() { return {eventSourceReceive: eventSourceReceive.slice(), eventSourceApply: eventSourceApply.slice(),
        eventSourcePaths: eventSourcePaths.slice(), targetRawProgress: targetRawProgress.slice(),
        targetDomMutations: targetDomMutations.slice(), mainThreadResponsiveness: mainThreadResponsiveness.slice(),
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

async function readTargetId(page, name, timeoutMs, target = null) {
  const row = await visibleRowByName(page, name, timeoutMs);
  const isDirectory = await row.evaluate(node => Boolean(node.querySelector('.name img[src*="directory"]')));
  if (target?.kind === 'directory' && !isDirectory) {
    throw new Error('synthetic browser target row is not a directory');
  }
  if (target?.kind === 'directory') target.observed_kind = isDirectory ? 'directory' : 'file';
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
    is_dir: Boolean(node.querySelector('.name img[src*="directory"]')),
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
        is_dir: Boolean(node.querySelector('.name img[src*="directory"]')),
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
        is_dir: Boolean(node.querySelector('.name img[src*="directory"]')),
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

async function fetchSummaryForReconciliation(page, targetPairId, timeoutMs) {
  return page.evaluate(async ({pairId, requestTimeoutMs}) => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), Math.max(1, requestTimeoutMs));
    try {
      const response = await fetch('/server/model/v1/summary', {
        credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
      });
      if (response.status < 200 || response.status >= 300) {
        return {status: response.status, summary: null, target_path_pair_count: 0};
      }
      let summary = null;
      try { summary = await response.json(); }
      catch (_) { return {status: response.status, summary: null, schema_error: 'invalid-json'}; }
      if (!summary || typeof summary !== 'object' || Array.isArray(summary)) {
        return {status: response.status, summary: null, schema_error: 'summary-not-object'};
      }
      if (!Array.isArray(summary.path_pairs)) {
        return {status: response.status, summary: null, schema_error: 'path_pairs-not-array'};
      }
      const pathPairs = Array.isArray(summary?.path_pairs) ? summary.path_pairs : [];
      const targetPathPairCount = pathPairs.filter(pair =>
        typeof pair?.path_pair_id === 'string' && pair.path_pair_id === pairId).length;
      return {status: response.status, summary, target_path_pair_count: targetPathPairCount};
    } catch (error) {
      return {status: 0, summary: null, transport_error: String(error?.message || error || 'summary fetch failed')};
    } finally {
      clearTimeout(timer);
    }
  }, {pairId: targetPairId, requestTimeoutMs: Math.min(5_000, Math.max(1, timeoutMs))});
}

async function waitForPathPairReconciliation(page, target, timeoutMs, evidence) {
  const readiness = evidence.readiness;
  const reconciliation = readiness.reconciliation;
  reconciliation.attempted = true;
  const startedAt = Date.now();
  const deadline = startedAt + timeoutMs;
  while (Date.now() < deadline) {
    const remainingMs = Math.max(1, deadline - Date.now());
    const response = await fetchSummaryForReconciliation(page, target.pair_id, remainingMs);
    const status = Number(response?.status || 0);
    reconciliation.poll_count += 1;
    const observation = {
      http_status: status,
      target_path_pair_count: Number(response?.target_path_pair_count || 0),
      path_pair_id_match: false,
      reconciled_local: false,
      reconciled_remote: false,
    };
    if (response?.transport_error) observation.transport_error = safeText(response.transport_error);
    if (response?.schema_error) observation.schema_error = safeText(response.schema_error);
    if (status === 0) {
      const error = new Error(`path-pair reconciliation summary transport failure; Queue was not attempted`);
      error.failure_classification = 'reconciliation-summary-transport';
      error.response_reported = false;
      error.endpoint = '/server/model/v1/summary';
      error.transport_error = safeText(response.transport_error || 'summary fetch failed');
      boundedPush(reconciliation.observations, observation, MAX_RECONCILIATION_OBSERVATIONS);
      reconciliation.last = observation;
      reconciliation.failure_classification = error.failure_classification;
      readiness.pass = false;
      throw error;
    }
    if (status < 200 || status >= 300) {
      const error = new Error(`path-pair reconciliation summary HTTP ${status || 'unknown'}; Queue was not attempted`);
      error.failure_classification = 'reconciliation-summary-http';
      error.http_status = status;
      error.response_reported = status > 0;
      error.endpoint = '/server/model/v1/summary';
      boundedPush(reconciliation.observations, observation, MAX_RECONCILIATION_OBSERVATIONS);
      reconciliation.last = observation;
      reconciliation.failure_classification = error.failure_classification;
      readiness.pass = false;
      throw error;
    }
    if (response?.schema_error) {
      const error = new Error(`path-pair reconciliation summary schema invalid (${safeText(response.schema_error)}); Queue was not attempted`);
      error.failure_classification = 'reconciliation-summary-schema';
      error.http_status = status;
      error.response_reported = true;
      error.endpoint = '/server/model/v1/summary';
      error.schema_error = response.schema_error;
      boundedPush(reconciliation.observations, observation, MAX_RECONCILIATION_OBSERVATIONS);
      reconciliation.last = observation;
      reconciliation.failure_classification = error.failure_classification;
      readiness.pass = false;
      throw error;
    }
    const pairState = summaryPathPairReconciliation(response.summary, target.pair_id);
    Object.assign(observation, pairState);
    boundedPush(reconciliation.observations, observation, MAX_RECONCILIATION_OBSERVATIONS);
    reconciliation.last = observation;
    if (!pairState.path_pair_id_match) {
      const error = new Error(`target path pair identity was not unique in the reconciliation summary; Queue was not attempted`);
      error.failure_classification = 'reconciliation-summary-schema';
      error.http_status = status;
      error.response_reported = true;
      error.endpoint = '/server/model/v1/summary';
      error.schema_error = 'target-pair-not-unique';
      reconciliation.failure_classification = error.failure_classification;
      readiness.pass = false;
      throw error;
    }
    if (!pairState.fields_valid) {
      const error = new Error('target path pair reconciliation flags were not boolean; Queue was not attempted');
      error.failure_classification = 'reconciliation-summary-schema';
      error.http_status = status;
      error.response_reported = true;
      error.endpoint = '/server/model/v1/summary';
      error.schema_error = 'reconciliation-flags-not-boolean';
      reconciliation.failure_classification = error.failure_classification;
      readiness.pass = false;
      throw error;
    }
    if (pairState.reconciled_local === true && pairState.reconciled_remote === true) {
      reconciliation.pass = true;
      reconciliation.elapsed_ms = Date.now() - startedAt;
      return observation;
    }
    await page.waitForTimeout(Math.min(RECONCILIATION_POLL_INTERVAL_MS, Math.max(1, deadline - Date.now())));
  }
  const error = new Error('target path pair did not report local and remote reconciliation before Queue timeout');
  error.failure_classification = 'reconciliation-timeout';
  error.response_reported = reconciliation.poll_count > 0;
  reconciliation.failure_classification = error.failure_classification;
  reconciliation.elapsed_ms = Date.now() - startedAt;
  readiness.pass = false;
  throw error;
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

function requireAcceptedActionResponse(response, name, endpointAction) {
  const status = Number(response?.status?.());
  if (status >= 200 && status < 300) return status;
  const error = new Error(`${name} action HTTP ${Number.isFinite(status) ? status : 'unknown'} rejected; waitForState skipped`);
  error.failure_classification = 'action-http-rejected';
  error.action = name;
  error.endpoint_action = endpointAction;
  error.http_status = Number.isFinite(status) ? status : 0;
  error.response_reported = true;
  throw error;
}

async function waitForActiveMaterialization(page, targetId, targetName, timeoutMs) {
  await page.waitForFunction(({id, name}) => {
    const timeline = window.__seedSyncPerfTimeline?.snapshot?.() || {};
    const row = Array.from(document.querySelectorAll('#file-list .file')).find(item => id
      ? item.getAttribute('data-file-id') === id
      : item.querySelector('.name .title')?.textContent?.trim() === name);
    if (!row) return false;
    const status = row.querySelector('.status .text')?.textContent?.trim().toLowerCase()
      || row.querySelector('.status img[id]')?.id;
    const stop = Array.from(row.querySelectorAll('.actions .button')).find(item =>
      item.textContent?.trim().includes('Stop'));
    const stopEnabled = Boolean(stop && !stop.disabled && stop.getAttribute('aria-disabled') !== 'true');
    const raw = (Array.isArray(timeline.targetRawProgress) ? timeline.targetRawProgress : [])
      .filter(item => item?.transferred_size != null
        && Number.isFinite(Number(item.transferred_size))
        && Number.isFinite(Number(item.t_ms)));
    let previousRaw = null;
    let rawAdvancing = false;
    for (const sample of raw.sort((a, b) => Number(a.t_ms) - Number(b.t_ms))) {
      const value = Number(sample.transferred_size);
      if (previousRaw != null && value > previousRaw) {
        rawAdvancing = true;
        break;
      }
      previousRaw = value;
    }
    const terminal = ['stopped', 'downloaded', 'complete', 'completed'].includes(status);
    return !terminal && (rawAdvancing || status === 'downloading' || stopEnabled);
  }, {id: targetId, name: targetName}, {timeout: timeoutMs});
  // The aggregate is intentionally long-lived; allow the visible formatter
  // enough wall time to produce 21 distinct size_info values before stopping.
  await waitForActiveProgressSamples(page, Math.max(timeoutMs, 90_000));
}

async function waitForActiveProgressSamples(page, timeoutMs) {
  await page.waitForFunction(minimumSamples => {
    const timeline = window.__seedSyncPerfTimeline?.snapshot?.() || {};
    const queueMarker = timeline.measuredQueueMs;
    if (typeof queueMarker !== 'number' || !Number.isFinite(queueMarker)) return false;
    const source = Array.isArray(timeline.targetDomMutations) ? timeline.targetDomMutations : [];
    const parseVisibleSize = value => {
      const match = String(value || '').trim().match(
        /^([0-9]+(?:[.,][0-9]+)?)\s*(B|KB|MB|GB|TB|PB)\b/i,
      );
      if (!match) return null;
      const amount = Number(match[1].replace(',', '.'));
      const units = {B: 0, KB: 1, MB: 2, GB: 3, TB: 4, PB: 5};
      const unit = units[String(match[2]).toUpperCase()];
      return Number.isFinite(amount) && unit != null ? amount * (1024 ** unit) : null;
    };
    let previousActive = null;
    let genuineVisibleSizeSamples = 0;
    let regression = false;
    for (const sample of source
      .filter(item => Number.isFinite(Number(item?.t_ms)) && Number(item.t_ms) >= queueMarker)
      .sort((a, b) => Number(a.t_ms) - Number(b.t_ms))) {
      const sizeInfo = String(sample && sample.size_info || '').trim();
      const sizeBytes = parseVisibleSize(sizeInfo);
      const status = String(sample && sample.status || '').toLowerCase();
      const terminal = ['stopped', 'downloaded', 'complete', 'completed'].includes(status);
      if (terminal) {
        previousActive = null;
        continue;
      }
      // Status-only mutations do not advance the rendered size and must not
      // break the interval to the next genuine visible change.
      if (sizeBytes == null) continue;
      if (sizeBytes === 0) {
        if (previousActive != null) regression = true;
        previousActive = null;
        continue;
      }
      if (previousActive == null) {
        previousActive = sizeBytes;
        genuineVisibleSizeSamples += 1;
      } else if (sizeBytes > previousActive) {
        previousActive = sizeBytes;
        genuineVisibleSizeSamples += 1;
      } else if (sizeBytes < previousActive) {
        regression = true;
        previousActive = null;
      }
    }
    return !regression && genuineVisibleSizeSamples >= minimumSamples;
  }, MIN_ACTIVE_PROGRESS_SAMPLES, {timeout: timeoutMs});
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

async function clickAction(page, target, targetId, name, endpointAction, allowedStates, timeoutMs,
  confirm = false, recordName = null, onAccepted = null) {
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
    const httpStatus = requireAcceptedActionResponse(response, name, endpointAction);
    const acceptedMarker = typeof onAccepted === 'function'
      ? await onAccepted({response, httpStatus, clickAt, responseAt}) : null;
    const renderedState = await waitForState(page, targetId, target.name, allowedStates, timeoutMs);
    const renderedAt = await page.evaluate(() => performance.now());
    const result = {
      name: recordName || (name === 'Delete Local' ? 'delete_local' : name.toLowerCase()), measured: true,
      pre_action: preAction, endpoint_action: endpointAction, http_status: httpStatus, response_reported: true,
      confirmation_identity_match: confirmationIdentity.identity_match,
      rendered_state: renderedState, click_to_http_response_ms: Number((responseAt - clickAt).toFixed(3)),
      click_to_rendered_state_ms: Number((renderedAt - clickAt).toFixed(3)),
      confirmation_open_to_click_ms: Number((clickAt - confirmationOpenedAt).toFixed(3)),
      confirmation_click_to_http_response_ms: Number((responseAt - clickAt).toFixed(3)),
      confirmation_click_to_rendered_state_ms: Number((renderedAt - clickAt).toFixed(3)),
    };
    if (acceptedMarker != null) result.accepted_marker_t_ms = acceptedMarker;
    return result;
  }
  const clickAt = await page.evaluate(() => performance.now());
  const clickIdentity = await verifyTargetIdentity(row, targetId, target.name);
  const responsePromise = page.waitForResponse(responseMatcher(endpointAction), {timeout: timeoutMs});
  await button.click();
  const response = await responsePromise;
  const responseAt = await page.evaluate(() => performance.now());
  const httpStatus = requireAcceptedActionResponse(response, name, endpointAction);
  const acceptedMarker = typeof onAccepted === 'function'
    ? await onAccepted({response, httpStatus, clickAt, responseAt}) : null;
  const renderedState = await waitForState(page, targetId, target.name, allowedStates, timeoutMs);
  const renderedAt = await page.evaluate(() => performance.now());
  const result = {
    name: recordName || (name === 'Queue' ? 'queue' : name.toLowerCase()), measured: true,
    pre_action: preAction,
    click_identity_match: clickIdentity.identity_match,
    endpoint_action: endpointAction, http_status: httpStatus, response_reported: true,
    rendered_state: renderedState, click_to_http_response_ms: Number((responseAt - clickAt).toFixed(3)),
    click_to_rendered_state_ms: Number((renderedAt - clickAt).toFixed(3)),
  };
  if (acceptedMarker != null) result.accepted_marker_t_ms = acceptedMarker;
  return result;
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
  return localAbsentControlsAreReady(precondition);
}

function localAbsentControlsAreReady(precondition) {
  return precondition?.controls?.Queue?.enabled === true
    && precondition?.controls?.['Delete Local']?.enabled !== true;
}

function directoryReadinessIsFresh(precondition) {
  return Boolean(precondition && String(precondition.status || '') === FRESH_REMOTE_ONLY_STATE
    && precondition.controls?.Queue?.enabled === true
    && precondition.controls?.Stop?.enabled !== true);
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
    precondition = await waitForNormalizationPrecondition(page, targetId, target.name, timeoutMs);
    readiness.initial_precondition = precondition;
    for (let index = 0; index <= MAX_READINESS_STEPS; index += 1) {
      if (readinessIsQueueable(precondition)) {
        readiness.final_precondition = precondition;
        readiness.normalized = true;
        readiness.pass = true;
        return;
      }
      if (index === MAX_READINESS_STEPS) {
        throw invalidPrecondition('synthetic browser target did not reach remote-only queueable readiness', precondition);
      }
      let actionName = null;
      let endpointAction = null;
      let allowedStates = null;
      let confirm = false;
      if (precondition.controls?.Stop?.enabled === true) {
        actionName = 'Stop'; endpointAction = 'stop'; allowedStates = ['stopped'];
      } else if (precondition.controls?.['Delete Local']?.enabled === true) {
        actionName = 'Delete Local'; endpointAction = 'delete_local';
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
      precondition = await waitForNormalizationPrecondition(page, targetId, target.name, timeoutMs);
    }
  } catch (error) {
    readiness.final_precondition = precondition || error.precondition || null;
    readiness.failure_classification = error.failure_classification || 'probe';
    readiness.pass = false;
    throw error;
  }
}

async function establishFreshReadiness(page, targetId, targetName, timeoutMs, evidence) {
  const readiness = evidence.readiness;
  readiness.attempted = true;
  try {
    const precondition = await readActionPrecondition(page, targetId, targetName, timeoutMs);
    readiness.initial_precondition = precondition;
    readiness.final_precondition = precondition;
    if (!directoryReadinessIsFresh(precondition)) {
      throw invalidPrecondition(
        `synthetic directory target must be fresh remote-only before Queue; observed ${precondition.status || 'unknown'}`,
        precondition,
      );
    }
    readiness.pass = true;
  } catch (error) {
    readiness.final_precondition = error.precondition || readiness.final_precondition || null;
    readiness.failure_classification = error.failure_classification || 'probe';
    readiness.pass = false;
    throw error;
  }
}

async function establishReadiness(page, target, targetId, timeoutMs, evidence) {
  if (target.kind === 'directory') {
    return establishFreshReadiness(page, targetId, target.name, timeoutMs, evidence);
  }
  return normalizeTarget(page, target, targetId, timeoutMs, evidence);
}

function recordCleanupError(evidence, record, step, error) {
  const item = {step, ...errorRecord(`cleanup-${step}`, error)};
  boundedPush(record.errors, item, MAX_ERRORS);
  boundedPush(evidence.errors, item, MAX_ERRORS);
}

async function cleanupDirectoryTarget(page, target, targetId, timeoutMs, evidence, record) {
  record.attempted = true;
  let observedState = null;
  try { observedState = await readTargetState(page, targetId, target.name, timeoutMs); }
  catch (error) {
    record.observed_state_error = errorRecord('cleanup-observe', error);
    recordCleanupError(evidence, record, 'observe', error);
  }
  record.observed_state = observedState?.status || null;
  record.observed_identity = observedState?.identity || null;
  const actionAccepted = record.queue_accepted === true || record.mutation_accepted === true;
  if (!actionAccepted) {
    const quiescent = Boolean(observedState && directoryReadinessIsFresh(observedState));
    record.residual_state = observedState?.status || null;
    record.residual_identity = observedState?.identity || null;
    record.residual_remote_only_queueable = quiescent;
    record.residual_local_absent = quiescent;
    record.transfer_quiescent = quiescent;
    record.noop = quiescent;
    if (!quiescent) {
      recordCleanupError(evidence, record, 'rejected-queue-state',
        new Error('rejected Queue did not leave the directory target remote-only and quiescent'));
    }
    record.pass = cleanupPass(record);
    return;
  }
  const stopRequired = !observedState || observedState.status !== 'stopped'
    || observedState.controls?.Stop?.enabled === true;
  if (stopRequired) {
    const stopStep = {name: 'cleanup_stop', measured: false, attempted: true};
    try {
      const waitMs = await waitForEnabledAction(page, targetId, target.name, 'Stop', timeoutMs);
      const result = await clickAction(page, target, targetId, 'Stop', 'stop', ['stopped'], timeoutMs);
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
  try {
    const residual = await readTargetState(page, targetId, target.name, timeoutMs);
    record.residual_state = residual?.status || null;
    record.residual_identity = residual?.identity || null;
    record.transfer_quiescent = residual?.status === 'stopped'
      && residual?.controls?.Stop?.enabled !== true;
  }
  catch (error) {
    record.residual_state = null;
    record.transfer_quiescent = false;
    recordCleanupError(evidence, record, 'residual-state', error);
  }
  record.pass = cleanupPass(record);
}

async function cleanupLegacyTarget(page, target, targetId, timeoutMs, evidence, record) {
  record.attempted = true;
  let observedState = null;
  try { observedState = await readTargetState(page, targetId, target.name, timeoutMs); }
  catch (error) {
    record.observed_state_error = errorRecord('cleanup-observe', error);
    recordCleanupError(evidence, record, 'observe', error);
  }
  record.observed_state = observedState?.status || null;
  record.observed_identity = observedState?.identity || null;
  const stopRequired = !observedState || ['queued', 'downloading', 'extracting'].includes(observedState.status)
    || (Number.isFinite(observedState.progress) && observedState.progress > 0 && observedState.progress < 100);
  if (stopRequired) {
    const stopStep = {name: 'cleanup_stop', measured: false, attempted: true};
    try {
      const waitMs = await waitForEnabledAction(page, targetId, target.name, 'Stop', timeoutMs);
      const result = await clickAction(page, target, targetId, 'Stop', 'stop', ['stopped'], timeoutMs, false);
      result.name = 'cleanup_stop'; result.measured = false; result.control_enabled_wait_ms = waitMs;
      Object.assign(stopStep, result, {completed: true});
    } catch (error) {
      stopStep.completed = false; stopStep.error = errorRecord('cleanup-stop', error);
      recordCleanupError(evidence, record, 'stop', error);
    }
    record.steps.push(stopStep);
  } else {
    record.steps.push({name: 'cleanup_stop', measured: false, attempted: false, skipped: true,
      reason: `target state was ${observedState?.status || 'unknown'}`});
  }
  let deleteCompleted = readinessIsQueueable(observedState);
  if (!deleteCompleted) {
    const deleteStep = {name: 'cleanup_delete_local', measured: false, attempted: true};
    try {
      await waitForEnabledAction(page, targetId, target.name, 'Delete Local', timeoutMs);
      const result = await clickAction(page, target, targetId, 'Delete Local', 'delete_local', LOCAL_ABSENT_STATES, timeoutMs, true);
      result.name = 'cleanup_delete_local'; result.measured = false;
      Object.assign(deleteStep, result, {completed: true});
      deleteCompleted = true;
    } catch (error) {
      deleteStep.completed = false; deleteStep.error = errorRecord('cleanup-delete-local', error);
      recordCleanupError(evidence, record, 'delete_local', error);
    }
    record.steps.push(deleteStep);
  } else {
    record.steps.push({name: 'cleanup_delete_local', measured: false, attempted: false, skipped: true,
      reason: 'target was already deleted'});
  }
  try {
    const residual = await readTargetState(page, targetId, target.name, timeoutMs);
    record.residual_state = residual?.status || null;
    record.residual_identity = residual?.identity || null;
    record.residual_local_absent = readinessIsQueueable(residual);
  } catch (error) {
    record.residual_state = null; record.residual_local_absent = false;
    recordCleanupError(evidence, record, 'residual-state', error);
  }
  if (deleteCompleted && record.residual_local_absent) record.restored_state = record.residual_state;
  else if (!record.errors.length && !record.residual_local_absent) {
    recordCleanupError(evidence, record, 'restore', new Error('target was not restored to a local-absent state'));
  }
  record.pass = cleanupPass(record);
}

async function cleanupTarget(page, target, targetId, timeoutMs, evidence, record) {
  if (target.kind === 'directory') return cleanupDirectoryTarget(page, target, targetId, timeoutMs, evidence, record);
  return cleanupLegacyTarget(page, target, targetId, timeoutMs, evidence, record);
}

async function exerciseActions(page, target, targetId, timeoutMs, evidence, scopedPath, captureBeforeCleanup) {
  const actions = evidence.actions;
  const legacy = target.kind !== 'directory';
  const cleanupRecord = {name: 'cleanup', measured: false, required: false, attempted: false,
    profile: legacy ? 'legacy-file' : 'cadence-directory',
    steps: [], errors: [], observed_state: null, restored_state: null, residual_state: null,
    residual_local_absent: false, residual_remote_only_queueable: false, transfer_quiescent: false,
    queue_accepted: false, mutation_accepted: false, noop: false, pass: false};
  try {
    // Legacy normalization can mutate a reused file target, so its cleanup
    // obligation starts before readiness. The cadence directory only accepts
    // a fresh remote-only row and begins cleanup after that proof.
    if (legacy) {
      cleanupRecord.required = true;
      evidence.cleanup.required = true;
    }
    await establishReadiness(page, target, targetId, timeoutMs, evidence);
    await attachTargetObserver(page, targetId, target.name, scopedPath);
    if (target.kind === 'directory') {
      await waitForPathPairReconciliation(page, target, timeoutMs, evidence);
    }
    const measurementEpoch = await page.evaluate(() =>
      window.__seedSyncPerfTimeline?.beginMeasurement?.() ?? null);
    evidence.measurement.epoch_t_ms = measurementEpoch?.epoch_t_ms ?? null;
    evidence.measurement.post_readiness = true;
    cleanupRecord.required = true;
    evidence.cleanup.required = true;
    const queueAction = await clickAction(
      page, target, targetId, 'Queue', 'queue', ['queued', 'downloading'], timeoutMs, false, 'queue',
      async () => {
        cleanupRecord.queue_accepted = true;
        cleanupRecord.mutation_accepted = true;
        const marker = await page.evaluate(() =>
          window.__seedSyncPerfTimeline?.markMeasuredQueue?.() ?? null);
        if (!finiteMeasurementMarker(marker)) {
          const error = new Error('Queue was accepted but the measured-Queue timeline marker was unavailable');
          error.failure_classification = 'measurement-boundary';
          error.action = 'Queue';
          error.endpoint_action = 'queue';
          error.response_reported = true;
          throw error;
        }
        return marker;
      },
    );
    actions.push(queueAction);
    evidence.measurement.measured_queue_t_ms = queueAction.accepted_marker_t_ms ?? null;
    await waitForActiveMaterialization(page, targetId, target.name, timeoutMs);
    const stopControlWaitMs = await waitForEnabledAction(page, targetId, target.name, 'Stop', timeoutMs);
    const stopAction = await clickAction(page, target, targetId, 'Stop', 'stop', ['stopped'], timeoutMs, false);
    stopAction.control_enabled_wait_ms = stopControlWaitMs;
    actions.push(stopAction);
    await page.evaluate(() => window.__seedSyncPerfTimeline?.markTransferEnded?.());
    if (legacy) {
      await waitForEnabledAction(page, targetId, target.name, 'Delete Local', timeoutMs);
      actions.push(await clickAction(
        page, target, targetId, 'Delete Local', 'delete_local', LOCAL_ABSENT_STATES, timeoutMs, true,
      ));
      actions.push(await clickAction(
        page, target, targetId, 'Queue', 'queue', ['queued', 'downloading'], timeoutMs, false, 'requeue',
      ));
    }
  } finally {
    // This must remain before Stop/Delete Local cleanup: the poll lineage is
    // intentionally about the active target, while cleanup creates a later
    // idle status that is not causal evidence for the observed cadence gap.
    if (typeof captureBeforeCleanup === 'function') {
      try { await captureBeforeCleanup(); }
      catch (error) { boundedPush(evidence.errors, errorRecord('breadcrumbs-before-cleanup', error), MAX_ERRORS); }
    }
    if (cleanupRecord.required) {
      await cleanupTarget(page, target, targetId, timeoutMs, evidence, cleanupRecord);
      evidence.cleanup = cleanupRecord;
      actions.push(cleanupRecord);
    }
  }
}

async function captureBreadcrumbsBeforeCleanup(baseUrl, apiToken, outputPath, timeoutMs) {
  if (!outputPath) return;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), Math.max(1, Math.min(timeoutMs, 20_000)));
  try {
    const response = await fetch(new URL('/server/breadcrumbs/get?limit=1', baseUrl), {
      headers: {Authorization: `Bearer ${apiToken}`}, cache: 'no-store', signal: controller.signal,
    });
    if (!response.ok) throw new Error(`breadcrumbs snapshot returned HTTP ${response.status}`);
    const payload = await response.json();
    // Persist only the bounded, privacy-filtered diagnostic subtrees needed
    // to explain an active gap; never copy ordinary breadcrumb entries.
    const snapshot = {
      schema: 'seedsync.performance-lab.progress-lineage-capture.v1',
      version: Number.isInteger(payload?.version) ? payload.version : null,
      reset_generation: Number.isInteger(payload?.reset_generation) ? payload.reset_generation : null,
      last_reset_reason: typeof payload?.last_reset_reason === 'string' ? payload.last_reset_reason : null,
      progress_lineage: payload?.progress_lineage || null,
      progress_lineage_health: payload?.progress_lineage_health || null,
      root_progress_health: payload?.root_progress_health || null,
    };
    const destination = path.resolve(outputPath);
    fs.mkdirSync(path.dirname(destination), {recursive: true});
    fs.writeFileSync(destination, `${JSON.stringify(snapshot, null, 2)}\n`, {encoding: 'utf8', mode: 0o600});
    try { fs.chmodSync(destination, 0o600); } catch (_) { /* best effort */ }
  } finally {
    clearTimeout(timer);
  }
}

function finalizeEvidence(evidence) {
  const measuredQueueTMs = evidence.measurement?.measured_queue_t_ms;
  const allTargetMutations = Array.isArray(evidence.samples.target_dom_mutations)
    ? evidence.samples.target_dom_mutations : [];
  const targetMutations = measuredTargetMutations(allTargetMutations, measuredQueueTMs);
  evidence.samples.target_dom_mutations = targetMutations.slice(0, MAX_MUTATIONS);
  evidence.measurement.sample_count = evidence.samples.target_dom_mutations.length;
  const cadenceSummary = cadence(evidence.samples.target_dom_mutations);
  const visibleProgressSummary = progressGap(evidence.samples.target_dom_mutations);
  const visibleSizeSummary = visibleSizeGap(evidence.samples.target_dom_mutations);
  const rawProgress = measuredTargetMutations(
    Array.isArray(evidence.samples.target_raw_progress) ? evidence.samples.target_raw_progress : [],
    measuredQueueTMs,
  ).slice(0, MAX_MUTATIONS);
  evidence.samples.target_raw_progress = rawProgress;
  const rawProgressSummary = progressGapForField(rawProgress, 'transferred_size');
  const heartbeats = (Array.isArray(evidence.samples.main_thread_responsiveness)
    ? evidence.samples.main_thread_responsiveness : [])
    .filter(sample => Number(sample?.t_ms) >= Number(measuredQueueTMs));
  evidence.samples.main_thread_responsiveness = heartbeats.slice(0, MAX_MAIN_THREAD_HEARTBEATS);
  const responsivenessSummary = mainThreadResponsiveness(evidence.samples.main_thread_responsiveness);
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
  evidence.samples.visible_progress = evidence.samples.progress;
  evidence.samples.visible_size_info = evidence.samples.target_dom_mutations
    .filter(sample => sample && sample.size_info != null)
    .map(sample => ({t_ms: sample.t_ms, status: sample.status, size_info: sample.size_info}));
  evidence.statistics.action_http_response = actionStats(evidence.actions, 'click_to_http_response_ms');
  evidence.statistics.action_rendered_state = actionStats(evidence.actions, 'click_to_rendered_state_ms');
  evidence.statistics.progress_gap = visibleSizeSummary;
  evidence.statistics.visible_size_info_gap = visibleSizeSummary;
  evidence.statistics.visible_progress_gap = visibleProgressSummary;
  evidence.statistics.raw_progress_gap = rawProgressSummary;
  evidence.statistics.browser_main_thread_responsiveness = responsivenessSummary;
  evidence.max_progress_gap_ms = visibleSizeSummary.max_ms;
  evidence.cycles = evidence.actions;
  evidence.thresholds.target_dom_p95_ms.observed_ms = receiveToDom.p95_ms;
  evidence.thresholds.target_dom_p95_ms.pass = receiveToDom.p95_ms != null && receiveToDom.p95_ms <= 200;
  evidence.thresholds.target_dom_max_ms.observed_ms = receiveToDom.max_ms;
  evidence.thresholds.target_dom_max_ms.pass = receiveToDom.max_ms != null && receiveToDom.max_ms <= 500;
  const progressAcceptance = progressGapAcceptance(visibleSizeSummary);
  const rawProgressAcceptance = progressGapAcceptance(rawProgressSummary);
  evidence.thresholds.minimum_progress_gap_count.observed = visibleSizeSummary.gap_count;
  evidence.thresholds.minimum_progress_gap_count.pass = progressAcceptance.minimum_gap_count;
  evidence.thresholds.progress_gap_p50_ms.observed_ms = visibleSizeSummary.p50_ms;
  evidence.thresholds.progress_gap_p50_ms.pass = progressAcceptance.p50;
  evidence.thresholds.progress_gap_p95_ms.observed_ms = visibleSizeSummary.p95_ms;
  evidence.thresholds.progress_gap_p95_ms.pass = progressAcceptance.p95;
  evidence.thresholds.max_progress_gap_ms.observed_ms = visibleSizeSummary.max_ms;
  evidence.thresholds.max_progress_gap_ms.pass = progressAcceptance.max;
  evidence.thresholds.progress_monotonic.observed = {
    monotonic: visibleSizeSummary.monotonic,
    regression_count: visibleSizeSummary.regression_count,
  };
  evidence.thresholds.progress_monotonic.pass = progressAcceptance.monotonic;
  evidence.thresholds.raw_progress_monotonic.observed = {
    monotonic: rawProgressSummary.monotonic,
    regression_count: rawProgressSummary.regression_count,
    sample_count: rawProgressSummary.sample_count,
  };
  evidence.thresholds.raw_progress_monotonic.pass = rawProgressSummary.sample_count > 0
    && rawProgressAcceptance.monotonic;
  evidence.thresholds.main_thread_responsiveness.observed = responsivenessSummary;
  evidence.thresholds.main_thread_responsiveness.pass = responsivenessSummary.count >= MIN_MAIN_THREAD_HEARTBEATS
    && responsivenessSummary.max_drift_ms != null
    && responsivenessSummary.max_drift_ms <= MAIN_THREAD_DRIFT_LIMIT_MS;
  const measurementBoundary = measurementBoundaryValid(evidence.measurement);
  evidence.thresholds.measurement_boundary.observed = {
    epoch_t_ms: evidence.measurement.epoch_t_ms,
    measured_queue_t_ms: evidence.measurement.measured_queue_t_ms,
    post_readiness: evidence.measurement.post_readiness,
  };
  evidence.thresholds.measurement_boundary.pass = measurementBoundary;
  const legacy = evidence.target?.kind !== 'directory';
  const requiredActions = legacy ? ['queue', 'stop', 'delete_local', 'requeue'] : ['queue', 'stop'];
  const rendered = evidence.statistics.action_rendered_state;
  evidence.thresholds.action_rendered_state_p95_ms.observed = {
    queue: rendered.queue || null, stop: rendered.stop || null,
    delete_local: rendered.delete_local || null, requeue: rendered.requeue || null,
  };
  evidence.thresholds.action_rendered_state_p95_ms.pass = requiredActions.every(action => {
    const stat = rendered[action];
    return stat && stat.p95_ms != null && stat.p95_ms <= ACTION_RENDER_LIMITS_MS[action];
  });
  const measured = evidence.actions.filter(action => action && action.measured !== false && !action.steps);
  evidence.thresholds.action_http_response_reported.pass = measured.length >= requiredActions.length
    && measured.every(action => action.response_reported === true && Number(action.http_status) >= 200 && Number(action.http_status) < 300);
  evidence.thresholds.browser_errors.observed = evidence.errors.length;
  evidence.thresholds.browser_errors.pass = evidence.errors.length === 0;
  evidence.cleanup.pass = cleanupPass({...evidence.cleanup, profile: legacy ? 'legacy-file' : 'cadence-directory'});
  evidence.pass = evidence.thresholds.target_dom_p95_ms.pass
    && evidence.thresholds.target_dom_max_ms.pass
    && evidence.thresholds.minimum_progress_gap_count.pass
    && evidence.thresholds.progress_gap_p50_ms.pass
    && evidence.thresholds.progress_gap_p95_ms.pass
    && evidence.thresholds.max_progress_gap_ms.pass
    && evidence.thresholds.progress_monotonic.pass
    && evidence.thresholds.raw_progress_monotonic.pass
    && evidence.thresholds.main_thread_responsiveness.pass
    && evidence.thresholds.measurement_boundary.pass
    && evidence.thresholds.action_rendered_state_p95_ms.pass
    && evidence.thresholds.action_http_response_reported.pass
    && evidence.readiness.pass
    && evidence.cleanup.pass
    && evidence.thresholds.browser_errors.pass;
  if (evidence.pass) evidence.failure_classification = 'none';
}

function measuredTargetMutations(samples, measuredQueueTMs) {
  const values = Array.isArray(samples) ? samples : [];
  return finiteMeasurementMarker(measuredQueueTMs)
    ? values.filter(sample => Number(sample?.t_ms) >= measuredQueueTMs)
    : [];
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
