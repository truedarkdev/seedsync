#!/usr/bin/env node
/* Browser evidence for the full upgrade lane.  Requires an existing Playwright
 * installation; this file intentionally does not add an npm dependency. */
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { inflateSync } from 'node:zlib';
import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';

const shutdownTimeoutMs = 5000;

async function closeWithinDeadline(close) {
  let timer;
  try {
    return await Promise.race([
      Promise.resolve().then(close),
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('browser shutdown timed out')), shutdownTimeoutMs); }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

if (process.argv[2] === '--shutdown-self-check') {
  const mode = process.argv[3];
  try {
    await closeWithinDeadline(() => mode === 'timeout' ? new Promise(() => {}) : Promise.reject(new Error('simulated close failure')));
    throw new Error('shutdown self-check unexpectedly completed');
  } catch (error) {
    process.stdout.write(JSON.stringify({ fallback: true, reason: error.message }) + '\n');
    process.exit(0);
  }
}

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.SEEDSYNC_PLAYWRIGHT_MODULE || 'playwright');

if (process.argv[2] === '--dispatch-check') {
  process.stdout.write(JSON.stringify({ interpreter: process.execPath, playwright: require.resolve(process.env.SEEDSYNC_PLAYWRIGHT_MODULE || 'playwright') }) + '\n');
  process.exit(0);
}

const [baseUrl, evidenceDir, mode = 'claim'] = process.argv.slice(2);
if (!baseUrl || !evidenceDir) throw new Error('usage: ship_readiness_browser.mjs <base-url> <evidence-dir>');
const evidenceHelper = process.env.SEEDSYNC_SHIP_EVIDENCE_HELPER;
if (!evidenceHelper) throw new Error('SEEDSYNC_SHIP_EVIDENCE_HELPER is required for retained browser evidence');
const screenshotRunId = process.env.SEEDSYNC_SHIP_RUN_ID;
if (!/^[a-z0-9][a-z0-9_-]{0,31}$/.test(screenshotRunId || '')) {
  throw new Error('SEEDSYNC_SHIP_RUN_ID is required for screenshot safety evidence');
}
fs.mkdirSync(evidenceDir, { recursive: true, mode: 0o700 });
fs.chmodSync(evidenceDir, 0o700);

const navigationTimeout = 20000;
const readinessTimeout = 20000;
const fixtureNames = ['default-remote-only.bin', 'root-directory-stopped', 'Nested Set', 'auto-extract.zip'];
const legacyMode = mode === 'legacy' || mode === 'legacy-restore';
const recoveryHandoverMode = process.env.SEEDSYNC_BROWSER_HANDOVER_RECOVERY === '1';
let evidenceName = mode === 'legacy-restore' ? 'browser-legacy-restore.json' : mode === 'legacy' ? 'browser-legacy.json' : mode === 'reuse' ? 'browser-reuse.json' : 'browser.json';
const errors = [];
const runtimeErrors = [];
const diagnosticFailures = [];
const expectedTransitions = [];
const streamConnections = [];
const streamTransitionEvidence = [];
const apiEvidence = {};
const navigation = [];
const browserStartedAt = Date.now();
const sseReconnectMinimumMs = 2800;
// The client retries SSE after three seconds.  Under container restart/load the
// observed recovery is later than one retry, so allow bounded state progress
// through several retry opportunities instead of accepting a fixed page-age.
const sseReconnectMaximumMs = 25000;
const sseRecoveryPollMs = 200;
const sseStabilityWindowMs = 1500;
const secretExposureSelector = [
  'input', 'textarea', '[contenteditable="true"]', '.secret-value',
  '[class*="password" i]', '[class*="secret" i]', '[class*="token" i]', '[class*="api" i]', '[class*="credential" i]',
  '[aria-label*="password" i]', '[aria-label*="secret" i]', '[aria-label*="token" i]', '[aria-label*="api" i]', '[aria-label*="credential" i]',
].join(', ');
const suppressedSecretFailure = 'secret-bearing UI state detected; rendered diagnostics suppressed';
const screenshotSafetyPolicyVersion = 1;
const privateScreenshotRoot = process.env.SEEDSYNC_SHIP_PRIVATE_SCREENSHOT_ROOT || null;
let secretExposureDetected = false;
let browser;
let claimCompletedAtMs = null;
let browserErrorGeneration = 0;
let context;
let page;
let shutdownRequested = false;
let claimClassification = 'not-applicable';
let claimPhase = 'not-started';
// Observe first-claim SSE recovery while the shell continues its work.  The
// later stability request consumes this in-flight/validated result instead of
// restarting an error-relative SLA window after its deadline.
let firstClaimSseRecoveryPromise = null;
let validatedFirstClaimSseRecovery = null;
let firstClaimSseRecoveryFailure = null;

function redact(value) {
  const result = spawnSync('python', [evidenceHelper, 'redact-stdin'], {
    input: String(value || ''), encoding: 'utf8', windowsHide: true,
  });
  return result.status === 0 ? result.stdout : 'diagnostic unavailable';
}

async function writeEvidence(payload, name = evidenceName) {
  await flushBrowserDiagnostics();
  const target = path.join(evidenceDir, name);
  const mergedApiEvidence = { ...apiEvidence, ...(payload.api && typeof payload.api === 'object' ? payload.api : {}) };
  fs.writeFileSync(target, redact(JSON.stringify({ errors, runtimeErrors, diagnosticFailures, expectedTransitions, streamConnections, streamTransitionEvidence, firstClaimSseRecovery: validatedFirstClaimSseRecovery, firstClaimSseRecoveryFailure, navigation, ...payload, api: mergedApiEvidence }, null, 2)), { mode: 0o600 });
  fs.chmodSync(target, 0o600);
}

