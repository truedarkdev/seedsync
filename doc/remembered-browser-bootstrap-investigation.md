# Remembered Browser Bootstrap Investigation

Last updated: 2026-04-07
Status: implementation landed, live validation passed

## Purpose

This note tracks the remembered-browser / first-access problem and keeps the
durable evidence that explains both the original symptom and the fix.

The goal is to preserve:

- what we observed in the live Docker app
- what we confirmed in persisted state and durable history
- what we still have not proven
- what regression checks still matter later

## Intended UX Model

This investigation should be judged against the intended browser-access UX,
not against the old temporary-session implementation.

Intended behavior:

- the first browser goes through the bootstrap or handover claim flow
- once claimed, that browser behaves like a durable trusted client
- a remembered browser is closer to an automatically issued browser-scoped API
  credential than to a short-lived UI session
- routine return visits reopen the app without forcing the user back through
  `/bootstrap`
- `/bootstrap` mainly appears for first claim, explicit recovery, or deliberate
  revoke or reset events

Implication for implementation work:

- a fix is not good enough if it only makes the cookie persistent while the
  backing server-side authorization still expires like a short-lived session

## Trigger For This Note

On 2026-04-05, the app again redirected the browser to `/bootstrap` even
though commit `824ca257e54a3e7efba6150d4efc1e4b4813b39e` had previously been
landed to make the remembered-browser cookie long-lived.

That commit looked like it should help, but the same user-visible symptom came
back in the live Docker-served app.

The live evidence showed a browser/server mismatch: the remembered browser
state existed on the client, but the server-side persisted `ui_sessions`
collection was empty, so the app had nothing to resolve and correctly fell
back to `/bootstrap`.

## Current State

The first-access claim flow has now been hardened so that browser access is
forced back to `/bootstrap` until the claim completes, and the first-admin
bootstrap path now creates a remembered browser session instead of a short-
lived one.

That closes the gap where an existing remembered session could still reach the
app while first access was open.

The bootstrap page still serves the exact assets it needs:

- `/assets/favicon.png`
- `/assets/logo.png`

Traversal-style variants of those asset paths still redirect to `/bootstrap`.

## Historical Symptom

- Visiting `http://127.0.0.1:8800/` redirected to `/bootstrap`
- Visiting `http://127.0.0.1:8800/dashboard` also redirected to `/bootstrap`
- Visiting `http://127.0.0.1:8800/bootstrap` returned the bootstrap page
- The live Chrome window title during repro was `SeedSync browser access`

Observed on 2026-04-05 against the local Docker-served app.

## Main Findings So Far

### 1. The live app was choosing bootstrap on purpose

This was not just a stale browser tab.

Runtime evidence from 2026-04-05:

- `GET /` returned `303` to `/bootstrap`
- `GET /dashboard` returned `303` to `/bootstrap`
- `GET /bootstrap` returned `200`

That means the server believed there was no usable remembered browser session.

### 2. The persisted auth store had no UI sessions for the remembered browser

Inside the running container, `/config/api-keys.json` showed:

```json
"ui_sessions": []
```

That is the strongest concrete explanation found so far for why the app was
falling back to `/bootstrap`: there was no stored browser authorization record
available to match the remembered browser cookie.

The most likely removal path is an auth-store reset or recovery path, not an
ordinary Docker restart.

### 3. The prior implementation used a 12-hour server-side UI session TTL

Relevant code:

- `src/python/web/auth_store.py`

Important prior behavior:

- `_UI_SESSION_TTL = timedelta(hours=12)`
- UI sessions are created with that TTL
- expired UI sessions are pruned before persistence writes

This meant:

- the cookie may live longer in the browser than before
- but the backing server-side authorization still aged out after 12 hours
- once the record was gone from the persisted store, the cookie alone could
  not reopen the app

This was the main mismatch with the intended UX model.
A remembered browser was behaving like a 12-hour UI session with a longer
lived cookie wrapper.

### 4. Expired UI sessions were actively removed during persistence

Relevant code path:

- `src/python/web/auth_store.py`
  - `__prune_expired_ui_sessions()`
  - `to_str()`
  - `to_file()`

Prior behavior:

- before the store is serialized, expired `ui_sessions` are pruned
- recurring persistence and shutdown persistence both reuse that logic

Likely outcome under the old model:

- once a remembered browser session passed the 12-hour TTL, later store
  writes could reduce `ui_sessions` back to `[]`

### 5. The redirect logic itself was not changed by `824ca257`

Relevant code:

- `src/python/web/web_app.py`

Current behavior:

- if `__get_ui_session()` cannot resolve a valid session, the app redirects
  to `/bootstrap`

So the problem was not primarily in the redirect path. The redirect was doing
what the current auth state told it to do.

### 6. Durable auth history is now recorded next to the store

The auth store now writes an append-only history file beside the main persisted
state:

- `api-keys.history.jsonl` next to `api-keys.json`

It records the lifecycle events that matter for this investigation, including:

- `store_loaded`
- `store_saved`
- `api_key_created`
- `api_key_updated`
- `api_key_rotated`
- `api_key_revoked`
- `api_key_deleted`
- `ui_session_created`
- `ui_sessions_discarded`
- bootstrap proof and bootstrap exchange consume or clear events

Why it exists:

- it preserves durable lifecycle evidence even when the current JSON store no
  longer shows the original state
- it lets us distinguish browser-cookie churn from persisted auth-store churn
- it makes revoke, discard, and recovery behavior visible across restarts or
  store reloads

That telemetry became active after rebuilding the Docker app.

Concrete newer evidence from 2026-04-07:

