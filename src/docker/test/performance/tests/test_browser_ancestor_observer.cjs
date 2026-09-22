const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const { test } = require("node:test");

const observer = require("../browser_ancestor_observer.cjs");

function tempRoot() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "seedsync-browser-observer-"));
}

test("offline self-check covers root response states, privacy, filter, and bounds", () => {
  const root = tempRoot();
  const output = path.join(root, "self-check.json");
  const summary = observer.syntheticSelfCheck(output, root);
  assert.equal(summary.status, "self_check_pass");
  assert.equal(summary.root_parse, "projected");
  assert.equal(summary.missing_parse, "missing");
  assert.equal(summary.oversize_parse, "oversize");
  assert.equal(summary.malformed_parse, "malformed");
  const written = JSON.parse(fs.readFileSync(output, "utf8"));
  assert.equal(written.filter_mutation_attempts, 0);
  assert.equal(written.root_api_snapshots.length, 2);
  assert.equal(written.root_api_snapshots[0].capture_phase, "before_dom_arm");
  assert.equal(written.root_api_snapshots[1].capture_phase, "armed");
  assert.ok(written.ancestor_samples.length <= observer.MAX_SAMPLES);
  assert.ok(written.sse.events.length <= observer.MAX_SSE_EVENTS);
  assert.ok(!JSON.stringify(written).includes("existing-filter"));
});

test("root projection keeps the typed field set and accounts for loss", () => {
  const rootId = "[\"pair-1\",\"root-1\"]";
  const matching = observer.projectRootApiResponse(JSON.stringify({
    model_version: 19,
    records: [{
      file_id: rootId,
      name: "private-name",
      state: "downloading",
      local_size: 10,
      remote_size: 20,
      transferred_size: 5,
      display_size_total: 20,
      display_transferred_size: 5,
      download_progress: 25,
      complete_local_coverage: false,
      final_move_succeeded: false,
    }],
  }), rootId, { http_status: 200, request_start_ms: 2, response_end_ms: 4, response_duration_ms: 2 });
  assert.equal(matching.parse_status, "projected");
  assert.equal(matching.match_status, "matched");
  assert.deepEqual(Object.keys(matching.root), observer.ROOT_FIELDS);
  assert.equal(matching.root.model_version, 19);
  assert.equal(matching.root.display_transferred_size, 5);
  assert.ok(!JSON.stringify(matching).includes(rootId));
  assert.ok(!JSON.stringify(matching).includes("private-name"));

  const changed = observer.projectRootApiResponse(JSON.stringify({ model_version: 20, records: [{ file_id: rootId, state: "downloading", transferred_size: 10 }] }), rootId, { http_status: 200 });
  assert.equal(changed.root.transferred_size, 10);
  assert.notEqual(matching.root.transferred_size, changed.root.transferred_size);

  const boundedSnapshots = observer.makeResult();
  for (let index = 0; index < observer.MAX_ROOT_API_SNAPSHOTS + 1; index += 1) {
    observer.addRootApiSnapshot(boundedSnapshots, index === 0 ? matching : changed, index === 0 ? "before_dom_arm" : "armed", index);
  }
  assert.equal(boundedSnapshots.root_api_snapshots.length, observer.MAX_ROOT_API_SNAPSHOTS);
  assert.equal(boundedSnapshots.root_api_snapshots_dropped, 1);
  assert.equal(boundedSnapshots.root_api_snapshot_capture.snapshots_observed, observer.MAX_ROOT_API_SNAPSHOTS + 1);
  assert.equal(boundedSnapshots.root_api_snapshots[0].capture_phase, "before_dom_arm");
  assert.equal(boundedSnapshots.root_api_snapshots.at(-1).root.transferred_size, 10);
  assert.equal(boundedSnapshots.root_api_snapshot_capture.chronology_complete, false);
  observer.addRootApiSnapshot(boundedSnapshots, { ...changed, payload_loss: true }, "armed_final", 999);
  assert.equal(boundedSnapshots.root_api_snapshot_capture.payload_loss, true);

  const missing = observer.projectRootApiResponse(JSON.stringify({ model_version: 20, records: [] }), rootId, { http_status: 200 });
  assert.equal(missing.parse_status, "missing");
  assert.equal(missing.loss.selected_root_unobserved, true);

  const malformed = observer.projectRootApiResponse("{", rootId, { http_status: 200 });
  assert.equal(malformed.parse_status, "malformed");
  assert.equal(malformed.loss.parse_failure, true);

  const oversize = observer.projectRootApiResponse("x".repeat(observer.MAX_API_BYTES + 1), rootId, { http_status: 200 });
  assert.equal(oversize.parse_status, "oversize");
  assert.equal(oversize.oversize, true);
  assert.equal(oversize.response_bytes, observer.MAX_API_BYTES);
  assert.equal(oversize.loss.response_oversize, true);

  const bounded = observer.projectRootApiResponse(JSON.stringify({
    model_version: 1,
    records: Array.from({ length: observer.MAX_API_RECORDS + 1 }, (_, index) => ({ file_id: `other-${index}` })),
  }), rootId, { http_status: 200 });
  assert.equal(bounded.records_seen, observer.MAX_API_RECORDS);
  assert.equal(bounded.records_dropped, 1);
  assert.equal(bounded.payload_loss, true);

  const oversizedSse = observer.projectSse("model-page", "x".repeat(32769), 4);
  assert.equal(oversizedSse.payload_projection, "oversize");
  assert.equal(oversizedSse.payload_skipped, true);
  assert.equal(oversizedSse.model_version, null);
});

