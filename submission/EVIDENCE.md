# Evidence

Every command runs from `submission/` against the snapshots in `logs/`. All of them are read-only and none touches the network. The `sqlite3` CLI is not installed here, so the queries use `python3 -I` with `mode=ro`. `q` is shorthand for:

```sh
q() { python3 -I -c "import sqlite3,sys;c=sqlite3.connect('file:logs/state.sqlite3?mode=ro',uri=True);[print(*r) for r in c.execute(sys.argv[1])]" "$1"; }
```

| Claim (WRITEUP / SPEND) | Source | Command | Result |
|---|---|---|---|
| Remote | `git remote -v` | `diff <(git -C .. remote -v) REPO_LINK.txt` | no diff. Public visibility **not checked** (that needs the network) |
| 37 distinct tasks | `Task` events | `q "select count(distinct json_extract(data,'$.id')) from events where type='Task'"` | 37 |
| 12 "does not match index" applies (5/4/3), all citing globals.css | event ids 12621–15586 | `q "select json_extract(data,'$.task.id'),count(*),sum(json_extract(data,'$.reason') like '%globals.css: does not match index%') from events where type='ApplyPending' and data like '%does not match index%' group by 1"` | 30c76b62 4 4 · a75c1f56 5 5 · c0b8d2a2 3 3 |
| Those 3, plus 807469ed, never Applied | `Applied` events | `q "select count(*) from events where type='Applied' and json_extract(data,'$.task.id') in ('30c76b62','a75c1f56','c0b8d2a2','807469ed')"` | 0 |
| Worktree edits differ from main | `logs/worktrees/<id>.txt` | `cd .. && for id in 30c76b62 a75c1f56 c0b8d2a2; do git -C ~/.fam/work/$id status --short \| awk '{print $2}' \| while read p; do git show main:$p \| cmp -s - ~/.fam/work/$id/$p \|\| echo "$id $p differs"; done; done` | 9 files differ |
| 807469ed "no applicable patch" | event 6071 | `q "select data from events where id=6071"` | reason quoted |
| 30e7ee2e blocked, never decided | event 13570 | `q "select json_extract(data,'$.need') from events where type='Blocked' and json_extract(data,'$.task.id')='30e7ee2e'"`, then `q "select count(*) from events where type in ('Decision','HumanDecision','Applied') and json_extract(data,'$.task.id')='30e7ee2e'"` | quote · 0 |
| Accepted/decided/applied per builder, 13 rejected ids, rewards | Decision, HumanDecision, Applied, Outcome | save the block below outside `submission/` as `stats.py`, then `python3 -I /path/to/stats.py logs/state.sqlite3` | see output below |
| Spend total, calls, per-who | `logs/ledger.jsonl` | `jq -s 'length,(map(.usd)\|add),(map(.exact)\|all),.[-1].spent' logs/ledger.jsonl` and `jq -rs 'group_by(.who)\|map([.[0].who,length,(map(.usd)\|add)])[]\|@tsv' logs/ledger.jsonl` | 12 · 0.624257 · true · 0.624257 |
| 9 rows / $0.537671 after 71eeb3f8 was assigned | ledger and event 15718 | `jq -s '[.[]\|select(.ts>=1793425179)]\|[length,(map(.usd)\|add)]' logs/ledger.jsonl` | 9, 0.537671 |
| Per-call ledger started in bb1a662 | git | `git -C .. log -S"def record" --format='%h %ad' --date=iso -- agents.py` | bb1a662 2026-10-31 08:32:30 +0300 |
| No cost column in runs | schema | `q "select sql from sqlite_master where name='runs'"` | no cost/usage field |
| Copies match originals | `MANIFEST.md` | `sha256sum logs/shell/2a4a1ede32-b200d2.jsonl ~/.fam/shell/2a4a1ede32-b200d2.jsonl` | equal; the mtime was kept with `cp -p` |

Final verdict = the `HumanDecision` where one exists, otherwise the last `Decision.accept`. Success rate = accepted ÷ decided (27 tasks with a decision). Tasks that never got a decision, such as 30e7ee2e, are not in the denominator.

## stats.py
```python
import sqlite3,json,sys,collections
c=sqlite3.connect(f'file:{sys.argv[1]}?mode=ro',uri=True)
ev=lambda t:[(i,json.loads(d)) for i,d in c.execute('select id,data from events where type=? order by id',(t,))]
tid=lambda d:d['task']['id'] if isinstance(d.get('task'),dict) else d.get('task')
auto={};human={};who={}
for i,d in ev('Decision'): auto[tid(d)]=d['accept']; who[tid(d)]=d['member']
for i,d in ev('HumanDecision'): human[tid(d)]=d['accept']
applied={tid(d) for i,d in ev('Applied')}
final={t:human.get(t,auto[t]) for t in auto}
rows=collections.defaultdict(lambda:[0,0,0])
for t,a in final.items():
    r=rows[who[t]]; r[0]+=1; r[1]+=a; r[2]+=(t in applied)
print('member decided accepted applied')
for m,(n,a,p) in sorted(rows.items()): print(m,n,a,p)
print('total',len(final),sum(final.values()),len(applied&set(final)))
print('rejected',sorted(t for t,a in final.items() if not a))
print('accepted_not_applied',sorted(t for t,a in final.items() if a and t not in applied))
print('applied_without_decision',sorted(applied-set(final)))
print('blocked',[(tid(d),d['need'][:80]) for i,d in ev('Blocked')])
rw=collections.defaultdict(list)
for i,d in ev('Outcome'): rw[d['member']].append(float(d['reward']))
print('mean_reward',{m:(round(sum(v)/len(v),3),len(v)) for m,v in rw.items()})
```

## stats.py output (snapshot)
```
member decided accepted applied
blaze 2 0 0
ember 6 3 2
pip 6 3 1
spark 13 8 7
total 27 14 10
rejected ['00518da8', '1edb7ef2', '383a3617', '3bc584b6', '45061d60', '45061d60+', '4dd2bc84', '94f14cb9', 'cf8b3eb7', 'd6787210', 'f0515e64', 'ff92a558', 'ff92a558+']
accepted_not_applied ['30c76b62', '807469ed', 'a75c1f56', 'c0b8d2a2']
applied_without_decision []
blocked [('d6787210+', 'approval of `/home/joel-abyesiza/.fam/work/4dd2bc84+` as a working folder, so I '), ('180616b0', 'the Fam guard to approve `/home/joel-abyesiza/.fam/work/4dd2bc84+`\n\nThe shell po'), ('30e7ee2e', 'file write access in the worktree (Edit/Write tool or approved shell writes)')]
mean_reward {'pip': (0.05, 1), 'blaze': (0.05, 2), 'ember': (0.25, 3), 'spark': (0.446, 3)}
```
