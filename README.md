# botcircuits-mcp

MCP server for the [BotCircuits](https://botcircuits.com) platform.

Connects Claude Code (or any MCP-capable AI assistant) to BotCircuits so you can
build and manage agents, sub-agents, and workflows through natural language.

---

## Quick start

### 1. Install

> **Note:** `botcircuits-mcp` is not yet published to PyPI. Install from GitHub.

**From GitHub (recommended):**

```bash
git clone https://github.com/botcircuits-ai/botcircuits-mcp.git
cd bc-mcp-server
pip install -e .
```

**Or install directly via pip + git:**

```bash
pip install git+https://github.com/botcircuits-ai/botcircuits-mcp.git
```

### 2. Get your BotCircuits access token

BotCircuits dashboard → **Account → Access Keys** → copy the token.

https://platform.botcircuits.com/account/access-keys

### 3. Add to your AI tool

**Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "botcircuits": {
      "command": "botcircuits-mcp",
      "env": {
        "BOTCIRCUITS_ACCESS_TOKEN": "<your-token>"
      }
    }
  }
}
```

**Claude Code CLI:**

```bash
claude mcp add botcircuits -e BOTCIRCUITS_ACCESS_TOKEN=<your-token> -- botcircuits-mcp
```

Restart your AI agent tool.

### 4. Build with plain English

Tell your AI assistant. If you don't name an app, it asks whether to create a new one or use an existing one
(new apps are always prompt-based agents; creating one needs an account-level access key):

```
In app <appId>: customers should be able to check an order's status. The order API is
GET https://api.example.com/orders/{order_id} with header X-Api-Key (I'll give you the key).
Also handle returns: ask for the order number and the reason, check eligibility with the
same API, then either confirm the return or hand off to a human.
```

The assistant follows the server's construct policy: the order lookup becomes an API tool
(`create_api_tool`), the returns process becomes a playbook (`create_playbook`) that calls the
same codehook, and the global instructions are updated to say when to use each.

### 5. Iterate

```
Add a live agent handoff if the customer is unhappy.
Ask for the email address before confirming the return.
```

The assistant reads the playbook (`get_playbook`), edits it and saves it (`update_playbook`).

---

## How it works
```
You → Claude Code → "customers should be able to return an order"
                          │
                    Claude Code receives the construct policy and the playbook
                    format automatically via the MCP initialize handshake
                          │
                    get_application_overview → recommend_implementation
                          │
                    create_api_tool (fetch)   create_playbook (multi-step process)
                          │
                    MCP server validates, compiles (console-compatible),
                    saves through the BotCircuits REST API
```

### Guidance delivery — zero setup required

| Mechanism | When | Contains |
|---|---|---|
| `server.instructions` | Every connection (MCP `initialize`) | Build flow, construct policy, playbook design method + checklist, playbook format |
| `get_authoring_guide(topic)` tool | On demand, any client | `capabilities`, `playbook_design`, `playbook`, `playbook_examples`, `workflow` |
| `botcircuits://…` resources | On demand, clients that read resources | Same five guides |
| `design_playbook` prompt | User-invoked (e.g. a slash command) | Step-by-step: overview → inputs → resources → variables → steps → validate → confirm → save |

### Playbooks that work, not just playbooks that save

- **Design method** in the instructions: how a playbook actually runs (steps continue through
  messages until an ASK pauses; answered ASKs are skipped; failed calls leave variables empty),
  a 9-step procedure, patterns (confirm before side effects, retry, handoff, numeric flags) and a
  pre-save checklist.
- **Worked examples** (`botcircuits/playbook_examples.py`) are data: tests validate and save every
  one, so what the AI copies is known to work.
- **`validate_playbook`** returns errors, design warnings and an `outline` of the conversation
  (⏸ where it waits). Design warnings: a path that ends silently, steps after a HANDOFF, an IF
  with no ELSE at the end, a retry loop that never waits for the customer, too many messages
  before a question. Button payloads are checked against `values` variables.

---

## What to build: the construct policy

Delivered to the host AI on connect and enforced by the save tools:

| Requirement | Construct | Tools |
|---|---|---|
| Fetch / single action (weather for a city, order lookup, create ticket) | **Agent tool** | `create_api_tool`, `deploy_codehook` + `create_agent_tool`, kb/json tools, `create_mcp_server` |
| Tone, scope, rules | **Instructions** | `update_agent_instructions` |
| Know-how applied flexibly with existing tools | **Skill** | `create_skill` |
| Ordered multi-step process, questions across turns, branching | **Playbook** (default) | `create_playbook` |
| More than **30 steps**, more than **20 conditional nodes**, or a step playbooks can't express (image, language selector, OAuth, integration, custom action, pause, AI task) | **Canvas workflow** | `upload_workflow` |

`upload_workflow` rejects a *new* workflow below those thresholds and points to `create_playbook`.
Playbooks and workflows are validated against the runtime's rules before anything is saved.

## Tool inventory (49)