test("runtime config requires an explicit safe origin and scoped paths", () => {
  const root = tempRoot();
  const selector = path.join(root, "selector.json");
  const profile = path.join(root, "profile");
  fs.writeFileSync(selector, JSON.stringify({ pair_id: "pair", root_id: "[\"pair\",\"root\"]" }));
  fs.mkdirSync(profile);
  assert.throws(() => observer.validateConfig({
    diagnosticsRoot: root,
    output: path.join(root, "out.json"),
    selectorPath: selector,
    profile,
    baseUrl: "http://user:secret@example.invalid",
    playwrightModule: "playwright",
    maxSeconds: 1,
  }), /origin/);
  assert.throws(() => observer.validateConfig({
    diagnosticsRoot: root,
    output: path.join(os.tmpdir(), "out.json"),
    selectorPath: selector,
    profile,
    baseUrl: "http://127.0.0.1:1",
    playwrightModule: "playwright",
    maxSeconds: 1,
  }), /scope/);
});

function startFixture(rootId, options = {}) {
  let rootRequests = 0;
  const heldResponses = new Set();
  const holdResponse = (request, response, body = null) => {
    if (body !== null) response.write(body);
    heldResponses.add(response);
    request.on("close", () => heldResponses.delete(response));
  };
  const server = http.createServer(async (request, response) => {
    const url = new URL(request.url, "http://127.0.0.1");
    if (url.pathname === "/dashboard") {
      response.writeHead(200, { "content-type": "text/html" });
      response.end(`<!doctype html><div id="filter-search"><input type="search" value="existing-filter"></div><a href="/dashboard/pair-1">Pair One</a>`);
      return;
    }
    if (url.pathname === "/dashboard/pair-1") {
      response.writeHead(200, { "content-type": "text/html" });
      response.end(`<!doctype html><div id="filter-search"><input type="search" value="existing-filter"></div><div id="file-list"></div><script>
        const fixtureRootId = ${JSON.stringify(rootId)};
        const stream = new EventSource('/server/model/v1/pairs/pair-1/stream');
        fetch('/server/model/v1/pairs/pair-1/roots').then(response => response.json()).then(() => setTimeout(() => {
          const row = document.createElement('div');
          row.className = 'file';
          row.dataset.fileId = fixtureRootId;
          row.innerHTML = '<span class="status"><span class="text">downloading</span></span><span class="size_info">10 / 20</span><div role="progressbar" aria-valuenow="25"></div>';
          document.querySelector('#file-list').append(row);
        }, 30));
      </script>`);
      return;
    }
    if (url.pathname === "/server/path-pairs") {
      if (options.hangPairHeaders === true) { holdResponse(request, response); return; }
      response.writeHead(200, { "content-type": "application/json" });
      if (options.hangPairBody === true) { holdResponse(request, response, '{"data":['); return; }
      response.end(JSON.stringify({ data: [{ id: "pair-1", name: "Pair One" }] }));
      return;
    }
    if (url.pathname === "/server/model/v1/pairs/pair-1/roots") {
      if (options.hangRootHeaders === true) { holdResponse(request, response); return; }
      await new Promise(resolve => setTimeout(resolve, 5));
      rootRequests += 1;
      const transferred = rootRequests >= 3 ? 10 : 5;
      response.writeHead(200, { "content-type": "application/json" });
      if (options.hangRootBody === true) { holdResponse(request, response, '{"model_version":1,"records":['); return; }
      response.end(JSON.stringify({ model_version: 7 + rootRequests, records: [{ file_id: rootId, name: "private-name", state: "downloading", local_size: 10, remote_size: 20, transferred_size: transferred, display_size_total: 20, display_transferred_size: transferred, download_progress: transferred * 5, complete_local_coverage: false, final_move_succeeded: false }] }));
      return;
    }
    if (url.pathname === "/server/model/v1/pairs/pair-1/stream") {
      response.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-cache", connection: "keep-alive" });
      const ssePayload = options.multibyteOversizeSse === true ? "€".repeat(11000) : options.oversizeSse === true ? "x".repeat(observer.MAX_SSE_PAYLOAD_BYTES + 1) : '{"model_version":8}';
      response.write(`event: model-page\ndata: ${ssePayload}\n\n`);
      setTimeout(() => response.end(), 50);
      return;
    }
    response.writeHead(404);
    response.end();
  });
  server.__fixtureCleanup = () => { for (const response of heldResponses) response.destroy(); };
  return new Promise(resolve => server.listen(0, "127.0.0.1", () => resolve(server)));
}

