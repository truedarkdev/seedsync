# Post-Integration Audit Rules

Use this rules document after all integration subjects are complete.

Document roles:
- `AGENTS.md` is the canonical audit rulebook and exit-criteria source
- this file is the detailed workflow and rulebook for the audit split
- `doc/post-integration-audit-active.md` is the active per-commit audit ledger for unfinished rows
- `doc/post-integration-audit.md` is the landing page and archive index
- `doc/integration-tracker.md` records reopened subjects, resulting local integration work, and summary audit state

Purpose:
- mark off each upstream commit as it is checked against current local state
- make the final fork-by-fork coverage pass resumable without relying on memory
- capture any missed work cleanly enough to reopen the right subject

Recommended process:
1. pick one fork
2. if `rapidcopy` still has unfinished audit rows, pick `rapidcopy`; do not switch to `thejuran` or another fork until `rapidcopy` is finished unless the maintainer explicitly changes the order
3. inventory every fork-local upstream commit in this ledger before making any dispositions
4. walk its remaining commit history oldest to newest
5. compare each inventoried commit in the recorded range against the pinned local audit base
6. record the triage and final disposition here
7. reopen the related subject or create a new integration task if the audit finds missed work
8. after each audit run, update the workflow prompt/templates if the run exposed a repeatable lesson or failure mode, and record that learning here or in `doc/integration-tracker.md` before continuing
9. after each audit run, note whether `explorer-fast` showed good judgment on the commit being reviewed, including whether it over-escalated, under-escalated, or misclassified the likely disposition
10. if working inside a planned autonomous audit wave, do not stop at a natural checkpoint just to summarize progress; keep going until the wave is finished or a real reviewer/maintainer exception interrupts it
11. once a maintainer-approved batch has started, you are not allowed to stop before the full batch is done unless a real reviewer-worthy or maintainer-worthy exception blocks further progress
12. 9 completed commits inside a batch is only a progress reminder, never a stopping point; after every 9 commits, explicitly confirm the remaining batch count and continue immediately
13. when the planned batch is finished, stop there: summarize the full batch result, count how many reviewers were spawned, note any workflow improvements, and wait for maintainer confirmation of the next batch and its size before continuing
14. record the maintainer-approved batch size in this ledger and treat it as sticky for later audit runs until the maintainer explicitly changes it; do not silently fall back to a smaller or ad hoc batch size on resume
15. unless the maintainer explicitly changes it, process the 3 oldest remaining upstream commits in parallel for first-pass triage, then move to the next 3 oldest remaining commits; this concurrency cap is separate from the maintained audit batch size

Reviewer gate:
- use `explorer-fast` or `explorer` for first-pass triage
- require `reviewer` whenever the confidence is not `high`, the evidence is only `behavioral inference` or `unclear`, the mapped integration subject is high-risk, the commit seems only partially covered, or the commit is being closed as `covered elsewhere` without a concrete direct match
- keep reviewer prompts narrow: provide one upstream commit, the triage result, the concrete local evidence already found, and a small fixed output schema instead of asking the reviewer to rediscover broad repo context from scratch
- do not treat `explorer-fast` as automatically correct; compare its output against the orchestrator's local evidence and the eventual reviewer result so the prompt can be recalibrated if it starts routing too many or too few commits to review
- when an explorer claims `direct local match`, it should name the matching local commit hash explicitly so the orchestrator can confirm quickly without reconstructing the match from surrounding context
- for docs-only commits without an exact local commit match, the agent should cite the exact current local command, sentence, or section that already covers the upstream intent; if it cannot point to that concrete live-doc evidence, send the commit to `reviewer`
- after a repeated run of accurate high-confidence `direct local match` results on a similar low-risk stretch, the orchestrator may use spot checks and light-touch confirmation instead of fully reconstructing every match by hand
- in a maintainer-directed low-context audit mode, prefer subagent-led evidence gathering and send anything suspicious, under-supported, or oddly classified to `reviewer` instead of expanding the orchestrator's own manual investigation

Coverage values:
- `full`
- `partial`
- `none`

Final disposition values:
- `already integrated`
- `covered elsewhere`
- `intentionally skipped`
- `needs subject reopen`
- `needs new integration task`
- `maintainer decision needed`

Fork-audit completion rule:
- do not mark a fork audit `reviewed` until every commit in the recorded range appears here, every row has a final disposition, every unresolved row links to follow-up work, any row marked `partial` explains what is already present and what follow-up remains, and a short delta check confirms whether new upstream commits appeared after the recorded fork tip at audit start