// Keep the remembered-browser cookie only in this private session workspace.
// The shell removes this exact temporary profile after the session group is
// reaped, so no cookie database becomes retained verifier evidence.
const temporaryProfileDir = process.env.SEEDSYNC_BROWSER_PROFILE_DIR;
if (!temporaryProfileDir || !path.isAbsolute(temporaryProfileDir)) {
  throw new Error('SEEDSYNC_BROWSER_PROFILE_DIR must name an absolute temporary profile directory');
}
fs.mkdirSync(temporaryProfileDir, { recursive: true, mode: 0o700 });
fs.chmodSync(temporaryProfileDir, 0o700);
context = await chromium.launchPersistentContext(temporaryProfileDir, { headless: true });
browser = context.browser();
page = await context.newPage();
page.on('console', message => {
  if (message.type() === 'error') captureBrowserDiagnostic('console-error', () => ({
    message: message.text(), source: message.type(), location: message.location(),
  }));
});
page.on('pageerror', error => captureBrowserDiagnostic('pageerror', () => ({
  message: error.message, source: error.name || 'Error', location: null,
})));
page.on('response', response => {
  try {
    const url = new URL(response.url());
    if (url.pathname !== '/server/stream') return;
    const contentType = response.headers()['content-type'] || '';
    const connection = {
      observedAfterMs: Date.now() - browserStartedAt,
      status: response.status(),
      contentType: contentType.startsWith('text/event-stream') ? 'text/event-stream' : 'other',
    };
    streamConnections.push(connection);
    if (connection.status === 200 && connection.contentType === 'text/event-stream') {
      void classifyRecoveredFirstClaimSseTransition();
    }
  } catch {
    diagnosticFailures.push({ kind: 'stream-response', classification: 'response-lifecycle-capture-failed', source: 'unavailable', location: 'unavailable', message: 'redacted diagnostic unavailable' });
  }
});

async function closeBrowserResources() {
  await closeWithinDeadline(async () => {
    if (context) {
      await context.close();
      context = undefined;
    }
    if (browser?.isConnected()) await browser.close();
    browser = undefined;
  });
}

function handleTermination(signal) {
  if (shutdownRequested) return;
  shutdownRequested = true;
  void closeBrowserResources()
    .catch(() => undefined)
    .finally(() => { process.exit(128 + (signal === 'SIGHUP' ? 1 : signal === 'SIGINT' ? 2 : 15)); });
}

process.once('SIGHUP', () => handleTermination('SIGHUP'));
process.once('SIGINT', () => handleTermination('SIGINT'));
process.once('SIGTERM', () => handleTermination('SIGTERM'));

function suppressSecretDiagnostics() {
  secretExposureDetected = true;
  errors.splice(0, errors.length, suppressedSecretFailure);
}

