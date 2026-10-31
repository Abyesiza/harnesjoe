# Fam harness: write-up

Repo: `https://github.com/Abyesiza/harnesjoe.git` (see `REPO_LINK.txt`; whether it is public was not checked). Every number below is reproduced by a command in `EVIDENCE.md`.

## What the harness does
Fam is a local family of Claude CLI agents. Skipper turns chat into `Task` events (37 distinct ids) and routes each to one builder (Spark, Ember, Blaze or Pip) by "learned task fit, capability, and current workload" (`Assigned`); reviewers (Sage, Blue, Quill, Hawk…) brief and vote. Each builder works in its own git worktree under `~/.fam/work/<id>`. The family votes (`Decision`), the human may override (`HumanDecision`), and accepted work is applied only if it applies cleanly (`Applied`), else it stays put (`ApplyPending`). Nothing is pushed.

## What it got wrong
- **Accepted work that never landed.** Tasks 30c76b62, a75c1f56 and c0b8d2a2 were accepted by the human but never got an `Applied` event. There are 12 `ApplyPending` events for them (5, 4 and 3), each reading "error: frontend/src/app/globals.css: does not match index". All three edited the shared `globals.css`; their worktree edits still differ from `main`. Accepted 807469ed had "no applicable patch".
- **Builders asked to edit got no write access.** Task 30e7ee2e (Spark) logged `Blocked`: "file write access in the worktree (Edit/Write tool or approved shell writes)". It never reached a decision. d6787210+ and 180616b0 blocked earlier on folder/guard approval.
- **Low builder success.** Final verdicts (the human's decision where one exists, otherwise the family vote), shown as accepted / decided, with applied in brackets: Spark 8/13 (7), Ember 3/6 (2), Pip 3/6 (1), Blaze 0/2 (0). Overall that is 14/27 accepted and 10/27 applied. Mean `Outcome` rewards are Spark 0.446 (n=3), Ember 0.25 (n=3), Pip 0.05 (n=1) and Blaze 0.05 (n=2).
- **Rejected work.** 13 tasks were rejected: 00518da8, 1edb7ef2, 383a3617, 3bc584b6, 45061d60, 45061d60+, 4dd2bc84, 94f14cb9, cf8b3eb7, d6787210, f0515e64, ff92a558 and ff92a558+. The first four Next.js attempts (4dd2bc84, d6787210, ff92a558, ff92a558+) were all rejected before the human accepted 9e1696b0.
- **Spend went mostly unrecorded.** Per-call logging began 2026-10-31: $0.624257 over 12 calls, nothing per task or for 25–30 Oct (`SPEND.md`).

## What to fix next (planned, not done)
1. Put file ownership in the scheduler. Refuse or queue a task that touches files already claimed by another open or unapplied task. Rebase accepted worktrees onto the current `main` before applying.
2. Show "accepted but not applied" as its own state in the UI, with a one-click rebase or retry.
3. Grant Edit/Write inside the task's own worktree by default, so builders don't block on 30e7ee2e-style permission gaps.
4. Put a task id and cost on every ledger row and `runs` row, and keep the ledger across days.
5. Route by measured accept rate, and stop giving Blaze and Pip UI work until their rates improve.
