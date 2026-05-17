# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies (creates .venv)
uv sync

# Download Presidio's NLP model (required on first run, ~560 MB)
uv run python -m spacy download en_core_web_lg   # preferred
uv run python -m spacy download en_core_web_sm   # fallback if bandwidth is limited

# ElasticSearch — must be running locally on port 9200 before seeding or starting the agent.
sudo systemctl start elasticsearch
curl localhost:9200/_cluster/health   # verify

# Seed synthetic healthcare data (drops and recreates the index)
uv run seed-es

# Start the agent UI (spawns the MCP server automatically as a subprocess)
uv run agent-ui        # → http://localhost:8000
# OR
./scripts/run_all.sh

# Run the MCP server in isolation (stdio, for debugging)
uv run mcp-server

# Health check
curl localhost:9200/_cluster/health   # ElasticSearch
curl localhost:8000/healthz           # agent UI
```

**Never use `uvicorn --reload`** — it orphans the MCP server subprocess.

**`uv` path quirk (snap environment):** `uv` is not on `$PATH` in spawned subprocesses.
`src/agent_ui/mcp_client.py` resolves it via `shutil.which("uv")` with a snap fallback
(`/home/<user>/snap/code/<rev>/.local/bin/uv`). If you move environments, update that fallback.

## Architecture

Three layers communicate in sequence:

```
ElasticSearch (local, port 9200)
       ↓  raw PHI docs
MCP server (stdio subprocess of the agent UI)
       ↓  PHI redacted — model never sees raw data
FastAPI + HTMX agent UI (port 8000)
       ↓  OpenAI tool-use loop
