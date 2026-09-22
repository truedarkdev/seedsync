/*
 * Maintained passive browser observer for the incoming-recovery performance
 * lane.  It deliberately observes one selected dashboard root and records
 * only bounded, identity-free projections.  The selector and output paths,
 * browser origin, and Playwright module are runtime configuration; no local
 * machine path or service origin is embedded here.
 */
const fs = require("fs");
const path = require("path");

const MAX_SAMPLES = 128;
const MAX_SSE_EVENTS = 128;
const MAX_SSE_SOURCES = 64;
const MAX_NAVIGATION_EVENTS = 32;
const MAX_API_TIMING = 64;
const MAX_API_BYTES = 1024 * 1024;
const MAX_API_RECORDS = 4096;
const MAX_ROOT_API_SNAPSHOTS = 64;
const ROOT_API_SNAPSHOT_INTERVAL_MS = 1000;
const MAX_SSE_PAYLOAD_BYTES = 32768;
const MAX_PAIR_API_BYTES = 65536;
const BROWSER_SESSION_DEADLINE_MS = 30000;
const PAIR_LOOKUP_HEADER_TIMEOUT_MS = 5000;
const PAIR_LOOKUP_BODY_TIMEOUT_MS = 5000;
const ROOT_API_HEADER_TIMEOUT_MS = 5000;
const ROOT_API_BODY_TIMEOUT_MS = 5000;
const MAX_TEXT = 96;
const MAX_COUNTER = 2147483647;
const SAFE_TOKEN = /^[A-Za-z0-9_-]{1,48}$/;
const SAFE_SIZE = /^[A-Za-z0-9%:/+., _-]{0,96}$/;
const ROOT_FIELDS = [
  "state", "local_size", "remote_size", "transferred_size",
  "display_size_total", "display_transferred_size", "download_progress",
  "complete_local_coverage", "final_move_succeeded", "model_version",
];

function boundedInt(value) {
  return Number.isInteger(value) && value >= 0 && value <= MAX_COUNTER ? value : null;
}

function boundedSafeInt(value) {
  return Number.isInteger(value) && value >= 0 && value <= Number.MAX_SAFE_INTEGER ? value : null;
}

function boundedBoolean(value) {
  return typeof value === "boolean" ? value : null;
}

function safeToken(value, fallback = "unknown") {
  return typeof value === "string" && SAFE_TOKEN.test(value) ? value : fallback;
}

function safeEventType(value) {
  return safeToken(value);
}

function safeDigest(value) {
  return typeof value === "string" && /^[0-9a-f]{8}$/.test(value) ? value : null;
}

function textFingerprint(value) {
  const text = typeof value === "string" ? value : "";
  let hash = 2166136261;
  for (const code of Buffer.from(text, "utf8")) {
    hash ^= code;
    hash = Math.imul(hash, 16777619) >>> 0;
  }
  return hash.toString(16).padStart(8, "0");
}

function projectFilter(value, present = true) {
  const text = typeof value === "string" ? value : "";
  return {
    present: present === true,
    nonempty: text.length > 0,
    text_length: Math.min(text.length, 512),
    text_fingerprint: textFingerprint(text),
    text_emitted: false,
  };
}

function projectSize(value) {
  if (typeof value !== "string") return { present: false, value: null };
  const text = value.trim().replace(/\s+/g, " ");
  if (!SAFE_SIZE.test(text) || text.length > MAX_TEXT) return { present: true, value: null };
  return { present: text.length > 0, value: text };
}

function projectAncestor(sample, atMs) {
  const progress = typeof sample?.progress === "string" &&
      /^(?:0|[1-9][0-9]{0,9})(?:\.[0-9]{1,3})?$/.test(sample.progress)
    ? sample.progress : null;
  return {
    at_ms: Math.max(0, boundedInt(atMs) ?? 0),
    present: sample?.present === true,
    status: safeToken(sample?.status),
    progress,
    size: projectSize(sample?.size),
  };
}

function projectModelVersion(value) {
  return boundedSafeInt(value);
}

function emptyRootProjection(modelVersion = null) {
  return {
    state: null,
    local_size: null,
    remote_size: null,
    transferred_size: null,
    display_size_total: null,
    display_transferred_size: null,
    download_progress: null,
    complete_local_coverage: null,
    final_move_succeeded: null,
    model_version: projectModelVersion(modelVersion),
  };
}

function projectRootRecord(record, modelVersion = null) {
  const source = record && typeof record === "object" && !Array.isArray(record) ? record : {};
  return {
    state: safeToken(source.state, null),
    local_size: boundedSafeInt(source.local_size),
    remote_size: boundedSafeInt(source.remote_size),
    transferred_size: boundedSafeInt(source.transferred_size),
    display_size_total: boundedSafeInt(source.display_size_total),
    display_transferred_size: boundedSafeInt(source.display_transferred_size),
    download_progress: Number.isInteger(source.download_progress) &&
        source.download_progress >= 0 && source.download_progress <= 100
      ? source.download_progress : null,
    complete_local_coverage: boundedBoolean(source.complete_local_coverage),
    final_move_succeeded: boundedBoolean(source.final_move_succeeded),
    model_version: projectModelVersion(modelVersion),
  };
}

function countInvalidRootFields(record, projection) {
  const source = record && typeof record === "object" && !Array.isArray(record) ? record : {};
  let invalid = 0;
  for (const key of ROOT_FIELDS) {
    if (!(key in source) || key === "model_version") continue;
    if (source[key] !== null && projection[key] === null) invalid += 1;
  }
  return invalid;
}

function emptyRootApiProjection() {
  return {
    request_start_ms: null,
    response_end_ms: null,
    response_duration_ms: null,
    http_status: null,
    response_bytes: 0,
    response_bytes_capped: false,
    body_limit_bytes: MAX_API_BYTES,
    parse_status: "unavailable",
    timeout_stage: null,
    oversize: false,
    match_status: "not_attempted",
    records_seen: 0,
    records_dropped: 0,
    invalid_fields: 0,
    payload_loss: false,
    loss: {
      response_oversize: false,
      parse_failure: false,
      records_dropped: 0,
      selected_root_unobserved: false,
    },
    root: emptyRootProjection(),
  };
}

function addRootApiSnapshot(result, snapshot, phase, capturedAtMs) {
  const boundedPhase = phase === "before_dom_arm" || phase === "armed" || phase === "armed_final"
    ? phase : "unknown";
  const projected = {
    ...snapshot,
    capture_at_ms: boundedInt(capturedAtMs),
    capture_phase: boundedPhase,
  };
  if (result.root_api_snapshots.length < MAX_ROOT_API_SNAPSHOTS) {
    result.root_api_snapshots.push(projected);
  } else {
    result.root_api_snapshots.splice(1, 1);
    result.root_api_snapshots.push(projected);
    result.root_api_snapshots_dropped = Math.min(MAX_COUNTER, result.root_api_snapshots_dropped + 1);
    result.root_api_snapshot_capture.chronology_complete = false;
  }
  result.root_api_snapshot_capture.snapshots_observed = Math.min(
    MAX_COUNTER, result.root_api_snapshot_capture.snapshots_observed + 1,
  );
  if (snapshot.payload_loss) result.root_api_snapshot_capture.payload_loss = true;
}

