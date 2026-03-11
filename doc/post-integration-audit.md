# Post-Integration Audit

This is the landing page for the split post-integration audit documentation.

Open these files by default:
- Rules and workflow: [post-integration-audit-rules.md](/mnt/c/Git/seedsync/doc/post-integration-audit-rules.md)
- Active unfinished ledger: [post-integration-audit-active.md](/mnt/c/Git/seedsync/doc/post-integration-audit-active.md)

Open archive files only when you need completed audit history or proof for already-closed rows.

## Current Status

| Fork | Total rows | Unfinished | Finished archived | Frozen tip at audit start | Active file |
| --- | --- | --- | --- | --- | --- |
| `thejuran` | `672` | `672` | `0` | `a8561cdc318460de32de082e3cf33f6b6a0093cb` | [post-integration-audit-active.md](/mnt/c/Git/seedsync/doc/post-integration-audit-active.md) |
| `rapidcopy` | `224` | `15` | `209` | `c300b72f808772b00cc977ccceaa23f3c373ce33` | [post-integration-audit-active.md](/mnt/c/Git/seedsync/doc/post-integration-audit-active.md) |

## Archive Files

| Fork | Archive chunk | Finished rows | File |
| --- | --- | --- | --- |
| `rapidcopy` | `001` | `44` | [post-integration-audit-archive/rapidcopy-001.md](/mnt/c/Git/seedsync/doc/post-integration-audit-archive/rapidcopy-001.md) |
| `rapidcopy` | `002` | `27` | [post-integration-audit-archive/rapidcopy-002.md](/mnt/c/Git/seedsync/doc/post-integration-audit-archive/rapidcopy-002.md) |
| `rapidcopy` | `003` | `50` | [post-integration-audit-archive/rapidcopy-003.md](/mnt/c/Git/seedsync/doc/post-integration-audit-archive/rapidcopy-003.md) |
| `rapidcopy` | `004` | `31` | [post-integration-audit-archive/rapidcopy-004.md](/mnt/c/Git/seedsync/doc/post-integration-audit-archive/rapidcopy-004.md) |
| `rapidcopy` | `005` | `27` | [post-integration-audit-archive/rapidcopy-005.md](/mnt/c/Git/seedsync/doc/post-integration-audit-archive/rapidcopy-005.md) |
| `rapidcopy` | `006` | `30` | [post-integration-audit-archive/rapidcopy-006.md](/mnt/c/Git/seedsync/doc/post-integration-audit-archive/rapidcopy-006.md) |

## Structure Rules

- keep rules and workflow separate from row ledgers
- keep the active ledger limited to unfinished rows only
- move finished rows into per-fork archive files in chunks of up to 50 rows
- keep the active ledger sorted oldest to newest within each fork