async function detectSecretExposure() {
  if (secretExposureDetected) return true;
  const exposed = await page.evaluate((selector) => {
    const visible = (node) => Boolean(node.offsetWidth || node.offsetHeight || node.getClientRects().length);
    const sensitive = /password|secret|token|api[_ -]?key|credential|cookie|authorization|auth(?:entication)?|bootstrap/i;
    const visibleText = String(document.body?.innerText || '');
    const textSecret = /(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|token|password|cookie|authorization|x-api-key|x-auth-token|bootstrap[_ -]?(?:secret|token)|credential)\s*(?:[:=]|is\s+)\s*(?!<redacted>)[^\s,;]{4,}/i.test(visibleText)
      || /[?&](?:api[_-]?key|token|password|credential|bootstrap[_-]?(?:secret|token))=[^&#\s]{1,}/i.test(visibleText)
      || /(?:bearer|basic)\s+[a-z0-9._~+\/-]{8,}/i.test(visibleText)
      || /[a-z][a-z0-9+.-]*:\/\/[^/@\s]+:[^@/\s]+@/i.test(visibleText);
    const controlSecret = Array.from(document.querySelectorAll(selector)).some((node) => {
      const label = node.labels?.length ? Array.from(node.labels).map(item => item.textContent || '').join(' ') : '';
      const attributes = Array.from(node.attributes || []).map(attribute => `${attribute.name}=${attribute.value}`).join(' ');
      const context = [node.name, node.id, node.className, node.getAttribute('aria-label'), node.getAttribute('autocomplete'), node.getAttribute('placeholder'), label, attributes]
        .filter(Boolean).join(' ');
      const value = 'value' in node ? String(node.value || '') : String(node.textContent || '').trim();
      const passwordControl = node instanceof HTMLInputElement && node.type === 'password';
      return visible(node) && sensitive.test(context) && !passwordControl && value.length > 0;
    })
    return textSecret || controlSecret;
  }, secretExposureSelector);
  if (exposed) suppressSecretDiagnostics();
  return exposed;
}

function safeDiagnosticField(value, fallback = 'unavailable') {
  if (typeof value !== 'string' || !value) return fallback;
  return value.replace(/[^A-Za-z0-9._:/@ -]/g, '_').slice(0, 160) || fallback;
}

function synchronousRedactedDiagnostic(value) {
  try {
    const result = spawnSync('python', [evidenceHelper, 'redact-stdin'], {
      input: String(value || ''), encoding: 'utf8', windowsHide: true,
    });
    if (result.status !== 0) throw new Error('redactor failed');
    return result.stdout.slice(0, 512);
  } catch {
    return null;
  }
}

function captureBrowserDiagnostic(kind, readEvent) {
  browserErrorGeneration += 1;
  let event;
  try {
    event = readEvent();
  } catch {
    const failure = { kind, classification: 'event-capture-failed', source: 'unavailable', location: 'unavailable' };
    runtimeErrors.push({ kind, classification: 'event-observed-diagnostic-capture-failed', source: 'unavailable', location: 'unavailable' });
    diagnosticFailures.push(failure);
    return;
  }
  const location = event?.location || {};
  const envelope = {
    kind,
    observedAfterMs: Date.now() - browserStartedAt,
    claimClassification,
    claimPhase,
    classification: 'captured-redacted',
    source: safeDiagnosticField(event?.source),
    location: {
      url: safeDiagnosticField(location.url),
      line: Number.isInteger(location.lineNumber) ? location.lineNumber : null,
      column: Number.isInteger(location.columnNumber) ? location.columnNumber : null,
    },
  };
  const message = synchronousRedactedDiagnostic(event?.message);
  if (message === null) {
    envelope.classification = 'redaction-failed';
    diagnosticFailures.push({ ...envelope, message: 'redacted diagnostic unavailable' });
  } else {
    envelope.message = message;
  }
  runtimeErrors.push(envelope);
  void classifyRecoveredFirstClaimSseTransition();
}

function isFirstClaimSseTransportError(error) {
  return error.kind === 'console-error'
    && error.message === 'Error in stream: %O Event'
    && error.source === 'error'
    && error.claimClassification === 'first-claim-bootstrap'
    && error.claimPhase === 'post-claim-complete'
    && claimClassification === 'first-claim-bootstrap'
    && claimPhase === 'post-claim-complete';
}

async function observeFirstClaimSseRecovery() {
  const matching = runtimeErrors.filter(isFirstClaimSseTransportError);
  if (matching.length !== 1) return null;
  const error = matching[0];
  const transition = {
    kind: 'first-claim-sse-transport', matchingCount: matching.length, classification: 'recovery-pending',
    errorObservedAfterMs: error.observedAfterMs, minimumRetryMs: sseReconnectMinimumMs,
    maximumRecoveryMs: sseReconnectMaximumMs, pollIntervalMs: sseRecoveryPollMs, pollCount: 0,
    claimCompletedAtMs,
  };
  streamTransitionEvidence.push(transition);
  const earliestRecoveryAfterMs = error.observedAfterMs + sseReconnectMinimumMs;
  const deadline = error.observedAfterMs + sseReconnectMaximumMs;
  const recoveryState = () => {
    const afterError = streamConnections.filter(connection => connection.observedAfterMs >= error.observedAfterMs);
    const postError = afterError.filter(connection => connection.observedAfterMs >= earliestRecoveryAfterMs);
    return {
      ignoredPreMinimumCount: afterError.length - postError.length,
      attemptCount: postError.length,
      statuses: postError.map(connection => connection.status),
      recovery: postError.find(connection => connection.status === 200 && connection.contentType === 'text/event-stream'),
    };
  };
  while (Date.now() - browserStartedAt < deadline) {
    const currentMatching = runtimeErrors.filter(isFirstClaimSseTransportError);
    const otherErrors = runtimeErrors.filter(item => !isFirstClaimSseTransportError(item));
    if (currentMatching.length !== 1 || otherErrors.length || diagnosticFailures.length) {
      transition.classification = 'recovery-aborted-error';
      transition.repeatedCount = currentMatching.length;
      transition.otherErrorCount = otherErrors.length;
      return null;
    }
    const state = recoveryState();
    transition.pollCount += 1;
    transition.ignoredPreMinimumCount = state.ignoredPreMinimumCount;
    transition.streamAttemptCount = state.attemptCount;
    transition.streamStatuses = state.statuses;
    if (!state.recovery) {
      await new Promise(resolve => setTimeout(resolve, sseRecoveryPollMs));
      continue;
    }
    transition.recoveryObservedAfterMs = state.recovery.observedAfterMs;
    transition.recoveryLatencyMs = state.recovery.observedAfterMs - error.observedAfterMs;
    const modelTimeoutMs = Math.min(15000, deadline - (Date.now() - browserStartedAt));
    if (modelTimeoutMs < 1000) break;
    transition.modelProbeStartedAfterMs = Date.now() - browserStartedAt;
    try {
      transition.recoveryModelRows = await page.evaluate(({ timeoutMs, minimumRows }) => new Promise((resolve, reject) => {
      const source = new EventSource('/server/stream');
      const timeout = setTimeout(() => { source.close(); reject(new Error('recovery model timed out')); }, timeoutMs);
      source.addEventListener('model-init', event => {
        clearTimeout(timeout);
        source.close();
        try {
          const model = JSON.parse(event.data);
          if (!Array.isArray(model) || model.length < minimumRows) throw new Error('recovery model was stale');
          resolve(model.length);
        } catch (error) { reject(error); }
      });
      source.onerror = () => { source.close(); clearTimeout(timeout); reject(new Error('recovery model stream failed')); };
    }), { timeoutMs: modelTimeoutMs, minimumRows: fixtureNames.length });
  } catch {
    transition.classification = 'model-not-fresh';
    return null;
  }
    try {
      await requireApi('first-claim-sse-recovery-status', '/server/status');
      transition.statusAfterRecovery = 200;
    } catch {
      transition.classification = 'status-not-ready';
      return null;
    }
    const stabilityUntil = Date.now() + sseStabilityWindowMs;
    while (Date.now() < stabilityUntil) {
      const repeated = runtimeErrors.filter(isFirstClaimSseTransportError);
      const otherErrors = runtimeErrors.filter(item => !isFirstClaimSseTransportError(item));
      if (repeated.length !== 1 || otherErrors.length || diagnosticFailures.length) {
        transition.classification = 'recovery-unstable';
        transition.repeatedCount = repeated.length;
        transition.otherErrorCount = otherErrors.length;
        return null;
      }
      await new Promise(resolve => setTimeout(resolve, sseRecoveryPollMs));
    }
    runtimeErrors.splice(runtimeErrors.indexOf(error), 1);
    transition.classification = 'recovered-after-single-transport-error';
    transition.stabilityWindowMs = sseStabilityWindowMs;
    expectedTransitions.push({ kind: transition.kind, count: 1, timingClass: 'post-claim-stateful-reconnect', errorObservedAfterMs: error.observedAfterMs, recoveryObservedAfterMs: state.recovery.observedAfterMs, recoveryLatencyMs: transition.recoveryLatencyMs, maximumRecoveryMs: sseReconnectMaximumMs, pollCount: transition.pollCount, modelFresh: true, recoveryModelRows: transition.recoveryModelRows, statusAfterRecovery: 200, stabilityWindowMs: sseStabilityWindowMs });
    const validated = {
      ...transition,
      errorGeneration: browserErrorGeneration,
      claimClassification,
      claimPhase,
      validatedAtMs: Date.now() - browserStartedAt,
    };
    validatedFirstClaimSseRecovery = validated;
    return validated;
  }
  const finalState = recoveryState();
  transition.classification = 'recovery-timeout';
  transition.elapsedAfterErrorMs = Date.now() - browserStartedAt - error.observedAfterMs;
  transition.streamAttemptCount = finalState.attemptCount;
  transition.ignoredPreMinimumCount = finalState.ignoredPreMinimumCount;
  transition.streamStatuses = finalState.statuses;
  return null;
}

function classifyRecoveredFirstClaimSseTransition() {
  if (validatedFirstClaimSseRecovery) return Promise.resolve(validatedFirstClaimSseRecovery);
  if (firstClaimSseRecoveryPromise) return firstClaimSseRecoveryPromise;
  if (claimClassification !== 'first-claim-bootstrap' || claimPhase !== 'post-claim-complete'
      || runtimeErrors.filter(isFirstClaimSseTransportError).length !== 1) return Promise.resolve(null);
  firstClaimSseRecoveryPromise = observeFirstClaimSseRecovery().catch(() => {
    firstClaimSseRecoveryFailure = { classification: 'recovery-classifier-failed' };
    return null;
  });
  return firstClaimSseRecoveryPromise;
}

async function requireFreshStabilityModel(timeoutMs) {
  return page.evaluate(({ timeoutMs, minimumRows }) => new Promise((resolve, reject) => {
    const source = new EventSource('/server/stream');
    const timeout = setTimeout(() => { source.close(); reject(new Error('stability model timed out')); }, timeoutMs);
    source.addEventListener('model-init', event => {
      clearTimeout(timeout); source.close();
      try {
        const model = JSON.parse(event.data);
        if (!Array.isArray(model) || model.length < minimumRows) throw new Error('stability model was stale');
        resolve(model.length);
      } catch (error) { reject(error); }
    });
    source.onerror = () => { source.close(); clearTimeout(timeout); reject(new Error('stability model stream failed')); };
  }), { timeoutMs, minimumRows: fixtureNames.length });
}

function parseStabilityRequest(request) {
  const expected = ['request_kind', 'requested_at', 'run_id', 'schema'];
  if (!request || Object.keys(request).sort().join(',') !== expected.join(',')
      || request.schema !== 1 || request.run_id !== screenshotRunId
      || request.request_kind !== 'pre-restart-stability' || typeof request.requested_at !== 'string'
      || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(request.requested_at)) {
    throw new Error('browser stability request is malformed or for another run');
  }
  return request;
}

async function waitForStabilityRequest() {
  const requestPath = path.join(evidenceDir, 'browser-stability-request.json');
  const deadline = Date.now() + 180000;
  while (!fs.existsSync(requestPath)) {
    if (Date.now() >= deadline) throw new Error('timed out waiting for browser stability request');
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  return parseStabilityRequest(JSON.parse(fs.readFileSync(requestPath, 'utf8')));
}

async function establishPreRestartStability(request) {
  const matching = runtimeErrors.filter(isFirstClaimSseTransportError);
  const otherErrors = runtimeErrors.filter(item => !isFirstClaimSseTransportError(item));
  if (diagnosticFailures.length || otherErrors.length || matching.length > 1) {
    throw new Error('browser stability checkpoint observed repeated or non-transport errors');
  }
  if (matching.length === 1 || firstClaimSseRecoveryPromise || validatedFirstClaimSseRecovery) {
    await classifyRecoveredFirstClaimSseTransition();
    if (firstClaimSseRecoveryFailure || runtimeErrors.length || diagnosticFailures.length
        || !validatedFirstClaimSseRecovery
        || validatedFirstClaimSseRecovery.claimClassification !== 'first-claim-bootstrap'
        || validatedFirstClaimSseRecovery.claimPhase !== 'post-claim-complete'
        || validatedFirstClaimSseRecovery.errorGeneration !== browserErrorGeneration
        || validatedFirstClaimSseRecovery.classification !== 'recovered-after-single-transport-error') {
      throw new Error('browser stability checkpoint did not recover the first-claim transport error');
    }
  }
  const snapshotGeneration = browserErrorGeneration;
  const modelRows = await requireFreshStabilityModel(15000);
  await requireApi('pre-restart-stability-status', '/server/status');
  const quietUntil = Date.now() + sseStabilityWindowMs;
  while (Date.now() < quietUntil) {
    if (browserErrorGeneration !== snapshotGeneration || runtimeErrors.length || diagnosticFailures.length) {
      throw new Error('browser stability checkpoint was invalidated by a later error');
    }
    await new Promise(resolve => setTimeout(resolve, sseRecoveryPollMs));
  }
  const ready = {
    schema: 1, run_id: screenshotRunId, request_kind: request.request_kind, requested_at: request.requested_at,
    error_generation: snapshotGeneration, runtime_error_count: runtimeErrors.length,
    diagnostic_failure_count: diagnosticFailures.length, model_rows: modelRows, status: 200,
    stability_window_ms: sseStabilityWindowMs, ready_at: new Date().toISOString(),
  };
  await writeEvidence({ browserStability: ready }, 'browser-stability.json');
  fs.writeFileSync(path.join(evidenceDir, 'browser-stability-ready.json'), `${JSON.stringify(ready)}\n`, { mode: 0o600, flag: 'wx' });
  return ready;
}

async function flushBrowserDiagnostics() {
  try {
    await detectSecretExposure();
  } catch {
    diagnosticFailures.push({ kind: 'diagnostic-flush', classification: 'page-context-unavailable', source: 'unavailable', location: 'unavailable', message: 'redacted diagnostic unavailable' });
  }
}

async function captureFailure(label, error, response = null) {
  if (await detectSecretExposure()) {
    return {
      label,
      message: suppressedSecretFailure,
      requestedUrl: response?.url?.() || null,
      responseStatus: response?.status?.() || null,
      finalUrl: page.url(),
      diagnosticsSuppressed: true,
      consoleAndPageErrors: [...errors],
    };
  }
  if (claimPhase.startsWith('bootstrap-') || claimPhase === 'post-claim-route' || claimPhase === 'post-claim-shell' || claimPhase === 'post-claim-status') {
    return {
      label,
      message: redact(error.message || error),
      requestedUrl: response?.url?.() || null,
      responseStatus: response?.status?.() || null,
      finalUrl: page.url(),
      claimClassification,
      claimPhase,
      bodySnippet: 'suppressed for bootstrap claim diagnostics',
      consoleAndPageErrors: [...errors],
    };
  }
  const screenshot = `${evidenceName.replace('.json', '')}-failure-${label}.png`;
  let bodySnippet = '';
  try {
    bodySnippet = redact((await page.locator('body').innerText({ timeout: 2000 })).slice(0, 1200));
  } catch (snippetError) {
    bodySnippet = `unavailable: ${redact(snippetError.message)}`;
  }
  try {
    await safeScreenshot(path.join(evidenceDir, screenshot), 5000);
  } catch (screenshotError) {
    if (await detectSecretExposure()) {
      return {
        label,
        message: suppressedSecretFailure,
        requestedUrl: response?.url?.() || null,
        responseStatus: response?.status?.() || null,
        finalUrl: page.url(),
        diagnosticsSuppressed: true,
        consoleAndPageErrors: [...errors],
      };
    }
    errors.push(`failure screenshot: ${redact(screenshotError.message)}`);
  }
  return {
    label,
    message: redact(error.message || error),
    requestedUrl: response?.url?.() || null,
    responseStatus: response?.status?.() || null,
    finalUrl: page.url(),
    bodySnippet,
    screenshot,
    consoleAndPageErrors: [...errors],
  };
}

function screenshotRelativePath(target) {
  const runRoot = path.resolve(evidenceDir, '..', '..');
  const relative = path.relative(runRoot, target).replaceAll(path.sep, '/');
  if (!/^evidence\/ship-readiness\/(?:after-(?:bootstrap|first-claim|files|restart|restart-files|restore-bootstrap|restore-legacy-files)|before-legacy-files|browser(?:-(?:claim|reuse|legacy(?:-restore)?))?-failure-[a-z0-9-]{1,100})\.png$/.test(relative)) {
    throw new Error('screenshot path is not an approved retained evidence path');
  }
  return relative;
}

function crc32(payload) {
  let value = 0xffffffff;
  for (const byte of payload) {
    value ^= byte;
    for (let bit = 0; bit < 8; bit += 1) value = (value >>> 1) ^ (value & 1 ? 0xedb88320 : 0);
  }
  return (value ^ 0xffffffff) >>> 0;
}

function pngDimensions(payload) {
  if (payload.length < 33 || payload.length > 8 * 1024 * 1024 || !payload.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))) {
    throw new Error('Playwright did not produce a bounded PNG');
  }
  let offset = 8; let width = 0; let height = 0; let channels = 0; let sawHeader = false; let sawImage = false; let sawEnd = false;
  const idat = [];
  while (offset < payload.length) {
    if (offset + 12 > payload.length) throw new Error('PNG chunk is truncated');
    const length = payload.readUInt32BE(offset); const type = payload.subarray(offset + 4, offset + 8); const end = offset + 12 + length;
    if (length > 8 * 1024 * 1024 || end > payload.length || crc32(payload.subarray(offset + 4, offset + 8 + length)) !== payload.readUInt32BE(offset + 8 + length)) throw new Error('PNG chunk is invalid');
    const data = payload.subarray(offset + 8, offset + 8 + length); offset = end;
    if (type.equals(Buffer.from('IHDR'))) {
      if (sawHeader || sawImage || sawEnd || length !== 13) throw new Error('PNG header is invalid');
      width = data.readUInt32BE(0); height = data.readUInt32BE(4); const depth = data[8]; const color = data[9];
      if (!width || !height || width > 16384 || height > 16384 || width * height > 64 * 1024 * 1024 || depth !== 8 || ![2, 6].includes(color) || data[10] !== 0 || data[11] !== 0 || data[12] !== 0) throw new Error('PNG dimensions are unsafe');
      channels = color === 2 ? 3 : 4; sawHeader = true;
    } else if (type.equals(Buffer.from('IDAT'))) {
      if (!sawHeader || sawEnd) throw new Error('PNG image data is invalid');
      idat.push(data); sawImage = true;
    } else if (type.equals(Buffer.from('IEND'))) {
      if (!sawHeader || !sawImage || sawEnd || length !== 0 || offset !== payload.length) throw new Error('PNG end is invalid');
      sawEnd = true;
    } else throw new Error('PNG metadata is not permitted in retained evidence');
  }
  if (!sawEnd || inflateSync(Buffer.concat(idat)).length !== height * (1 + width * channels)) throw new Error('PNG pixel data is invalid');
  return { width, height };
}