function projectRootApiResponse(body, selectedRootId, metadata = {}) {
  const output = emptyRootApiProjection();
  output.request_start_ms = boundedInt(metadata.request_start_ms);
  output.response_end_ms = boundedInt(metadata.response_end_ms);
  output.response_duration_ms = boundedInt(metadata.response_duration_ms);
  output.http_status = Number.isInteger(metadata.http_status) && metadata.http_status >= 0 && metadata.http_status <= 999
    ? metadata.http_status : null;

  const text = typeof body === "string" ? body : Buffer.isBuffer(body) ? body.toString("utf8") : "";
  const byteLength = Buffer.byteLength(text, "utf8");
  const reportedBytes = Number.isInteger(metadata.response_bytes) && metadata.response_bytes >= 0
    ? metadata.response_bytes : byteLength;
  output.response_bytes = Math.min(reportedBytes, MAX_API_BYTES);
  output.response_bytes_capped = reportedBytes > MAX_API_BYTES || byteLength > MAX_API_BYTES;
  if (metadata.unavailable === true) {
    output.parse_status = "unavailable";
    output.payload_loss = true;
    output.loss.parse_failure = true;
    output.loss.selected_root_unobserved = true;
    return output;
  }
  if (metadata.timeout_stage === "headers" || metadata.timeout_stage === "body") {
    output.parse_status = "timeout";
    output.timeout_stage = metadata.timeout_stage;
    output.payload_loss = true;
    output.loss.parse_failure = true;
    output.loss.selected_root_unobserved = true;
    return output;
  }
  if (metadata.oversize === true || byteLength > MAX_API_BYTES) {
    output.parse_status = "oversize";
    output.oversize = true;
    output.payload_loss = true;
    output.loss.response_oversize = true;
    output.loss.selected_root_unobserved = true;
    output.match_status = "not_attempted";
    return output;
  }
  if (output.http_status !== null && (output.http_status < 200 || output.http_status >= 300)) {
    output.parse_status = "http_error";
    output.payload_loss = true;
    output.loss.parse_failure = true;
    output.loss.selected_root_unobserved = true;
    return output;
  }

  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch (_) {
    output.parse_status = "malformed";
    output.payload_loss = true;
    output.loss.parse_failure = true;
    output.loss.selected_root_unobserved = true;
    return output;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed) || !Array.isArray(parsed.records)) {
    output.parse_status = "malformed";
    output.payload_loss = true;
    output.loss.parse_failure = true;
    output.loss.selected_root_unobserved = true;
    return output;
  }

  const modelVersion = parsed.model_version;
  const records = parsed.records;
  output.records_seen = Math.min(records.length, MAX_API_RECORDS);
  output.records_dropped = Math.max(0, records.length - output.records_seen);
  output.loss.records_dropped = output.records_dropped;
  if (output.records_dropped > 0) output.payload_loss = true;
  let match = null;
  for (let index = 0; index < output.records_seen; index += 1) {
    const record = records[index];
    if (record && typeof record === "object" && !Array.isArray(record) &&
        typeof selectedRootId === "string" && typeof record.file_id === "string" &&
        record.file_id === selectedRootId) {
      match = record;
      break;
    }
  }
  if (match === null) {
    output.parse_status = "missing";
    output.match_status = "missing";
    output.payload_loss = true;
    output.loss.selected_root_unobserved = true;
    output.root = emptyRootProjection(modelVersion);
    return output;
  }
  output.parse_status = "projected";
  output.match_status = "matched";
  output.root = projectRootRecord(match, modelVersion);
  output.invalid_fields = countInvalidRootFields(match, output.root);
  if (output.invalid_fields > 0) output.payload_loss = true;
  return output;
}

function projectSse(eventType, data, atMs, source = null) {
  let payload = null;
  let parse = "unavailable";
  const payloadBytes = typeof data === "string" ? Buffer.byteLength(data, "utf8") : 0;
  const payloadOversize = payloadBytes > MAX_SSE_PAYLOAD_BYTES;
  if (typeof data === "string" && !payloadOversize) {
    try {
      const parsed = JSON.parse(data);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        payload = parsed;
        parse = "projected";
      }
    } catch (_) { /* bounded metadata intentionally retains no raw payload */ }
  }
  const modelVersion = projectModelVersion(payload?.model_version ?? payload?.global_model_version);
  const projected = {
    at_ms: Math.max(0, boundedInt(atMs) ?? 0),
    event_type: safeEventType(eventType),
    model_version: modelVersion,
    model_version_available: modelVersion !== null,
    payload_projection: payloadOversize ? "oversize" : parse,
    payload_oversize: payloadOversize,
    payload_skipped: payloadOversize || payload === null,
    payload_bytes: Math.min(payloadBytes, MAX_SSE_PAYLOAD_BYTES + 1),
  };
  if (source && typeof source === "object") {
    projected.source_id = boundedInt(source.source_id);
    projected.route_class = safeEventType(source.route_class);
    projected.scope_class = safeEventType(source.scope_class);
    projected.scope_digest = safeDigest(source.scope_digest);
  }
  return projected;
}

function projectSource(source) {
  return {
    source_id: boundedInt(source?.source_id),
    route_class: safeEventType(source?.route_class),
    scope_class: safeEventType(source?.scope_class),
    scope_digest: safeDigest(source?.scope_digest),
    request_start_ms: boundedInt(source?.request_start_ms),
    open_ms: boundedInt(source?.open_ms),
    first_model_page_ms: boundedInt(source?.first_model_page_ms),
    close_ms: boundedInt(source?.close_ms),
    navigation_ms: boundedInt(source?.navigation_ms),
    error_count: boundedInt(source?.error_count) ?? 0,
    event_count: boundedInt(source?.event_count) ?? 0,
  };
}

function projectNavigationEvent(event) {
  return {
    at_ms: boundedInt(event?.at_ms),
    kind: safeEventType(event?.kind),
    route_class: safeEventType(event?.route_class),
  };
}

function readG6Snapshot(value) {
  if (!value || typeof value !== "object" || !value.target || typeof value.target !== "object") {
    return { available: false, source: "g6_model_snapshot", reason: "missing" };
  }
  const target = value.target;
  return {
    available: true,
    source: "g6_model_snapshot",
    observed_utc: typeof value.observed_utc === "string" ? value.observed_utc : null,
    state: safeToken(target.state),
    local_size: boundedSafeInt(target.local_size),
    transferred_size: boundedSafeInt(target.transferred_size),
    remote_size: boundedSafeInt(target.remote_size),
    separate_from_ancestor: true,
  };
}

