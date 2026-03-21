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
| `70800124629800a98a1f6f05f2fe16b727a0f177` `docs: start milestone v3.2 Security Hardening II` | `reviewed` | `intentionally skipped` | `.planning` milestone doc only; this fork does not mirror upstream `.planning` workflow files. |
| `b0f024195126177c2b0246579131b6bcf06efb51` `docs: complete project research` | `reviewed` | `intentionally skipped` | `.planning/research` docs only; preserve the idea through normal integration work instead of importing the planning tree. |
| `560c488027cdd22f8d21d321ac2c2ce5349f0c68` `docs: define milestone v3.2 requirements` | `reviewed` | `intentionally skipped` | `.planning/REQUIREMENTS.md` only; not part of this fork's durable workflow model. |
| `5febd5d51ec30aa5d38027e13500147c6c42e7d4` `docs: create milestone v3.2 roadmap (5 phases)` | `reviewed` | `intentionally skipped` | Planning-only roadmap in `.planning`. |
| `b2b20f99299f9a1ce6fd3815dd26c837621d264b` `docs(47): research phase isolated-backend-hardening` | `reviewed` | `intentionally skipped` | Planning/research-only phase doc in `.planning`. |
| `9d68b1a5a42e1997f2ee8f58f4abb957a0d1ed7c` `docs(47): create phase plan` | `reviewed` | `intentionally skipped` | Planning-only phase doc in `.planning`. |
| `7f025d7864c3e56dfe6cda3b15ced4ea7832a752` `test(47-01): add failing tests for config file 0600 permission hardening` | `reviewed` | `already integrated` | Adapted together with `b5afc96c0599c9e82e7b92f920487c3b79a4e587` in local commit `f4efff8e`. |
| `97bb7d0104dbc0bf8c3a96039d0afe259dbbf9f5` `test(47-03): add failing tests for SSH topology redaction` | `reviewed` | `already integrated` | Adapted together with `b7c5a40fd0ef2c3d52cc07b1bb8f87fb78ca8ec5` and `e99c1ec375134b504b772ce211664e25f2305902` in local commit `caa3ebb8`. |
| `b5afc96c0599c9e82e7b92f920487c3b79a4e587` `feat(47-01): harden config file permissions to 0600 in Persist` | `reviewed` | `already integrated` | Adapted together with `7f025d7864c3e56dfe6cda3b15ced4ea7832a752` in local commit `f4efff8e`. |
| `b7c5a40fd0ef2c3d52cc07b1bb8f87fb78ca8ec5` `feat(47-03): implement SSH topology redaction in _redact_sensitive()` | `reviewed` | `already integrated` | Adapted together with `97bb7d0104dbc0bf8c3a96039d0afe259dbbf9f5` and `e99c1ec375134b504b772ce211664e25f2305902` in local commit `caa3ebb8`. |
| `e99c1ec375134b504b772ce211664e25f2305902` `fix(47-02): change Angular restart service from GET to POST` | `reviewed` | `already integrated` | Adapted together with `97bb7d0104dbc0bf8c3a96039d0afe259dbbf9f5` and `b7c5a40fd0ef2c3d52cc07b1bb8f87fb78ca8ec5` in local commit `caa3ebb8`. |
| `00a0c86214261ad58592eb6978b71a948ae91e47` `docs(47-01): complete config file permission hardening plan` | `not yet reviewed` |  |  |
| `60304acf2129b5a32ad66116d7a8a27d4372f31d` `docs(47-03): complete SSH topology redaction plan` | `not yet reviewed` |  |  |
| `4709e977a240979a77f02772fcb148ae442b5606` `docs(47-02): complete restart endpoint CSRF fix plan` | `not yet reviewed` |  |  |
| `1f1a12b453e67743d14e7c4ec0259f0eacfd7a01` `docs(phase-47): complete phase execution` | `not yet reviewed` |  |  |
| `41c53e5d57a35ea543ee9dd8f43883961011aefe` `docs(48): research phase config and webhook layer` | `not yet reviewed` |  |  |
| `fa96fdf303b99919f10698511f12a3801d9f2cfe` `docs(48): create phase plan` | `not yet reviewed` |  |  |
| `5c899615b5ce4ece819e766951efe7be10907892` `feat(48-01): add api_token config field and extend config API redaction` | `not yet reviewed` |  |  |
| `a29cae1108bf481181bc363903de4d8c6db765e3` `docs(48-01): complete api_token config field and redaction plan` | `not yet reviewed` |  |  |
| `7063a03b8e4cd212b597adbb2827720c872a6130` `feat(48-02): add webhook payload size cap with tests` | `not yet reviewed` |  |  |
| `e670f5d436089766732175f907b1fcb993991ddd` `feat(48-02): add startup security warnings with tests` | `not yet reviewed` |  |  |
| `44953995cfe8c58ee3b3c5e470fed53adc773bfd` `docs(48-02): complete webhook payload size cap and startup warnings plan` | `not yet reviewed` |  |  |
| `20db99f5af5fce18d92f440d147935e80dd4fb72` `docs(phase-48): complete phase execution` | `not yet reviewed` |  |  |
| `e3badf3c2365d72f79dbb628c5c3a2dbbdc580e7` `docs(49): research phase path-traversal-guards` | `not yet reviewed` |  |  |
| `cbfa789568548fa57fb4a0ef7379692c46bba41e` `docs(49): add research and plan for path traversal guards` | `not yet reviewed` |  |  |
| `be88511c2ddd5db187cf197d94a04098c81c334c` `test(49-01): add failing path traversal guard tests` | `not yet reviewed` |  |  |
| `89c7a7c5f569fd2bc6925049eb5d1d957ea1d13f` `feat(49-01): implement realpath-based path traversal guards` | `not yet reviewed` |  |  |
| `fd33fa151d3ef44d21883894df39486b16981c63` `docs(49-01): complete path traversal guards plan` | `not yet reviewed` |  |  |
| `0342b5bc9c4e9ad7b7a02322a03fe662dd256d4d` `docs(phase-49): complete phase execution` | `not yet reviewed` |  |  |
| `04ed3ace76b760ef6263f22ec376fc97d55f9279` `fix: make first-run SSH timeouts recoverable instead of fatal` | `not yet reviewed` |  |  |
| `7bf7fb2826b759bf1feb456a250e6d00cb35651c` `fix: update tests for api_token field, config redaction, and POST restart route` | `not yet reviewed` |  |  |
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

- `00a0c86214261ad58592eb6978b71a948ae91e47` `docs(47-01): complete config file permission hardening plan`

## Next Candidate Batch

- `00a0c86214261ad58592eb6978b71a948ae91e47` `docs(47-01): complete config file permission hardening plan`
- `60304acf2129b5a32ad66116d7a8a27d4372f31d` `docs(47-03): complete SSH topology redaction plan`
- `4709e977a240979a77f02772fcb148ae442b5606` `docs(47-02): complete restart endpoint CSRF fix plan`
- `1f1a12b453e67743d14e7c4ec0259f0eacfd7a01` `docs(phase-47): complete phase execution`
