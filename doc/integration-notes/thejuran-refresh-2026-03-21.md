# TheJuran Refresh Ledger - 2026-03-21

Tracked checkpoint:
- `a8561cdc318460de32de082e3cf33f6b6a0093cb`

Frozen upstream tip:
- `36c13b3f0acf5b9f0df738cd9964a44f31885771`

Range rule:
- Process this range strictly oldest-to-newest.
- Do not move past a commit without recording a disposition here or in the tracker.
- Treat this ledger as the source of truth until its durable state is folded back into `doc/integration-tracker.md`.

Disposition values:
- `already integrated`
- `covered elsewhere`
- `intentionally skipped`
- `needs subject reopen`
- `needs new integration task`
- `maintainer decision needed`

Review state values:
- `reviewed`
- `not yet reviewed`

## Current Ledger

| Upstream commit | Review state | Disposition | Notes |
| --- | --- | --- | --- |
| `f63e7cf5935433389acce7cc943859a834147ff4` `fix: prevent race condition in extract integration tests` | `not yet reviewed` |  |  |
| `1eae2c56a9f2556d0ead2cafa8cbe93b7fd5349f` `fix: use POST for restart in E2E setup script` | `not yet reviewed` |  | Local commit `caa3ebb8` already updates the same caller for batch coherence, so this commit may disposition as `covered elsewhere` when reviewed in order. |
| `d671686e982bdec4bc42910689d9db1125a37943` `ci: add dependabot config ignoring webpack minor+ bumps` | `not yet reviewed` |  |  |
| `6f4920dddd29315067db8022b509d11d22f06b59` `ci: ignore major version bumps in dependabot config` | `not yet reviewed` |  |  |
| `4090a2aea9ce7acc4cbf02663a003dff860cb9e9` `ci: ignore TypeScript and zone.js minor+ bumps in dependabot` | `not yet reviewed` |  |  |
| `cea63a27b502869241d704aabe266e18200d167c` `fix: resolve 13 of 16 dependabot security alerts` | `not yet reviewed` |  |  |
| `c5eb482bfbf3d8eab73d31f746ef795272a3505a` `chore: add GSD milestone M001 — Angular 21 Migration` | `not yet reviewed` |  |  |
| `db497146b623aaa860d641c24b78e18919215242` `feat: upgrade Angular 19 → 21 with full dependency refresh (M001)` | `not yet reviewed` |  |  |
| `3767adafbc3893ad85643de7b2b7212d8dc7b2e9` `fix: move provideZoneChangeDetection into appConfig, clean empty imports` | `not yet reviewed` |  |  |
| `c6318bc50b88ab1ffa978852193d61be321cc42a` `refactor: address code review findings from Angular 21 migration` | `not yet reviewed` |  |  |
| `ab74f8c53d9daf1e765315808bf995a6ea01974a` `fix: resolve undici CVE via npm override, revert strictTemplates` | `not yet reviewed` |  |  |
| `32387fca344f2b3c75eb9ab2e0f2cf4eceb58da1` `fix(ci): migrate to application builder to fix Karma test hang in Docker` | `not yet reviewed` |  |  |
| `36c13b3f0acf5b9f0df738cd9964a44f31885771` `fix(e2e): add change detection waits after clicks for esbuild builder` | `not yet reviewed` |  |  |

## Next Unresolved Commit

- `f63e7cf5935433389acce7cc943859a834147ff4` `fix: prevent race condition in extract integration tests`

## Next Candidate Batch

- `f63e7cf5935433389acce7cc943859a834147ff4` `fix: prevent race condition in extract integration tests`
- `1eae2c56a9f2556d0ead2cafa8cbe93b7fd5349f` `fix: use POST for restart in E2E setup script`