function makeResult() {
  return {
    schema: "incoming-recovery-g21-browser-ancestor-observer.v1",
    started_utc: new Date().toISOString(),
    status: "starting",
    auth_write_attempted: false,
    command_request_count: 0,
    filter_mutation_attempts: 0,
    original_filter: projectFilter("", false),
    observed_surface: "ancestor_root",
    dom_selector: "root_id",
    navigation: {
      dashboard_goto_start_ms: null,
      dashboard_domcontentloaded_ms: null,
      pair_api_match: false,
      pair_link_match_count: 0,
      selected_pair_route_clicked: false,
      selected_pair_route_class: null,
      file_list_ready_ms: null,
      selected_scope_verified: false,
      root_selector_pair_binding: false,
      events: [],
    },
    pair_lookup: emptyPairLookupReceipt(),
    browser_session: {
      startup_deadline_ms: BROWSER_SESSION_DEADLINE_MS,
      startup_deadline_exceeded: false,
    },
    timestamp_basis: {
      kind: "shared_capture_wall_clock_epoch_ms",
      origin_utc: null,
      event_expression: "Date.now() - capture_origin_ms",
      cross_document: true,
      monotonic: false,
      clock_adjustment_limit: "wall-clock adjustments can affect relative values",
    },
    api_timing: [],
    resource_timing: [],
    root_api: emptyRootApiProjection(),
    root_api_snapshots: [],
    root_api_snapshots_dropped: 0,
    root_api_snapshot_capture: {
      cadence_ms: ROOT_API_SNAPSHOT_INTERVAL_MS,
      max_snapshots: MAX_ROOT_API_SNAPSHOTS,
      snapshots_observed: 0,
      retention_policy: "initial_plus_recent",
      chronology_complete: true,
      payload_loss: false,
    },
    observer_capture: {
      method: "MutationObserver",
      flush_interval_ms: 1000,
      armed_at_ms: null,
      armed_utc: null,
      first_sample_ms: null,
      samples_observed: 0,
      first_present_root_ms: null,
      root_receipt_source: "mutation_observer",
      retention_policy: "initial_plus_recent",
      chronology_complete: true,
    },
    ancestor_row_found: false,
    ancestor_samples: [],
    ancestor_samples_dropped: 0,
    sse: { opened: 0, errors: 0, events: [], events_dropped: 0, payload_loss: false, payload_oversize_events: 0, payload_skipped_events: 0, retention_policy: "initial_plus_recent", chronology_complete: true, sources: [] },
    child_snapshot_receipt: { available: false, source: "g6_model_snapshot", reason: "not_supplied" },
    causal_claim: "unavailable_without_queue_to_target_join",
  };
}

function pathInside(root, candidate, allowSame = false) {
  const relative = path.relative(root, candidate);
  return (allowSame && relative === "") || (relative !== "" && !relative.startsWith("..") && !path.isAbsolute(relative));
}

function validateConfig(config) {
  if (!config || typeof config !== "object") throw new Error("config");
  const diagnosticsRoot = path.resolve(String(config.diagnosticsRoot || ""));
  const output = path.resolve(String(config.output || ""));
  const selectorPath = path.resolve(String(config.selectorPath || ""));
  const profile = path.resolve(String(config.profile || ""));
  if (!diagnosticsRoot || !output || !selectorPath || !profile) throw new Error("paths");
  if (!fs.existsSync(diagnosticsRoot) || !fs.statSync(diagnosticsRoot).isDirectory()) throw new Error("root");
  if (!pathInside(diagnosticsRoot, output) || !pathInside(diagnosticsRoot, selectorPath) || !pathInside(diagnosticsRoot, profile, true)) throw new Error("scope");
  if (!fs.existsSync(selectorPath) || !fs.statSync(selectorPath).isFile()) throw new Error("selector");
  if (!fs.existsSync(profile) || !fs.statSync(profile).isDirectory()) throw new Error("profile");
  let g6SnapshotPath = null;
  if (config.g6SnapshotPath) {
    g6SnapshotPath = path.resolve(String(config.g6SnapshotPath));
    if (!pathInside(diagnosticsRoot, g6SnapshotPath) || !fs.existsSync(g6SnapshotPath) || !fs.statSync(g6SnapshotPath).isFile()) throw new Error("g6_scope");
  }
  const baseUrl = String(config.baseUrl || "");
  let parsedUrl;
  try { parsedUrl = new URL(baseUrl); } catch (_) { throw new Error("origin"); }
  if (!/^https?:$/.test(parsedUrl.protocol) || parsedUrl.username || parsedUrl.password || parsedUrl.hash || parsedUrl.search) throw new Error("origin");
  const moduleName = String(config.playwrightModule || "playwright");
  if (!moduleName || moduleName.length > 256 || /[\x00-\x1F\x7F]/.test(moduleName)) throw new Error("playwright_module");
  const maxSeconds = Number(config.maxSeconds ?? 600);
  if (!Number.isFinite(maxSeconds) || maxSeconds < 1 || maxSeconds > 600) throw new Error("max_seconds");
  return { diagnosticsRoot, output, selectorPath, profile, g6SnapshotPath, baseUrl: baseUrl.replace(/\/$/, ""), playwrightModule: moduleName, maxSeconds: Math.floor(maxSeconds) };
}

function makeAtomicWriter(output, diagnosticsRoot) {
  const resolved = path.resolve(output);
  if (!pathInside(path.resolve(diagnosticsRoot), resolved)) throw new Error("output_scope");
  const parent = path.dirname(resolved);
  if (!fs.existsSync(parent) || !fs.statSync(parent).isDirectory()) throw new Error("output_parent");
  return value => {
    const temporary = `${resolved}.${process.pid}.${Date.now()}.tmp`;
    const text = JSON.stringify(value, null, 2) + "\n";
    const fd = fs.openSync(temporary, "w", 0o600);
    try {
      fs.writeFileSync(fd, text, { encoding: "utf8" });
      fs.fsyncSync(fd);
    } finally {
      fs.closeSync(fd);
    }
    fs.renameSync(temporary, resolved);
    try { fs.chmodSync(resolved, 0o600); } catch (_) { /* Windows ACLs remain caller-owned */ }
  };
}

function selectorPairBinding(rootId, pairId) {
  try {
    const parts = JSON.parse(String(rootId));
    return Array.isArray(parts) && String(parts[0]) === String(pairId);
  } catch (_) {
    return false;
  }
}

function selectorValue(value, name) {
  const text = String(value || "");
  if (!text || text.length > 512 || /[\x00-\x1F\x7F]/.test(text)) throw new Error(`${name}_shape`);
  return text;
}

function emptyPairLookupReceipt() {
  return {
    request_start_ms: null,
    response_end_ms: null,
    response_duration_ms: null,
    http_status: null,
    response_bytes: 0,
    response_bytes_capped: false,
    body_limit_bytes: MAX_PAIR_API_BYTES,
    parse_status: "unavailable",
    timeout_stage: null,
    match_status: "not_attempted",
    payload_loss: false,
  };
}