function privateScreenshotFile(pathname) {
  const root = path.resolve(privateScreenshotRoot);
  const candidate = path.resolve(root, pathname);
  if (!candidate.startsWith(`${root}${path.sep}`)) throw new Error('private screenshot path escaped its staging root');
  return candidate;
}

function assertPrivateSource(pathname) {
  const info = fs.lstatSync(pathname);
  if (!info.isFile() || info.isSymbolicLink() || info.nlink !== 1 || info.uid !== process.getuid() || (info.mode & 0o777) !== 0o600) {
    throw new Error('private screenshot source provenance is invalid');
  }
  return { mode: '0600', owner_uid: info.uid, hardlinks: 1, regular: true };
}

function atomicCopy(source, destination) {
  const temporary = `${destination}.${process.pid}.tmp`;
  fs.copyFileSync(source, temporary, fs.constants.COPYFILE_EXCL);
  fs.renameSync(temporary, destination);
}

function publishPrivateScreenshot(source, target, relativePath, attestation, privacy) {
  const currentPrivacy = assertPrivateSource(source);
  if (JSON.stringify(currentPrivacy) !== JSON.stringify(privacy)) {
    throw new Error(`private screenshot source privacy changed before publication: ${source}`);
  }
  const sourceHash = createHash('sha256').update(fs.readFileSync(source)).digest('hex');
  atomicCopy(source, target);
  const destinationHash = createHash('sha256').update(fs.readFileSync(target)).digest('hex');
  if (destinationHash !== sourceHash || destinationHash !== attestation.sha256) throw new Error('published screenshot digest mismatch');
  const sourceAttestation = `${source}.safety.json`; const destinationAttestation = `${target}.safety.json`;
  assertPrivateSource(sourceAttestation);
  atomicCopy(sourceAttestation, destinationAttestation);
  const attestationHash = createHash('sha256').update(fs.readFileSync(sourceAttestation)).digest('hex');
  if (attestationHash !== createHash('sha256').update(fs.readFileSync(destinationAttestation)).digest('hex')) throw new Error('published screenshot attestation mismatch');
  const record = { schema: 1, policy_version: screenshotSafetyPolicyVersion, run_id: screenshotRunId, relative_path: relativePath,
    source_backend: 'wsl-private-posix', source_privacy: privacy, source_sha256: sourceHash, destination_sha256: destinationHash,
    attestation_sha256: attestationHash, published_safe: true };
  const recordPath = `${target}.publication.json`; const temporary = `${recordPath}.${process.pid}.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify(record)}\n`, { mode: 0o600, flag: 'wx' }); fs.renameSync(temporary, recordPath);
}

