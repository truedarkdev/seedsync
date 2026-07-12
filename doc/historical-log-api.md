# Historical log query API

SeedSync keeps a bounded rotating JSON-lines history at `config/logs/history.jsonl` under the configured data directory and exposes it separately from the live SSE stream. The endpoint is read-only and requires an API key or remembered browser session with the `admin` scope.

```text
GET /server/logs/history/v1?level=WARNING,ERROR&logger=seedsync.Controller&text=timeout&direction=desc&limit=100
Authorization: Bearer <admin API key>
```

`start` and `end` are Unix timestamps and may span at most 31 days. `level`, exact `logger`, free-text `text`, `direction` (`asc` or `desc`), and `limit` (1-500) compose. Continue with the opaque `page.next_cursor` value in an otherwise identical query. A cursor whose evidence record has rotated out is rejected; restart the query to obtain a new consistent window.

The versioned `seedsync.log-history.v1` response contains ordered `records`, `page`, and `evidence`. Each record includes a stable `id`, UTC `timestamp`, Unix `epoch`, severity, numeric severity, logger, message, optional exception, and a `truncated` flag. Evidence reports bytes scanned, malformed records skipped, scan truncation, output truncation, output bytes, and the active scan/record/response ceilings. Reads scan at most 8 MiB, each exposed text field is capped at 16 KiB, and one response is capped at approximately 1 MiB including its small metadata envelope. Persistence rotates at the application log size/count policy (currently 10 MiB plus ten backups).

Logger names, messages, and exceptions pass through the history-specific credential and absolute-path redaction policy both when written and when read. Treat output as diagnostic evidence, not a complete audit trail: rotation, scan/output caps, partial writes, malformed lines, truncation, and redaction can omit context. Do not ask tools or models to infer secrets or filesystem locations that have intentionally been removed. SeedSync embeds no model and sends no log data to an external AI service.

On POSIX systems SeedSync requires the history directory and files to be owned by the runtime user, enforces mode `0700` on the directory and `0600` on active and rotated files, rejects symlinks and unexpected object types, and uses no-follow file opening where the platform supports it. Windows does not provide equivalent POSIX ownership/mode guarantees through this implementation; restrictive creation intent and object checks are applied, but administrators must protect the configured data directory with appropriate Windows ACLs. Reparse-point behavior can differ from POSIX symlinks and is not claimed as a complete Windows ACL or reparse-point security boundary.
