# Manifest

Snapshot taken 2026-10-31 at about 05:42Z while the daemon was running. `daemon.log`, `ledger.*`, `shell/c8d0fc3c97-d19e9a.jsonl` and `state.sqlite3` were still being written, so their sha256 matches the original only as of copy time.

- **Copied with `cp -p`, original mtime kept:** `logs/daemon.log`, `logs/ledger.jsonl`, `logs/ledger.json` and `logs/shell/*.jsonl`.
- **`logs/state.sqlite3`:** made with the SQLite `backup()` API, read-only from the source and consistent with the WAL. Its mtime was set to the source file's mtime with `touch -r`.
- **Generated for this pack, with no original timestamp:** `logs/git-log.txt` (`git log --all --date=iso`) and `logs/worktrees/<id>.txt` (per-worktree branch log, ancestry against `main`, `git status --short` and diff stat). The worktree source trees were not copied. They are code, not logs, and they are still in `~/.fam/work/`.
- **Left out on purpose:** `~/.fam/token`, `mcp.json`, `uploads/`, `profile.json` and `briefs.npz`. Before copying, the copied files were grepped for the token value and for `sk-ant-`, `ghp_`, `github_pat_`, `Authorization: Bearer`, `AKIA` and `xox[bp]-`. There were 0 hits.
- **Already present when this task started; not produced or checked by this task:** the root-level `events.jsonl`, `messages.jsonl`, `runs.jsonl`, `ledger.*` and `shell-*.jsonl`, all with mtime 05:39:24–25Z. They look like exports with an added `time_eat` field. `shell-b714b5f688-52ab73.jsonl` no longer exists in `~/.fam/shell/`. `WRITEUP.md` was rewritten by this task.

