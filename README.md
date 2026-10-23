# Flowwright

**Describe it. Validate it. Automate it.**

Flowwright is a conversational workflow builder. Describe an automation in plain words, for example
*"when an invoice arrives in Gmail, if the amount is above ₹1,00,000 notify the manager on Telegram, otherwise
save it to OneDrive"*, and Flowwright asks only for what is still missing, as multiple-choice questions, until the
workflow is complete. You get an n8n-style workflow (JSON), a flow diagram, and, for a few step types, a real run.

**The core idea: the LLM proposes, code decides.** One LLM call per turn proposes the whole workflow and the
next question. Code then keeps a value only if the user's own words back it and it validates, never lets the
model change an earlier answer, checks the workflow's shape, and builds the output without the LLM.

| | |
| --- | --- |
| **Backend** | Python 3.11, FastAPI, LangGraph, SQLite (`app/`) |
| **Frontend** | React 19 + Vite, Mermaid diagrams (`frontend/react-app/`) |
| **LLM** | Groq `openai/gpt-oss-120b`, falling back to `openai/gpt-oss-20b` when rate limited |
| **Security** | Accounts with Argon2 passwords and JWT tokens; every session is private to its owner |

---

## Contents

- [Quick start](#quick-start)
- [Manual setup](#manual-setup)
- [Configuration](#configuration)
- [Using Flowwright](#using-flowwright)
- [How it works](#how-it-works)
- [API](#api)
- [Project structure](#project-structure)
- [Tests](#tests)
- [Deployment](#deployment)
- [Troubleshooting](#troubleshooting)
- [Limits](#limits)

---

## Quick start

**You need:** [Python 3.11+](https://www.python.org/downloads/), [Node.js 20+ (LTS)](https://nodejs.org) and a free
[Groq API key](https://console.groq.com/keys).

**Windows (PowerShell)**, from the repository folder:

```powershell
.\scripts\setup.ps1      # once: Python env, dependencies, .env with a generated secret
notepad .env             # set GROQ_API_KEY=...
.\scripts\start.ps1      # opens the API and the web app in two windows
```

**macOS / Linux:**

```bash
bash scripts/setup.sh    # once
nano .env                # set GROQ_API_KEY=...
bash scripts/start.sh    # Ctrl+C stops both
```

Then open **http://127.0.0.1:5173**, create an account on the sign-in screen and describe an automation.

| Service | URL |
| --- | --- |
| Web app | http://127.0.0.1:5173 |
| API | http://127.0.0.1:8001 (interactive docs: http://127.0.0.1:8001/docs) |
| Health check | http://127.0.0.1:8001/health |

`setup` is safe to run again: it skips what is already done and never overwrites an existing `.env`.

---

## Manual setup

What the scripts do, step by step:

```powershell
# 1. Backend
python -m venv .venv
.venv\Scripts\activate                 # macOS / Linux: source .venv/bin/activate
pip install -r requirements-dev.txt

# 2. Configuration
copy .env.example .env                 # macOS / Linux: cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"
#   paste the output into .env as AUTH_SECRET_KEY=..., and set GROQ_API_KEY=...

# 3. Frontend
cd frontend/react-app
npm install
cd ../..

# 4. Run (two terminals)
uvicorn app.main:app --port 8001 --reload        # terminal 1: API
cd frontend/react-app && npm run dev             # terminal 2: web app on http://127.0.0.1:5173
```

---

## Configuration

All settings live in `.env` (copied from [.env.example](.env.example)). Variables already set in the environment
take precedence.

| Variable | Required | Default | What it does |
| --- | --- | --- | --- |
| `GROQ_API_KEY` | **yes** | | Groq key for the planner LLM |
| `AUTH_SECRET_KEY` | **yes** | | Signs login tokens; at least 32 characters. The server refuses to start without it. Changing it signs everyone out |
| `LLM_MODEL` | | `openai/gpt-oss-120b` | Main model |
| `LLM_FALLBACK_MODEL` | | `openai/gpt-oss-20b` | Used while the main model is rate limited; `none` disables it |
| `LLM_TEMPERATURE` | | `0` | |
| `LLM_REASONING_EFFORT` | | `low` | gpt-oss reasoning effort: `low`, `medium`, `high`, or `none` for other models |
| `CORS_ORIGINS` | | `http://localhost:5173,http://127.0.0.1:5173` | Browser origins allowed to call the API |
| `SESSION_DB` | | `sessions.db` | SQLite file for conversations |
| `AUTH_DB` | | `auth.db` | SQLite file for accounts |
| `LOG_LEVEL` | | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |
| `SLACK_WEBHOOK_URL` | | | Slack incoming webhook used by the Run button's Slack steps |

The web app reads `VITE_API_URL` (default `http://127.0.0.1:8001`); to change it, copy
[frontend/react-app/.env.example](frontend/react-app/.env.example) to `frontend/react-app/.env.local`.

---

## Using Flowwright

1. **Sign in.** Create an account (password: 12-128 characters) on the sign-in screen.
2. **Describe an automation**, e.g. *"Every weekday at 6 PM, email me the new rows of the Orders sheet"*.
3. **Answer the questions.** Click an option, reply with its letter or number (`B`, `2`), or type your own
   answer. Each step is completed in order: its app, then what it does, then its settings.
4. **Get the workflow.** When nothing is missing, the right-hand panel shows the steps, the workflow JSON and a
   flow diagram. Keep chatting to change it, or describe a new automation.
5. **Come back later.** Every conversation is saved and listed under **Recent** in the sidebar.

### Running a workflow

When a workflow is generated, a **Run workflow** panel appears: click **Configure execution**, type the trigger's
data as JSON (e.g. `{"amount": 1500}`) and click **Execute workflow**. Each step reports `ok`, `failed` or
`skipped` (a branch the condition did not take).

The runner executes four step types; a workflow with any other step is refused before anything runs:

| Step | What it does |
| --- | --- |
| Manual or Webhook trigger | Its data is the JSON you typed |
| If | Compares a field of the data and follows the yes or no branch |
| HTTP Request | Calls a public http(s) URL (localhost and private networks are blocked; redirects are not followed) |
| Slack message | Posts to the incoming webhook in `SLACK_WEBHOOK_URL` |

Try it: *"When my webhook is called, if the amount is above 1000, send it with an HTTP Request POST to
https://httpbin.org/post"*, then run it with `{"amount": 1500}` and with `{"amount": 200}`.

---

## How it works

```
user message (or picked option)
   → plan      (LLM: the whole workflow + the best next question)
   → merge     (code: keep only what the user said, validate it, never drift earlier answers)
   → check     (code: is every app, operation and required setting known?)
        ├─ no  → ask       → multiple-choice question
        └─ yes → generate  → workflow JSON (no LLM) → schema, graph and grounding checks → diagram
```

The full graph is described at the top of [app/pipeline/graph.py](app/pipeline/graph.py).

| Rule (enforced in code) | Example |
| --- | --- |
| An app is set only if the user named it, or their words imply a built-in node | "notify me" → *Which app should notify you?* |
| A value is kept only if the user's own words contain it (whole words) and it validates | an invented spreadsheet name is dropped and asked; "Git" is not read from "github" |
| A kept value changes only when the latest message changes it | the model cannot drift an earlier answer |
| Formats are checked per kind: email, time, timezone, URL, number, channel, choice | "evening" is not a time → asked again with 5:00 / 6:00 / 7:00 PM |
| A condition's field is a data reference, never a number | "above ₹50,000" is `greater_than 50000` on `invoice.amount` |
| A step's app is asked before its settings | "which model?" is never asked before the app is known |
| A workflow is generated only when every step is complete and its shape is valid | one trigger first, every step reachable, no cycles |

**Branches.** A step after a condition runs on its `true` or `false` outcome, so workflows can branch, nest
conditions and join again. Data from earlier steps is referenced as `{{step_id.field}}`.

**Logs.** Every turn writes one JSON line to stderr: what the model proposed, what code kept, rejected or ignored
and why, the next question and the latency. Personal data in those values is masked
(see [app/core/turnlog.py](app/core/turnlog.py)).

**Privacy.** Identity numbers (Aadhaar- and PAN-like) are removed before text is sent to the LLM; emails and phone
numbers are sent, because a workflow may need them.

---

## API

Every endpoint except `/health`, `/ready`, `/auth/register` and `/auth/login` needs an
`Authorization: Bearer <access_token>` header. A session belongs to the user who created it; for anyone else it
does not exist (404). Interactive documentation is served at `/docs`.

| Endpoint | What it does |
| --- | --- |
| `POST /auth/register` `{email, password}` | Creates an account |
| `POST /auth/login` `{email, password}` | Returns `{access_token, token_type, user}`; the token is valid for 30 minutes |
| `GET /auth/me` | The signed-in user |
| `GET /health` · `GET /ready` | Liveness · readiness (database reachable and LLM key set) |
| `POST /chat` `{session_id?, message}` | One conversation turn. Leave out `session_id` to start a new session; the reply carries its id |
| `GET /sessions` | The user's sessions, newest first: `{id, title, mode, generated, updated_at}` |
| `GET /sessions/{id}` | One session with its full message history |
| `POST /sessions/{id}/new` | Starts a new workflow in the session (no LLM); a generated one is kept under `previous_workflows` |
| `POST /sessions/{id}/run` `{input}` | Runs the generated workflow (see [Running a workflow](#running-a-workflow)) |
| `DELETE /sessions/{id}` | Deletes the session |

`POST /chat` and `GET /sessions/{id}` return the reply (`reply` as Markdown and `parts` for custom layouts), the
current `question` (`{target, text, options, allow_custom, step, note}`), the draft (`name`, `steps`, `fields`),
and once generated, the `workflow` JSON and its Mermaid `diagram`.

Request bodies are limited to 1 MB (413). Two simultaneous changes to one session get 409 instead of losing one.
Every response carries an `X-Request-ID` header, which also appears on every log line and in the body of a 500.

---

## Project structure

```
.
├── app/                              # backend (FastAPI)
│   ├── main.py                       # HTTP API: auth, sessions, chat, run; CORS, request ids, limits
│   ├── core/
│   │   ├── config.py                 # settings from .env
│   │   ├── logging.py                # log format with the request id
│   │   ├── turnlog.py                # one JSON log line per turn
│   │   └── llm/
│   │       ├── client.py             # Groq via LangChain: retries, fallback model, reply recovery
│   │       └── prompts/planner.txt   # the planner prompt
│   ├── pipeline/                     # one conversation turn
│   │   ├── graph.py                  # LangGraph wiring: ingest → plan → merge → check → ask / generate
│   │   ├── planner.py                # the only LLM call per turn
│   │   ├── planner_schema.py         # what the LLM must return (TurnPlan)
│   │   ├── merge.py                  # code decides: keep only grounded, valid values
│   │   ├── normalize.py              # parsing: times, timezones, channels, fields, operators
│   │   ├── validation.py             # per-kind validation, open items, readiness
│   │   ├── questions.py              # the next multiple-choice question
│   │   ├── generation/               # workflow JSON (builder, schema, output checks)
│   │   └── diagram/mermaid.py        # Mermaid flowchart from the workflow JSON
│   ├── runtime/executor.py           # runs a workflow: Manual/Webhook, If, HTTP Request, Slack
│   ├── security/                     # auth (Argon2, JWT), PII redaction, URL guard for runs
│   └── state/                        # WorkflowState model, modes, SQLite session store
├── frontend/react-app/               # web app (React + Vite)
│   ├── src/App.jsx                   # sign-in, chat, sidebar, workflow panels
│   ├── src/MermaidDiagram.jsx        # draws the workflow diagram
│   ├── src/api.js                    # API client with the auth token
│   └── public/favicon.svg
├── tests/                            # pytest suite; the LLM is scripted, no API key needed
├── scripts/                          # setup and start commands (Windows .ps1, macOS/Linux .sh)
├── requirements.txt                  # backend runtime dependencies
├── requirements-dev.txt              # + test tools
├── .env.example                      # configuration template
└── pytest.ini
```

---

## Tests

```bash
pytest
```

The suite covers the conversation pipeline, grounding, conditions, the LLM client, sessions, auth and ownership,
API limits and the workflow runner. The LLM's replies are scripted and HTTP is faked, so no API key or network is
needed. Test settings use a temporary folder, so your `sessions.db` and `auth.db` are never touched.

---

## Deployment

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8001 --workers 4 --proxy-headers

cd frontend/react-app
npm ci
VITE_API_URL=https://api.your-domain.com npm run build      # serve the dist/ folder as static files
```

- Set `GROQ_API_KEY`, `AUTH_SECRET_KEY` and `CORS_ORIGINS` (the web app's real origin) in the environment.
- Put the API behind a reverse proxy that terminates HTTPS and rate-limits `/auth/login` and `/chat`.
- Point health checks at `/health` (liveness) and `/ready` (readiness).
- Keep `SESSION_DB` and `AUTH_DB` on a persistent local disk (not a network share) and back them up.

---

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `RuntimeError: AUTH_SECRET_KEY is missing or too short` | Set `AUTH_SECRET_KEY` in `.env` (32+ characters). `scripts/setup` generates one for a new `.env` |
| `npm` / `node` is not recognized | Install Node.js 20+ (LTS) from https://nodejs.org and open a new terminal |
| `npm error enoent spawn ...\cmd.exe ENOENT` (Windows) | The Windows `ComSpec` variable points to the wrong place. Fix it in an admin PowerShell: `[Environment]::SetEnvironmentVariable('ComSpec', 'C:\Windows\System32\cmd.exe', 'Machine')`, or for npm only: `npm config set script-shell "C:\Windows\System32\cmd.exe"` |
| `running scripts is disabled on this system` (Windows) | Run `powershell -ExecutionPolicy Bypass -File scripts\setup.ps1` (same for `start.ps1`) |
| The web app says it cannot connect to the backend | Start the API (`scripts/start` or `uvicorn ...`) and check http://127.0.0.1:8001/health |
| A CORS error in the browser console | Add the web app's address to `CORS_ORIGINS` in `.env` and restart the API |
| "The AI service has reached its usage limit" | Groq's free quota is used up; wait the time shown, or use a key with more quota |
| Port 8001 or 5173 already in use | Stop the other process, or change the port (and `VITE_API_URL` / `CORS_ORIGINS` to match) |

---

## Limits

- **Only four step types run** (Manual/Webhook trigger, If, HTTP Request, Slack). Other steps are designed, not
  run. The output is n8n-style but not yet an importable n8n file.
- **A run is one request from the UI**: no public webhook URL, schedule or retries.
- **No app catalogue.** Any app the user names is accepted; nothing checks that an app or operation really
  exists or that a setting is the one that app needs.
- **Grounding is word matching**: it catches invented values, not every misreading of the user's words.
- **One condition shape**: one field compared with one value; no AND/OR, no loops.
- **SQLite storage** suits a single server; several servers need a shared database. Sessions do not expire, and
  there is no password reset yet.
- **No built-in rate limiting**: limit `/auth/login` and `/chat` at the reverse proxy.
- **The planner can still be wrong** about which steps exist; code rejects unbacked values but cannot add a
  missing step on its own.