async function stopFixture(server) {
  if (typeof server.__fixtureCleanup === "function") server.__fixtureCleanup();
  await new Promise(resolve => server.close(resolve));
}

test("local Playwright fixture preserves root-response before DOM-observer ordering", async t => {
  let playwright;
  try {
    playwright = require(process.env.PERF_PLAYWRIGHT_MODULE || "playwright");
  } catch (_) {
    return t.skip("Playwright is not installed in this worker environment");
  }
  const root = tempRoot();
  const profile = path.join(root, "profile");
  fs.mkdirSync(profile);
  const rootId = "[\"pair-1\",\"root-1\"]";
  const selectorPath = path.join(root, "selector.json");
  const output = path.join(root, "capture.json");
  fs.writeFileSync(selectorPath, JSON.stringify({ pair_id: "pair-1", root_id: rootId }));
  const server = await startFixture(rootId);
  try {
    const address = server.address();
    const baseUrl = `http://127.0.0.1:${address.port}`;
    let result;
    try {
      result = await observer.liveCapture({ diagnosticsRoot: root, output, selectorPath, profile, baseUrl, playwrightModule: process.env.PERF_PLAYWRIGHT_MODULE || "playwright", maxSeconds: 1 });
    } catch (error) {
      if (/browser|chromium|executable/i.test(String(error?.message || error))) return t.skip(`Playwright browser unavailable: ${error.message}`);
      throw error;
    }
    assert.equal(result.status, "completed");
    assert.equal(result.root_api.parse_status, "projected");
    assert.equal(result.root_api.match_status, "matched");
    assert.ok(result.root_api_snapshots.length >= 2);
    assert.ok(new Set(result.root_api_snapshots.map(snapshot => snapshot.root.transferred_size)).size >= 2);
    assert.equal(result.navigation.selected_scope_verified, true);
    assert.equal(result.filter_mutation_attempts, 0);
    assert.equal(result.ancestor_row_found, true);
    assert.ok(result.root_api.response_end_ms <= result.observer_capture.armed_at_ms);
    assert.ok(result.observer_capture.armed_at_ms <= result.observer_capture.first_present_root_ms);
    assert.ok(result.sse.events.some(event => event.event_type === "model-page" && event.model_version === 8));
    const written = fs.readFileSync(output, "utf8");
    assert.ok(!written.includes(rootId));
    assert.ok(!written.includes("Pair One"));
    assert.ok(!written.includes("existing-filter"));
  } finally {
    await stopFixture(server);
  }
});

