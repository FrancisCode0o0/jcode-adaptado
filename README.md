# jcode — adapted for mejorador-audio and Abschrift (0.10.0-ma.3)

> **This is a fork.** Upstream: `gitty.informatik.hs-mannheim.de/fischerj/jcode`,
> branch `multimodal`, commit `b37d004cb7` (2026-09-23), by Prof. Dr. Joern Fischer,
> MIT license. Everything below the line *"Original README"* is upstream's
> documentation, unchanged except where marked.
>
> The fork turns jcode into a background **executor** for the
> [mejorador-audio](https://github.com/FrancisCode0o0/mejorador-audio) project:
> it runs long scripts and test batteries, reads their reports and diagnoses
> failures — it does not fix code. The original behaviour is still available
> as **builder** mode.
>
> Since version `0.10.0-ma.2` the same fork also serves the
> [Abschrift](https://github.com/FrancisCode0o0) course project as a
> **verifier** (it writes acceptance tests and reviews diffs) with its own
> project folder and models. Without the new options jcode behaves exactly as
> for mejorador-audio — except that `run_command` no longer sees API keys (E4).
>
> Since `0.10.0-ma.3` every agent of the VM can run in a **cell** (bubblewrap):
> `run_command` sees only its project folder; the home folder is empty, the
> environment holds no key. Required for Abschrift; prepared but **off** for
> mejorador-audio until its plan step 1.0.e. Only the MejoraDeAudio chat
> changes this repository, also for what only Abschrift uses.

## What this fork changes

Every change in `jcode.py` carries a `FORK:` comment; the new logic lives in
`jcode_extras.py` so the diff against upstream stays small.

| Change | Why |
|---|---|
| **Two modes**, `/mode executor` (default) and `/mode builder` (Spanish aliases `ejecutor`, `constructor`). The mode is shown in the splash screen and in the prompt (`[EXECUTOR] Task>`). Switching starts a new session. | The upstream prompt tells the agent to fix code until tests pass — the opposite of an executor. Builder mode keeps that behaviour for the rare case it is needed. |
| **Executor mode**: own system prompt (run, read, diagnose, report — never fix); no `edit_file`, `insert_lines`, `delete_lines`, `web_search`, `fetch_url`, `take_screenshot`; `write_file` only under `code/reports/`; tool calls the mode does not offer are refused even if the model makes them. `view_image` and `view_pdf_page` stay (read-only). | An executor must not be able to change the project. |
| **Git check after every task** (also after Ctrl+C): lists files changed in `./code` outside `reports/`; in executor mode in red, with the command to undo. Nothing is restored automatically. | `run_command` can still change files from the shell; this is the safety net. |
| **Automatic model fallback** (`model_fallback` in settings.json): a 429 for a *daily* quota switches to the next model at once; a *per-minute* 429 waits (server-suggested delay) and retries, switching only if it persists. When the quota day turns over (midnight Pacific), the next task goes back to the first model. All models exhausted → the task stops, the session is kept. | Upstream retries 4 times and gives up: an unattended job would die halfway. |
| **Reasoning effort per model** (`reasoning_effort` in each chain entry); `/reasoning` still overrides. | Fewer tokens on Flash Lite; models that reject the parameter get `null`. |
| **Limits in settings.json**: steps per mode (15 / 40), compaction threshold (60 000 tokens, optionally tighter per model), maximum `run_command` timeout (4 h). `/max-turns` still works for one-off cases. | Tune without touching code. Every request re-sends the whole history, and the free Flash Lite tier allows ~250 K tokens per minute. |
| **Compaction keeps AGENTS.md**: all leading system messages survive a compaction (upstream summarised the AGENTS.md rules away); the summary request is sized to the threshold. | The project rules must hold for the whole session. |
| **`run_command` timeout**: description fixed (upstream said "default 30", the real default is 60 s), tells the model to pass long timeouts for long scripts, capped by settings. | jcode waits without sending requests, so a 2-hour script costs ~2 requests. |
| **Paths next to jcode.py**: `settings.json`, `code/` and `logs/` are found next to `jcode.py`, not in the current directory. | Upstream silently ignored settings.json when started from another folder. |
| **Logs**: one dated file per session in `logs/` (outside the sandbox), instead of overwriting `logFile.md`. | History of every run; the agent cannot touch it; `git status` in `./code` stays clean. |
| **`/models`** asks the active provider with its API key. | Upstream's call had no key, so it only worked for local servers. |
| **Startup warning** in the splash screen when no fallback model could be activated (e.g. API key variable not set). | Upstream printed it before clearing the screen. |

### Added for Abschrift (version 0.10.0-ma.2, Abschrift plan step 0.1)

| Change | Why |
|---|---|
| **E1 · `--project <folder>`** (also `--project=<folder>`, only before the task): `settings.json`, `code/` and `logs/` come from that folder. The folder must exist and contain a `settings.json`, otherwise jcode stops with exit code 2. Without the option everything stays next to `jcode.py`. The splash screen shows `Project`. | One jcode installation, two projects with their own models, sandbox and logs. A typo must never run a task on the other project. |
| **E2 · verifier mode** (`/mode verifier`, aliases `verificador`, `pruefer`): the executor's tools; `write_file` only under `modes.verifier.write_paths`; own system prompt for writing acceptance tests from SPEC.md and reviewing diffs against SPEC.md/STIL.md, with the exit answer `KEINE FEHLER`. The mode **exists only** when settings.json configures it. The git check after each task accepts changes inside the write paths and flags everything else in red. | The reviewing agent must be able to write tests, but never the code it reviews. |
| **E3 · `fallback_exhausted`**: `"default_provider"` (default, as before) or `"stop"`: jcode never leaves `model_fallback`; with no usable chain model it does not start (exit code 2), and an exhausted chain ends the task. Daily-quota detection only knows Gemini's wording; for other providers a 429 means "5 attempts, then the next model" (unchanged code path). | The reviewer must never silently become a model of the family that builds the code. |
| **E4 · `run_command` without secrets**: every variable whose name contains `KEY`, `TOKEN`, `SECRET`, `PASSWORD`, `PASSWD` or `CREDENTIAL` (case-insensitive) is removed from the command's environment, except names in `env_passthrough`. jcode itself keeps them (it needs its API key). The splash screen lists the hidden names, never values. | No shell command — and no model reading its output — can pass a key to another provider. |

Limit of E4 alone: jcode and its commands run as the same Linux user, so a
command could still *read files* holding keys (`~/.bashrc`, `/proc/<jcode>/environ`).
The cell of 0.10.0-ma.3 closes that: inside it, those files do not exist.
`GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_<n>`/`GIT_CONFIG_VALUE_<n>` are removed (or
kept, via `GIT_CONFIG_COUNT` in `env_passthrough`) as one group, because
removing only the `KEY_` part breaks every git command.

### Added in 0.10.0-ma.3 (mejorador-audio plan step 1.0.b1)

| Change | Why |
|---|---|
| **E7 · cells** (`jcode_cell.py`, `"sandbox"` in settings.json): with `"enabled": true` every `run_command` runs in bubblewrap — system read-only; `/home`, `/mnt`, `/media`, `/run/user`, `/srv`, `/tmp`, `/var/tmp` replaced by empty in-memory folders; then only the configured paths (`code/` writable, its `.git` read-only, Node/browsers read-only); empty environment except a short list; own PID namespace (a timeout also ends what the command left running); network open. bwrap itself starts with a two-variable environment (it is process 1 in the cell). jcode checks the cell from inside at start-up and refuses to start if it does not work; `"required": true` (Abschrift) also refuses `"enabled": false`. | One Linux user, one cell per agent (decision of 2026-10-20): the git check after a task then covers everything the agent could change, and a free model that may train on its input never sees a key or another project. |
| **search_files / list_files stay in `code/`**: `..` in the glob is refused, symlinks that lead out of the sandbox are skipped. | Upstream (and every fork version up to ma.2) let one `search_files` call read e.g. `~/.bashrc` with the API key and pass it to the model. |
| **Protected sub-folders** (`read_only_in_workdir`): every folder between the work folder and the protected one is mounted onto itself first. | A read-only `tests/acceptance` alone is bypassed with `mv tests x` (tested). |
| **Interactive cells** (Gemini CLI): where the kernel still allows TIOCSTI, the program gets its own pseudo-terminal (`script`) in a new session. | Otherwise a process inside could type commands into Fran's shell that run after the cell ends (tested). |

#### Scripts

| Script | What it does |
|---|---|
| `tools/check_secrets.py` | Merged version (mejorador-audio 1.0 + Abschrift ma.2). Refuses commits with secrets: known formats (Google `AQ.…` and `AIza…`, `sk-…`, NVIDIA `nvapi-…`, Groq `gsk_…`, GitHub tokens, private keys, Kaggle, quoted `api_key = "…"` assignments) **and** the values of every secret-looking variable set right now. `--staged` (default, the hook), `--all`, `--range A..B` (every line the commits add, also lines removed later), `--diff FILE\|-`, `FILE…`. `--install-hook` only sets `core.hooksPath .githooks`; it never writes a hook file. No "allow" marker. Names file, line and rule (or variable NAME), never a value. |
| `vm/session_check.sh [--quick] [project]` | Merged traffic light. Always: Python + openai, settings.json (modes, chain, cell), every chain key set (never shown), jcode-adaptado clean → `git pull --ff-only`, sandbox clean (the agent's own output folders only warn) and without secrets, `logs/`, hook, cell (built and inspected from inside), jcode tests. Without a project (mejorador-audio): venv, key length, pull of `code/`, disk, node, ffmpeg, ntfy, read-only `datos/`. With a project (Abschrift): chain without Google, `fallback_exhausted: stop`, cell on and required, trusted hook of the verifier clone. |
| `vm/setup_abschrift.sh` | E8: meeting repo `~/repos/abschrift.git`, clones `~/jcode-projekte/abschrift/code` and `~/abschrift-gemini`, project folder, Gemini CLI 0.64.0 in `~/.local/opt/gemini-cli`, Abschrift's Playwright browsers in `~/.cache/ms-playwright-abschrift`, all three cells checked. Changes nothing of mejorador-audio; never overwrites. |
| `vm/abschrift/start_jcode.sh`, `start_gemini.sh` | The only way to start the two agents: clean clone or stop, `--branch <phase>`, `git pull --ff-only`, then the agent in its cell. Git in the Gemini clone runs in a sync cell (no network, no home), because Gemini CLI can write that clone's `.git`. |
| `vm/abschrift/push.sh gemini\|verifier` | Fran's push to the meeting repo: git runs only in the meeting repo; new commits are scanned for secrets; Gemini commits touching `tests/acceptance/` are refused; fast-forward only. |
| `vm/abschrift/npm_install.sh` | npm packages and browsers of an Abschrift clone, in an install cell (npm runs package scripts). |
| `vm/install_ma3.sh` | VM installation of ma.3: pull, bubblewrap, AppArmor check (Ubuntu's own fix only if needed), SSH check, `setup_abschrift.sh`, tests, both session checks → `MA3 READY`. |
| `vm/verify_abschrift.sh` | The Abschrift 0.1.g list, checked for real; report without keys (scanned before it is shown). |
| `vm/windows/install_ma3.ps1` | Windows part: package checked against `MANIFEST.sha256`, copied into both working copies, `check_secrets.py --all`, hook files executable in git, commit, push. |
| `vm/agent-hooks/pre-commit` | Trusted hook for the Abschrift verifier clone (outside every cell). |
| `tools/make_package.py` | Builds the delivery ZIP (for the MejoraDeAudio chat). |

## Install

Step by step: the installation guide of 0.10.0-ma.3 (MejoraDeAudio project).
In short: Windows `vm/windows/install_ma3.ps1`, then in the VM
`bash vm/install_ma3.sh` (after block D, `vm/setup_vm.sh`).

Run the tests (no network, no key; the cell tests need bubblewrap):
`python3 -m pytest -q` · mutation check: `python3 tests/mutation_check.py`.

## settings.json keys added by the fork

| Key | Default | Meaning |
|---|---|---|
| `default_mode` | `"executor"` | Mode at start-up |
| `modes.<mode>.max_steps` | 15 / 40 | Tool-use steps per task |
| `compact_threshold_tokens` | 60000 | History is compacted above this |
| `max_command_timeout_s` | 14400 | Cap for the `timeout` of one `run_command` |
| `model_fallback` | `[]` | Ordered list of `{provider, model, reasoning_effort, compact_threshold_tokens?}`; the first entry is the start model. Empty = upstream behaviour. |
| `modes.verifier` | (absent) | `{"max_steps": 30, "write_paths": ["tests/acceptance/", "reports/"]}`. Absent or without a valid write path = no verifier mode. Paths must be relative, without `..`. |
| `fallback_exhausted` | `"default_provider"` | `"stop"`: never use a model outside `model_fallback` (needs a non-empty chain). |
| `env_passthrough` | `[]` | Exact variable names `run_command` may still see although they look secret, e.g. `["KAGGLE_KEY", "KAGGLE_API_TOKEN"]` for mejorador-audio. |

| `sandbox` | (absent = off) | Cell for `run_command` (see `jcode_cell.py`): `enabled`, `required`, `read_only`, `read_write` (absolute or `~/…`), `read_only_in_workdir` (relative), `git_read_only` (default true), `env`, `env_keep`, `expect_absent` (what the check must not see inside), `interactive`, `network`. |

Invalid values fall back to the default with a warning; jcode always starts
(exceptions, on purpose: a wrong `--project`, `fallback_exhausted: "stop"`
without a usable model, and a cell that is switched on but does not work).
The model ids in the shipped `settings.json` must be checked with `/models`.

---

# Original README

A minimal, single-file coding agent (~1,200 lines of Python) for local LLMs — with optional cloud fallback.

jcode connects to a local **LM Studio** or **Ollama** server (or, via `settings.json`, to **OpenAI**, **Anthropic**, or **Gemini**), takes a task in plain language, and works autonomously inside a sandboxed `./code` directory: it reads, writes, and edits files, runs commands, searches the web, looks at images/screenshots/PDF pages, writes its own smoke tests, and keeps fixing the code until the tests pass.

```
   _               _
  (_) ___ ___   __| |  ___
  | |/ __/ _ \ / _  | / _ \
  | | (_| (_) | (_| ||  __/
 _/ |\___\___/ \____| \___|
|__/
```

## Features

- **Autonomous test-fix loop** — the agent writes a test for every change, runs it, analyzes failures, fixes the code, and repeats until green (up to `MAX_STEPS`, default 200).
- **Sandboxed** — all file operations are confined to `./code`; path-escape attempts (`../`, absolute paths) are blocked. Destructive shell commands (`rm -rf /`, `sudo`, `dd`, `mkfs`, shutdown, fork bombs, `curl | sh`, …) are refused.
- **Zero-config local backends** — auto-detects LM Studio (port 1234) first, then Ollama (port 11434).
- **Cloud providers via `settings.json`** — switch to OpenAI, Anthropic, or Gemini at runtime with `/backend <name>`; all are used through their OpenAI-compatible endpoints, so the single `openai` client dependency covers everything.
- **Project guidelines via `AGENTS.md`** — project-specific rules (coding style, per-language test conventions) are loaded from `code/AGENTS.md` and injected as binding instructions.
- **Vision** — the agent can look at images, screenshots and PDF pages (see the `view_image`/`take_screenshot`/`view_pdf_page` tools below) to visually check its own work, e.g. a generated plot or a running GUI.
- **Session-wide context management** — token usage is tracked (server-reported or estimated); when the context nears its limit, older history is compacted into an LLM-written summary so long sessions keep working.
- **Adjustable reasoning effort** — `/reasoning high|medium|low|max|none` controls the model's thinking effort at runtime (backend-dependent; `none` turns it off).
- **Robust against flaky servers** — visible retries with backoff; if the backend goes down, the session is preserved and the task can simply be resubmitted.
- **Transparent** — every request, tool call, and result is logged to `logFile.md`; file changes are shown as colored unified diffs in the terminal.
- **Tools available to the model:**
  - `list_files`, `read_file` (optionally with line numbers), `search_files` (regex grep over the sandbox, hits as `file:line: content`)
  - `write_file`, `edit_file` (search/replace), `insert_lines`, `delete_lines`
  - `run_command` (blocks known-destructive patterns; `pip`/`pip3` calls are redirected to jcode's own Python interpreter)
  - `web_search` (DuckDuckGo) and `fetch_url` (fetches a page and extracts its text)
  - `view_image` (png/jpg/gif/webp/bmp/svg), `take_screenshot` (whole screen, e.g. of a GUI app under test), `view_pdf_page` (renders one PDF page) — images are delivered to the model as an inline image right after the tool result

## Requirements

- Python 3.10+
- `pip install openai prompt_toolkit ddgs`
- One of:
  - [LM Studio](https://lmstudio.ai/) with the local server running (default `http://localhost:1234`), or
  - [Ollama](https://ollama.com/) (`http://localhost:11434`), or
  - an API key for OpenAI / Anthropic / Gemini (see `settings.json` below)
- Optional, only needed for the vision tools (the agent will tell you what's missing and how to install it when it tries to use one):
  - `Pillow` — `view_image`, `take_screenshot`, `view_pdf_page`
  - `cairosvg` — `view_image` on `.svg` files
  - `pymupdf` — `view_pdf_page`
  - `certifi` — CA bundle for `fetch_url`; falls back to the system default if not installed. Set `JCODE_INSECURE_SSL=1` to disable TLS verification (e.g. behind a re-signing corporate proxy) — use with care.

## Usage

```bash
# One-shot task
python jcode.py "Write fib.py with fib(n) and a smoke test that checks fib(10)==55."

# Interactive mode (multiline input; history is kept across tasks)
python jcode.py
```

All generated files land in `./code`, which is created automatically.

### Interactive commands

| Command | Effect |
|---|---|
| `/status` | Show context usage, message counts, model, sandbox |
| `/new`, `/neu`, `/reset` | Clear the session history |
| `/models` | List models available on the current backend |
| `/model <name>` | Switch model |
| `/backend <name>` | Switch to `lm-studio`, `ollama`, or a provider from `settings.json` (e.g. `openai`, `anthropic`, `gemini`) |
| `/reasoning <level>` | Set reasoning effort: `high`, `medium`, `low`, `max`, or `none` (off); no argument resets to the backend default |
| `/max-turns N` | Set the step limit per task |
| `Ctrl+C` | Abort the current input or running task (session is preserved) |
| `Ctrl+D`, or `Ctrl+C` on an empty prompt | Quit |

## Configuration

### settings.json (optional)

Placed next to `jcode.py`. Defines cloud providers and, optionally, the backend to start on. API keys are read from environment variables — don't put literal keys in the file.

```json
{
  "default_provider": "",
  "providers": {
    "openai": {
      "base_url": "https://api.openai.com/v1",
      "api_key_env": "OPENAI_API_KEY",
      "model": "gpt-4.1"
    },
    "anthropic": {
      "base_url": "https://api.anthropic.com/v1/",
      "api_key_env": "ANTHROPIC_API_KEY",
      "model": "claude-sonnet-4-5"
    },
    "gemini": {
      "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
      "api_key_env": "GEMINI_API_KEY",
      "model": "gemini-2.5-pro"
    }
  }
}
```

- `default_provider`: name of a provider to activate at startup; leave `""` to use the auto-detected local backend.
- Each provider needs `base_url` (an OpenAI-compatible endpoint), `api_key_env` (environment variable holding the key), and optionally `model`.
- If the file is missing or malformed, jcode prints a warning and runs purely locally.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python jcode.py
Task> /backend anthropic
```

### AGENTS.md (optional)

If `code/AGENTS.md` exists, it is loaded at session start and treated as binding project guidelines — e.g. "simplicity first", surgical changes only, and the per-language verification conventions (how to compile/test C, C++, Java, PHP, Python, HTML, CSS). Keeping language-specific rules here rather than in the system prompt keeps every request lean and lets you adapt conventions per project without touching code.

### Constants in jcode.py

| Constant | Default | Meaning |
|---|---|---|
| `MODEL` | (auto) | Model name; auto-corrected to the closest available model |
| `MAX_STEPS` | 200 | Tool-use steps per task |
| `CMD_TIMEOUT` | 60 s | Default timeout per shell command (overridable per call via the `timeout` argument) |
| `CONTEXT_LIMIT` | 256 000 | Model context window (tokens) |
| `COMPACT_THRESHOLD` | 250 000 | History compaction kicks in above this |
| `KEEP_RECENT` | 8 | Most recent messages kept uncompressed when compacting |
| `REQUEST_TIMEOUT` | 11 600 s | Timeout per LLM request (local models are slow) |
| `REASONING_EFFORT` | (backend default) | Reasoning effort sent to the backend; set at runtime with `/reasoning` |
| `MAX_IMAGE_SIDE` | 1280 px | Longest edge images are scaled to before being sent to the model |
| `MAX_IMAGES_IN_CONTEXT` | 4 | Older images beyond this are replaced by a text stub to save context |

## How it works

1. The task is appended to the session history and sent to the model together with the tool schemas (derived automatically from the Python type annotations — no schema drift).
2. The model plans, calls tools (read → edit → run test), and gets each result back.
3. Failing tests loop back into analysis and fixes; only when the tests are green does the model answer with a summary and the turn ends.
4. Near the context limit, the older history is summarized by the model itself and replaced, so the session can continue indefinitely.

## Safety notes

jcode blocks known-destructive command patterns and confines file access to the sandbox, but `run_command` still executes shell commands on your machine with your user's permissions. Review `logFile.md` when in doubt, and treat web content fetched by the agent as untrusted data.

## License

MIT License — Copyright (c) 2026 Prof. Dr. Joern Fischer

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