function projectPairLookupResponse(body, pairId, metadata = {}) {
  const output = emptyPairLookupReceipt();
  output.request_start_ms = boundedInt(metadata.request_start_ms);
  output.response_end_ms = boundedInt(metadata.response_end_ms);
  output.response_duration_ms = boundedInt(metadata.response_duration_ms);
  output.http_status = Number.isInteger(metadata.http_status) && metadata.http_status >= 0 && metadata.http_status <= 999
    ? metadata.http_status : null;
  const text = typeof body === "string" ? body : "";
  const byteLength = Buffer.byteLength(text, "utf8");
  const reportedBytes = Number.isInteger(metadata.response_bytes) && metadata.response_bytes >= 0 ? metadata.response_bytes : byteLength;
  output.response_bytes = Math.min(reportedBytes, MAX_PAIR_API_BYTES);
  output.response_bytes_capped = reportedBytes > MAX_PAIR_API_BYTES || byteLength > MAX_PAIR_API_BYTES;
  if (metadata.timeout_stage === "headers" || metadata.timeout_stage === "body") {
    output.parse_status = "timeout";
    output.timeout_stage = metadata.timeout_stage;
    output.payload_loss = true;
    return { name: null, receipt: output };
  }
  if (metadata.unavailable === true) {
    output.parse_status = "unavailable";
    output.payload_loss = true;
    return { name: null, receipt: output };
  }
  if (metadata.oversize === true || byteLength > MAX_PAIR_API_BYTES) {
    output.parse_status = "oversize";
    output.payload_loss = true;
    return { name: null, receipt: output };
  }
  if (output.http_status !== null && (output.http_status < 200 || output.http_status >= 300)) {
    output.parse_status = "http_error";
    output.payload_loss = true;
    return { name: null, receipt: output };
  }
  let parsed;
  try { parsed = JSON.parse(text); } catch (_) {
    output.parse_status = "malformed";
    output.payload_loss = true;
    return { name: null, receipt: output };
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed) || !Array.isArray(parsed.data)) {
    output.parse_status = "malformed";
    output.payload_loss = true;
    return { name: null, receipt: output };
  }
  const records = parsed.data;
  const recordsSeen = Math.min(records.length, 1024);
  if (records.length > recordsSeen) output.payload_loss = true;
  for (let index = 0; index < recordsSeen; index += 1) {
    const item = records[index];
    if (item && typeof item === "object" && !Array.isArray(item) &&
        typeof item.id === "string" && item.id === pairId && typeof item.name === "string" && item.name.length > 0) {
      output.parse_status = "projected";
      output.match_status = "matched";
      return { name: item.name, receipt: output };
    }
  }
  output.parse_status = "missing";
  output.match_status = "missing";
  output.payload_loss = true;
  return { name: null, receipt: output };
}

async function fetchPairLookup(page, pairId, started, sessionRemainingMs = null) {
  const requestStartMs = Math.max(0, Date.now() - started);
  if (sessionRemainingMs !== null && sessionRemainingMs <= 0) {
    return projectPairLookupResponse("", pairId, { request_start_ms: requestStartMs, response_end_ms: requestStartMs, timeout_stage: "headers" });
  }
  const fetched = await page.evaluate(async ({ pair, limit, headerTimeout, bodyTimeout, sessionRemaining }) => {
    const startedAt = performance.now();
    const controller = new AbortController();
    let stage = "headers";
    let timer = null;
    const headerBudget = Math.max(1, Math.min(headerTimeout, Number.isInteger(sessionRemaining) ? sessionRemaining : headerTimeout));
    const arm = budget => { timer = setTimeout(() => controller.abort(), budget); };
    const clear = () => { if (timer !== null) { clearTimeout(timer); timer = null; } };
    try {
      arm(headerBudget);
      const response = await fetch("/server/path-pairs", { credentials: "same-origin", signal: controller.signal });
      clear();
      const headerLength = Number(response.headers.get("content-length"));
      if (Number.isFinite(headerLength) && headerLength > limit) return { status: response.status, oversize: true, response_bytes: limit + 1, duration_ms: Math.max(0, Math.round(performance.now() - startedAt)) };
      stage = "body";
      arm(Math.max(1, Math.min(bodyTimeout, Number.isInteger(sessionRemaining) ? sessionRemaining : bodyTimeout)));
      if (!response.body || typeof response.body.getReader !== "function") { clear(); return { status: response.status, unavailable: true, duration_ms: Math.max(0, Math.round(performance.now() - startedAt)) }; }
      const reader = response.body.getReader();
      const chunks = [];
      let total = 0;
      while (true) {
        const item = await reader.read();
        if (item.done) break;
        if (!item.value) continue;
        total += item.value.byteLength;
        if (total > limit) { await reader.cancel(); clear(); return { status: response.status, oversize: true, response_bytes: limit + 1, duration_ms: Math.max(0, Math.round(performance.now() - startedAt)) }; }
        chunks.push(item.value);
      }
      clear();
      const bytes = new Uint8Array(total);
      let offset = 0;
      for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
      return { status: response.status, body: new TextDecoder().decode(bytes), response_bytes: total, duration_ms: Math.max(0, Math.round(performance.now() - startedAt)) };
    } catch (_) {
      clear();
      return controller.signal.aborted ? { status: null, timeout_stage: stage, duration_ms: Math.max(0, Math.round(performance.now() - startedAt)) } : { status: null, unavailable: true, duration_ms: Math.max(0, Math.round(performance.now() - startedAt)) };
    }
  }, { pair: pairId, limit: MAX_PAIR_API_BYTES, headerTimeout: PAIR_LOOKUP_HEADER_TIMEOUT_MS, bodyTimeout: PAIR_LOOKUP_BODY_TIMEOUT_MS, sessionRemaining: sessionRemainingMs });
  return projectPairLookupResponse(fetched.body || "", pairId, {
    request_start_ms: requestStartMs,
    response_end_ms: Math.max(0, Date.now() - started),
    response_duration_ms: boundedInt(fetched.duration_ms),
    http_status: fetched.status,
    oversize: fetched.oversize === true,
    unavailable: fetched.unavailable === true,
    timeout_stage: fetched.timeout_stage,
    response_bytes: fetched.response_bytes,
  });
}

