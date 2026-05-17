# ES-MCP-TEST

Healthcare-domain proof-of-concept: an MCP server that mediates access to
ElasticSearch and redacts PHI/PII before any data reaches an LLM agent.

## Stack

| Layer | Technology | Port |
|-------|-----------|------|
| Data store | ElasticSearch (local) | 9200 |
| PHI redaction + tools | MCP server (Python, FastMCP, stdio) | — |
| Chat UI + tool-use loop | FastAPI + HTMX | 8000 |
| LLM | OpenAI API (`gpt-4o` by default) | — |

## Quick start

```bash
# 1. Install dependencies
uv sync

# 2. Download Presidio NLP model (~560 MB, required for note anonymisation)
uv run python -m spacy download en_core_web_lg

# 3. Start ElasticSearch (must be running on port 9200)
sudo systemctl start elasticsearch
curl localhost:9200/_cluster/health   # verify

# 4. Seed synthetic data (500 patients, ~1500 encounters)
uv run seed-es

# 5. Copy .env.example → .env and set OPENAI_API_KEY

# 6. Start the agent UI (MCP server spawns automatically)
uv run agent-ui        # → http://localhost:8000
```

## Architecture

```
ElasticSearch (port 9200)
       ↓  raw PHI documents
MCP server  (stdio subprocess)
       ↓  PHI redacted — model never sees raw data
FastAPI + HTMX agent UI (port 8000)
       ↓  OpenAI tool-use loop
OpenAI API (gpt-4o)
```

### PHI trust boundary

Every MCP tool calls `Redactor.redact(doc)` on every ES hit before returning.
Nothing downstream (agent UI, OpenAI) ever receives raw PHI.

Redaction pipeline (`src/mcp_phi/redaction/`):

1. **`structured_rules.py`** — deterministic field transforms: drops `ssn`, `first_name`, `last_name`; converts `dob` → 10-year `age_bucket`; masks `phone` (last-4 only), `email` (domain only); flattens `address` → `city` + `state`.
2. **`free_text.py`** — Presidio on `note` fields; replaces PERSON, PHONE_NUMBER, DATE_TIME, LOCATION, US_SSN, EMAIL_ADDRESS, US_DRIVER_LICENSE with `<ENTITY_TYPE>` tokens.

Aggregation tools never return raw documents — only bucket counts — so they are inherently PHI-safe and do not run through the redactor.

## MCP tools

### Document lookup (redacted records)

| Tool | Purpose |
|------|---------|
| `search_patients` | Full-text search on patients by name or MRN |
| `get_patient` | Single patient record by MRN |
| `search_encounters` | Encounters by patient MRN and/or note keyword |
| `get_encounter_note` | Presidio-anonymised clinical note for one encounter |
| `search_encounters_by_diagnosis` | Encounters with a specific ICD code or description keyword |
| `search_encounters_by_date_range` | Encounters within a date window |
| `search_patients_by_demographics` | Patients filtered by state, city, age range, or insurance ID |

### Counting

| Tool | Purpose |
|------|---------|
| `count_records` | Exact count with optional filters: doc_type, state, city, min_age, max_age, diagnosis_code, provider_name, date range. **Use for all "how many" questions.** |

### Aggregations (counts/buckets only — no PHI)

| Tool | Purpose |
|------|---------|
| `aggregate_diagnoses` | Top ICD codes by encounter frequency; optional code-prefix filter |
| `aggregate_encounters_by_date` | Encounter volume over time (day/week/month/quarter/year) |
| `aggregate_patients_by_age` | Patient population by 10-year age bucket |
| `aggregate_patients_by_location` | Patient count by state, city, or zip |
| `aggregate_encounters_by_provider` | Encounter count per provider |
| `aggregate_diagnoses_by_cohort` | Top diagnoses for a cohort filtered by state/city/age |
| `aggregate_encounters_per_patient` | Patients ranked by encounter volume |
| `run_aggregate` | Catch-all: agent passes any ES `aggs` JSON; `top_hits` stripped automatically |

## Agent behaviour

`src/agent_ui/agent.py` — `run_turn()` uses `AsyncOpenAI`. Each turn:

1. Injects a system prompt that instructs the model to always call tools for data questions.
2. Builds context from `[system] + cleaned_history + current_user_message`.
   - "Cleaned history" strips old tool call/result pairs so the model cannot serve stale cached data; it must re-fetch via a live tool call every turn.
3. Loops up to 6 iterations: send → handle tool calls → send results → repeat until `finish_reason == "stop"`.

## ElasticSearch index

Single index: **`healthcare`**, differentiated by `_docType` keyword.

- **`elig`** — patient/eligibility: `patient_id` (MRN), `ssn`, `first_name`, `last_name`, `dob`, `address{}`, `phone`, `email`, `insurance_id`
- **`claims`** — encounters: `encounter_id`, `patient_id` (FK), `provider_name`, `encounter_date`, `diagnoses[]` (nested: `code` keyword + `description` text), `note` (text, english analyser — deliberately contains raw PHI to exercise Presidio)

Seeding is reproducible (`Faker.seed(42)`, `random.seed(42)`): 500 elig + ~1500 claims.

## Configuration

All config via `.env` (copy `.env.example`). Loaded by `pydantic-settings` into `src/mcp_phi/config.py`.

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `OPENAI_API_KEY` | Yes | — | Agent UI refuses to start without it |
| `OPENAI_MODEL` | No | `gpt-4o` | Any OpenAI chat-completions model |
| `ES_URL` | No | `http://localhost:9200` | |
| `ES_INDEX` | No | `healthcare` | |
| `AUDIT_DIR` | No | `./audit` | JSONL audit log, no PHI |
| `LOG_LEVEL` | No | `INFO` | |

## Source layout

```
src/
├── mcp_phi/
│   ├── config.py              # pydantic-settings singleton (all env vars)
│   ├── es_client.py           # Elasticsearch singleton
│   ├── audit.py               # write_event() → audit/YYYY-MM-DD.jsonl
│   └── redaction/
│       ├── structured_rules.py  # deterministic field-level PHI transforms
│       ├── free_text.py         # Presidio NER anonymisation (lazy-loaded)
│       ├── redactor.py          # Redactor.redact() — orchestrates both stages
│       └── policy.py            # allowed_fields(role) stub for future RBAC
├── mcp_server/
│   ├── server.py              # FastMCP entry point
│   └── tools.py               # all 16 MCP tools + shared helpers
├── agent_ui/
│   ├── main.py                # FastAPI app, lifespan, routes
│   ├── agent.py               # run_turn() tool-use loop, SYSTEM_PROMPT
│   ├── mcp_client.py          # get_server_params() — resolves uv path
│   ├── session.py             # InMemorySessionStore (get() only)
│   └── templates/             # base.html, _message.html (HTMX partials)
└── seed/
    ├── generate.py            # Faker-based synthetic data generators
    ├── mappings.py            # ES index mapping definition
    └── load.py                # seed-es entry point
```

## Out of scope (POC)

TLS, real authn/authz, role-based field filtering (interface stubbed in `policy.py`),
persistent audit storage, BAA-grade infrastructure.