| Group | Tools |
|---|---|
| Applications (10) | `list_applications`, `get_application`, `get_application_overview`, `get_application_core_settings`, `update_application_core_settings`, `get_agent_instructions`, `update_agent_instructions`, `create_application`, `delete_application`\*, `publish_application`\* |
| Design (2) | `recommend_implementation`, `get_authoring_guide` |
| Playbooks (6) | `list_playbooks`, `get_playbook`, `validate_playbook`, `create_playbook`, `update_playbook`, `delete_playbook` |
| Workflows (5) | `list_workflows`, `get_workflow`, `validate_workflow_definition`, `upload_workflow`, `delete_workflow` |
| Agent tools (5) | `list_agent_tools`, `get_agent_tool`, `create_agent_tool`, `update_agent_tool`, `delete_agent_tool` |
| Codehooks / API tools (4) | `list_codehooks`, `create_api_tool`, `deploy_codehook`, `delete_codehook` |
| Sub-agents (5) | `list_sub_agents`, `get_sub_agent`, `create_sub_agent`, `update_sub_agent`, `delete_sub_agent` |
| Skills (5) | `list_skills`, `get_skill`, `create_skill`, `update_skill`, `delete_skill` |
| MCP servers (4) | `list_mcp_servers`, `create_mcp_server`, `update_mcp_server`, `delete_mcp_server` |
| Knowledge (3) | `list_knowledge_sources`, `add_text_knowledge`, `add_web_knowledge` |

\* Still disabled through MCP — use the console. Delete tools carry the MCP `destructiveHint` annotation.

Resources: `botcircuits://capability-guide`, `botcircuits://playbook-design`, `botcircuits://playbook-schema`,
`botcircuits://playbook-examples`, `botcircuits://workflow-schema`
(also available through `get_authoring_guide` for clients that don't read resources).

### Changes from 0.1

| Before | Now | Why |
|---|---|---|
| `list_skills` … `delete_skill` on `/prompt-config/tools` | `*_agent_tool` | The console renamed these to **Tools**; `*_skill` now manages real **Skills** (`/prompt-config/skills`) |
| `save_workflow_stm`, `save_workflow_slots`, `convert_intermediate_to_platform` | removed | Bypassed validation and the construct policy; `upload_workflow` / `validate_workflow_definition` cover them |
| `update_application_core_settings` sent `authConfig: {}` | merges with stored settings | It wiped the stored auth config |
| `upload_workflow` | validates, enforces the policy, waits for journey provisioning | It could save broken definitions and fail with "journey not found" |
| Canvas edges used block ids for choice handles | state ids | Branches broke when the workflow was re-saved in the console |
| Id remapping rewrote any string equal to a state id | only edge fields | A button payload like `billing` became a numeric id |
| Cards slot `bc_workflow_option` | `workflow_option` | The runtime slot name (legacy value is rewritten) |
| `delete_workflow` deleted only the journey | deletes the action, journey and agent tools | Left orphan records and tools |

## Project structure

```
bc-mcp-server/
├── pyproject.toml
├── botcircuits/
│   ├── config.py            Pydantic settings (BOTCIRCUITS_* env vars)
│   ├── client.py            Async httpx wrapper over the BotCircuits REST API
│   ├── guides.py            Capability policy, playbook and workflow schemas for the host AI
│   ├── server.py            FastMCP entrypoint: instructions, resources, get_authoring_guide
│   ├── workflow/            Pure logic (no I/O)
│   │   ├── playbook.py      Port of the console playbook compiler
│   │   ├── transform.py     Intermediate workflow JSON -> platform format + canvas layout
│   │   ├── validator.py     stmDefinition checks (runtime silent-failure rules)
│   │   ├── complexity.py    Construct policy (30 steps / 20 conditional nodes)
│   │   └── codegen.py       HTTP-API codehook generator
│   └── tools/               MCP tools, one module per resource
└── tests/                   Fake BotCircuits API + tool tests
```

The `workflow/` modules are shared with `botcircuits-agent-builder-copilot`; keep them in sync.


## Setup (development)

```bash
cd bc-mcp-server
cp .env.example .env
# Add BOTCIRCUITS_ACCESS_TOKEN

pip install -e .
# Entry point: botcircuits-mcp
```

---

## Configuration

| Variable | Default | Description |
|---|---|---|
| `BOTCIRCUITS_API_BASE_URL` | `https://api.botcircuits.com` | Use `https://dev-api.botcircuits.com` for dev |
| `BOTCIRCUITS_ACCESS_TOKEN` | — | BotCircuits dashboard → Settings → Access Keys |
| `BOTCIRCUITS_DEFAULT_APP_ID` | — | Optional default agent ID |
---

## Running

```bash
botcircuits-mcp
```

**Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "botcircuits": {
      "command": "botcircuits-mcp",
      "env": {
        "BOTCIRCUITS_ACCESS_TOKEN": "<your-token>"
      }
    }
  }
}
```

**Claude Code CLI:**

```bash
claude mcp add botcircuits -- botcircuits-mcp
```

---

## Authoring reference

The host AI receives the playbook format and the construct policy on connect; the canvas
workflow schema is served on demand. See `botcircuits/guides.py` for the exact text, or read
the resources above.

## Development

```bash
uv pip install -p .venv -e . pytest pytest-asyncio ruff
.venv/bin/pytest            # no network: tools run against tests/fakes.py
.venv/bin/ruff check botcircuits tests
```