OpenAI API (gpt-4o by default)
```

### PHI Redaction — the critical invariant

**Every MCP tool that returns documents must call `Redactor.redact(doc)` on every ES hit before returning.**
This is the trust boundary. The agent UI and OpenAI API are downstream and must never receive raw PHI.

Aggregation tools (`aggregate_*`, `run_aggregate`, `count_records`) return only counts/buckets —
no raw document fields — so they are PHI-safe without running through the redactor.

Redaction is a two-stage pipeline in `src/mcp_phi/redaction/`:

1. **`structured_rules.py`** — deterministic field transforms: drops `ssn`, `first_name`, `last_name`; converts `dob` → 10-year `age_bucket`; masks `phone` (last-4 only), `email` (keeps domain); flattens `address` → `city` + `state`.
2. **`free_text.py`** — Presidio `AnalyzerEngine` + `AnonymizerEngine` on `note` fields, replacing PERSON, PHONE_NUMBER, DATE_TIME, LOCATION, US_SSN, EMAIL_ADDRESS, US_DRIVER_LICENSE entities with `<ENTITY_TYPE>` tokens.

Entry point: `Redactor.redact(doc, role=None)` in `redactor.py` — returns `(redacted_doc, stats)`. The `stats` dict is written to `audit/YYYY-MM-DD.jsonl` (no PHI in audit; only document IDs and entity counts).

### Role-based filtering (production hook)

`src/mcp_phi/redaction/policy.py` — `allowed_fields(role)` returns `None` in the POC (all fields pass). To add role enforcement, return a `frozenset[str]` of permitted field names. The `role` parameter is already threaded through `Redactor.redact()` → `apply_structured()`. No other files need changing.

### MCP server

`src/mcp_server/server.py` creates a `FastMCP` instance, then calls `register_tools(mcp)` from `tools.py`. Tools are registered via `@mcp.tool()` decorators inside `register_tools()` — this avoids circular imports. Transport is always `stdio`; `FastAPI` spawns the process via `mcp.client.stdio.stdio_client` during lifespan startup.

**Document lookup tools** (return redacted records): `search_patients`, `get_patient`,
`search_encounters`, `get_encounter_note`, `search_encounters_by_diagnosis`,
`search_encounters_by_date_range`, `search_patients_by_demographics`.

**Count tool**: `count_records` — accepts full demographic + clinical filters
(`state`, `city`, `min_age`, `max_age`, `diagnosis_code`, `provider_name`, date range).
Use this for all "how many" queries; it calls `es.count()` which returns the exact total
with no document limit.

**Aggregation tools** (PHI-safe — counts/buckets only, always `size=0`):
`aggregate_diagnoses`, `aggregate_encounters_by_date`, `aggregate_patients_by_age`,
`aggregate_patients_by_location`, `aggregate_encounters_by_provider`,
`aggregate_diagnoses_by_cohort`, `aggregate_encounters_per_patient`.

**Catch-all**: `run_aggregate` — agent constructs any ES `aggs` JSON as a string;
`top_hits` sub-aggregations are stripped automatically to prevent document-level PHI leakage.

All document tools follow the pattern: ES query → `_redact_hits()` → `audit.write_event()` → return.

**Module-level helpers in `tools.py`** (not tools — internal utilities):

| Helper | Purpose |
|--------|---------|
| `_dob_cutoff(years)` | Returns ISO date cutoff for an age bound, handling Feb-29 |
| `_age_filter(min_age, max_age)` | Builds ES `range` clause on `dob`; returns `None` if both unset |
| `_redact_hits(hits, drop_note)` | Redacts a list of ES source dicts; accumulates stats; replaces the repeated 5-line loop |
| `_age_ranges()` | Builds ES `date_range` bucket list for `aggregate_patients_by_age` |
| `_strip_top_hits(agg_def)` | Recursively removes `top_hits` from agg defs (PHI safety for `run_aggregate`) |

### Agent UI tool-use loop

`src/agent_ui/agent.py` — `run_turn()` is async (uses `openai.AsyncOpenAI`).

**System prompt** (`SYSTEM_PROMPT` constant): injected as the first message on every turn.
Instructs the model to always use tools for data questions and never answer from memory or
prior conversation context.

**History management** (`_clean_history`): before each turn, old tool call/result pairs are
stripped from history so the model cannot serve stale cached data. Only plain user ↔ assistant
text exchanges are preserved for conversational context. Within the current turn's loop,
full tool scaffolding is maintained as required by the OpenAI API.

**Turn loop**: sends `[system, *cleaned_prior, current_user]` → if `finish_reason == "tool_calls"`,
dispatches to MCP via `mcp_session.call_tool()`, appends results, loops up to 6 iterations.
Exits on `finish_reason == "stop"`.

The shared MCP session lives in `app.state.mcp_session` (set during lifespan) and is reused
across all requests. Session conversation history is held in `InMemorySessionStore`
(`src/agent_ui/session.py`) — a plain dict keyed by UUID, one list per browser tab.
Only `get(session_id)` is used; the store auto-creates an empty list on first access.

### Starlette TemplateResponse API

`src/agent_ui/main.py` uses the **new** Starlette `TemplateResponse` kwargs signature:
```python
templates.TemplateResponse(request=request, name="template.html", context={...})
```
Do not revert to the old positional form `(name, {"request": request, ...})` — newer Starlette
treats the first positional arg as `request`, which causes the context dict to be used as the
template name (an unhashable key error).

### Presidio lazy initialization

`free_text._get_engines()` loads Presidio + spaCy on **first tool call**, not at import time.
This is intentional — the MCP server starts fast, and the first `get_encounter_note` call will
be slow (~2–4 s) while the model loads.

## ElasticSearch index

Single index: **`healthcare`**. Document type is differentiated by the `_docType` keyword field.

- **`_docType: "elig"`** — patient/eligibility records. Fields: `patient_id` (MRN, e.g. `MRN-000042`), `ssn`, `first_name`, `last_name`, `dob` (date), `address{}` (`street`, `city` keyword, `state` keyword, `zip` keyword), `phone`, `email`, `insurance_id`
- **`_docType: "claims"`** — encounter/claims records. Fields: `encounter_id` (e.g. `ENC-000042`), `patient_id` (FK to elig), `provider_name` (text + `.keyword`), `encounter_date` (date), `diagnoses[]` (**nested**: `code` keyword + `description` text), `note` (text, english analyser — **deliberately contains patient name, DOB, phone** to exercise Presidio)

All MCP tools filter on `_docType` as the first `must` clause in every query. Seeding is reproducible (`Faker.seed(42)`, `random.seed(42)`): 500 elig + ~1500 claims records.

Note: `diagnoses` is a **nested** type — use nested queries and nested aggregations when filtering or aggregating on `diagnoses.code` / `diagnoses.description`.

## Configuration

All config via `.env` (copy from `.env.example`). Loaded by `pydantic-settings` in `src/mcp_phi/config.py` — `settings` is a module-level singleton imported by all packages.

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `OPENAI_API_KEY` | **Yes** | — | Agent UI refuses to start without a valid key |
| `OPENAI_MODEL` | No | `gpt-4o` | Any OpenAI chat-completions model |
| `ES_URL` | No | `http://localhost:9200` | |
| `ES_INDEX` | No | `healthcare` | |
| `AUDIT_DIR` | No | `./audit` | JSONL audit log; no PHI stored |
| `LOG_LEVEL` | No | `INFO` | |
