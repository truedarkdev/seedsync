# Remembered Browser Bootstrap Investigation

Last updated: 2026-04-05
Status: implementation landed, targeted validation passed

## Purpose

This note tracks the long-running issue where a browser that was previously
remembered still gets sent back to `/bootstrap` later instead of reopening the
normal app flow.

The goal is to keep a durable record of:
- what we have observed
- what we have already tried or confirmed
- what we have not yet ruled out
- what the next focused checks should be

## Intended UX Model

This investigation should be judged against the intended browser-access UX, not
against the current temporary-session implementation.

Intended behavior:
- the first browser goes through the bootstrap or handover claim flow
- once claimed, that browser should behave like a durable trusted client
- a remembered browser is closer to an automatically issued browser-scoped API
  credential than to a short-lived UI session
- routine return visits should reopen the app without forcing the user back
  through `/bootstrap`
- `/bootstrap` should mainly appear for first claim, explicit recovery, or
  deliberate revoke or reset events

Implication for implementation work:
- a fix is not good enough if it only makes the cookie persistent while the
  backing server-side authorization still expires like a short-lived session

## Trigger For This Note

On 2026-04-05, the app again redirected the browser to `/bootstrap` even
though commit `824ca257e54a3e7efba6150d4efc1e4b4813b39e` had previously been
landed to make the remembered-browser cookie long-lived.

That commit looked like it should help, but the same user-visible symptom came
back in the live Docker-served app.

## Current Symptom

- Visiting `http://127.0.0.1:8800/` redirects to `/bootstrap`
- Visiting `http://127.0.0.1:8800/dashboard` also redirects to `/bootstrap`
- Visiting `http://127.0.0.1:8800/bootstrap` returns the bootstrap page
- The live Chrome window title during repro was `SeedSync browser access`

Observed on 2026-04-05 against the local Docker-served app.

## What Commit `824ca257` Actually Changed

Commit `824ca257e54a3e7efba6150d4efc1e4b4813b39e` added cookie `Max-Age`
handling when issuing the `seedsync_ui_session` cookie from:

- `src/python/web/handler/admin.py`
  - bootstrap exchange
  - first API key bootstrap
  - remember browser session

Relevant effect:
- the browser cookie is now persistent instead of being session-only

Important limitation:
- this commit did not change the server-side UI session lifetime
- this commit did not change the `/bootstrap` fallback logic

## Main Findings So Far

### 1. The live app is currently choosing bootstrap on purpose

This is not just a stale browser tab.

Runtime evidence from 2026-04-05:
- `GET /` returned `303` to `/bootstrap`
- `GET /dashboard` returned `303` to `/bootstrap`
- `GET /bootstrap` returned `200`

That means the server believes there is no usable remembered browser session.

### 2. The persisted auth store currently has no UI sessions

Inside the running container, `/config/api-keys.json` currently shows:

```json
"ui_sessions": []
```

That is the strongest concrete explanation found so far for why the app is
falling back to `/bootstrap`: there is no stored browser authorization record
available to match the remembered browser cookie.

### 3. The prior implementation used a 12-hour server-side UI session TTL

Relevant code:
- `src/python/web/auth_store.py`

Important prior behavior:
- `_UI_SESSION_TTL = timedelta(hours=12)`
- UI sessions are created with that TTL
- expired UI sessions are pruned before persistence writes

This meant:
- the cookie may live longer in the browser than before
- but the backing server-side authorization still ages out after 12 hours
- once the record is gone from the persisted store, the cookie alone cannot
  reopen the app

This is the main current mismatch with the intended UX model.
A remembered browser is supposed to behave like a durable trusted client, not
like a 12-hour UI session with a longer-lived cookie wrapper.

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
- once a remembered browser session passes the 12-hour TTL, later store writes
  can reduce `ui_sessions` back to `[]`

### 5. The redirect logic itself was not changed by `824ca257`

Relevant code:
- `src/python/web/web_app.py`

Current behavior:
- if `__get_ui_session()` cannot resolve a valid session, the app redirects to
  `/bootstrap`

So the problem is not primarily in the redirect path. The redirect is doing
what the current auth state tells it to do.

## What We Have Confirmed

- The live Docker app repro is real on 2026-04-05.
- The browser is not taking the remembered-browser path.
- Commit `824ca257` is present and still only affects cookie issuance.
- The live persisted auth store currently contains no UI sessions.
- The prior server-side remembered session model expired after 12 hours.
- Expired sessions are pruned from persistence.

## What We Have Not Yet Proven

- Whether the missing UI session was removed because of normal 12-hour expiry
  or because the store was reset for another reason
- Whether any earlier `ui_sessions` entry existed in this exact container state
  and later disappeared
- Whether `api-keys.json` was ever considered invalid and replaced via the
  `PersistError` fallback path
- Whether browser cookie presence or absence exactly matched the empty store at
  the time of repro
- Which implementation shape best matches the intended durable-browser model:
  long-lived persisted browser credential, refresh-on-use retention, or another
  durable browser authorization design

## Implementation Conclusion

The code now separates the two browser-access lifetimes:

- bootstrap and recovery flows continue to use short-lived UI sessions
- `/server/browser/v1/remember` now issues a remembered-browser record that
  is stored durably as hidden state attached to the API key
- normal browser auth resolution still reads the same cookie name, but it now
  accepts either a short-lived bootstrap UI session or a durable remembered
  browser record
- deleting or revoking the API key clears any remembered-browser state tied to
  that key

Practical effect:

- a remembered browser should stay remembered until the API key itself is
  revoked or deleted
- the old "cookie lives longer than the server-side session" mismatch should
  no longer be the default remembered-browser behavior

## Plausible Explanations Ranked By Current Evidence

### Most likely

The long-lived cookie fix only solved browser-side persistence.
Under the old model, the backing UI session still expired after 12 hours,
got pruned from the auth store, and then the app correctly redirected back to
`/bootstrap`.

Translated into UX terms, the old implementation behaved like
"remember this browser for one short session window" instead of
"trust this browser until revoke or recovery."

### Also possible

`/config/api-keys.json` may have been reset after a parse or load problem.
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
the live Docker/browser flow:

1. create a fresh remembered browser session
2. confirm the new `ui_sessions` entry is written to `/config/api-keys.json`
3. revoke the backing API key and confirm the remembered-browser state
   disappears with it
4. verify the browser falls back to bootstrap after the revoke
5. confirm bootstrap is still short-lived while remembered-browser state is
   governed by the API key lifecycle rather than a separate browser setting

### Additional targeted checks

- check for `.bak` or related backup evidence that `api-keys.json` was replaced
- capture startup logs around auth-store loading
- directly confirm browser cookie presence after remember-browser flow
- shape the implementation around the intended product meaning:
  - remembered browser = durable trusted browser credential
  - bootstrap = first claim or explicit recovery path
  - remembered-browser state is hidden behind the API key lifecycle and is
    cleared when the key is revoked or deleted

## What We Should Avoid Repeating Blindly

- assuming cookie persistence alone solves the full remembered-browser problem
- treating a redirect to `/bootstrap` as a frontend routing issue first
- retrying the same live repro without capturing the persisted auth-store state
- calling the issue solved until the browser cookie and the server-side session
  retention model are both verified together
