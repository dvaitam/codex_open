Agent Async CLI
================

An async coding agent that can:
- Select a local repo path
- Choose a provider (OpenAI, Gemini, XAI, Claude) and model
- Create a programming task
- Run iteratively, executing shell commands chosen by the model
- Stream commands and process output live to the console and JSONL logs

Today it runs locally with a LocalExecutor. The design cleanly separates provider, agent policy, and executor so future deployments can run provider/executor remotely.

Quick start
-----------

Web UI
------

Start the built-in server and open the UI:

```
python -m agent_async.server --host 127.0.0.1 --port 8765
```

Then visit http://127.0.0.1:8765 in your browser. Enter a Repo URL (e.g., GitHub HTTPS or SSH), pick a provider and model, write the task, and start a run. The server clones the repo into a `workspace/` folder and the agent works inside that directory. The command stream and outputs will appear live, including cloning progress.

Recent repos
------------
- The server keeps a small `data/repos.json` with recently used repo URLs.
- The New Task view shows a datalist and quick chips you can click to fill the Repo URL field.
- Repos are auto-saved when you start a run; you can also add one explicitly via POST `/api/repos` with `{repo_url}`.

Sending command outputs to the model
-----------------------------------
- By default, the agent now sends the full stdout/stderr of each command back to the model as context.
- You can enable truncation in the UI (New Task → Output to Model) and set a character limit (e.g., 4000).
- The truncation setting is stored per-run and applied during that run only.

SSH for Git (SSH URLs)
----------------------
- Paste your SSH private key in the UI (New Task → API Key section → SSH Key for Git) and click "Save SSH Key".
- The server stores it at `data/ssh/id` with `0600` permissions and uses it for git clone/push when using SSH URLs (e.g., `git@github.com:...`).
- It sets `GIT_SSH_COMMAND='ssh -i data/ssh/id -o StrictHostKeyChecking=no'` for git operations in clone and PR push.
- Remove the key anytime with the "Remove SSH Key" button.
- Note: This stores a key on your local machine running the server; do not use sensitive keys unless you trust this environment.
Models and API keys
-------------------

- Enter an API key for your selected provider in the UI.
- Click "Fetch Models" to list available models from the provider's API.
- You can still type a custom model name if needed.
- API keys persist in your browser's localStorage by provider (never written to server disk). The last selected provider is also remembered and preselected on load.

CLI (optional)
--------------

You can also run via CLI for quick checks:

```
python -m agent_async.cli start --repo ~/code/my-repo --provider simple --task "List files" 
python -m agent_async.cli watch --run <run_id>
```

Environment
-----------

- Python 3.11+
- Set provider API keys as needed:
  - OPENAI_API_KEY
  - ANTHROPIC_API_KEY (Claude)
  - GEMINI_API_KEY (Google AI Studio)
  - XAI_API_KEY (xAI)

Design
------

- providers/: pluggable adapters exposing a minimal streaming interface.
- agent/: system prompt and simple JSON-action policy.
- exec/: LocalExecutor that streams stdout/stderr as events.
- runs/: per-run folder with events.jsonl and transcripts.

Limitations
-----------

- No external deps; HTTP clients are sketched for future wiring.
- In this sandbox, network calls are disabled; providers are structured but not executed.
- A SimpleProvider can be used for dry runs.

Tuning
------
- Reduce log noise from event polling by default; set `AGENT_ASYNC_QUIET_EVENTS=0` to log every request.
- Enable HTTP request/response logs for provider calls with `AGENT_ASYNC_DEBUG_HTTP=1`.
