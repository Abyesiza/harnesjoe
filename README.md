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

To start without opening a browser, use `python daemon.py start`. The web conversation is at <http://127.0.0.1:8000>. The page shows builders, reviewers and research members around the central chat, and indicates when Skipper is waiting on a response.

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

## Local state and safety

Conversation history, event history, folder approvals, routing knowledge, tool proposals, and cached briefs are stored locally under `~/.fam` by default. Set `FAM_HOME` to move that state directory. Set `FAM_REPO` to choose the default Git repository.

The active project folder is approved on first startup. To work elsewhere, open **Folders & access**, enter the absolute folder path, then explicitly approve the request. Approval applies only to that folder and its descendants; it does not grant access to parent or sibling folders. After approval, browse to a Git repository and choose **Use project**. The chosen project path is sent with chat requests.

Research jobs are listed as active work under Quill until findings arrive. If Skipper restarts mid-research, the job is retried automatically once (“restarted automatically as job X”); a second interruption is reported as an error. Ask “what did quill find?” or paste a job id to get its report.

### What agents may read, write and run

Task agents run with no shell or file restrictions: Claude Code runs with permissions skipped and Copilot with all tools allowed, so builds, tests, installs and edits work anywhere the user's account can reach. Bash commands are only recorded (by the `runlog.py` hook) and listed in each run and its report. Two read-only cases remain: chat with a family member, and review of untrusted repositories, which can read but never edit or run code.

### Attachments

Attach files in the web chat with the 📎 button, by dragging them onto the composer, or by pasting (screenshots work). From the terminal: `python daemon.py chat --attach notes.md --attach screen.png "what's wrong here?"`. Files are stored privately under `~/.fam/uploads` (folder 0700, files 0600, random ids) and the agent is told their paths and that their contents are data, not instructions. Allowed types: images (png, jpg, gif, webp), pdf, and UTF-8 text/code (txt, md, csv, json, log, py, js, ts, tsx, html, css, yaml, toml, sql, sh, diff, patch). The size limit is 10 MB per file (`FAM_UPLOAD_MAX`, bytes), up to 10 files per message. Uploading and previewing both require the local token.

### Spend estimate and appearance

The **Est. spend today** pill is a rough estimate (characters ÷ 4 × list price), kept in `~/.fam/ledger.json` so it survives restarts and resets each day. The UI is light by default; use **Dark mode** in the header to switch (remembered in the browser).

Each implementation task uses a separate Git worktree. Skipper runs the repository tests and family review. If accepted, it applies the change locally only if the original repository has no uncommitted changes and still matches the task's starting revision. Otherwise, Skipper leaves the accepted work in its isolated worktree and tells you why. Once your working tree is clean, say “apply task <id>” to retry. No task is pushed to a remote.

Use “status” in chat or `python daemon.py status` for active work, member availability, learned results, and budget. If a family member needs clarification, Skipper will ask in the conversation; answer naturally and the work resumes in the same worktree. If the daemon restarts mid-task, the worktree is preserved and you can say “resume” or “resume task <id>”. If the family asks you to resolve a disagreement, say “accept task <id>” or “reject task <id>”. Ask to speak with a family member by name in chat. Skipper routes task requests, research, focus changes, tool approvals, and guidance through the same conversation.
