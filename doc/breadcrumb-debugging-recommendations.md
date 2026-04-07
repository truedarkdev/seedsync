# Breadcrumb Debugging Recommendations

Last updated: 2026-04-07

This note separates the kinds of detail that belong in normal logs from the
kind that belongs in targeted breadcrumbs. The goal is to keep the app easy to
support without turning breadcrumb tracing into a noisy debug stream.

## Belongs in normal logs

- Startup and shutdown milestones, config load/save, auth changes, queue
  starts/stops, and other durable lifecycle events.
- Operator value: creates a persistent history that survives a single failure
  window and is easy to support from ticket notes.
- Spam risk: low when these are event-based and not emitted in tight loops.

## Belongs in targeted breadcrumbs

- Queue decisions, scan transitions, transfer and extraction state changes,
  retry boundaries, and the immediate lead-up to a failure.
- Compact correlation details that explain which flow, stage, or retry a
  breadcrumb belongs to.
- Operator value: reconstructs the last few steps before a bug without
  enabling broad debug logging.
- Spam risk: low if the entries stay short, bounded, and state-change driven.

## Should be rejected

- Raw command dumps, full payloads, secrets, tokens, host keys, or long free-
  form traces copied from another system.
- High-frequency heartbeat noise, repeated polling chatter, or large object
  snapshots that do not explain a specific failure.
- Operator value: weak, because these are hard to scan and usually repeat what
  a normal log line can already say.
- Spam risk: high, because they can flood the recorder, obscure the useful
  breadcrumbs, and make the opt-in facility feel expensive.

## Rule Of Thumb

If the detail helps answer "what changed right before the failure?" it usually
belongs in breadcrumbs. If it helps answer "what happened over time in normal
operation?" it belongs in logs. If it mostly dumps raw state, reject it.