- live validation recorded `ui_session_created` with reason
  `remembered_browser_session_created`
- the same persistent browser profile reopened to `/dashboard`
- `GET /server/stream` returned `200`
- `GET /server/admin/api-keys/v1` returned `200`
- fresh claims were recorded in durable history
- the history showed `api_key_created`
- the history showed `ui_session_created`
- the history showed `ui_sessions_discarded`
- the history showed `api_key_revoked` for the older key

## What We Have Confirmed

- The live Docker app repro was real on 2026-04-05.
- The browser was not taking the remembered-browser path.
- Commit `824ca257` is present and still only affects cookie issuance.
- The live persisted auth store had no UI sessions.
- The prior server-side remembered session model expired after 12 hours.
- Expired sessions are pruned from persistence.
- Durable auth-store history is now being written next to `api-keys.json`.
- Fresh 2026-04-07 claim activity is visible in `api-keys.history.jsonl`.
- The first-admin bootstrap path now creates remembered-browser state tied to
  the created API key.
- Targeted auth tests passed with `python -m pytest
  tests/unittests/test_web/test_handler/test_admin_handler.py -q` (`14
  passed`).
- Live Docker plus Playwright validation confirmed the same browser profile
  reopened to `/dashboard` and kept access to `/server/stream` and
  `/server/admin/api-keys/v1`.
- The first-access hardening now forces browser access back to `/bootstrap`
  until the claim completes.
- Exact bootstrap assets are allowed while traversal-style asset paths still
  redirect.

## What We Have Not Yet Proven

- Whether the missing UI session was removed because of normal 12-hour expiry,
  a reset or recovery path, or another store mutation
- Whether any earlier `ui_sessions` entry existed in this exact container
  state and later disappeared
- Whether `api-keys.json` was ever considered invalid and replaced via the
  `PersistError` fallback path
- Whether browser cookie presence or absence exactly matched the empty store at
  the time of repro
- Which remaining edge cases still need explicit regression coverage around
  store reset, recovery, or rehydrate behavior

## Implementation Conclusion

The code now separates the two browser-access lifetimes and closes the first
access bypass:

- bootstrap proof exchange remains short-lived, while first-admin bootstrap now
  creates remembered-browser state tied to the API key
- `/server/browser/v1/remember` now issues a remembered-browser record that is
  stored durably as hidden state attached to the API key
- normal browser auth resolution still reads the same cookie name, but it now
  accepts either a short-lived bootstrap UI session or a durable remembered
  browser record
- deleting or revoking the API key clears any remembered-browser state tied to
  that key
- while the first-access claim is open, existing browser sessions are forced
  back to `/bootstrap` until claim completion
- the bootstrap page keeps only the exact static assets it needs, while
  traversal-style asset requests still bounce back to `/bootstrap`

Practical effect:

- a remembered browser should stay remembered until the API key itself is
  revoked or deleted
- the old "cookie lives longer than the server-side session" mismatch should
  no longer be the default remembered-browser behavior
- the open-first-claim security gap no longer leaves an existing browser able
  to skip the bootstrap gate
- the first-admin bootstrap path now reopens to the authenticated app after a
  browser close/reopen in the validated profile
- the bootstrap-logo regression from the hardening is contained by the exact
  asset allowlist

Bootstrap hardening note:

- the asset allowlist is intentionally tiny
- only the exact bootstrap logo and favicon are served directly
- any traversal-style or path-manipulated asset request still redirects to
  `/bootstrap`

## Plausible Explanations Ranked By Current Evidence

### Most likely

The long-lived cookie fix only solved browser-side persistence.
Under the old model, the backing UI session still expired after 12 hours, got
pruned from the auth store, and then the app correctly redirected back to
`/bootstrap`.

Translated into UX terms, the old implementation behaved like "remember this
browser for one short session window" instead of "trust this browser until
revoke or recovery."

### Also possible

`/config/api-keys.json` may have been reset after a parse or load problem, or
via a recovery path that replaced the store with a fresh one.
The current load path can replace the store with a fresh one if a
`PersistError` happens while loading persisted data.

### Lower confidence possibilities

- malformed timestamp data caused unexpected prune behavior
- another invalidation path cleared the remembered session
- request trust or origin gates rejected an otherwise-present session in a way
  we have not yet observed directly

## Relevant Files

- `src/python/web/auth_store.py`
- `src/python/web/handler/admin.py`
- `src/python/web/web_app.py`
- `src/python/seedsync.py`

## Recommended Next Checks

### Highest-value next investigation

Capture and preserve the full lifecycle of one remembered-browser session in
the live Docker/browser flow if a regression reappears:

1. create a fresh remembered browser session through the first-admin or
   remember-browser path
2. confirm the new `ui_sessions` entry is written to `/config/api-keys.json`
3. revoke the backing API key and confirm the remembered-browser state
   disappears with it
4. verify the browser falls back to bootstrap after the revoke
5. confirm the remembered-browser state is governed by the API key lifecycle
   rather than a separate browser-only setting

### Additional targeted checks

- check for `.bak` or related backup evidence that `api-keys.json` was replaced
- capture startup logs around auth-store loading
- directly confirm browser cookie presence after remember-browser flow
- shape the implementation around the intended product meaning:
  - remembered browser = durable trusted browser credential
  - bootstrap = first claim or explicit recovery path
  - remembered-browser state is hidden behind the API key lifecycle and is
    cleared when the key is revoked or deleted
- add a regression check that an open first-access claim cannot be bypassed by
  an already remembered browser
- keep the bootstrap asset allowlist exact so traversal-style asset requests
  still redirect to `/bootstrap`
