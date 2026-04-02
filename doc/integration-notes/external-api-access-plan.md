# Staged Plan - Scoped API Keys for External API Access

Status: staged plan with backend foundation, the browser/bootstrap follow-up, the Settings UI slice, Slice 4 write-path hardening, and the Slice 5 live-browser UI debug pass now completed locally. The next work should focus on Slice 6 post-hardening follow-ups, then the final security regression lane, rather than reopening the already-landed backend auth boundary or browser bootstrap boundary.

Immediate next step: start Slice 6 post-hardening follow-ups on top of the already-landed backend auth, browser bootstrap, Settings UI, Slice 4 write-path hardening, and Slice 5 live-browser verification. The main remaining work is the follow-up hardening around URL-path config writes, the first-admin bootstrap exception, and reverse-proxy / TLS-offload handling for strict origin checks.

## Core Direction

Keep the original feature intent intact:

- named API keys with explicit scopes
- backend-issued browser session for the built-in UI
- legacy `general.api_token` compatibility during rollout
- SSE constraints handled without relying on custom `EventSource` headers
- host allowlisting as secondary hardening, not the main security boundary

The current code shape still points to the same seams:

- `src/python/web/web_app.py` currently owns the `/server/*` host gate, but not the app shell routes.
- `src/python/seedsync.py` still warns that `general.api_token` is stored but not enforced.
- `src/angular/src/app/services/base/stream-service.registry.ts` uses plain `EventSource`.
- `src/angular/src/app/pages/settings/settings-page.component.ts` is the natural UI anchor later, but it is not part of the first slice.

## First Execution Slice

Backend foundation only:

1. Add a dedicated persisted auth store for scoped API keys.
2. Route every `/server/*` request through one central method/path/scope gate before the handler runs.
3. Keep legacy-token compatibility in the backend, but only for the non-admin external route path during rollout.
4. Add the migration-state endpoint and legacy-token disable/clear endpoints.

### Concrete backend scope model

- `read` for non-mutating API calls
- `write` for mutating API calls and server commands
- `stream` for `/server/stream`
- `admin` for API-key management, bootstrap, and migration-state endpoints

### Backend slice details

- Persist keys in a new structured auth store such as `api-keys.json`, separate from `settings.cfg`.
- Store only metadata and a verifier/hash, never the raw secret after creation.
- Keep the route map central so unregistered `/server/*` routes fail closed.
- Preserve legacy `general.api_token` only as compatibility for external non-admin routes during rollout.
- Never allow the legacy token to satisfy `/server/admin/*` or the later non-loopback UI bootstrap path.
- Keep the migration-state path explicit so operators can see whether legacy compatibility is present, enabled, or clearable.

### Explicit out of scope for slice 1

- backend-issued browser session cookie
- shell/static asset bootstrap protection
- UI login/session flow
- Settings-page key management UI
- CSRF handling for cookie-auth writes
- converting or blocking the legacy mutating GET routes
- any UI service changes beyond reading the backend contract later

### Acceptance criteria for slice 1

- Every `/server/*` route is checked by one central auth and scope gate.
- Unknown or unregistered `/server/*` routes fail closed.
- Scoped API keys can be created, listed, updated, revoked, and rotated from the backend store without persisting raw secrets.
- Legacy token compatibility still works for the intended external non-admin path, but it is rejected for admin and bootstrap-class access.
- `GET /server/admin/migration/v1` reports the legacy-token state clearly.
- `POST /server/admin/migration/v1/legacy-api-token/disable` and `POST /server/admin/migration/v1/legacy-api-token/clear` update that state cleanly.

## Follow-Up Slices

### Slice 2: UI bootstrap and SSE session path

Completed locally:

- The app shell and static asset routes now use an explicit loopback browser-bootstrap boundary.
- Non-loopback browser shell/static requests fail closed; there is no new remote-browser login flow.
- Loopback browser access still issues the built-in UI session cookie, including deep links.
- `/server/stream` remains aligned with the browser session cookie so the built-in UI works without bearer headers in `EventSource`.

### Slice 3: Settings UI

- Completed locally:
  - Added an API Access section inside the existing Settings page.
  - Provided key CRUD, scope selection, rotation, revocation, and migration controls.
  - Surfaced the migration banner while legacy compatibility remains active.
  - Consumed the existing admin/API-key JSON endpoints through a dedicated Angular service.

### Slice 5: Full Playwright UI debug pass for API Access

Completed locally:

