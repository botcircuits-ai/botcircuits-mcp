# botcircuits-mcp — Implementation

## 1. Design

The server is a **thin API wrapper**: no LLM, no API key beyond `BOTCIRCUITS_ACCESS_TOKEN`.
The host AI (Claude Code, Cursor, …) reasons and writes playbooks/workflows; the server
supplies the guidance, validates and compiles what the AI writes, and calls the REST API.

What the server adds over raw API calls:

- **Policy** — which construct fits a requirement (tool / skill / playbook / workflow),
  delivered as guidance and enforced at save time (`workflow/complexity.py`).
- **Correctness** — definitions are checked against the runtime's rules before saving
  (`workflow/validator.py`); several runtime failures are otherwise silent.
- **Console compatibility** — playbooks are compiled by a port of the console compiler and
  workflows get a canvas layout that round-trips through the console's `transformSchema`.

```
User ─► Host AI ─► tools/call ─► botcircuits-mcp ─► BotCircuits REST API (Authorization: access token)
             ▲                         │
             └── instructions, resources, get_authoring_guide
```

## 2. Layout

```
botcircuits/
  config.py        BOTCIRCUITS_API_BASE_URL, BOTCIRCUITS_ACCESS_TOKEN, BOTCIRCUITS_DEFAULT_APP_ID
  client.py        one _request helper + every endpoint (routes from botcircuits-platform/src/services)
  guides.py        CAPABILITY_GUIDE, PLAYBOOK_SCHEMA, WORKFLOW_SCHEMA
  server.py        FastMCP: instructions, 3 resources, get_authoring_guide
  workflow/        pure logic, shared with botcircuits-agent-builder-copilot
    constants.py   stmDefinition vocabulary (as the runtime implements it)
    validator.py   authoring checklist of bc-tech-docs/08 §10
    playbook.py    port of pages/playbooks/compiler/compile-playbook.js
    transform.py   intermediate workflow JSON -> platform format + React Flow metadata
    complexity.py  construct policy; thresholds 30 steps / 20 conditional nodes
    codegen.py     HTTP-API codehook generator
  tools/
    applications.py  overview, core settings (merged), global instructions
    workflows.py     recommend_implementation + canvas workflow CRUD
    playbooks.py     playbook CRUD
    journeys.py      shared: action upsert, journey provisioning, agent-tool exposure
    agent_tools.py   /prompt-config/tools (function, kb, workflow, json)
    codehooks.py     codehooks, create_api_tool
    sub_agents.py    /prompt-config/tools with type=sub_agent
    skills.py        /prompt-config/skills
    mcp_servers.py   /prompt-config/mcp-servers
    knowledge.py     knowledge sources
tests/
  fakes.py         in-memory BotCircuits API over httpx.MockTransport
  test_tools.py    end-to-end tool tests (no network)
```

## 3. Registries

| REST path | Console name | MCP tools |
|---|---|---|
| `/prompt-config/tools` (type ≠ sub_agent) | Tools | `*_agent_tool`, `create_api_tool` |
| `/prompt-config/tools` (type = sub_agent) | Sub-agents | `*_sub_agent` |
| `/prompt-config/skills` | Skills | `*_skill` |
| `/prompt-config/mcp-servers` | MCP | `*_mcp_server` |
| `/prompt-config/instructions` | Instructions | `get/update_agent_instructions` |
| `/agent/actions` + `/model/journey/{id}` | Playbooks / Workflows | `*_playbook`, `*_workflow` |
| `/model/codehooks` | Functions | `list_codehooks`, `deploy_codehook`, `create_api_tool` |
| `/knowledge/data-sources` | Knowledge | `list_knowledge_sources`, `add_*_knowledge` |

## 4. Playbooks and workflows

Both are journeys: an action record (`actionType: "workflow"`) plus a journey record with
`stm.stmDefinition`. They differ in `stm.metadata` and the action's `authoringMode`:

| | Playbook | Canvas workflow |
|---|---|---|
| authored as | sections/steps (PLAYBOOK_SCHEMA) | intermediate JSON (WORKFLOW_SCHEMA) |
| compiled by | `workflow/playbook.py` | `workflow/transform.py` |
| `stm.metadata` | `{authoringMode: 'playbook', playbook, nodes: [], edges: []}` | React Flow `{nodes, edges}` |
| action `authoringMode` | `playbook` | `workflow` |
| policy | default for multi-step processes | new ones need > 30 steps, > 20 conditional nodes, or unsupported steps |

Save sequence (both): validate → `POST /agent/actions` → wait for the journey (created
asynchronously; falls back to `POST /model/journey`, as the console does) → `POST …/slots`
(merged with stored slots) → `PUT …/definition`. Nothing is written if validation fails.

Exposure: after creating, the host AI asks whether to connect the journey to a sub-agent or
as a main agent tool. `expose_as_agent_tool` registers a `type=workflow` tool — playbooks use
the console convention (`playbook-<id>`, `managedByPlaybook`). Deleting a playbook/workflow
deletes the action (which removes the journey) and every agent tool that starts it.

### Canvas transform rules

- States are renumbered to numeric ids; only edge fields are remapped (`next`,
  `stateConfig.next`, `choices[].next`, `conditions[].next`). Unknown targets are kept so
  validation reports them.
- Each state is its own `MainWorkflowNode` block. Edge handles: `start`;
  `source-condition-<stateId>-<choiceId>-right`; `source-else-condition-<stateId>-right`;
  `source-<blockId>-<stateId>-right`. Choice ids are numbers (the canvas parses them with `+`).
- `bc_workflow_option` (older schema) is rewritten to the runtime's `workflow_option`.

## 5. API tools

`create_api_tool` generates a Node.js codehook from a spec (method, URL, query, headers,
body template with `{param}` / `{env.NAME}` placeholders, result path, slot mapping),
deploys it (config → presigned upload → deploy) and registers a `type=function` agent
tool. The same codehook works as a playbook RUN FUNCTION step: both callers pass
`{context, slots, defaultInput}` — a function tool sets it via `toolData.defaultInput` (JSON
text, validated and encoded by `create_agent_tool` / `update_agent_tool` / sub-agent capabilities).
Secrets live in codehook environment variables.

## 6. Tests

`tests/fakes.py` implements the routes the tools use, including asynchronous journey
provisioning and write-only MCP tokens. Tests cover playbook create/update/expose,
validation refusals, the 30/20 policy, the canvas handle and payload-remap fixes, the
settings merge, prompt-config validation and API-tool deployment.