| file | bytes | mtime (UTC) | first ts | last ts | sha256 |
|---|---|---|---|---|---|
| EVIDENCE.md | 5555 | 2026-10-31T05:44:18Z | n/a | n/a | a2493034f35ff36d16e6f2513dbe6432d19466bce3a62b30844fc934890563d0 |
| MANIFEST.md.tmp | 1545 | 2026-10-31T05:44:29Z | n/a | n/a | 7f668009ce19b13e8ec5cb4d550e01afebc1d9d62fd45c1ccf09a388cbbae16e |
| REPO_LINK.txt | 113 | 2026-10-31T05:42:58Z | n/a | n/a | ecbcfed9baf829ac9ab25a8e49436267b4d3ae621e633c1a0565547a792c6b70 |
| SPEND.md | 1593 | 2026-10-31T05:43:08Z | n/a | n/a | eef5b90db09e6f4c7cbf32d1ed8622acc24b2b5e5db57e01d6d5ef4f6743a7f2 |
| WRITEUP.md | 2951 | 2026-10-31T05:43:39Z | n/a | n/a | 4204d0939c95ce1bd1c5dc6e23c89d75c6e212dbb498248f23eee7e3644656ef |
| events.jsonl | 22659264 | 2026-10-31T05:39:24Z | 2026-10-25T10:42:55Z | 2026-10-31T05:39:12Z | b4f9f3db3efbece4aa80b446593ee633201aa56aba74aa43e15786dcb8a95192 |
| ledger.json | 63 | 2026-10-31T05:39:25Z | n/a | n/a | 429b5ede64bbafcab853b2d2cd2f30ac9950929a2aca1430435d37ba0654c212 |
| ledger.jsonl | 393 | 2026-10-31T05:39:25Z | 2026-10-31T05:30:34Z | 2026-10-31T05:31:29Z | 21ff51c0b6f4fc8ae0ed45d05697f67c752f071bcf47bcd5576421963fd41868 |
| messages.jsonl | 413049 | 2026-10-31T05:39:25Z | 2026-10-25T10:43:22Z | 2026-10-31T05:38:27Z | 1f705c4314939712fe52e3311be16832ae720b1319a8ebb8c03556e0343a7c4e |
| runs.jsonl | 109029 | 2026-10-31T05:39:25Z | 2026-10-25T12:29:55Z | 2026-10-31T05:38:27Z | 366dc131e66ba1b8acdb2103e5f0d689907a18afd9415e921c9cf4cfae7dee60 |
| shell-2a4a1ede32-b200d2.jsonl | 33950 | 2026-10-31T05:39:25Z | 2026-10-27T21:47:18Z | 2026-10-27T21:53:24Z | 73957f893274055f2b442b5207234edf5b7cca9a036cdfdb77203ff4a9a35a25 |
| shell-b714b5f688-52ab73.jsonl | 21181 | 2026-10-31T05:39:25Z | 2026-10-31T05:38:33Z | 2026-10-31T05:39:16Z | b762374a35cdcd5e3418897992f60e455b1af1c1b37db9e9af80dd32e1afbaf6 |
| logs/daemon.log | 63243 | 2026-10-31T05:42:11Z | n/a | n/a | 8dd80140f3207015be013eb1db9176535a045d1dbf239ce5890113072d1ce7b1 |
| logs/git-log.txt | 1140 | 2026-10-31T05:42:14Z | n/a | n/a | 553b4028e2d3c0d7dad563bb88194f978989ae01e2fedd3730610252dd5c598e |
| logs/ledger.json | 62 | 2026-10-31T05:40:53Z | n/a | n/a | 293a090424493653f5fdaec3d3003260e20e1be06d794eb83bb8cbca1ceaa07e |
| logs/ledger.jsonl | 1570 | 2026-10-31T05:40:53Z | 2026-10-31T05:30:34Z | 2026-10-31T05:40:53Z | 62baa90563d2a51ada13548c189e4886f253027b9cec9d1df18d3c77937a8269 |
| logs/state.sqlite3 | 27074560 | 2026-10-31T05:42:14Z | 2026-10-25T10:42:55Z | 2026-10-31T05:42:14Z | 4eebf54f67fa8e82e896955b724417654a0648f6d186664b6185c1290d7a336b |
| logs/shell/2a4a1ede32-b200d2.jsonl | 33950 | 2026-10-27T21:53:24Z | 2026-10-27T21:47:18Z | 2026-10-27T21:53:24Z | 73957f893274055f2b442b5207234edf5b7cca9a036cdfdb77203ff4a9a35a25 |
| logs/shell/c8d0fc3c97-d19e9a.jsonl | 38375 | 2026-10-31T05:42:14Z | 2026-10-31T05:40:39Z | 2026-10-31T05:42:14Z | 21eae269d760442397445ffaa7c88fa09dce0fff741fdeee5b2c79b7380a275c |
| logs/worktrees/17ef7f30.txt | 102 | 2026-10-31T05:42:21Z | n/a | n/a | c9f10f7c39bceea8da38b0a1037cecce6e4276c04c876853abf01a24c16acea3 |
| logs/worktrees/30c76b62.txt | 360 | 2026-10-31T05:42:21Z | n/a | n/a | 241d221b7800499aed7bcf5342c17b002ed0109211924af0ce13cb7175273311 |
| logs/worktrees/32b70565.txt | 102 | 2026-10-31T05:42:21Z | n/a | n/a | 2cc34966e942ef6ee29e41d931ac4272ed2a7162d647f31e5d6cf881d9ed0865 |
| logs/worktrees/383a3617.txt | 102 | 2026-10-31T05:42:21Z | n/a | n/a | 931c614a5038872bfdc05d8353d46e19ee7b0cffc47d7a010847bd797883c62e |
| logs/worktrees/4dd2bc84+.txt | 143 | 2026-10-31T05:42:21Z | n/a | n/a | b06f88b1cb7e6682541b6a9e4ab67dc399d60881a1a7ef8eb59d3ed4e026e58c |
| logs/worktrees/71eeb3f8.txt | 102 | 2026-10-31T05:42:21Z | n/a | n/a | 8da68a4c914a02ac25b8e3c091471a277fb5a0bad47ef2792fb424620587f9ec |
| logs/worktrees/72ff7402.txt | 102 | 2026-10-31T05:42:21Z | n/a | n/a | 29b06692bcf50c52de6dc94385622d904186164d17a37e4f28c64331102a80dd |
| logs/worktrees/7c7e1c10.txt | 102 | 2026-10-31T05:42:21Z | n/a | n/a | 288d5eeb10e3742a7e3f3d317de0f30073686bac1d3ed8a497ebcdae3d40952d |
| logs/worktrees/807469ed.txt | 102 | 2026-10-31T05:42:21Z | n/a | n/a | aa6027cc9586342ee429367a5d49f582edd579073035cc7bfc2b92a02526c43f |
| logs/worktrees/a75c1f56.txt | 331 | 2026-10-31T05:42:21Z | n/a | n/a | 53e9cd48a836314d7f53327fd2c7135115da033d589174df3e80f333d6aa2b03 |
| logs/worktrees/c0b8d2a2.txt | 274 | 2026-10-31T05:42:21Z | n/a | n/a | ef3c2c7f4aefd0824b4bea040b0b98a77e78a45d205c974840f8b9102d7edc6f |
| logs/worktrees/cf8b3eb7.txt | 290 | 2026-10-31T05:42:21Z | n/a | n/a | 4840069078d999026a3a55dd854a9c7d12356d31bc51e8fe2a7c726aabffb02b |
| logs/worktrees/f6f303fd.txt | 102 | 2026-10-31T05:42:21Z | n/a | n/a | cd257fa360d1aeec5a4146f58fc16fdcdf61c6690d0fee65b668b653b32dd043 |