async function safeScreenshot(target, timeout = undefined) {
  // This checks rendered text and control state before capture. PNG byte
  // inspection cannot establish that visual secret disclosure did not occur.
  if (await detectSecretExposure()) throw new Error(suppressedSecretFailure);
  const relativePath = screenshotRelativePath(target);
  const captureTarget = privateScreenshotRoot ? privateScreenshotFile(relativePath) : target;
  fs.mkdirSync(path.dirname(captureTarget), { recursive: true, mode: 0o700 });
  fs.chmodSync(path.dirname(captureTarget), 0o700);
  await page.screenshot({ path: captureTarget, fullPage: true, ...(timeout ? { timeout } : {}) });
  fs.chmodSync(captureTarget, 0o600);
  const privacy = privateScreenshotRoot ? assertPrivateSource(captureTarget) : null;
  const payload = fs.readFileSync(captureTarget);
  const dimensions = pngDimensions(payload);
  const parsed = new URL(page.url());
  const attestation = {
    schema: 1,
    policy_version: screenshotSafetyPolicyVersion,
    run_id: screenshotRunId,
    relative_path: relativePath,
    sha256: createHash('sha256').update(payload).digest('hex'),
    width: dimensions.width,
    height: dimensions.height,
    route: parsed.pathname,
    state: claimPhase,
    captured_at: new Date().toISOString(),
    secret_exposure: false,
  };
  const sidecar = `${captureTarget}.safety.json`;
  const temporary = `${sidecar}.${process.pid}.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify(attestation)}\n`, { mode: 0o600, flag: 'wx' });
  fs.chmodSync(temporary, 0o600);
  fs.renameSync(temporary, sidecar);
  fs.chmodSync(sidecar, 0o600);
  if (privateScreenshotRoot) {
    publishPrivateScreenshot(captureTarget, target, relativePath, attestation, privacy);
  }
}

