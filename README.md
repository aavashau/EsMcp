# ES-MCP-TEST

Healthcare-domain proof-of-concept: an MCP server that mediates access to
ElasticSearch and redacts PHI/PII before any data reaches an LLM agent.

## Stacks

- **ElasticSearch** (Docker, single-node) — data store with synthetic healthcare records
- **MCP server** (Python, FastMCP) — exposes redacted query tools; PHI never leaves this boundary unmasked
- **Agent UI** (FastAPI + HTMX) — chat interface, runs the Anthropic tool-use loop, acts as MCP client

## Status

Initial repo. Implementation plan lives outside the repo (in `~/.claude/plans/`)
pending refinement before scaffolding begins.

## Out of scope (POC)

TLS, real authn/authz, role-based field filtering (interface stubbed only),
persistent audit, BAA-grade infra. These are production add-ons.
