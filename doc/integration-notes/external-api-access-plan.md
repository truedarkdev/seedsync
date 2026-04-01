# Staged Plan - Scoped API Keys for External API Access

Status: staged plan with backend foundation, the browser/bootstrap follow-up, and the Settings UI slice now implemented locally. The next work should focus on Slice 4 write-path hardening, not on reopening the backend auth boundary or the browser bootstrap boundary that the first three slices established.

Immediate next step: start Slice 4 write-path hardening on top of the already-landed backend auth, browser bootstrap, and Settings UI foundation. The app shell/static routes now fail closed outside loopback or the explicit trusted local Docker runtime source, local browser access still issues the built-in UI session cookie, and `/server/stream` remains aligned with that session flow. That trusted bootstrap source is operationally sensitive and is not intended to be writable through the generic write-scoped config setter.

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

### Slice 4: Write-path hardening

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
- Keep this at the end of the API access work plan rather than treating it as a blocker for the earlier backend/browser slices.

## Notes To Preserve

- Do not treat host allowlisting as the real authorization boundary.
- Do not mint a privileged session from `index.html` alone.
- Do not use query-string API keys as the primary SSE design.
- Keep browser session state separate from API-key storage.
- Keep the plan backend-first so the security boundary exists before the UI depends on it.
