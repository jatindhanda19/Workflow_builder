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
app/
├── main.py                     # FastAPI app and HTTP endpoints (entry point)
├── core/
│   ├── config.py               #   settings from .env (provider, model, keys)
│   └── llm/                    #   LLM client: strict JSON schemas, retries
│       ├── client.py
│       └── prompts/planner.txt #   the planner prompt
├── pipeline/
│   ├── graph.py                # LangGraph wiring of one turn
│   ├── planner.py              # 1. AI: design the workflow, predict the next MCQ
│   ├── planner_schema.py       #    what the planner returns
│   ├── merge.py                # 2. code: ground, validate and keep earlier answers
│   ├── validation.py           # 3. code: per-kind validation and readiness
│   ├── normalize.py            #    times, timezones, lists, channels
│   ├── questions.py            # 4. the MCQ: checked target, validated options
│   ├── generation/             # 5. workflow JSON (no LLM) and its checks
│   └── diagram/mermaid.py      # 6. flow diagram
└── state/                      # session state: models, modes, in-memory store
frontend/
└── streamlit_app.py            # chat UI, option buttons, diagram and JSON
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

### Rate limits

Groq's free tier limits tokens per model per day. When the main model (`LLM_MODEL`,
default `openai/gpt-oss-120b`) hits its limit, the backend switches to
`LLM_FALLBACK_MODEL` (default `openai/gpt-oss-20b`, which has its own quota). It stays
on the fallback until the provider's wait time is over. If every model is limited,
the chat says when to try again and keeps the answers given so far. Set
`LLM_FALLBACK_MODEL=none` to disable the fallback.