async function navigateReady(label, target, expectedPaths) {
  let response = null;
  try {
    response = await page.goto(target, { waitUntil: 'domcontentloaded', timeout: navigationTimeout });
    const final = new URL(page.url());
    const acceptedPaths = Array.isArray(expectedPaths) ? expectedPaths : [expectedPaths];
    if (!response || response.status() !== 200) throw new Error(`${label} navigation HTTP ${response?.status?.() ?? 'no response'}`);
    if (!acceptedPaths.includes(final.pathname)) throw new Error(`${label} final path ${final.pathname} was not one of ${acceptedPaths.join(', ')}`);
    await page.locator('app-root').waitFor({ state: 'visible', timeout: readinessTimeout });
    navigation.push({ label, requestedUrl: target, finalUrl: page.url(), responseStatus: response.status(), acceptedPaths, readiness: 'app-root visible' });
  } catch (error) {
    const failure = await captureFailure(label, error, response);
    navigation.push({ label, requestedUrl: target, finalUrl: failure.finalUrl, responseStatus: failure.responseStatus, readiness: 'failed', failure });
    throw error;
  }
}

async function classifyStandaloneBootstrap() {
  claimPhase = 'bootstrap-load';
  const response = await page.goto(new URL('/bootstrap', baseUrl).href, { waitUntil: 'domcontentloaded', timeout: navigationTimeout });
  if (!response || response.status() !== 200 || new URL(page.url()).pathname !== '/bootstrap') {
    throw new Error('bootstrap route was not ready');
  }
  await page.locator('body.bootstrap-page').waitFor({ state: 'visible', timeout: readinessTimeout });
  const firstClaim = page.getByRole('button', { name: 'Claim session', exact: true });
  const remembered = page.getByRole('button', { name: 'Remember browser', exact: true });
  const secretInput = page.locator('#browser-secret');
  const shell = page.locator('app-root');
  if (
    await page.locator('form#bootstrap-form').count() === 1
    && await page.getByRole('heading', { name: 'Claim the first local session', exact: true }).count() === 1
    && await firstClaim.count() === 1
    && await secretInput.count() === 0
    && await shell.count() === 0
  ) {
    claimClassification = 'first-claim-bootstrap';
    claimPhase = 'bootstrap-first-claim-ready';
    navigation.push({ label: 'bootstrap-classification', finalUrl: page.url(), responseStatus: response.status(), claimClassification, claimPhase });
    return firstClaim;
  }
  if (
    await secretInput.count() === 1
    && await remembered.count() === 1
    && await firstClaim.count() === 0
    && await shell.count() === 0
  ) {
    claimClassification = 'remembered-api-key-bootstrap';
    claimPhase = 'bootstrap-remembered-api-key';
    navigation.push({ label: 'bootstrap-classification', finalUrl: page.url(), responseStatus: response.status(), claimClassification, claimPhase });
    return null;
  }
  claimClassification = 'unknown-bootstrap';
  claimPhase = 'bootstrap-unrecognized';
  throw new Error('bootstrap state did not match first-claim or remembered-api-key contract');
}