async function fetchRootProjection(page, baseUrl, pairId, rootId, started, requestStartMs, sessionRemainingMs = null) {
  if (sessionRemainingMs !== null && sessionRemainingMs <= 0) {
    return projectRootApiResponse("", rootId, { request_start_ms: requestStartMs, response_end_ms: requestStartMs, timeout_stage: "headers" });
  }
  const endpoint = `${baseUrl}/server/model/v1/pairs/${encodeURIComponent(pairId)}/roots`;
  const fetched = await page.evaluate(async ({ endpoint, limit, headerTimeout, bodyTimeout, sessionRemaining }) => {
    const startedAt = performance.now();
    const controller = new AbortController();
    let stage = "headers";
    let timer = null;
    const arm = budget => { timer = setTimeout(() => controller.abort(), budget); };
    const clear = () => { if (timer !== null) { clearTimeout(timer); timer = null; } };
    try {
      arm(Math.max(1, Math.min(headerTimeout, Number.isInteger(sessionRemaining) ? sessionRemaining : headerTimeout)));
      const response = await fetch(endpoint, { credentials: "same-origin", signal: controller.signal });
      clear();
      const headerLength = Number(response.headers.get("content-length"));
      if (Number.isFinite(headerLength) && headerLength > limit) {
        return { status: response.status, oversize: true, response_bytes: limit + 1, duration_ms: Math.max(0, Math.round(performance.now() - startedAt)) };
      }
      stage = "body";
      arm(Math.max(1, Math.min(bodyTimeout, Number.isInteger(sessionRemaining) ? sessionRemaining : bodyTimeout)));
      if (!response.body || typeof response.body.getReader !== "function") { clear(); return { status: response.status, unavailable: true, duration_ms: Math.max(0, Math.round(performance.now() - startedAt)) }; }
      const reader = response.body.getReader();
      const chunks = [];
      let total = 0;
      try {
        while (true) {
          const item = await reader.read();
          if (item.done) break;
          if (!item.value) continue;
          total += item.value.byteLength;
          if (total > limit) {
            await reader.cancel();
            clear();
            return { status: response.status, oversize: true, response_bytes: limit + 1, duration_ms: Math.max(0, Math.round(performance.now() - startedAt)) };
          }
          chunks.push(item.value);
        }
        clear();
      } catch (_) {
        try { await reader.cancel(); } catch (_) { /* bounded failure cleanup */ }
        clear();
        return controller.signal.aborted ? { status: response.status, timeout_stage: stage, response_bytes: Math.min(total, limit), duration_ms: Math.max(0, Math.round(performance.now() - startedAt)) } : { status: response.status, unavailable: true, response_bytes: Math.min(total, limit), duration_ms: Math.max(0, Math.round(performance.now() - startedAt)) };
      }
      const bytes = new Uint8Array(total);
      let offset = 0;
      for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
      const body = new TextDecoder().decode(bytes);
      return { status: response.status, body, response_bytes: total, duration_ms: Math.max(0, Math.round(performance.now() - startedAt)) };
    } catch (_) {
      clear();
      return controller.signal.aborted ? { status: null, timeout_stage: stage, duration_ms: Math.max(0, Math.round(performance.now() - startedAt)) } : { status: null, unavailable: true, duration_ms: Math.max(0, Math.round(performance.now() - startedAt)) };
    }
  }, { endpoint, limit: MAX_API_BYTES, headerTimeout: ROOT_API_HEADER_TIMEOUT_MS, bodyTimeout: ROOT_API_BODY_TIMEOUT_MS, sessionRemaining: sessionRemainingMs });
  const responseEndMs = Math.max(0, Date.now() - started);
  const metadata = {
    request_start_ms: requestStartMs,
    response_end_ms: responseEndMs,
    response_duration_ms: boundedInt(fetched.duration_ms),
    http_status: fetched.status,
    oversize: fetched.oversize === true,
    unavailable: fetched.unavailable === true,
    timeout_stage: fetched.timeout_stage,
    response_bytes: fetched.response_bytes,
  };
  if (fetched.unavailable) return projectRootApiResponse("", rootId, { ...metadata, unavailable: true });
  if (fetched.oversize) return projectRootApiResponse("", rootId, metadata);
  return projectRootApiResponse(fetched.body, rootId, metadata);
}

