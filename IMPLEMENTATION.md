# botcircuits-mcp — Implementation

---

## 1. Design philosophy

The server is a **thin API wrapper** — no LLM, no pipeline, no extra API key beyond
`BOTCIRCUITS_ACCESS_TOKEN`.

The user is already talking to an intelligent AI assistant (Claude Code, Cursor, Copilot,
etc.). That assistant can reason about requirements, generate structured JSON, and iterate
on feedback. Duplicating that intelligence inside the MCP server would mean requiring a
separate API key, adding latency for an extra LLM round-trip, and building infrastructure
the host already provides for free. The analogy is N8N MCP — the AI is the brain, the
MCP server is the hands.

```
User
 │  "build me a support workflow"
 ▼
Host AI (Claude Code, Cursor, ...)
 │  receives workflow schema via MCP initialize handshake
 │  reasons about requirements
 │  generates intermediate workflow JSON
 │  calls upload_workflow(app_id, workflow_id, json)
 ▼
botcircuits-mcp
 │  transforms intermediate JSON → BotCircuits platform format
 │  PUT  /apps/{appId}/model/journey/{journeyId}/definition  (STM)
 │  POST /apps/{appId}/model/journey/{journeyId}/slots       (slots)
 ▼
BotCircuits REST API
```

---

## 2. Repository layout

```
bc-mcp-server/
├── pyproject.toml
├── .env.example
└── botcircuits/
    ├── config.py               pydantic-settings — reads BOTCIRCUITS_* env vars
    ├── client.py               async httpx wrapper over the BotCircuits REST API
    ├── server.py               FastMCP entrypoint, schema delivery via instructions + resource
    └── tools/
        ├── __init__.py         register_all() — wires all tool modules into the server
        ├── applications.py     7 application (agent) management operations
        ├── sub_agents.py       5 sub-agent operations
        ├── workflows.py        7 workflow CRUD + routing operations
        ├── skills.py           5 skill operations
        └── workflow_schema.py  platform format transform + upload_workflow
```

---

## 3. MCP protocol — how the server connects