async function completeFirstBootstrapClaim(claim) {
  claimPhase = 'bootstrap-first-claim-click';
  await claim.click();
  claimPhase = 'post-claim-route';
  await page.waitForFunction(() => window.location.pathname !== '/bootstrap', undefined, { timeout: readinessTimeout });
  if (new URL(page.url()).pathname !== '/') throw new Error('first claim did not return to the application route');
  claimPhase = 'post-claim-shell';
  await page.locator('app-root').waitFor({ state: 'visible', timeout: readinessTimeout });
  claimPhase = 'post-claim-status';
  await requireApi('after-first-claim', '/server/status');
  claimPhase = 'post-claim-complete';
  claimCompletedAtMs = Date.now() - browserStartedAt;
  navigation.push({ label: 'after-first-claim-shell', finalUrl: page.url(), claimClassification, claimPhase, readiness: 'app-root visible and status HTTP 200' });
}

async function requireFixtureRows(label) {
  const visibleFixtureRows = {};
  for (const name of fixtureNames) {
    try {
      await page.getByText(name, { exact: false }).first().waitFor({ state: 'visible', timeout: readinessTimeout });
      visibleFixtureRows[name] = true;
    } catch (error) {
      visibleFixtureRows[name] = false;
      const failure = await captureFailure(`${label}-${name.replace(/[^a-z0-9]+/gi, '-').toLowerCase()}`, error);
      navigation.push({ label: `${label}-fixture-row`, readiness: 'failed', failure });
      throw new Error(`${label} fixture row was not visible: ${name}`);
    }
  }
  navigation.push({ label: `${label}-fixture-rows`, readiness: 'all expected fixture rows visible' });
  return visibleFixtureRows;
}

async function requireApi(label, endpoint) {
  const status = await page.evaluate(async path => {
    const response = await fetch(path, { credentials: 'same-origin' });
    return response.status;
  }, endpoint);
  apiEvidence[label] = {
    endpoint,
    status,
    readiness: status === 200 ? 'API HTTP 200' : 'API request failed',
  };
  if (status !== 200) {
    const failure = await captureFailure(`${label}-api`, new Error(`${endpoint} HTTP ${status}`));
    navigation.push({ label: `${label}-api`, endpoint, responseStatus: status, readiness: 'failed', failure });
    throw new Error(`${label} API readiness failed: ${endpoint} HTTP ${status}`);
  }
  navigation.push({ label: `${label}-api`, endpoint, responseStatus: status, readiness: 'API HTTP 200' });
}

