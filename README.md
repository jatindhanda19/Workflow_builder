# Flowwright

**Describe it. Validate it. Automate it.** A conversational workflow builder.

Describe an automation in simple words ("when an invoice arrives, if the amount is above ₹1,00,000
notify the manager, otherwise save it"). The builder asks only what it still needs, as
**multiple-choice questions**, and produces a workflow JSON, a flow diagram, and, for a few step
types, a real run.

**The core idea: the LLM proposes, code decides.** One LLM call per turn proposes the whole workflow
and the next question. Code then keeps a value only if the user's own words back it and it validates,
never lets the model drift an earlier answer, checks the workflow's shape, and builds the output with
no LLM. Every turn logs what the model proposed and what code kept, rejected or ignored, and why.

- **Backend:** FastAPI + LangGraph (`app/`), with sign-in and per-user sessions
- **Frontend:** React (Vite) chat UI with sign-in and clickable answer options (`frontend/react-app/`)
- **LLM:** Groq (`openai/gpt-oss-120b`, with `openai/gpt-oss-20b` while it is rate limited) through LangChain;
  every reply is validated against the `TurnPlan` schema

## How it works

Every turn the AI planner returns the whole workflow as n8n-style steps (one
trigger, then actions and `If` conditions) together with the parameters each step
needs, plus its prediction of the best next question. Code then decides what is kept:

| Rule (enforced in code) | Example |
| --- | --- |
| An app is set only if the user named it, or their words imply a built-in node | "notify me" → *Which app should notify you?* (Slack / Gmail / Telegram …) |
| A value is stored only if the user said it (its evidence is in their messages, as whole words) and it validates | an invented spreadsheet name is discarded and asked instead; "Git" is not taken from "github" |
| A stored value changes only when the latest message changes it | the AI cannot drift an earlier answer |
| Formats are checked per kind: email, time, timezone, URL, number, channel, choice | "evening" is not a time → asked again with 5:00 / 6:00 / 7:00 PM |
| A condition's field is a data reference, never a number; its comparison is read from the user's sentence | "10000" typed as the field → *10000 looks like a value, not a field name*; "above ₹50,000" is `greater_than 50000` |
| The workflow is ready only when every app and required parameter is known and its shape is valid | "otherwise save it" with no step for the otherwise case → asked, not ended |
| The generated JSON must pass schema, graph and grounding checks | one trigger first, every node reachable, no cycles |

Data from earlier steps is referenced as `{{step_id.field}}`, e.g. `{{shopify_trigger.customer_email}}`.

## If / otherwise

Each step follows the step before it, or the earlier steps it names in `after`. A step after a condition
says which outcome it runs on (`branch: true / false`), so a workflow can branch, nest conditions and
join again:

```
Gmail: New invoice → invoice.amount > 100000?
                        ├─ yes → Telegram: Notify manager
                        └─ no  → OneDrive: Save invoice
```

Two steps put on the same outcome ("if so, add it to Airtable and send it to Slack") run one after the
other, in the order written. An outcome with no step ends the workflow; it may only be empty when the user did not say what to do
"otherwise".

## Questions are multiple choice (MCQ)

- The AI picks which missing item to ask and predicts 2-5 likely answers from the
  request and the conversation.
- Choice parameters always list all their valid choices. App questions list apps
  that can do the step. Other options are shown only if they pass validation, and
  email addresses only if the user wrote them.
- Very vague requests get a clarifying MCQ of concrete automations that match them.
- "Start a new workflow?" is an MCQ too.
- Answer by clicking an option, replying with its letter or number (`B`, `2`), or
  typing the option. You can also type your own answer, except on fixed-choice
  questions.

## API

Every endpoint except `/health`, `/ready` and `/auth/register|login` needs `Authorization: Bearer <access_token>`.
A session belongs to the user who created it; for anyone else it does not exist (404).

| Endpoint | What it does |
| --- | --- |
| `POST /auth/register` `{email, password}` | Creates an account (password: 12-128 characters) |
| `POST /auth/login` `{email, password}` | Returns `{access_token, token_type, user}`; the token is valid for 30 minutes |
| `GET /auth/me` | The signed-in user |
| `GET /health` / `GET /ready` | Liveness / readiness (the session database answers and the LLM key is set) |
| `POST /chat` `{session_id?, message}` | One conversation turn (the only call that uses the LLM). Leave out `session_id` to start a new session; the reply carries the id the server created |
| `GET /sessions` | The user's sessions, most recently changed first: `{id, title, mode, generated, updated_at}`. The title is the workflow's name, or the start of the first message |
| `GET /sessions/{id}` | The session, with its full `messages` history |
| `POST /sessions/{id}/new` | Starts a new workflow, no LLM: a generated one moves to `previous_workflows`; the draft and chat are cleared |
| `POST /sessions/{id}/run` | Runs the generated workflow (below) |
| `DELETE /sessions/{id}` | Deletes the session |

Session ids are 1-64 letters, digits, `-` or `_`. Request bodies are limited to 1 MB (413). If two requests change
the same session at once, the second gets 409 and nothing is lost. Every response has an `X-Request-ID` header
(the caller's, or a new one), which is also on every log line and in the body of a 500.

`POST /chat` (and `GET /sessions/{id}`) returns:

- `reply`: the bot message as one Markdown string (what the React UI shows), and `parts`, the same
  message in pieces for a custom UI: `saved` (what this turn stored, e.g. `Time: 6:00 PM`), `lead`, `plan`
  (rows of `{role: When | If | Then, label, branch: yes | no | null}`), `context` (`Step 1 of 4 · …`),
  `note` (why the last answer was rejected), `text` (the question or main sentence) and `more`. Every
  assistant message in `messages` carries its `parts` too.
- `question`: `{target, text, options, allow_custom, step, note}`. `note` is kept out of `text`; when an
  answer was rejected, what it may have meant comes first in `options` ("evening" → 5:00 / 6:00 / 7:00 PM).
- `name` and `steps`: the draft as `{id, kind, app, operation, title, params}`, each param
  `{key, name, label, kind, value, display, status: filled | missing | ambiguous, note, required, choices}`.
  `display` is the value as shown to people (`18:00` → `6:00 PM`); `fields` rows have it too.
- `workflow` JSON and a Mermaid `diagram` once generated, and `previous_workflows` (oldest first), each with
  `reason`: `edited` (an older version of the current workflow) or `new_workflow` (replaced by a new one).

Browsers may call the API from the origins in `CORS_ORIGINS` (comma-separated; default
`http://localhost:5173,http://127.0.0.1:5173`).

`POST /sessions/{id}/run` with `{"input": {...}}` runs the generated workflow and returns each step's
status (`ok`, `failed`, `skipped`), output and error. Secrets such as `SLACK_WEBHOOK_URL` come from the
backend's environment, never from the chat. HTTP Request steps may only call public http(s) addresses: localhost,
private networks and the cloud metadata service are refused (also when a public name resolves to one), and
redirects are not followed. Step errors are shown without secrets or personal data.

## Privacy

- Identity numbers (Aadhaar- and PAN-like) are removed from what is sent to the LLM. Emails and phone numbers
  are sent, because a workflow may need them ("email me at …").
- The per-turn log replaces emails, phone numbers and identity numbers with `[EMAIL]`, `[PHONE]`, ….
- Passwords are stored as Argon2 hashes.

## Project structure

```
workflow-builder/
├── app/
│   ├── main.py                       # FastAPI app: the endpoints above, auth, CORS, request ids, limits
│   ├── core/
│   │   ├── config.py                 # settings from .env: Groq key, model, fallback model, CORS, log level
│   │   ├── logging.py                # log format with the request id
│   │   ├── turnlog.py                # one JSON log line per turn: proposed / kept / rejected / ignored
│   │   └── llm/
│   │       ├── client.py             # Groq via LangChain: retries, rate-limit fallback model
│   │       └── prompts/planner.txt   # the one prompt: plan the workflow + next question
│   ├── pipeline/                     # one conversation turn
│   │   ├── graph.py                  # LangGraph wiring: ingest → plan → merge → check → ask / generate
│   │   ├── planner.py                # the only LLM call per turn
│   │   ├── planner_schema.py         # what the LLM must return (TurnPlan)
│   │   ├── merge.py                  # code decides: keep only grounded, valid values
│   │   ├── normalize.py              # parsing: times, timezones, channels, fields, operators
│   │   ├── validation.py             # per-kind validation, readiness, condition checks
│   │   ├── questions.py              # the next multiple-choice question
│   │   ├── generation/
│   │   │   ├── builder.py            # workflow JSON from state (no LLM)
│   │   │   ├── checks.py             # schema, graph and grounding checks on the output
│   │   │   └── schema.py             # Workflow / node / edge models + JSON schema
│   │   └── diagram/mermaid.py        # Mermaid flowchart from the workflow JSON
│   ├── runtime/executor.py           # runs a workflow: Manual/Webhook, If, HTTP Request, Slack
│   ├── security/
│   │   ├── auth.py                   # accounts (Argon2), JWT access tokens, the signed-in user
│   │   ├── pii.py                    # detects and redacts emails, phone and identity numbers
│   │   └── runtime_guardrails.py     # which URLs a run may call; clean error text
│   └── state/
│       ├── models.py                 # WorkflowState, Step, Param
│       ├── machine.py                # modes: collecting → ready → post_generation
│       └── store.py                  # sessions in SQLite: one row per session, owner, version
├── frontend/react-app/               # React chat UI; talks to the backend over HTTP
├── tests/                            # pytest suite; the LLM is scripted, no API key needed
│   ├── test_api.py                   # the HTTP API: steps, reply parts, new workflow, CORS
│   ├── test_api_security.py          # sign-in, owner-only sessions, limits, request ids, errors
│   ├── test_auth.py                  # register, login, users cannot reach each other's sessions
│   ├── test_clarification.py         # ambiguity, edits, new requests, rate limits
│   ├── test_conditions.py            # if / otherwise branches, field vs value
│   ├── test_grounding.py             # whole-word grounding
│   ├── test_llm_client.py            # the Groq client, with the model faked
│   ├── test_runtime.py               # the executor, with HTTP faked; blocked destinations
│   ├── test_store.py                 # sessions survive a restart; owners, versions, upgrades
│   └── test_turnlog.py               # the per-turn log line
├── requirements.txt                  # runtime dependencies
├── requirements-dev.txt              # + test tools
├── pytest.ini
├── .env.example                      # copy to .env and fill in your key
└── README.md
```

## How one turn works

```
user message (or picked option)
   → plan      (AI: whole workflow + next question)
   → merge     (code: keep only what the user said, validate it)
   → check     (code: every app and required parameter known?)
        ├─ no  → ask       → multiple-choice question
        └─ yes → generate  → workflow JSON → diagram
```

See the docstring at the top of [app/pipeline/graph.py](app/pipeline/graph.py) for the full graph.

## Logs

Every `/chat` turn writes one JSON line to stderr: what the model proposed, what code kept, rejected or
ignored and why, the question asked next, and the turn's latency. See
[app/core/turnlog.py](app/core/turnlog.py) for an example line.

```
{"event": "turn", "turn": 1, "message_kind": "build", "proposed": [...],
 "kept": [{"field": "post.channel", "value": "#dev"}],
 "rejected": [{"field": "post.time", "reason": "\"evening\" is not an exact time ..."}],
 "ignored": [{"field": "issue_trigger.app", "reason": "\"Git\" was not named by the user"}], ...}
```

## Running locally

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements-dev.txt

copy .env.example .env            # then set GROQ_API_KEY and AUTH_SECRET_KEY

# Terminal 1: backend
uvicorn app.main:app --port 8001 --reload

# Terminal 2: frontend (Node.js 20+)
cd frontend/react-app
npm install
npm run dev                       # http://127.0.0.1:5173
```

The React app calls `VITE_API_URL` (default `http://127.0.0.1:8001`; see `frontend/react-app/.env.example`).
Create an account on its sign-in screen.

## Running in production

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8001 --workers 4 --proxy-headers

cd frontend/react-app && npm ci && VITE_API_URL=https://api.example.com npm run build   # serve dist/
```

- Set `AUTH_SECRET_KEY`, `GROQ_API_KEY` and `CORS_ORIGINS` (the React app's real origin) in the environment.
  The server does not start without a strong `AUTH_SECRET_KEY`.
- Put it behind a reverse proxy that terminates HTTPS and rate-limits `/auth/login` and `/chat`.
- Point the platform's health checks at `/health` (liveness) and `/ready` (readiness).
- Keep `SESSION_DB` and `AUTH_DB` on a persistent local disk (not a network share) and back them up.

## Tests

```bash
pytest
```

The LLM's replies are scripted in the tests, so no API key or network is needed.

## Limits

- **It runs only four step types.** The Run button executes Manual or Webhook triggers, If, HTTP
  Request and Slack incoming-webhook messages; a workflow with any other step is designed but not run.
  The output is not an importable n8n file, and app/operation names are whatever the planner chose.
- **A run is one request, started from the UI.** The trigger's data is JSON typed into the UI; there is
  no public webhook URL, schedule or retry.
- **No app catalogue.** Any app name the user gives is accepted; nothing checks that the app or
  operation exists or that a parameter is the one that app really needs.
- **Grounding is word matching.** A value is kept if its words appear in what the user wrote, so it
  catches invented values but not every misreading of the user's words.
- **One condition shape.** Conditions compare one field with one value; there is no AND/OR and
  no loops.
- **Sessions and accounts are SQLite files** (`SESSION_DB`, `AUTH_DB`), fine for one server; several servers
  need a shared database. Old sessions do not expire, and there is no password reset or logout-everywhere.
- **No built-in rate limit.** Limit `/auth/login` and `/chat` at the reverse proxy.
- **The URL check happens before the request.** A hostname whose DNS answer changes between the check and the
  request (DNS rebinding) could still reach a private address; block outbound traffic to private ranges at the
  network level for full protection.
- **The planner can still be wrong.** It decides which steps exist and which question comes next;
  code rejects unbacked values but cannot fix a missing or extra step.