- Exercised the API Access UI against the live Docker-served app, including:
  - create
  - update/edit
  - rotate
  - revoke
  - legacy disable
  - legacy clear
- Checked non-happy-path browser behavior for blank-name and no-scope validation errors.
- Captured live screenshots for the key management flow, secret reveal, and legacy migration banner states.
- Did not uncover a blocking API Access UI bug in the live browser pass, so no follow-up UI patch was required for this slice.

- Run a full live-browser Playwright debugging pass over the new API Access UI, not just targeted happy-path checks.
- Exercise every primary action end-to-end:
  - create
  - update/edit
  - rotate
  - revoke
  - legacy disable
  - legacy clear
- Check important non-happy-path UX and correctness details:
  - labeling clarity, especially `Key ID` vs secret
  - loading, empty, and error states
  - secret reveal wording and behavior
  - confirmation dialog copy
  - layout/spacing/readability in the real Settings page
- Fix the issues found in that browser pass as one follow-up batch before treating the API Access UI as fully polished.

### Slice 6: Post-hardening follow-ups

- Move `/server/config/set/<section>/<key>/<value>` away from URL-path values and into a POST body so secrets and other sensitive config values no longer travel in the request path.
- Decide and implement the intended protection for the first-admin bootstrap route so the one-time localhost bootstrap flow is not left as a cross-site-triggerable exception by accident.
- Decide and document the supported reverse-proxy / TLS-offload behavior for strict origin matching, and add proxy-aware handling if those deployments are expected to work with cookie-authenticated write/admin requests.
- Treat this as follow-up hardening work after Slice 4, not as a blocker for the write-path hardening already landed.

### Slice 7: Revoked key lifecycle polish

- Keep revoked API keys available for audit/history, but do not show them by default in the API Access list.
- Add an explicit UI affordance, such as a button or toggle, to reveal previously revoked keys when needed.
- Fully lock revoked key records in the UI so they cannot be edited after revocation.
- Add an explicit remove/delete path for revoked keys so operators can permanently dismiss old revoked entries from the visible history when they no longer want to keep them around.
- Implement the backend/API contract needed for that delete path if it does not already exist, and cover the new behavior in both UI and backend verification.
- After the implementation lands, run a full live-browser / Playwright debug pass over the revoked-key flow to confirm the buttons are actually clickable, the behavior works end-to-end, and the visual style still reads correctly in the real Settings page.

### Slice 4: Write-path hardening

Completed locally:

- Converted the legacy config and autoqueue write-by-GET routes to POST-only handlers and updated the in-repo callers that still depended on those routes.
- Added same-origin browser gating for cookie-authenticated non-read server writes, including `admin`-scoped actions.
- Tightened the origin check to compare the full origin tuple rather than hostname alone.
- Verified the slice with targeted Python tests plus rebuilt live Docker probes for same-origin and cross-origin cookie-auth write/admin behavior.

- Convert or block the legacy mutating GET routes.
- Add same-origin and CSRF enforcement for cookie-auth writes.
- Keep legacy token compatibility only where the rollout still requires it.

### Final follow-up: security regression lane

- Add a small dedicated security-regression test lane for the external API access model.
- Cover negative cases that should stay blocked, especially:
  - reverse-proxy or same-host topology assumptions around passwordless local-browser trust
  - mixed auth precedence across bearer, cookie, and irrelevant `Authorization` headers
  - revoked or rotated key behavior on protected routes
  - admin bootstrap boundary checks
- Do a full security audit of the four access categories (`read`, `write`, `stream`, `admin`) and verify that no privilege leaks or unintended capability overlaps exist between them.
- Run that audit with one dedicated security subagent per access category, with each subagent responsible for checking the security boundary and wrapping for its own category before the results are merged.
- Check specifically for cross-scope leaks such as:
  - write-capable actions reachable from read-only access
  - API-key or migration-management actions reachable from non-admin scopes
  - stream access accidentally exposing admin-only or write-capable data paths
  - any route or UI/session behavior where combined lower scopes accidentally behave like `admin`
- Keep this at the end of the API access work plan rather than treating it as a blocker for the earlier backend/browser slices.

## Notes To Preserve

- Do not treat host allowlisting as the real authorization boundary.
- Do not mint a privileged session from `index.html` alone.
- Do not use query-string API keys as the primary SSE design.
- Keep browser session state separate from API-key storage.
- Keep the plan backend-first so the security boundary exists before the UI depends on it.