test("local Playwright fixtures bound hung pair/root phases and retain output", { timeout: 55000 }, async t => {
  let playwright;
  try {
    playwright = require(process.env.PERF_PLAYWRIGHT_MODULE || "playwright");
  } catch (_) {
    return t.skip("Playwright is not installed in this worker environment");
  }
  const cases = [
    { label: "pair-headers", options: { hangPairHeaders: true }, check: result => {
      assert.equal(result.status, "capture_failure");
      assert.equal(result.pair_lookup.parse_status, "timeout");
      assert.equal(result.pair_lookup.timeout_stage, "headers");
    } },
    { label: "pair-body", options: { hangPairBody: true }, check: result => {
      assert.equal(result.status, "capture_failure");
      assert.equal(result.pair_lookup.parse_status, "timeout");
      assert.equal(result.pair_lookup.timeout_stage, "body");
    } },
    { label: "root-headers", options: { hangRootHeaders: true }, check: result => {
      assert.equal(result.root_api.parse_status, "timeout");
      assert.equal(result.root_api.timeout_stage, "headers");
      assert.equal(result.root_api.payload_loss, true);
      assert.ok(result.status === "completed" || result.status === "capture_failure");
    } },
    { label: "root-body", options: { hangRootBody: true }, check: result => {
      assert.equal(result.root_api.parse_status, "timeout");
      assert.equal(result.root_api.timeout_stage, "body");
      assert.equal(result.root_api.payload_loss, true);
      assert.ok(result.status === "completed" || result.status === "capture_failure");
    } },
    { label: "sse-oversize", options: { oversizeSse: true }, check: result => {
      assert.ok(result.sse.payload_oversize_events >= 1);
      assert.ok(result.sse.payload_skipped_events >= 1);
      assert.equal(result.sse.payload_loss, true);
      assert.ok(result.sse.events.some(event => event.payload_projection === "oversize" && event.model_version === null));
    } },
    { label: "sse-multibyte-oversize", options: { multibyteOversizeSse: true }, check: result => {
      const multibytePayload = "€".repeat(11000);
      assert.ok(multibytePayload.length <= observer.MAX_SSE_PAYLOAD_BYTES);
      assert.ok(Buffer.byteLength(multibytePayload, "utf8") > observer.MAX_SSE_PAYLOAD_BYTES);
      assert.ok(result.sse.payload_oversize_events >= 1);
      assert.ok(result.sse.payload_skipped_events >= 1);
      assert.equal(result.sse.payload_loss, true);
      assert.ok(result.sse.events.some(event => event.payload_projection === "oversize" && event.model_version === null));
    } },
  ];
  for (const item of cases) {
    const root = tempRoot();
    const profile = path.join(root, "profile");
    fs.mkdirSync(profile);
    const rootId = "[\"pair-1\",\"root-1\"]";
    const selectorPath = path.join(root, "selector.json");
    const output = path.join(root, `${item.label}.json`);
    fs.writeFileSync(selectorPath, JSON.stringify({ pair_id: "pair-1", root_id: rootId }));
    const server = await startFixture(rootId, item.options);
    try {
      const address = server.address();
      const result = await observer.liveCapture({ diagnosticsRoot: root, output, selectorPath, profile, baseUrl: `http://127.0.0.1:${address.port}`, playwrightModule: process.env.PERF_PLAYWRIGHT_MODULE || "playwright", maxSeconds: 1 });
      assert.equal(fs.existsSync(output), true);
      const written = JSON.parse(fs.readFileSync(output, "utf8"));
      assert.equal(written.schema, "incoming-recovery-g21-browser-ancestor-observer.v1");
      item.check(result);
    } finally {
      await stopFixture(server);
    }
  }
});