async function liveCapture(rawConfig) {
  const config = validateConfig(rawConfig);
  const result = makeResult();
  const started = Date.now();
  result.started_utc = new Date(started).toISOString();
  result.timestamp_basis.origin_utc = result.started_utc;
  result.max_seconds = config.maxSeconds;
  const write = makeAtomicWriter(config.output, config.diagnosticsRoot);
  let pairId = null;
  let rootId = null;
  let startupDeadlineMs = started + BROWSER_SESSION_DEADLINE_MS;
  const startupBudget = () => {
    const remaining = Math.max(0, startupDeadlineMs - Date.now());
    if (remaining <= 0) {
      result.browser_session.startup_deadline_exceeded = true;
      throw new Error("startup_deadline");
    }
    return remaining;
  };
  let context;
  try {
    const selector = JSON.parse(fs.readFileSync(config.selectorPath, "utf8").replace(/^\uFEFF/, ""));
    pairId = selectorValue(selector.pair_id, "pair");
    rootId = selectorValue(selector.root_id, "root");
    result.navigation.root_selector_pair_binding = selectorPairBinding(rootId, pairId);
    if (config.g6SnapshotPath) {
      try {
        result.child_snapshot_receipt = readG6Snapshot(JSON.parse(fs.readFileSync(config.g6SnapshotPath, "utf8").replace(/^\uFEFF/, "")));
      } catch (_) {
        result.child_snapshot_receipt = { available: false, source: "g6_model_snapshot", reason: "malformed" };
      }
    }
    const { chromium } = require(config.playwrightModule);
    context = await chromium.launchPersistentContext(config.profile, { headless: true, viewport: { width: 1280, height: 800 }, timeout: startupBudget() });
    await context.addInitScript(({ originMs, maxSsePayloadBytes }) => {
      const atMs = () => Math.max(0, Date.now() - originMs);
      const safeTokenInPage = value => typeof value === "string" && /^[A-Za-z0-9_-]{1,48}$/.test(value) ? value : "unknown";
      const state = { opened: 0, errors: 0, events: [], eventsDropped: 0, payloadOversize: 0, payloadSkipped: 0, sources: [], sourcesDropped: 0, navigation: [] };
      const sourceByInstance = new WeakMap();
      const fingerprint = value => {
        const text = typeof value === "string" ? value : "";
        let hash = 2166136261;
        for (const code of new TextEncoder().encode(text)) { hash ^= code; hash = Math.imul(hash, 16777619) >>> 0; }
        return hash.toString(16).padStart(8, "0");
      };
      const describeUrl = value => {
        try {
          const pathname = new URL(String(value), window.location.href).pathname;
          if (/^\/server\/model\/v1\/pairs\/[^/]+\/stream$/.test(pathname)) return { route_class: "model_stream", scope_class: "scoped", scope_digest: fingerprint(pathname) };
          if (pathname === "/server/model/v1/summary/stream") return { route_class: "model_summary_stream", scope_class: "summary", scope_digest: null };
        } catch (_) { /* no raw URL is retained */ }
        return { route_class: "other", scope_class: "other", scope_digest: null };
      };
      const Original = window.EventSource;
      class ObservedEventSource extends Original {
        constructor(...args) {
          const source = { source_id: state.opened + 1, ...describeUrl(args[0]), request_start_ms: atMs(), open_ms: null, first_model_page_ms: null, close_ms: null, navigation_ms: null, error_count: 0, event_count: 0 };
          super(...args);
          state.opened += 1;
          if (state.sources.length < 64) state.sources.push(source);
          else { state.sources.splice(1, 1); state.sources.push(source); state.sourcesDropped += 1; }
          sourceByInstance.set(this, source);
          const capture = type => event => {
            source.event_count += 1;
            const eventAtMs = atMs();
            if (type === "open" && source.open_ms === null) source.open_ms = eventAtMs;
            if (type === "error") { state.errors += 1; source.error_count += 1; }
            if (state.events.length >= 128) { state.events.splice(1, 1); state.eventsDropped += 1; }
            const dataText = typeof event.data === "string" ? event.data : "";
            const payloadCodeUnits = dataText.length;
            const payloadBytes = payloadCodeUnits > maxSsePayloadBytes ? maxSsePayloadBytes + 1 : new TextEncoder().encode(dataText).byteLength;
            let data = null;
            const payloadOversize = payloadCodeUnits > maxSsePayloadBytes || payloadBytes > maxSsePayloadBytes;
            if (!payloadOversize) {
              try { data = dataText ? JSON.parse(dataText) : null; } catch (_) { /* unavailable */ }
            } else {
              state.payloadOversize += 1;
            }
            if (!data) state.payloadSkipped += 1;
            const candidate = data && typeof data === "object" && !Array.isArray(data) ? (Number.isInteger(data.model_version) ? data.model_version : data.global_model_version) : null;
            const modelVersion = Number.isInteger(candidate) && candidate >= 0 && candidate <= 2147483647 ? candidate : null;
            if (type === "model-page" && source.first_model_page_ms === null) source.first_model_page_ms = eventAtMs;
            state.events.push({ at_ms: eventAtMs, event_type: safeTokenInPage(type), model_version: modelVersion, model_version_available: modelVersion !== null, payload_projection: payloadOversize ? "oversize" : data ? "projected" : "unavailable", payload_oversize: payloadOversize, payload_skipped: !data, payload_bytes: Math.min(payloadBytes, maxSsePayloadBytes + 1), source_id: source.source_id, route_class: source.route_class, scope_class: source.scope_class, scope_digest: source.scope_digest });
          };
          ["open", "error", "status", "model-page", "model-summary", "model-patch", "model-reset"].forEach(type => this.addEventListener(type, capture(type)));
        }
        close(...args) { const source = sourceByInstance.get(this); if (source && source.close_ms === null) source.close_ms = atMs(); return super.close(...args); }
      }
      if (Original) window.EventSource = ObservedEventSource;
      const recordNavigation = kind => {
        const event = { at_ms: atMs(), kind: safeTokenInPage(kind) };
        if (state.navigation.length < 32) state.navigation.push(event);
        for (const source of state.sources) if (source.close_ms === null && source.navigation_ms === null) source.navigation_ms = event.at_ms;
      };
      window.addEventListener("pagehide", () => recordNavigation("pagehide"));
      window.addEventListener("beforeunload", () => recordNavigation("beforeunload"));
      window.__g21Capture = state;
    }, { originMs: started, maxSsePayloadBytes: MAX_SSE_PAYLOAD_BYTES });
    const page = await context.newPage();
    page.on("framenavigated", frame => {
      if (frame !== page.mainFrame() || result.navigation.events.length >= MAX_NAVIGATION_EVENTS) return;
      let routeClass = "unknown";
      try {
        const pathname = new URL(frame.url()).pathname;
        routeClass = pathname === "/dashboard" ? "dashboard" : /^\/dashboard\/[^/]+$/.test(pathname) ? "dashboard_selected_pair" : "other";
      } catch (_) { /* no URL retained */ }
      result.navigation.events.push({ at_ms: Math.max(0, Date.now() - started), kind: "frame_navigated", route_class: routeClass });
    });
    const requestStarted = new Map();
    const routeDescriptor = value => {
      try {
        const pathname = new URL(value).pathname;
        if (pathname === "/server/path-pairs") return { route_class: "path_pairs", scope_digest: null };
        if (/^\/server\/model\/v1\/pairs\/[^/]+\/roots$/.test(pathname)) return { route_class: "model_roots", scope_digest: null };
        if (/^\/server\/model\/v1\/pairs\/[^/]+\/stream$/.test(pathname)) return { route_class: "model_stream", scope_digest: textFingerprint(pathname) };
      } catch (_) { /* no URL retained */ }
      return null;
    };
    page.on("request", request => {
      try {
        if (new URL(request.url()).pathname.startsWith("/server/command/")) result.command_request_count += 1;
        const descriptor = routeDescriptor(request.url());
        if (descriptor) requestStarted.set(request, { kind: descriptor.route_class, scope_digest: descriptor.scope_digest, at_ms: Math.max(0, Date.now() - started) });
      } catch (_) { /* ignore malformed browser event */ }
    });
    page.on("response", response => {
      const timing = requestStarted.get(response.request());
      if (!timing || result.api_timing.length >= MAX_API_TIMING) return;
      result.api_timing.push({ route_class: timing.kind, scope_digest: timing.scope_digest, request_start_ms: timing.at_ms, status: Number.isInteger(response.status()) ? response.status() : null, response_end_ms: Math.max(0, Date.now() - started), response_duration_ms: Math.max(0, Date.now() - started - timing.at_ms) });
      requestStarted.delete(response.request());
    });
    result.status = "dashboard";
    result.navigation.dashboard_goto_start_ms = Math.max(0, Date.now() - started);
    await page.goto(`${config.baseUrl}/dashboard`, { waitUntil: "domcontentloaded", timeout: startupBudget() });
    result.navigation.dashboard_domcontentloaded_ms = Math.max(0, Date.now() - started);
    const filter = page.locator("#filter-search input[type=search]");
    const filterPresent = await filter.count() > 0;
    result.original_filter = projectFilter(filterPresent ? await filter.inputValue() : "", filterPresent);
    result.status = "pair_route";
    const pairLookup = await fetchPairLookup(page, pairId, started, startupBudget());
    result.pair_lookup = pairLookup.receipt;
    const pairName = pairLookup.name;
    if (!pairName) throw new Error("path_pair_lookup_failed");
    result.navigation.pair_api_match = true;
    const pairLink = page.getByRole("link", { name: pairName, exact: true });
    await pairLink.waitFor({ state: "visible", timeout: startupBudget() });
    result.navigation.pair_link_match_count = await pairLink.count();
    if (result.navigation.pair_link_match_count !== 1) throw new Error("pair_link_ambiguous");
    await pairLink.click();
    result.navigation.selected_pair_route_clicked = true;
    result.navigation.selected_pair_route_class = /^\/dashboard\/[^/]+$/.test(new URL(page.url()).pathname) ? "dashboard_selected_pair" : "unexpected_route";
    await page.waitForSelector("#file-list", { state: "attached", timeout: startupBudget() });
    result.navigation.file_list_ready_ms = Math.max(0, Date.now() - started);
    result.navigation.selected_scope_verified = result.navigation.selected_pair_route_class === "dashboard_selected_pair" && result.navigation.root_selector_pair_binding;
    if (!result.navigation.selected_scope_verified) throw new Error("selected_scope_unverified");
    const captureRootApiSnapshot = async phase => {
      const requestStart = Math.max(0, Date.now() - started);
      const sessionRemaining = startupDeadlineMs === null ? null : startupBudget();
      const snapshot = await fetchRootProjection(page, config.baseUrl, pairId, rootId, started, requestStart, sessionRemaining);
      addRootApiSnapshot(result, snapshot, phase, Math.max(0, Date.now() - started));
      return snapshot;
    };
    result.status = "root_api";
    result.root_api = await captureRootApiSnapshot("before_dom_arm");
    result.status = "ancestor_observer";
    const observerArm = await page.evaluate(({ ancestorId, originMs }) => {
      const normalize = () => {
        const node = [...document.querySelectorAll(".file[data-file-id]")].find(item => item.getAttribute("data-file-id") === ancestorId);
        return { present: !!node, status: node?.querySelector(".status img")?.id || node?.querySelector(".status .text")?.textContent?.trim() || "unknown", progress: node?.querySelector("[role=progressbar]")?.getAttribute("aria-valuenow") || null, size: node?.querySelector(".size_info")?.textContent?.trim().replace(/\s+/g, " ") || null };
      };
      const emit = () => {
        const sample = normalize();
        const text = JSON.stringify(sample);
        if (text === window.__g21AncestorLast) return;
        window.__g21AncestorLast = text;
        const samples = (window.__g21AncestorSamples ||= []);
        if (samples.length >= 128) {
          samples.splice(1, 1);
          window.__g21AncestorSamplesDropped = (window.__g21AncestorSamplesDropped || 0) + 1;
        }
        samples.push({ at_ms: Math.max(0, Date.now() - originMs), ...sample });
      };
      const list = document.querySelector("#file-list");
      if (!list) throw new Error("file_list_missing");
      new MutationObserver(emit).observe(list, { childList: true, subtree: true, attributes: true, characterData: true });
      emit();
      return { at_ms: Math.max(0, Date.now() - originMs), utc: new Date().toISOString() };
    }, { ancestorId: rootId, originMs: started });
    startupDeadlineMs = null;
    result.observer_capture.armed_at_ms = observerArm.at_ms;
    result.observer_capture.armed_utc = observerArm.utc;
    result.ancestor_row_found = await page.evaluate(id => !![...document.querySelectorAll(".file[data-file-id]")].find(node => node.getAttribute("data-file-id") === id), rootId);
    result.status = "armed";
    write(result);
    const end = Date.now() + config.maxSeconds * 1000;
    let nextWrite = Date.now() + 1000;
    let nextRootSnapshot = Date.now() + ROOT_API_SNAPSHOT_INTERVAL_MS;
    while (Date.now() < end) {
      await page.waitForTimeout(250);
      if (Date.now() >= nextRootSnapshot) {
        await captureRootApiSnapshot("armed");
        nextRootSnapshot = Date.now() + ROOT_API_SNAPSHOT_INTERVAL_MS;
      }
      if (Date.now() >= nextWrite) {
        const state = await page.evaluate(() => ({ samples: window.__g21AncestorSamples || [], samplesDropped: window.__g21AncestorSamplesDropped || 0, sse: window.__g21Capture || { opened: 0, errors: 0, events: [], eventsDropped: 0, sources: [], navigation: [] } }));
        result.ancestor_samples = state.samples.slice(0, MAX_SAMPLES).map(item => projectAncestor(item, item.at_ms));
        result.ancestor_samples_dropped = Math.min(MAX_COUNTER, (boundedInt(state.samplesDropped) ?? 0) + Math.max(0, state.samples.length - result.ancestor_samples.length));
        const samplesDropped = boundedInt(state.samplesDropped) ?? 0;
        result.observer_capture.samples_observed = Math.min(MAX_COUNTER, state.samples.length + samplesDropped);
        result.observer_capture.chronology_complete = samplesDropped === 0;
        result.observer_capture.first_sample_ms = state.samples.length ? boundedInt(state.samples[0].at_ms) : null;
        const firstPresent = state.samples.find(item => item.present === true);
        result.observer_capture.first_present_root_ms = firstPresent ? boundedInt(firstPresent.at_ms) : null;
        result.sse = { opened: boundedInt(state.sse.opened) ?? 0, errors: boundedInt(state.sse.errors) ?? 0, events: state.sse.events.slice(0, MAX_SSE_EVENTS), events_dropped: boundedInt(state.sse.eventsDropped) ?? 0, payload_loss: (boundedInt(state.sse.payloadSkipped) ?? 0) > 0 || state.sse.events.some(item => item.payload_projection !== "projected"), payload_oversize_events: boundedInt(state.sse.payloadOversize) ?? 0, payload_skipped_events: boundedInt(state.sse.payloadSkipped) ?? 0, retention_policy: "initial_plus_recent", chronology_complete: (boundedInt(state.sse.eventsDropped) ?? 0) === 0 && (boundedInt(state.sse.sourcesDropped) ?? 0) === 0, sources: state.sse.sources.slice(0, MAX_SSE_SOURCES).map(projectSource), sources_dropped: boundedInt(state.sse.sourcesDropped) ?? 0 };
        result.navigation.events = result.navigation.events.concat(state.sse.navigation.slice(0, MAX_NAVIGATION_EVENTS).map(projectNavigationEvent)).slice(0, MAX_NAVIGATION_EVENTS);
        write(result);
        nextWrite = Date.now() + 1000;
      }
    }
    await captureRootApiSnapshot("armed_final");
    const state = await page.evaluate(() => ({ samples: window.__g21AncestorSamples || [], samplesDropped: window.__g21AncestorSamplesDropped || 0, sse: window.__g21Capture || { opened: 0, errors: 0, events: [], eventsDropped: 0, sources: [], navigation: [] } }));
    result.ancestor_samples = state.samples.slice(0, MAX_SAMPLES).map(item => projectAncestor(item, item.at_ms));
    result.ancestor_samples_dropped = Math.min(MAX_COUNTER, (boundedInt(state.samplesDropped) ?? 0) + Math.max(0, state.samples.length - result.ancestor_samples.length));
    const samplesDropped = boundedInt(state.samplesDropped) ?? 0;
    result.observer_capture.samples_observed = Math.min(MAX_COUNTER, state.samples.length + samplesDropped);
    result.observer_capture.chronology_complete = samplesDropped === 0;
    result.observer_capture.first_sample_ms = state.samples.length ? boundedInt(state.samples[0].at_ms) : null;
    const firstPresent = state.samples.find(item => item.present === true);
    result.observer_capture.first_present_root_ms = firstPresent ? boundedInt(firstPresent.at_ms) : null;
    result.sse = { opened: boundedInt(state.sse.opened) ?? 0, errors: boundedInt(state.sse.errors) ?? 0, events: state.sse.events.slice(0, MAX_SSE_EVENTS), events_dropped: boundedInt(state.sse.eventsDropped) ?? 0, payload_loss: (boundedInt(state.sse.payloadSkipped) ?? 0) > 0 || state.sse.events.some(item => item.payload_projection !== "projected"), payload_oversize_events: boundedInt(state.sse.payloadOversize) ?? 0, payload_skipped_events: boundedInt(state.sse.payloadSkipped) ?? 0, retention_policy: "initial_plus_recent", chronology_complete: (boundedInt(state.sse.eventsDropped) ?? 0) === 0 && (boundedInt(state.sse.sourcesDropped) ?? 0) === 0, sources: state.sse.sources.slice(0, MAX_SSE_SOURCES).map(projectSource), sources_dropped: boundedInt(state.sse.sourcesDropped) ?? 0 };
    result.navigation.events = result.navigation.events.concat(state.sse.navigation.slice(0, MAX_NAVIGATION_EVENTS).map(projectNavigationEvent)).slice(0, MAX_NAVIGATION_EVENTS);
    result.resource_timing = await page.evaluate(({ baseUrl, originMs }) => {
      const classify = value => {
        try {
          const pathname = new URL(value, baseUrl).pathname;
          if (pathname === "/server/path-pairs") return "path_pairs";
          if (/^\/server\/model\/v1\/pairs\/[^/]+\/roots$/.test(pathname)) return "model_roots";
          if (/^\/server\/model\/v1\/pairs\/[^/]+\/stream$/.test(pathname)) return "model_stream";
        } catch (_) { /* identity-free timing only */ }
        return null;
      };
      return performance.getEntriesByType("resource").map(entry => {
        const routeClass = classify(entry.name);
        if (!routeClass) return null;
        return { route_class: routeClass, start_ms: Math.max(0, Math.round(performance.timeOrigin + entry.startTime - originMs)), duration_ms: Math.max(0, Math.round(entry.duration)) };
      }).filter(Boolean).slice(0, 64);
    }, { baseUrl: config.baseUrl, originMs: started });
    result.status = "completed";
  } catch (error) {
    result.status = "capture_failure";
    if (startupDeadlineMs !== null && Date.now() >= startupDeadlineMs) result.browser_session.startup_deadline_exceeded = true;
    result.failure_class = String(error?.name || "Error").replace(/[^A-Za-z0-9_]/g, "_").slice(0, 48);
  } finally {
    result.ended_utc = new Date().toISOString();
    try { write(result); } finally { if (context) await context.close(); }
  }
  return result;
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function syntheticSelfCheck(output, diagnosticsRoot) {
  const result = makeResult();
  result.status = "self_check";
  result.original_filter = projectFilter("existing-filter", true);
  const rootId = "[\"pair\",\"root\"]";
  const matchingBody = JSON.stringify({ model_version: 42, records: [{ file_id: rootId, name: "private-name", state: "downloading", local_size: 12, remote_size: 20, transferred_size: 8, display_size_total: 20, display_transferred_size: 8, download_progress: 40, complete_local_coverage: false, final_move_succeeded: false }] });
  result.root_api = projectRootApiResponse(matchingBody, rootId, { request_start_ms: 4, response_end_ms: 9, response_duration_ms: 5, http_status: 200 });
  addRootApiSnapshot(result, result.root_api, "before_dom_arm", 9);
  addRootApiSnapshot(result, projectRootApiResponse(JSON.stringify({ model_version: 43, records: [{ file_id: rootId, state: "downloading", transferred_size: 9 }] }), rootId, { request_start_ms: 20, response_end_ms: 25, response_duration_ms: 5, http_status: 200 }), "armed", 25);
  result.ancestor_row_found = true;
  result.ancestor_samples = [projectAncestor({ present: true, status: "default", progress: "42", size: "1.2 GiB / 2.0 GiB" }, 10)];
  result.sse.events = [projectSse("model-summary", JSON.stringify({ model_version: 42, private_id: "not-retained" }), 11)];
  result.sse.payload_loss = false;
  result.child_snapshot_receipt = readG6Snapshot({ observed_utc: "2026-09-21T00:00:00.000Z", target: { state: "downloading", local_size: 120, transferred_size: 80, remote_size: 200 } });
  const missing = projectRootApiResponse(JSON.stringify({ model_version: 43, records: [] }), rootId, { http_status: 200 });
  const oversize = projectRootApiResponse("x".repeat(MAX_API_BYTES + 1), rootId, { http_status: 200 });
  const malformed = projectRootApiResponse("{not-json", rootId, { http_status: 200 });
  assert(result.root_api.parse_status === "projected" && result.root_api.match_status === "matched", "matching_root");
  assert(missing.parse_status === "missing" && missing.match_status === "missing", "missing_root");
  assert(oversize.parse_status === "oversize" && oversize.oversize && oversize.payload_loss, "oversize_root");
  assert(malformed.parse_status === "malformed" && malformed.payload_loss, "malformed_root");
  assert(Object.keys(result.root_api.root).join(",") === ROOT_FIELDS.join(","), "root_field_schema");
  assert(result.root_api_snapshots.length === 2 && result.root_api_snapshots[0].capture_phase === "before_dom_arm" && result.root_api_snapshots[1].capture_phase === "armed", "root_snapshot_order");
  const serialized = JSON.stringify(result);
  assert(!serialized.includes(rootId) && !serialized.includes("private-name") && !serialized.includes("existing-filter") && !serialized.includes("not-retained"), "privacy");
  assert(result.ancestor_samples.length <= MAX_SAMPLES && result.sse.events.length <= MAX_SSE_EVENTS, "bounds");
  result.status = "self_check_pass";
  result.ended_utc = new Date().toISOString();
  if (output) {
    const root = path.resolve(diagnosticsRoot || path.dirname(path.resolve(output)));
    if (!fs.existsSync(root)) fs.mkdirSync(root, { recursive: true });
    makeAtomicWriter(output, root)(result);
  }
  return { schema: result.schema, status: result.status, root_parse: result.root_api.parse_status, missing_parse: missing.parse_status, oversize_parse: oversize.parse_status, malformed_parse: malformed.parse_status, filter_unchanged: result.filter_mutation_attempts === 0, output: output || null };
}

function parseArgs(argv) {
  const config = {};
  let selfCheck = false;
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--self-check") { selfCheck = true; continue; }
    if (!arg.startsWith("--") || index + 1 >= argv.length) throw new Error("arguments");
    const key = arg.slice(2).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
    config[key] = argv[++index];
  }
  return { config, selfCheck };
}