The server uses [FastMCP](https://github.com/anthropics/mcp) (`mcp[cli]` package).
FastMCP handles the full MCP lifecycle over `stdio`:

```
Host AI process                   botcircuits-mcp process
      │                                    │
      │── initialize ──────────────────────►│  sends: server name, capabilities,
      │◄─ initialize result ────────────────│  tools list, instructions, resources
      │                                    │
      │── tools/list ───────────────────────►│
      │◄─ 26 tool definitions ──────────────│
      │                                    │
      │── tools/call { name, args } ────────►│  tool executes → REST API call
      │◄─ result ───────────────────────────│
```

The transport is `stdio` — the host AI spawns the server as a subprocess and
communicates via stdin/stdout JSON-RPC. This is the standard MCP deployment model
for local tools.

---

## 4. Schema delivery — no CLAUDE.md, no API key required

The BotCircuits workflow schema (all node types, slot rules, examples) is delivered to
the host AI via two built-in MCP mechanisms in `server.py`:

### 4.1 `server.instructions` — automatic, on every connection

```python
mcp = FastMCP(name="BotCircuits", instructions=_INSTRUCTIONS)
```

The `_INSTRUCTIONS` string contains:
- The server's role description ("you are the reasoning engine, not this server")
- The two-registry distinction (see Section 6)
- The full workflow creation flow (which operations to call in what order)
- The complete intermediate workflow JSON schema embedded inline

The MCP `initialize` response includes this `instructions` field. The host AI receives
it before the first tool call and treats it as persistent system context for the session.
No `CLAUDE.md`, no manual setup — any MCP-capable AI tool receives the schema just by
connecting.

### 4.2 `botcircuits://workflow-schema` resource — on demand

```python
@mcp.resource("botcircuits://workflow-schema", mime_type="text/markdown")
def workflow_schema_resource() -> str:
    return _WORKFLOW_SCHEMA
```

An MCP resource the host AI can fetch at any time via `resources/read` for a fresh copy
of the schema mid-conversation.

---

## 5. Tool structure — 26 operations across 5 modules

All operations are registered via `register_all()` in `tools/__init__.py`:

```python
def register_all(mcp: FastMCP) -> None:
    applications.register(mcp)
    sub_agents.register(mcp)
    workflows.register(mcp)
    workflow_schema.register(mcp)
    skills.register(mcp)
```

Each `register(mcp)` function decorates its operations with `@mcp.tool()`. FastMCP
introspects the function signature and docstring to generate the JSON schema sent
to the host AI in `tools/list`.

---

## 6. Two registries — the key data model distinction

The BotCircuits backend uses two separate registries under an application, and
understanding which operations go where is the most important thing to get right.

```
Application
├── /agent/core-settings         system prompt, language, KB config
│
├── /prompt-config/tools         ← THE MAIN REGISTRY
│   ├── type = "sub_agent"       Sub-Agents: instructions + capabilities dict
│   ├── type = "workflow"        Skills of type workflow
│   ├── type = "function"        Skills of type function (Lambda/webhook)
│   ├── type = "kb"              Skills of type knowledge base
│   └── type = "json"            Skills of type JSON
│
├── /agent/actions               ← WORKFLOW ROUTING ONLY
│   └── type = "workflow"        Links a Journey ID to the agent's action router
│
└── /model/journey               Workflow definitions (STM + slots)
```

### /prompt-config/tools (sub-agents and skills share one endpoint)

The same `GET/POST/PUT/DELETE /apps/{appId}/prompt-config/tools` endpoint serves
both sub-agents and skills — differentiated by `type`:

- `type = "sub_agent"` → sub-agents (handled by `sub_agents.py`)
- `type != "sub_agent"` → skills: workflow, function, kb, json (handled by `skills.py`)

`list_sub_agents` fetches all entries from the endpoint and filters to `type == "sub_agent"`.
`list_skills` fetches all entries and returns those that are not sub-agents.

---

## 7. Operation groups and REST endpoint mapping

### Applications (`applications.py` — 7 operations)

| Operation | Method | Endpoint |
|---|---|---|
| `list_applications` | GET | `/apps` |
| `get_application` | GET | `/apps/{appId}` |
| `create_application` | POST | `/apps` |
| `delete_application` | DELETE | `/apps/{appId}` |
| `publish_application` | POST | `/apps/{appId}/publish` |
| `get_application_core_settings` | GET | `/apps/{appId}/agent/core-settings` |
| `update_application_core_settings` | POST | `/apps/{appId}/agent/core-settings` |

### Sub-Agents (`sub_agents.py` — 5 operations)

All hit `/prompt-config/tools` with `type = "sub_agent"`.

| Operation | Method | Endpoint |
|---|---|---|
| `list_sub_agents` | GET | `/apps/{appId}/prompt-config/tools` (filtered) |
| `get_sub_agent` | GET | `/apps/{appId}/prompt-config/tools/{id}` |
| `create_sub_agent` | POST | `/apps/{appId}/prompt-config/tools` |
| `update_sub_agent` | PUT | `/apps/{appId}/prompt-config/tools/{id}` |
| `delete_sub_agent` | DELETE | `/apps/{appId}/prompt-config/tools/{id}` |

The `toolData` payload shape for a sub-agent:
```json
{
  "instructions": "You are a billing specialist...",
  "tools": {
    "lookup_order": {
      "name": "lookup_order",
      "type": "function",
      "description": "Look up an order by ID",
      "parameters": {
        "order_id": { "type": "string", "description": "Order ID", "required": true }
      },
      "toolData": {}
    }
  }
}
```

`update_sub_agent` fetches the current record first and merges only supplied fields,
making partial updates safe.

### Workflows (`workflows.py` — 7 operations)

| Operation | Method | Endpoint |
|---|---|---|
| `list_workflows` | GET | `/apps/{appId}/model/journey` |
| `get_workflow` | GET | `/apps/{appId}/model/journey/{journeyId}` |
| `save_workflow_stm` | POST | `/apps/{appId}/model/journey/{journeyId}/context` |
| `save_workflow_slots` | POST | `/apps/{appId}/model/journey/{journeyId}/slots` |
| `delete_workflow` | DELETE | `/apps/{appId}/model/journey/{journeyId}` |

### Skills (`skills.py` — 5 operations)

All hit `/prompt-config/tools` with `type != "sub_agent"`.

| Operation | Method | Endpoint |
|---|---|---|
| `list_skills` | GET | `/apps/{appId}/prompt-config/tools` (filtered) |
| `get_skill` | GET | `/apps/{appId}/prompt-config/tools/{id}` |
| `create_skill` | POST | `/apps/{appId}/prompt-config/tools` |
| `update_skill` | PUT | `/apps/{appId}/prompt-config/tools/{id}` |
| `delete_skill` | DELETE | `/apps/{appId}/prompt-config/tools/{id}` |

Skill types: `workflow` · `function` · `kb` · `json`

### Workflow Schema (`workflow_schema.py` — 2 operations)

| Operation | Description |
|---|---|
| `convert_intermediate_to_platform` | Pure deterministic transform, no API call |
| `upload_workflow` | `convert_intermediate_to_platform` + `save_workflow_stm` + `save_workflow_slots` in one call |

---

## 8. The REST API client (`client.py`)

A thin async wrapper using `httpx.AsyncClient`. Every call follows the same pattern:

```python
async def get_skill(app_id: str, skill_id: str) -> dict:
    async with _client() as c:
        r = await c.get(f"/apps/{app_id}/prompt-config/tools/{skill_id}")
        return await _raise_for(r)
```

`_client()` creates a new `AsyncClient` per call with the base URL and auth header
from `config.Settings`. `_raise_for()` raises `RuntimeError` on any 4xx/5xx response,
including the error body. FastMCP converts this into a tool error response the host AI
can read and recover from.

The `Authorization` header sends `BOTCIRCUITS_ACCESS_TOKEN` directly. The BotCircuits
backend accepts both Cognito JWT tokens (dashboard login) and static API access keys
(programmatic access). The MCP server uses the latter.

---

## 9. The platform format transform (`workflow_schema.py`)

This is the only non-trivial logic in the server. Everything else is CRUD.

### Why a transform is needed

The host AI generates workflow JSON in an **intermediate format** designed for
readability — state IDs are descriptive `snake_case` strings, `displayTextOptions`
are plain strings, and there is no ReactFlow metadata.

The BotCircuits platform requires a **platform format** with random 5-digit numeric
state IDs, `blockId` on every stateConfig, `displayTextOptions` coerced to objects,
and a full ReactFlow graph for the visual editor.

### Transform pipeline

```
intermediate { stmDefinition, slots }
         │
         ▼  1. ID generation
         │     generate unique random 5-digit start_id (start state is special:
         │     stateId == blockId — a platform invariant)
         │     for every other state: generate unique new_id and block_id
         │     build id_map  { snake_case_id → numeric_id }
         │     build block_map { numeric_id → block_id }
         │
         ▼  2. State translation (per state)
         │     translate all embedded string references via id_map
         │       (next fields, choice branch nexts, journeyId values, etc.)
         │     add blockId to stateConfig
         │     add displayText "", intentPrompt "", responseModifyByAI false
         │     coerce displayTextOptions strings → {"displayText":"..."} objects
         │     add validationErrorDisplayTextOptions for prompt states
         │     add autoFillFromEntity default for prompt states
         │
         ▼  3. slotStateMap
         │     for each state: if stateConfig has "slot" or "slotToAssign"
         │       record { slot: slot_name, state: new_state_id }
         │       skip "bc_workflow_option" (platform reserved slot)
         │
         ▼  4. ReactFlow layout (BFS from startAt)
         │     assign (x, y) = (col * 350, row * 150)
         │     choice branches each get col + 1
         │     map state-level positions → block-level positions
         │
         ▼  5. ReactFlow nodes
         │     StartNode: id = start_id, type = "StartNode",
         │                position {x:0,y:0}, data.stateConfig
         │     MainWorkflowNode per block: id = block_id,
         │                type = "MainWorkflowNode", data.children = [states in block]
         │
         ▼  6. ReactFlow edges (block-level, not state-level)
         │     start → first block:  sourceHandle = "start"
         │     normal → next block:  sourceHandle = "source-{bid}-{nid}-right"
         │     choice branch:        sourceHandle = "source-condition-{bid}-{cid}-right"
         │     choice default:       sourceHandle = "source-else-condition-{bid}-right"
         │     all targets:          targetHandle = "target-{bid}-left-top"
         │
         ▼
platform format { slots, stm: { stmDefinition, metadata, slotStateMap } }
```

## 10. Configuration (`config.py`)

`pydantic-settings` reads from environment variables (prefix `BOTCIRCUITS_`) and an
optional `.env` file:

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BOTCIRCUITS_", env_file=".env")
    api_base_url: str = "https://api.botcircuits.com"
    access_token: str = ""
    default_app_id: Optional[str] = None
```

`settings` is a module-level singleton loaded at import time. `client.py` reads
`settings.access_token` and `settings.api_base_url` on every call.

---

## 11. Intermediate vs platform format — side-by-side

```
Intermediate (host AI generates)       Platform (BotCircuits API expects)
────────────────────────────────        ──────────────────────────────────────
{                                       {
  "stmDefinition": {                      "slots": { ... },   ← promoted to top level
    "startAt": "ask_issue",               "stm": {
    "states": {                             "stmDefinition": {
      "start": {                              "startAt": "46867",
        "type": "StartNode",                  "states": {
        "next": "ask_issue",                    "46867": {        ← start: stateId==blockId
        "stateConfig": {                          "next": "20699",
          "stateType": "start",                   "stateConfig": {
          "name": "Start",                          "blockId": "46867",
          "next": "ask_issue"                       "stateType": "start",
        }                                           "next": "20699"
      },                                          }
      "ask_issue": {                            },
        "type": "buttonsPrompt",                "20699": {
        "next": "end",                            "next": "77548",
        "stateConfig": {                          "stateConfig": {
          "stateType": "prompt",                    "blockId": "84943",
          "type": "buttons",                        "stateType": "prompt",
          "name": "Issue Type",                     "type": "buttons",
          "slot": "issue_type",                     "name": "Issue Type",
          "displayTextOptions":                      "slot": "issue_type",
            ["What do you need?"],                   "displayText": "",
          "data": [...]                              "intentPrompt": "",
        }                                            "responseModifyByAI": false,
      }                                              "autoFillFromEntity": true,
    }                                                "blockId": "84943",
  },                                                 "displayTextOptions": [
  "slots": {                                           {"displayText": "What do you need?"}
    "issue_type": {                                  ],
      "slot": "issue_type",                          "validationErrorDisplayTextOptions": [
      "dataType": "values",                            {"displayText": ""}
      "content": "billing,technical",               ],
      ...                                            "data": [...]
    }                                             }
  }                                           }
}                                           },
                                            "metadata": {
                                              "nodes": [
                                                {"id":"46867","type":"StartNode",...},
                                                {"id":"84943","type":"MainWorkflowNode",
                                                 "data":{"children":[...]},...}
                                              ],
                                              "edges": [
                                                {"id":"edge-46867-84943",
                                                 "source":"46867","sourceHandle":"start",
                                                 "target":"84943",...}
                                              ]
                                            },
                                            "slotStateMap": [
                                              {"slot":"issue_type","state":"20699"}
                                            ]
                                          }
                                        }
```

---

## 12. Data model relationships

```
Account
└── Application (App)
    ├── core-settings              system prompt, language, KB top-results count
    │
    ├── /prompt-config/tools
    │   ├── Sub-Agents             type="sub_agent"
    │   │   ├── instructions       sub-agent's system prompt
    │   │   ├── capabilities       named capabilities (workflow/function/kb/json)
    │   │   └── parameters         inputs the Main Agent passes on delegation
    │   └── Skills                 type=workflow|function|kb|json
    │       ├── parameters         inputs the skill accepts
    │       └── toolData           type-specific config
    │
    ├── /agent/actions
    │   └── Workflow routing       actionType="workflow" — links workflow_id to intent router
    │
    └── /model/journey             Workflow definitions
        ├── trigger                intent name or event list
        ├── stm                    stmDefinition + metadata + slotStateMap
        └── slots                  slot variable definitions
```

---

## 13. Dependency summary

| Package | Role |
|---|---|
| `mcp[cli]` | MCP protocol implementation, FastMCP framework, stdio transport |
| `httpx` | Async HTTP client for BotCircuits REST API calls |
| `pydantic` | Field validation in Settings |
| `pydantic-settings` | Env var + .env file loading for Settings |
| `python-dotenv` | .env file support |

No LLM SDK (`anthropic`, `openai`) is a dependency. The server makes zero LLM calls.
