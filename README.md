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
cd botcircuits-mcp
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
create botcircuits agent.

usecase:
Help a customer report a lost or stolen bank card. Ask whether the card is lost or stolen and whether they noticed any unrecognized transactions. Do not ask for the full card number, PIN, password, or security code. Summarize their answers and hand off to a banking specialist. If they report unrecognized transactions, include that concern in the handoff. Do not claim that the card has been blocked or replaced
```

### 5. Iterate

```
Add a live agent handoff if the customer is unhappy.
Ask for the email address before confirming the return.
```


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
| `server.instructions` | Every connection (MCP `initialize`) | Build flow, construct policy, troubleshooting entry point, playbook design method + checklist, playbook format |
| `get_authoring_guide(topic)` tool | On demand, any client | `capabilities`, `playbook_design`, `playbook`, `playbook_examples`, `workflow`, `troubleshooting` |
| `botcircuits://…` resources | On demand, clients that read resources | Same six guides |
| `design_playbook` prompt | User-invoked (e.g. a slash command) | Step-by-step: overview → inputs → resources → variables → steps → validate → confirm → save |

### Playbooks that work

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

### Troubleshooting from runtime traces

When the user reports a misbehaving agent (wrong answer, error, stuck in a playbook, wrong branch,
slow), the host AI reads the runtime traces before changing anything — broad to narrow:
`find_problem_sessions` → `get_session_trace` (per-turn digest + detected `issues`) →
`get_turn_trace` (one turn's span tree: model calls, tools, steps, branch decisions with the values
they were judged on). `get_authoring_guide("troubleshooting")` maps each issue to its likely
configuration fix. Read-only; traces are kept for 30 days.

---

## What to build: the construct policy

Delivered to the host AI on connect and enforced by the save tools:

| Requirement | Construct | Tools |
|---|---|---|
| Fetch / single action (weather for a city, order lookup, create ticket) | **Agent tool** | `create_api_tool`, `deploy_codehook` + `create_agent_tool`, kb/json tools, `create_mcp_server` |
| Tone, scope, rules | **Instructions** | `update_agent_instructions` |
| Know-how applied flexibly with existing tools | **Skill** | `create_skill` |
| Ordered multi-step process, questions across turns, branching | **Playbook** (default) | `create_playbook` |
| More than **30 steps**, more than **20 conditional nodes** | **Canvas workflow** | `upload_workflow` |

`upload_workflow` rejects a *new* workflow below those thresholds and points to `create_playbook`.
Playbooks and workflows are validated against the runtime's rules before anything is saved.

## Tool inventory (53)

| Group | Tools |
|---|---|
| Applications (10) | `list_applications`, `get_application`, `get_application_overview`, `get_application_core_settings`, `update_application_core_settings`, `get_agent_instructions`, `update_agent_instructions`, `create_application`, `delete_application`\*, `publish_application`\* |
| Design (2) | `recommend_implementation`, `get_authoring_guide` |
| Playbooks (6) | `list_playbooks`, `get_playbook`, `validate_playbook`, `create_playbook`, `update_playbook`, `delete_playbook` |
| Workflows (5) | `list_workflows`, `get_workflow`, `validate_workflow_definition`, `upload_workflow`, `delete_workflow` |
| Agent tools (5) | `list_agent_tools`, `get_agent_tool`, `create_agent_tool`, `update_agent_tool`, `delete_agent_tool` |
| Codehooks / API tools (5) | `list_codehooks`, `get_codehook`, `create_api_tool`, `deploy_codehook`, `delete_codehook` |
| Sub-agents (5) | `list_sub_agents`, `get_sub_agent`, `create_sub_agent`, `update_sub_agent`, `delete_sub_agent` |
| Skills (5) | `list_skills`, `get_skill`, `create_skill`, `update_skill`, `delete_skill` |
| MCP servers (4) | `list_mcp_servers`, `create_mcp_server`, `update_mcp_server`, `delete_mcp_server` |
| Knowledge (3) | `list_knowledge_sources`, `add_text_knowledge`, `add_web_knowledge` |
| Troubleshooting (3) | `find_problem_sessions`, `get_session_trace`, `get_turn_trace` (read-only) |

\* Still disabled through MCP — use the console. Delete tools carry the MCP `destructiveHint` annotation.

Resources: `botcircuits://capability-guide`, `botcircuits://playbook-design`, `botcircuits://playbook-schema`,
`botcircuits://playbook-examples`, `botcircuits://workflow-schema`, `botcircuits://troubleshooting`
(also available through `get_authoring_guide` for clients that don't read resources).

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
