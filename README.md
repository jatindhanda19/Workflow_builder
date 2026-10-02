# Conversational Workflow Builder

Describe an automation in plain words ("when an invoice arrives, if the amount is above ₹1,00,000
notify the manager, otherwise save it"). The builder asks only what it still needs, as
**multiple-choice questions**, and produces a workflow JSON, a flow diagram, and, for a few step
types, a real run.

**The core idea: the LLM proposes, code decides.** One LLM call per turn proposes the whole workflow
and the next question. Code then keeps a value only if the user's own words back it and it validates,
never lets the model drift an earlier answer, checks the workflow's shape, and builds the output with
no LLM. Every turn logs what the model proposed and what code kept, rejected or ignored, and why.

- **Backend:** FastAPI + LangGraph (`app/`)
- **Frontend:** Streamlit chat UI with clickable answer options (`frontend/`)
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

`POST /chat` returns `question: {target, text, options, allow_custom}`, the
collected `fields`, and, once generated, the `workflow` JSON and a Mermaid `diagram`.

`POST /sessions/{id}/run` with `{"input": {...}}` runs the generated workflow and returns each step's
status (`ok`, `failed`, `skipped`), output and error. Secrets such as `SLACK_WEBHOOK_URL` come from the
backend's environment, never from the chat.

## Project structure

```
workflow-builder/
├── app/
│   ├── main.py                       # FastAPI app: /chat, /sessions/{id}, /sessions/{id}/run, /health
│   ├── core/
│   │   ├── config.py                 # settings from .env: Groq key, model, fallback model
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
│   └── state/
│       ├── models.py                 # WorkflowState, Step, Param
│       ├── machine.py                # modes: collecting → ready → post_generation
│       └── store.py                  # sessions in SQLite (one row per session)
├── frontend/streamlit_app.py         # chat UI; talks to the backend over HTTP
├── tests/                            # pytest suite; the LLM is scripted, no API key needed
│   ├── test_clarification.py         # ambiguity, edits, new requests, rate limits
│   ├── test_conditions.py            # if / otherwise branches, field vs value
│   ├── test_grounding.py             # whole-word grounding
│   ├── test_llm_client.py            # the Groq client, with the model faked
│   ├── test_runtime.py               # the executor, with HTTP faked
│   ├── test_store.py                 # sessions survive a restart
│   └── test_turnlog.py               # the per-turn log line
├── requirements.txt
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
pip install -r requirements.txt

copy .env.example .env            # then set GROQ_API_KEY

# Terminal 1: backend
uvicorn app.main:app --port 8001 --reload

# Terminal 2: frontend
streamlit run frontend/streamlit_app.py
```

The frontend reads `API_URL` from the environment (default `http://127.0.0.1:8001`).

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
- **Sessions are one SQLite file** (`SESSION_DB`, default `sessions.db`); no authentication, and no
  expiry of old sessions.
- **The planner can still be wrong.** It decides which steps exist and which question comes next;
  code rejects unbacked values but cannot fix a missing or extra step.
