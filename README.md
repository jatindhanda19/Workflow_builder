A conversational workflow builder that works like n8n. The user describes **any**
automation, with **any** app (Shopify, Stripe, GitHub, Jira, Notion, HubSpot, Slack,
Gmail, Google Sheets, webhooks, schedules, …). The builder asks only what it still
needs, always as **multiple-choice questions**, validates every answer in code, and
produces a workflow JSON plus a flow diagram.

- **Backend:** FastAPI + LangGraph (`app/`)
- **Frontend:** Streamlit chat UI with clickable answer options (`frontend/`)
- **LLM:** Groq or OpenAI via LangChain. One call per turn designs the workflow and
  predicts the next question. Grounding, validation, readiness, generation and the
  diagram are handled in code.

## How it works

Every turn the AI planner returns the whole workflow as n8n-style steps (one
trigger, then actions and `If` conditions) together with the parameters each step
needs, plus its prediction of the best next question. Code then decides what is kept:

| Rule (enforced in code) | Example |
| --- | --- |
| An app is set only if the user named it, or their words imply a built-in node | "notify me" → *Which app should notify you?* (Slack / Gmail / Telegram …) |
| A value is stored only if the user said it (its evidence is in their messages) and it validates | an invented spreadsheet name is discarded and asked instead |
| A stored value changes only when the latest message changes it | the AI cannot drift an earlier answer |
| Formats are checked per kind: email, time, timezone, URL, number, channel, choice | "evening" is not a time → asked again with 5:00 / 6:00 / 7:00 PM |
| The workflow is ready only when every app and required parameter is known | |
| The generated JSON must pass schema, graph and grounding checks | one trigger first, every node reachable, no cycles |

Data from earlier steps is referenced n8n-style, e.g. `{{shopify_trigger.customer_email}}`.

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

## Project structure

```
workflow-builder/
├── app/                              # Backend package
│   ├── main.py                       # FastAPI app and HTTP endpoints (entry point)
│   │
│   ├── core/                         # Shared infrastructure
│   │   ├── config.py                 #   settings loaded from .env (provider, model, keys)
│   │   └── llm/                      #   LLM client: strict JSON schemas, retries
│   │       ├── client.py
│   │       └── prompts/              #   prompt templates (.txt)
│   │
│   ├── pipeline/                     # One conversation turn, stage by stage
│   │   ├── graph.py                  #   LangGraph wiring of the stages below
│   │   ├── intent/                   #   1. what is the user's goal?
│   │   │   ├── classifier.py         #      goal-first intent classification
│   │   │   ├── rules.py              #      rule-based fast path
│   │   │   ├── followup.py           #      post-generation: question / edit / new request
│   │   │   └── schema.py
│   │   ├── extraction/               #   2. pull field values out of the message
│   │   │   ├── extract.py
│   │   │   ├── normalize.py          #      email / text / yes-no normalisation
│   │   │   └── schema.py
│   │   ├── planning/                 #   3. which nodes and fields apply
│   │   │   ├── plan.py
│   │   │   └── facts.py
│   │   ├── validation/               #   4. are the answers valid and complete?
│   │   │   ├── fields.py             #      per-field validation
│   │   │   ├── consistency.py        #      cross-field conflict detection
│   │   │   └── readiness.py          #      decides when the workflow is complete
│   │   ├── questions/                #   5. pick and phrase the next question
│   │   │   ├── selector.py
│   │   │   └── ack.py                #      acknowledgement messages
│   │   ├── generation/               #   6. build the workflow JSON (no LLM)
│   │   │   ├── builder.py
│   │   │   ├── checks.py             #      output schema validation
│   │   │   └── schema.py
│   │   └── diagram/                  #   7. flow diagram from the workflow JSON
│   │       ├── builder.py
│   │       └── mermaid.py
│   │
│   ├── registry/                     # Domain data: node types and their fields
│   │   ├── model.py                  #   NodeType / FieldDef definitions
│   │   ├── nodes.py                  #   all node types (add new ones here)
│   │   └── display.py                #   human-readable values and summaries
│   │
│   └── state/                        # Session state across turns
│       ├── models.py                 #   WorkflowState and related models
│       ├── machine.py                #   modes: collecting → ready → post_generation
│       ├── updater.py                #   merges each turn's extraction into state
│       └── store.py                  #   in-memory session store
│
├── frontend/
│   └── streamlit_app.py              # Chat UI; talks to the backend over HTTP
│
├── docs/
│   └── DECISIONS.md                  # Design decisions and how to extend
│
├── tests/                            # pytest suite
│   ├── conftest.py
│   ├── test_units.py
│   ├── test_conversations.py
│   ├── test_generalization.py
│   └── test_demo_regressions.py      # replays of real demo failures
│
├── requirements.txt
├── .env.example                      # Copy to .env and fill in keys
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

See the docstring at the top of [app/pipeline/graph.py](app/pipeline/graph.py) for the full graph, and
[docs/DECISIONS.md](docs/DECISIONS.md) for why it is built this way.

## Running locally

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt

copy .env.example .env            # then fill in your API key

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
