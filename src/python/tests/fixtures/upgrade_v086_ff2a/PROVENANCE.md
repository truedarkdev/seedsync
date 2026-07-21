# Original SeedSync v0.8.6 upgrade fixture

These three persisted files were captured from the normalized live v0.8.6
upgrade lab run (`norm2`). Their structures are anchored to original SeedSync
commit `ff2a1039935beccbbf7ec76134b41d2e91137742`, whose `Config`,
`ControllerPersist`, and `AutoQueuePersist` definitions produce respectively:

- the five legacy settings sections and keys represented by `settings.cfg`;
- `downloaded` and `extracted` string lists in `controller.persist`; and
- JSON-encoded `pattern` objects in the `patterns` list in
  `autoqueue.persist`.

The fixture and live capture are content-identical after ignoring trailing
CR/LF bytes. The normalized SHA-256 digests are pinned independently in
`test_migration_coordinator.py` so accidental fixture drift fails the focused
test without depending on ignored `tmp/` lab artifacts.
