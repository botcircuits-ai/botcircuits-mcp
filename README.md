# botcircuits-mcp

MCP server for the [BotCircuits](https://botcircuits.com) platform.

Connects Claude Code (or any MCP-capable AI assistant) to BotCircuits so you can
build and manage agents, sub-agents, and workflows through natural language — with
no extra API key and no LLM inside the server.

---

## How it works

The MCP server is a **pure API wrapper**. It has no LLM inside it.

```
You → Claude Code → "build me a fund transfer workflow"
                          │
                    Claude Code receives the full workflow schema
                    automatically via MCP initialize handshake
                          │
                    Claude Code reasons about requirements,
                    generates the intermediate workflow JSON
                          │
                    calls upload_workflow(app_id, workflow_id, json)
                          │
                    MCP server transforms JSON → platform format
                    MCP server saves to BotCircuits REST API
```

This is the same model as N8N MCP — the AI assistant is the brain, the MCP
server is the hands. No `ANTHROPIC_API_KEY`, no separate LLM billing.

### Schema delivery — zero setup required

The workflow schema (all node types, slot rules, examples) is delivered to the
host AI via two built-in MCP mechanisms — no `CLAUDE.md` or manual setup needed:

| Mechanism | When | How |
|---|---|---|
| `server.instructions` | On every connection, automatically | Sent during the MCP `initialize` handshake — Claude Code receives the full schema as part of its context before the first tool call |
| `botcircuits://workflow-schema` resource | On demand | Fetchable anytime via MCP `resources/read` for a fresh copy |

Any MCP-capable AI tool (Claude Code, Cursor, Copilot, Codex, etc.) receives
the schema automatically just by connecting to this server.

---

## Tool inventory

### Agents (7)
| Tool | Description |
|---|---|
| `list_agents` | List all agents in your account |
| `get_agent` | Get an agent's full configuration |
| `create_agent` | Create a new agent |
| `delete_agent` | Delete an agent |
| `publish_agent` | Publish agent to production |
| `get_agent_core_settings` | Get system prompt, error message, language, KB settings |
| `update_agent_core_settings` | Update system prompt and core LLM settings |

### Sub-Agents (5)
| Operation | Description |
|---|---|
| `list_sub_agents` | List all sub-agents on an agent |
| `get_sub_agent` | Get a sub-agent's full configuration including instructions and capabilities |
| `create_sub_agent` | Create a sub-agent with instructions and capabilities |
| `update_sub_agent` | Update instructions, capabilities, description, or parameters |
| `delete_sub_agent` | Delete a sub-agent |

### Workflows (7)
| Tool | Description |
|---|---|
| `list_workflows` | List all workflows in an agent |
| `get_workflow` | Get a workflow's full definition |
| `save_workflow_stm` | Upload state machine definition + visual metadata |
| `save_workflow_slots` | Upload slot (variable) definitions |
| `delete_workflow` | Delete a workflow |

### Skills (4)
| Tool | Description |
|---|---|
| `list_skills` | List all skills registered on an agent |
| `get_skill` | Get a skill's full configuration |
| `create_skill` | Create a new skill (workflow · function · kb · json) |
| `update_skill` | Update a skill's name, description, type, parameters, or configuration |
| `delete_skill` | Delete a skill |

### Workflow Schema (2)
| Tool | Description |
|---|---|
| `convert_intermediate_to_platform` | Transform intermediate JSON → platform format (no LLM, no API key) |
| `upload_workflow` | Convert + upload STM + slots in one step |

---

## Project structure

```
bc-mcp-server/
├── pyproject.toml
├── .env.example
└── botcircuits/
    ├── config.py          # Pydantic settings (BOTCIRCUITS_* env vars)
    ├── client.py          # Async httpx wrapper over the BotCircuits REST API
    ├── server.py          # FastMCP entrypoint — schema delivered via instructions + resource
    └── tools/
        ├── applications.py     # Agent management tools
        ├── sub_agents.py       # Sub-agent management tools
        ├── workflows.py        # Workflow CRUD tools
        ├── skills.py           # Skill management tools
        └── workflow_schema.py  # convert_intermediate_to_platform + upload_workflow
```

---

## Quick start

### 1. Install

```bash
pip install botcircuits-mcp
```

### 2. Get your BotCircuits access token

BotCircuits dashboard → **Settings → Access Keys** → copy the token.

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

Restart your AI tool. The BotCircuits workflow schema is delivered automatically
on connection — no further setup needed.

### 4. Create your first agent and workflow

Tell your AI assistant (in plain English):

```
Create a BotCircuits agent called "Support Bot" with the description
"You are a helpful customer support assistant."
```

The assistant calls `create_agent` and `update_agent_core_settings` for you.

```
Create a workflow called "Support Triage" that:
- Greets the user
- Asks whether they have a billing or technical issue (buttons)
- For billing: collects their order ID (format ORD-XXXXXX) then calls
  https://api.example.com/billing/{order_id} to look up the status
- For technical: searches the knowledge base and answers
- Ends with a satisfaction rating from 1 to 5
```

The assistant generates the intermediate workflow JSON and calls:
1. `upload_workflow` — transforms + saves the state machine and slots

```
Publish the agent.
```

The assistant calls `publish_agent`. Your bot is live.

### 5. Iterate

Continue the conversation to refine:

```
Add a live agent handoff if the satisfaction rating is 1 or 2.
Change the greeting to mention we're available 24/7.
Add a webhook after the billing lookup to log the result.
```

Each instruction updates the existing workflow in place.

---

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

No `ANTHROPIC_API_KEY`. No other LLM key. The AI assistant you're already using
(Claude Code, Cursor, etc.) does all the reasoning.

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

## Workflow creation flow

```
1. create_agent          name="Support Bot"

2. update_agent_core_settings
                         app_id=...
                         agent_description="You are a helpful support assistant."

3. upload_workflow       app_id=...  workflow_id="fund_transfer"
                         intermediate={ <Claude Code generates this JSON> }

4. publish_agent         app_id=...
```

At step 4, Claude Code already has the full schema in context (delivered
automatically at connection time) and generates the intermediate JSON itself —
no pipeline, no extra LLM call, no API key.

---

## Workflow schema — quick reference

The full schema is embedded in the server and delivered automatically on connection.
Below is a quick reference — see `server.py:_WORKFLOW_SCHEMA` for the complete version.

### Intermediate format structure

```json
{
  "stmDefinition": {
    "startAt": "start",
    "states": {
      "start":     { "type": "StartNode",      "next": "greet",     "stateConfig": { ... } },
      "greet":     { "type": "messagePrompt",  "next": "ask_issue", "stateConfig": { ... } },
      "ask_issue": { "type": "buttonsPrompt",  "next": "end",       "stateConfig": { ... } },
      "end":       { "type": "codehookAction", "next": null,        "stateConfig": { "stateType": "action", "type": "end", "name": "End" } }
    }
  },
  "slots": {
    "issue_type": {
      "slot": "issue_type", "displayText": "Issue Type",
      "captureFromUserInput": true, "dataType": "values",
      "content": "billing,technical,general", "dependencies": []
    }
  }
}
```

### Slot `dataType` reference

| `dataType` | `content` | Example |
|---|---|---|
| `custom` | AI extraction description | `"The customer's full legal name"` |
| `regex` | Regex pattern | `"^ORD-\\d{6}$"` |
| `values` | Comma-separated allowed values | `"billing,technical,general"` |
| `number` | Optional min-max hint | `"1-5"` |
| `boolean` | *(empty)* | `""` |
| `email` | *(empty)* | `""` |
| `date` | Optional format hint | `"YYYY-MM-DD"` |
| `any` | *(empty)* | `""` |

### Supported node types

| `type` | `stateType` | Purpose |
|---|---|---|
| `StartNode` | `start` | First state — required |
| `messagePrompt` | `message` | Send text, no reply |
| `questionPrompt` | `prompt` | Free-text question, saves to slot |
| `buttonsPrompt` | `prompt` | Multiple-choice buttons |
| `cardsPrompt` | `prompt` | Rich card carousel |
| `webhookAction` | `action` | Call external HTTP API |
| `codehookAction` | `action` | Call AWS Lambda |
| `aiTask` | `action` | Bounded LLM call, result → slot |
| `docSearchAction` | `action` | RAG knowledge base query |
| `journeyAction` | `action` | Execute nested workflow |
| `liveAgentAction` | `action` | Human handoff (terminal) |
| `pauseAction` | `action` | Pause until external event |
| *(any action)* with `"type": "end"` | `action` | Terminate workflow |
| `choice` | `choice` | Conditional branch |
