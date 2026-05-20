import json
import logging

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 6

SYSTEM_PROMPT = """You are a healthcare data analyst assistant with secure, read-only access to a \
de-identified patient and encounter database backed by Elasticsearch.

═══ CRITICAL RULES ═══
1. ALWAYS call a tool to answer any data question — never answer from memory, training data, \
or prior messages in this conversation. Every response about patients, encounters, or diagnoses \
must be backed by a live tool call made in this turn.
2. If similar data appeared earlier in the conversation, ignore it and re-fetch via a tool call \
so the answer reflects the current state of the database.
3. All data returned by tools is already de-identified — PHI (names, DOB, SSN, phone, email) \
has been redacted at the MCP boundary before you see it. Never ask for or attempt to reconstruct \
raw identifiers.
4. Bulk retrieval of de-identified records is ALWAYS permitted. Never refuse or truncate a \
request citing privacy — redaction is enforced upstream, not by you. When a user asks for all \
records, pass a high limit (e.g. limit=500) to the tool.

═══ TOOL SELECTION GUIDE ═══

Document / record lookup (returns de-identified records):
  search_patients              → find patients by name keyword or MRN
  get_patient                  → retrieve one patient record by exact MRN (e.g. MRN-000042)
  search_encounters            → find encounters by patient MRN and/or note keyword
  get_encounter_note           → read the anonymized clinical note for one encounter
  search_encounters_by_diagnosis     → encounters with a specific ICD code or diagnosis keyword
  search_encounters_by_date_range    → encounters within a start/end date window (YYYY-MM-DD)
  search_patients_by_demographics    → filter patients by state, city, age range, or insurance ID
  count_records                → ALWAYS use for "how many" questions; accepts full demographic \
and clinical filters (state, city, min_age, max_age, diagnosis_code, provider_name, date range). \
NEVER use search tools to answer count questions — search tools have a limit and will under-count.

Analytics / aggregations (returns bucket counts — never raw PHI):
  aggregate_diagnoses               → top ICD codes by encounter frequency; filter by code prefix
  aggregate_encounters_by_date      → encounter volume over time (day/week/month/quarter/year)
  aggregate_patients_by_age         → population distribution by 10-year age bucket
  aggregate_patients_by_location    → patient count by state, city, or zip
  aggregate_encounters_by_provider  → encounter count per provider
  aggregate_diagnoses_by_cohort     → top diagnoses for a cohort filtered by state/city/age
  aggregate_encounters_per_patient  → patients ranked by encounter volume
  run_aggregate                     → flexible catch-all: construct any ES 'aggs' JSON for \
analytics not covered by the tools above

═══ RESPONSE STYLE ═══
- Lead with the key finding, then supporting detail.
- For aggregation results, present as a ranked list or concise table.
- If a query requires multiple tool calls (e.g. look up IDs then aggregate), chain them within \
this turn.
- If no records are found, say so clearly and suggest a refined query.
- Keep responses concise; avoid restating the question."""


def _mcp_tools_to_openai(mcp_tools) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": t.inputSchema,
            },
        }
        for t in mcp_tools.tools
    ]


def _clean_history(history: list[dict]) -> list[dict]:
    """Return only user and text-only assistant messages, stripping tool call/result pairs.

    This prevents the model from serving stale tool results cached in prior turns.
    Tool call scaffolding (role='tool', assistant entries with tool_calls) is excluded;
    the model must re-fetch any data it needs via a live tool call this turn.
    """
    clean = []
    for msg in history:
        if msg["role"] == "tool":
            continue
        if msg["role"] == "assistant" and msg.get("tool_calls"):
            # Keep the text portion only if there is one
            text = msg.get("content", "").strip()
            if text:
                clean.append({"role": "assistant", "content": text})
            continue
        clean.append(msg)
    return clean


async def run_turn(
    mcp_session,
    mcp_tools,
    history: list[dict],
    api_key: str,
    model: str,
) -> tuple[str, list[dict]]:
    """Run the tool-use loop for one user turn.

    history is modified in-place with the full turn (tool calls + results).
    Returns (final_assistant_text, tool_call_records).
    """
    client = AsyncOpenAI(api_key=api_key)
    tools = _mcp_tools_to_openai(mcp_tools)
    tool_records: list[dict] = []

    # Build the messages list for this turn:
    # system prompt + de-cached history (no old tool results) + current user message
    # The current user message is already the last entry in history (appended by the caller).
    prior = _clean_history(history[:-1])
    current_user = history[-1]
    working_messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *prior,
        current_user,
    ]

    for _ in range(MAX_ITERATIONS):
        response = await client.chat.completions.create(
            model=model,
            tools=tools,
            messages=working_messages,
        )

        choice = response.choices[0]
        msg = choice.message

        assistant_entry: dict = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            assistant_entry["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ]
        working_messages.append(assistant_entry)
        history.append(assistant_entry)

        if choice.finish_reason == "stop":
            return msg.content or "", tool_records

        if choice.finish_reason == "tool_calls" and msg.tool_calls:
            for tc in msg.tool_calls:
                tool_name = tc.function.name
                try:
                    tool_args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    tool_args = {}
                try:
                    mcp_result = await mcp_session.call_tool(tool_name, tool_args)
                    result_text = "\n".join(
                        item.text for item in mcp_result.content if hasattr(item, "text")
                    )
                except Exception as exc:
                    result_text = f"Tool error: {exc}"
                    logger.exception("MCP tool call failed: %s", tool_name)

                tool_records.append({
                    "name": tool_name,
                    "args": tool_args,
                    "result": result_text,
                })
                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_text,
                }
                working_messages.append(tool_msg)
                history.append(tool_msg)

    return "I reached the maximum reasoning steps. Please try a more specific question.", tool_records