if (require.main === module) {
  (async () => {
    try {
      const parsed = parseArgs(process.argv.slice(2));
      const environmentConfig = {
        diagnosticsRoot: process.env.DIAGNOSTICS_ROOT,
        output: process.env.OUTPUT_PATH,
        selectorPath: process.env.SELECTOR_PATH,
        profile: process.env.BROWSER_PROFILE,
        g6SnapshotPath: process.env.G6_SNAPSHOT_PATH,
        baseUrl: process.env.PROBE_BASE_URL,
        playwrightModule: process.env.PLAYWRIGHT_MODULE,
        maxSeconds: process.env.MAX_SECONDS,
      };
      for (const [key, value] of Object.entries(environmentConfig)) {
        if (parsed.config[key] === undefined && value !== undefined && value !== "") parsed.config[key] = value;
      }
      if (parsed.selfCheck) {
        const output = parsed.config.output || null;
        const root = parsed.config.diagnosticsRoot || (output ? path.dirname(path.resolve(output)) : process.cwd());
        console.log(JSON.stringify(syntheticSelfCheck(output, root)));
      } else {
        console.log(JSON.stringify(await liveCapture(parsed.config)));
      }
    } catch (error) {
      console.error(String(error?.message || "observer_failed"));
      process.exitCode = 1;
    }
  })();
}

module.exports = {
  MAX_API_BYTES,
  MAX_API_RECORDS,
  MAX_PAIR_API_BYTES,
  MAX_ROOT_API_SNAPSHOTS,
  MAX_SAMPLES,
  MAX_SSE_PAYLOAD_BYTES,
  MAX_SSE_EVENTS,
  BROWSER_SESSION_DEADLINE_MS,
  PAIR_LOOKUP_HEADER_TIMEOUT_MS,
  PAIR_LOOKUP_BODY_TIMEOUT_MS,
  ROOT_API_HEADER_TIMEOUT_MS,
  ROOT_API_BODY_TIMEOUT_MS,
  ROOT_API_SNAPSHOT_INTERVAL_MS,
  ROOT_FIELDS,
  addRootApiSnapshot,
  emptyRootApiProjection,
  emptyRootProjection,
  makeResult,
  projectAncestor,
  projectFilter,
  projectRootApiResponse,
  projectRootRecord,
  projectSse,
  readG6Snapshot,
  syntheticSelfCheck,
  validateConfig,
  liveCapture,
};