async function waitForRestartRequest(stability) {
  const request = path.join(evidenceDir, 'browser-restart-request.json');
  const deadline = Date.now() + 120000;
  while (!fs.existsSync(request)) {
    if (browserErrorGeneration !== stability.error_generation || runtimeErrors.length || diagnosticFailures.length) {
      await writeEvidence({ browserStabilityInvalidated: { error_generation: browserErrorGeneration, ready_generation: stability.error_generation } }, 'browser-stability-invalid.json');
      throw new Error('browser stability ready state was invalidated before restart request');
    }
    if (Date.now() >= deadline) throw new Error('timed out waiting for current-runtime restart request');
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  const parsed = JSON.parse(fs.readFileSync(request, 'utf8'));
  const expected = ['restart_requested', 'run_id', 'schema', 'stability_generation'];
  if (!parsed || Object.keys(parsed).sort().join(',') !== expected.join(',') || parsed.schema !== 1 || parsed.run_id !== screenshotRunId
      || parsed.stability_generation !== stability.error_generation || parsed.restart_requested !== true) {
    throw new Error('restart request does not match current browser stability generation');
  }
}

async function runReuse() {
  evidenceName = 'browser-reuse.json';
  await navigateReady('restart-root', baseUrl, '/');
  await requireApi('restart-status', '/server/status');
  await navigateReady('restart-settings', new URL('/settings', baseUrl).href, '/settings');
  await requireApi('restart-settings', '/server/config/get');
  await navigateReady('restart-files', baseUrl, '/');
  const visibleFixtureRows = await requireFixtureRows('restart-files');
  await safeScreenshot(path.join(evidenceDir, 'after-restart-files.png'));
  await writeEvidence({ url: page.url(), visibleFixtureRows, rememberedSession: true });
}

async function main() {
  const claimMode = mode === 'claim' || mode === 'claim-reuse';
  let claimButtonCount = 0;
  if (claimMode && recoveryHandoverMode) {
    const claim = await classifyStandaloneBootstrap();
    if (claimClassification !== 'first-claim-bootstrap' || claim === null) {
      throw new Error('recovery claim flow reached remembered/API-key bootstrap instead of first claim');
    }
    await completeFirstBootstrapClaim(claim);
  } else {
    const initialUrl = baseUrl;
    const initialPaths = legacyMode ? ['/dashboard'] : ['/'];
    await navigateReady('root-initial', initialUrl, initialPaths);
  }
  await safeScreenshot(path.join(evidenceDir, mode === 'legacy-restore' ? 'after-restore-bootstrap.png' : mode === 'reuse' ? 'after-restart.png' : 'after-bootstrap.png'));

  if (legacyMode) {
    const label = mode === 'legacy-restore' ? 'restored-legacy-files' : 'legacy-files';
    const visibleFixtureRows = await requireFixtureRows(label);
    await safeScreenshot(path.join(evidenceDir, mode === 'legacy-restore' ? 'after-restore-legacy-files.png' : 'before-legacy-files.png'));
    await writeEvidence({ url: page.url(), visibleFixtureRows });
    return;
  }

  if (claimClassification === 'first-claim-bootstrap') {
    claimButtonCount = 1;
  } else if (claimMode) {
    const claim = page.getByRole('button', { name: /claim|continue|start/i });
    claimButtonCount = await claim.count();
    if (claimButtonCount !== 1) throw new Error(`first-claim control count: ${claimButtonCount}`);
    await claim.first().click();
    await requireApi('after-first-claim', '/server/status');
  }
  await safeScreenshot(path.join(evidenceDir, 'after-first-claim.png'));

  // Settings can legitimately render secret-bearing controls after the first
  // claim. Verify its API contract without visiting that presentation surface.
  await requireApi('settings', '/server/config/get');

  await navigateReady('files', baseUrl, '/');
  await requireApi('files', '/server/status');
  const visibleFixtureRows = await requireFixtureRows('files');
  await safeScreenshot(path.join(evidenceDir, mode === 'reuse' ? 'after-restart-files.png' : 'after-files.png'));

  const endpoints = ['/server/status', '/server/config/get', '/server/path-pairs', '/server/autoqueue/get', '/server/logs/history/v1?limit=5'];
  const api = await page.evaluate(async paths => {
    const result = {};
    for (const endpoint of paths) {
      const response = await fetch(endpoint, { credentials: 'same-origin' });
      // Record only HTTP and shape metadata. Responses can include configured
      // notification endpoints, so raw API bodies never become evidence.
      const text = await response.text();
      let type = 'text';
      try { type = Array.isArray(JSON.parse(text)) ? 'array' : typeof JSON.parse(text); } catch { /* non-JSON status is still useful */ }
      result[endpoint] = { status: response.status, type, length: text.length };
    }
    return result;
  }, endpoints);
  for (const endpoint of endpoints) if (api[endpoint].status !== 200) throw new Error(`API ${endpoint}: ${api[endpoint].status}`);

  const model = await page.evaluate(() => new Promise((resolve, reject) => {
    const source = new EventSource('/server/stream');
    const timeout = setTimeout(() => { source.close(); reject(new Error('timed out waiting for model-init')); }, 15000);
    source.addEventListener('model-init', event => {
      clearTimeout(timeout);
      source.close();
      const normalize = file => ({
        name: file.name, is_dir: file.is_dir, state: file.state,
        remote_size: file.remote_size, local_size: file.local_size,
        file_id: file.file_id, path_pair_id: file.path_pair_id,
        path_pair_name: file.path_pair_name,
        children: (file.children || []).map(normalize),
      });
      resolve(JSON.parse(event.data).map(normalize));
    });
    source.onerror = () => { source.close(); clearTimeout(timeout); reject(new Error('model stream failed')); };
  }));
  const autoqueuePatterns = await page.evaluate(async () => {
    const response = await fetch('/server/autoqueue/get', { credentials: 'same-origin' });
    if (!response.ok) throw new Error(`AutoQueue HTTP ${response.status}`);
    const body = await response.json();
    return (Array.isArray(body) ? body : []).map(entry => typeof entry === 'string' ? entry : entry.pattern).filter(pattern => typeof pattern === 'string').sort();
  });
  const actions = claimMode ? await page.evaluate(async () => {
    const result = {};
    for (const file of ['negative-nonmatch.bin', 'root-directory-stopped', 'transient-manual.zip']) {
      const response = await fetch(`/server/command/queue/${encodeURIComponent(file)}`, { method: 'POST', credentials: 'same-origin' });
      result[`queue:${file}`] = response.status;
    }
    // The archive is bounded (8 MiB) and this wait is only to let the queued
    // transfer reach a commandable terminal state; the shell verifier still
    // proves the resulting file/extracted payload on disk.
    await new Promise(resolve => setTimeout(resolve, 15000));
    const extract = await fetch('/server/command/extract/transient-manual.zip', { method: 'POST', credentials: 'same-origin' });
    result['extract:transient-manual.zip'] = extract.status;
    return result;
  }) : {};
  for (const [action, status] of Object.entries(actions)) if (status !== 200) throw new Error(`${action}: ${status}`);
  await writeEvidence({ url: page.url(), claimClassification, claimPhase, claimButtonCount, visibleFixtureRows, api, model, autoqueuePatterns, actions });
  if (mode === 'claim-reuse') {
    if (claimClassification !== 'first-claim-bootstrap' || claimPhase !== 'post-claim-complete') {
      throw new Error('browser claim readiness marker requires completed first-claim shell and status assertions');
    }
    await writeEvidence({ claimClassification, claimPhase, persistentProfile: false, retainedCookieDatabase: false, markerAfter: 'post-claim-route-shell-status' }, 'browser-claim-ready.json');
    const stabilityRequest = await waitForStabilityRequest();
    const stability = await establishPreRestartStability(stabilityRequest);
    await waitForRestartRequest(stability);
    await runReuse();
  }
}

try {
  await main();
} catch (error) {
  const failure = await captureFailure('browser-run', error);
  await writeEvidence({ url: page.url(), failure });
  process.exitCode = 1;
} finally {
  await closeBrowserResources();
}
