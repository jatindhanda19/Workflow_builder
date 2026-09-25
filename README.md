A conversational workflow builder. The user describes an automation in plain
language; the backend asks only the questions it still needs, validates every
answer in code, and produces a deterministic workflow JSON plus a flow diagram.

- **Backend:** FastAPI + LangGraph (`app/`)
- **Frontend:** Streamlit chat UI (`frontend/`)
- **LLM:** Groq or OpenAI via LangChain. The LLM is used only for classifying
  intent, extracting fields and rewording questions. Readiness, planning,
  generation and diagrams are handled in code.

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
├── requirements.txt
├── .env.example                      # Copy to .env and fill in keys
└── README.md
```

## How one turn works

```
user message
   → intent       (what is the goal?)
   → extraction   (which field values were given?)
   → state        (merge values, detect conflicts)
   → planning     (which nodes and fields are required?)
   → validation   (is everything filled and valid?)
        ├─ no  → questions  → ask the next question
        └─ yes → generation → workflow JSON → diagram
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

