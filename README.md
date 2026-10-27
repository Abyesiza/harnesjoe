# Fam

Fam is a local software family managed through Skipper. Tell Skipper the outcome you want; it chooses a capable, available builder, tracks tests and review, and reports meaningful updates. Accepted changes are applied to your local repository only when it is clean and unchanged since the task started. They are never pushed automatically.

## Requirements

- Python 3.12 or later
- Dependencies from `requirements.txt`
- `claude` and `copilot` CLIs installed and authenticated. Skipper and most family members use Claude; Pip and Bastion use Copilot when its CLI is available, and fall back to Claude otherwise.
- Git

From the project directory:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
# for tests and linting:
pip install -r requirements-dev.txt
chmod 600 .env  # if you use a local .env
```

Run the tests with `python -m pytest -q`.

Set provider credentials in your shell or in a local, untracked `.env` file. Skipper reads provider configuration from the environment; keep credentials out of source control.

## Run Skipper

Start the daemon and open the web conversation with one command:

```sh
python daemon.py up
```

To start without opening a browser, use `python daemon.py start`. The web conversation is at <http://127.0.0.1:8000>. The page shows builders, reviewers and research members around the central chat, and indicates when Skipper is waiting on a response. Each member card shows whether it is working; builders also show their model and price. The four builders (Spark, Ember, Blaze, Pip) share one job, writing the least code, and differ by engine and cost: Spark runs on Haiku (cheapest), Pip on Copilot with a Claude fallback, Ember on Sonnet and Blaze on Opus. A task goes to the best fit for its workload, and a failed review retries with the next more expensive builder.

```sh
python daemon.py talk
python daemon.py chat "Add a health endpoint and tests"
python daemon.py status
python daemon.py stop
python daemon.py restart
```

`restart` uses `systemctl --user restart fam.service` when the service is active, otherwise it stops and starts the background server. Either way it waits until `/api/health` answers from the new process and `~/.fam/token` exists. Run it after editing `.env` (for example adding `TAVILY_API_KEY`) or code. `python daemon.py --help` lists every command.

Web research uses Tavily first when `TAVILY_API_KEY` is set, then Brave (`BRAVE_API_KEY`), then DuckDuckGo. The provider that served each search is logged and shown in the dashboard's Research tab. Finished research, reviews, and failures are posted to chat as short structured reports (asked, result, sources, CLIs and fallbacks used, duration, problems, next step).

The daemon binds to loopback and is not exposed to the network. It starts background maintenance checks automatically. Set `PORT` to use another local port.

To install a user-level Linux systemd service that starts with your user session:

```sh
python daemon.py install-service
python daemon.py service-status
python daemon.py service-stop
```

The service runs the checked-out source tree at its current path. Run `install-service` again after moving the project.
The generated service carries the current CLI executable directories into its `PATH`, so Claude and Copilot remain available when systemd starts Skipper outside an interactive shell.

## Next.js UI (optional)

`frontend/` is a Next.js App Router version of the web page. The original `web/index.html` still works and is still served by `server.py`.

```sh
python daemon.py start          # API on 127.0.0.1:8000
cd frontend
npm ci
npm run dev                     # UI on http://localhost:3000
```

Next proxies `/api/*` to the Python server, so the browser only talks to Next. Set `API_URL` (server side, default `http://127.0.0.1:8000`) if the API runs elsewhere. Open the UI at `localhost` or `127.0.0.1`: the `/token` route refuses other hosts, and it reads the per-session token from the Python page the same way the old page does. Check with `npm run lint`, `npm run typecheck` and `npm run build`.

## Local state and safety

Conversation history, event history, folder approvals, routing knowledge, tool proposals, and cached briefs are stored locally under `~/.fam` by default. Set `FAM_HOME` to move that state directory. Set `FAM_REPO` to choose the default Git repository.

The active project folder is approved on first startup. To work elsewhere, open **Folders & access**, enter the absolute folder path, then explicitly approve the request. Approval applies only to that folder and its descendants; it does not grant access to parent or sibling folders. After approval, browse to a Git repository and choose **Use project**. The chosen project path is sent with chat requests.

Research jobs are listed as active work under Quill until findings arrive. If Skipper restarts mid-research, the job is retried automatically once (“restarted automatically as job X”); a second interruption is reported as an error. Ask “what did quill find?” or paste a job id to get its report.

### What agents may read, write and run

Task agents run with no shell or file restrictions: Claude Code runs with permissions skipped and Copilot with all tools allowed, so builds, tests, installs and edits work anywhere the user's account can reach. Bash commands are only recorded (by the `runlog.py` hook) and listed in each run and its report. Two read-only cases remain: chat with a family member, and review of untrusted repositories, which can read but never edit or run code.

### Skills

Ranger copies the skills (folders with a `SKILL.md`) from repositories it scores at 0.7 or higher into `~/.fam/skills/<owner>__<repo>/`. To add your own, drop a folder with a `SKILL.md` into `~/.fam/skills/<any name>/`. Every family member (builders, reviewers, Quill and the rest, but not Skipper's routing) is offered up to three skills that fit what it is asked (name, description and path, matched to the request) and may read them. Say “skills” in chat to see what is available and how often each was offered. Lessons from the same reviews become rules only after they are seen twice.

### Working together

Members share a notebook per task. Advice from Sage, Blue and Tess, findings from consulted members, and anything you add to a running task (“tell task 1234abcd: use the existing form component”) reach the builder before its next step, even mid-build: it gets one follow-up pass with the new information. Builders also see what other running tasks in the same folder are changing and the advice those tasks were given. A task that repeats one already running is passed to it instead of starting a second agent, and when two running tasks change the same file each is shown the other's changes and told to stay compatible instead of redoing the work. Work done in place in a plain folder runs one agent at a time. When the tests or reviewers reject a change, their output and fixes go back to the same builder in the same worktree, which fixes and is checked again, up to 3 rounds per builder; then the next more expensive builder continues from the same work, and only when every builder has had a go does Skipper stop and report the open problems (the work is kept, never discarded).

### How you work

The family learns how you like work done and passes it to every member in their prompts. What you state (“keep functions small”, “always put results in the repo folder”) is in force at once. Habits Sage notices in your own commits (in approved git folders) and in your recent chat requests are in force once seen twice. This runs on the scheduled check and when you say “learn from my work”. Say “habits” to see everything learned and where it came from, and “forget 3” to drop one. It is stored in `~/.fam/profile.json`; your commit diffs and messages are sent to the model for this, with lockfiles, `node_modules` and `.env` files left out.

### Attachments

Attach files in the web chat with the 📎 button, by dragging them onto the composer, or by pasting (screenshots work). From the terminal: `python daemon.py chat --attach notes.md --attach screen.png "what's wrong here?"`. Files are stored privately under `~/.fam/uploads` (folder 0700, files 0600, random ids) and the agent is told their paths and that their contents are data, not instructions. Allowed types: images (png, jpg, gif, webp), pdf, and UTF-8 text/code (txt, md, csv, json, log, py, js, ts, tsx, html, css, yaml, toml, sql, sh, diff, patch). The size limit is 10 MB per file (`FAM_UPLOAD_MAX`, bytes), up to 10 files per message. Uploading and previewing both require the local token.

### Spend estimate and appearance

The **Est. spend today** pill is a rough estimate (characters ÷ 4 × list price), kept in `~/.fam/ledger.json` so it survives restarts and resets each day. The UI is light by default; use **Dark mode** in the header to switch (remembered in the browser).

Each implementation task uses a separate Git worktree. Skipper runs the repository tests and family review. If accepted, it applies the change locally only if the original repository has no uncommitted changes and still matches the task's starting revision. Otherwise, Skipper leaves the accepted work in its isolated worktree and tells you why. Once your working tree is clean, say “apply task <id>” to retry. No task is pushed to a remote.

Use “status” in chat or `python daemon.py status` for active work, member availability, learned results, and budget. If a family member needs clarification, Skipper will ask in the conversation; answer naturally and the work resumes in the same worktree. If the daemon restarts mid-task, the worktree is preserved and you can say “resume” or “resume task <id>”. If the family asks you to resolve a disagreement, say “accept task <id>” or “reject task <id>”. Ask to speak with a family member by name in chat. Skipper routes task requests, research, focus changes, tool approvals, and guidance through the same conversation.
