# Model spend

Source: `logs/ledger.jsonl`, a snapshot of `~/.fam/ledger.jsonl` taken 2026-10-31T05:40:53Z. Every row has `exact: true`, which means the USD figure is the cost the Claude CLI reported, not an estimate.

## Total recorded: $0.624257 (12 calls, cap $5.00)
The 12 calls ran from 2026-10-31T05:30:34Z to 05:40:53Z. The last row's `spent` value (0.624257) equals the sum of the `usd` column.

## By member and model
| who | calls | usd |
|---|---|---|
| Skipper claude claude-sonnet-5-5 | 5 | 0.397400 |
| Blue claude claude-sonnet-5-5 | 2 | 0.124905 |
| Sage claude claude-sonnet-5-5 | 2 | 0.094833 |
| Ranger claude claude-haiku-5-5 | 1 | 0.003788 |
| Hawk claude claude-haiku-5-5 | 1 | 0.001666 |
| Spark claude claude-haiku-5-5 | 1 | 0.001665 |

## What was not recorded
- **Spend per task is not recorded.** Ledger rows have no task id, and the `runs` table has no cost column. No mapping is inferred here.
- **Spend from 2026-10-25 to 2026-10-31 05:30Z is not recorded.** Per-call rows in `ledger.jsonl` started with commit `bb1a662` (2026-10-31 08:32 EAT). Before that, `ledger.json` kept only a running total for the day, and `Ledger.attach` drops it when the day changes. So the 27 decided tasks in `logs/state.sqlite3` have no spend on record.
- **The ledger was still being written during this snapshot.** 9 of the 12 rows ($0.537671) were recorded after task 71eeb3f8 was assigned at 05:39:39Z. They fall inside the window when this pack was being built, but the ledger does not say which task they belong to.

No price table or token count was used to fill these gaps.
