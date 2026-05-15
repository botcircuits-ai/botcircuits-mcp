"""
BotCircuits MCP Server

A thin API wrapper over BotCircuits. No LLM inside — the host AI (Claude Code,
Cursor, Copilot, etc.) does all the reasoning and generates the workflow JSON.
This server handles the platform-specific transform and the REST API calls.

Only BOTCIRCUITS_ACCESS_TOKEN is needed. No LLM API key required.
"""

from mcp.server.fastmcp import FastMCP
from .tools import register_all

# ─────────────────────────────────────────────────────────────────────────────
# WORKFLOW SCHEMA — delivered to the host LLM via two mechanisms:
#   1. server `instructions` — sent automatically during MCP initialize handshake
#   2. @mcp.resource("botcircuits://workflow-schema") — fetchable on demand
#
# This means any MCP client (Claude Code, Cursor, etc.) receives the schema
# without the user needing a CLAUDE.md or any manual setup.
# ─────────────────────────────────────────────────────────────────────────────

_WORKFLOW_SCHEMA = """\
## BotCircuits Workflow Schema

You are the reasoning engine. When the user asks you to build or modify a workflow,
generate the intermediate JSON yourself using this schema, then call upload_workflow
to persist it. No API key needed — this server only calls BotCircuits REST APIs.

### Intermediate format — two top-level keys: stmDefinition + slots
Rules:
- State IDs: descriptive snake_case (e.g. "ask_email", "check_account")
- First state: stateType "start", type "StartNode"
- Every non-terminal state must have a non-null "next"
- Choice states: state-level "next": null — routing is in stateConfig
- displayTextOptions: array of plain strings (not objects)
- Every slot name used anywhere must have a definition in "slots"

### Node types

START (required first state):
{ "type": "StartNode", "next": "<id>",
  "stateConfig": { "stateType": "start", "name": "Start", "next": "<id>" } }

TEXT MESSAGE (sends text, no reply):
{ "type": "messagePrompt", "next": "<id>",
  "stateConfig": { "stateType": "message", "type": "text", "name": "...",
                   "displayTextOptions": ["Hello!"], "responseModifyByAI": false } }

QUESTION PROMPT (free-text, saves to slot):
{ "type": "questionPrompt", "next": "<id>",
  "stateConfig": { "stateType": "prompt", "type": "text", "name": "...",
                   "slot": "<slot_name>", "displayTextOptions": ["..."],
                   "autoFillFromEntity": true } }

BUTTONS PROMPT (multiple-choice, saves selected payload to slot):
{ "type": "buttonsPrompt", "next": "<id>",
  "stateConfig": { "stateType": "prompt", "type": "buttons", "name": "...",
                   "slot": "<slot_name>", "displayTextOptions": ["..."],
                   "data": [{"title":"...", "payload":"...", "actionType":"set_value"}],
                   "autoFillFromEntity": true } }

CARDS PROMPT (carousel — slot must be "bc_workflow_option"):
{ "type": "cardsPrompt", "next": "<id>",
  "stateConfig": { "stateType": "prompt", "type": "cards", "name": "...",
                   "slot": "bc_workflow_option", "displayTextOptions": ["..."],
                   "data": [{"title":"...", "description":"...",
                             "buttons":[{"title":"...", "payload":"...", "actionType":"set_value"}]}] } }

                             
CUSTOM ACTION (custom action, optional - saves result to slot):
{ "type": "customAction", "next": "<id>",
  "stateConfig": { "stateType": "action", "type": "customAction",
                   "id": "<unique id, that represent the action task>", "name": "...", "slotToFill": "<slot_name>" } }


WEBHOOK ACTION (HTTP API call):
{ "type": "webhookAction", "next": "<id>",
  "stateConfig": { "stateType": "action", "type": "webhook", "name": "...",
                   "skipNotify": true,
                   "webhookConfig": { "url": "https://api.example.com/{slot}",
                     "method": "POST", "headers": [], "parameters": [],
                     "requestBody": "{}", "requestType": "raw",
                     "responseMapping": [{"key": "data.field", "slot": "result_slot"}] },
                   "requestMapper":  {"type":"inline_script","inlineScript":""},
                   "responseMapper": {"type":"inline_script","inlineScript":""} } }

CODEHOOK ACTION:
{ "type": "codehookAction", "next": "<id>",
  "stateConfig": { "stateType": "action", "type": "codehook", "name": "...",
                   "codehookId": "<id>", "defaultInput": "{}", "skipNotify": false } }

AI TASK (bounded LLM call, saves result to slot):
{ "type": "aiTask", "next": "<id>",
  "stateConfig": { "stateType": "action", "type": "aiTask", "name": "...",
                   "skipNotify": false,
                   "assignToSlots": {"executors": [
                     {"inputPrompt": "Summarise: {slot}", "slotToAssign": "result"}]} } }

DOC SEARCH (RAG knowledge base):
{ "type": "docSearchAction", "next": "<id>",
  "stateConfig": { "stateType": "action", "type": "docSearch", "name": "...",
                   "inputPrompt": "query with {slot}", "slotToAssign": "answer",
                   "skipNotify": false } }

JOURNEY ACTION (nested workflow):
{ "type": "journeyAction", "next": "<id>",
  "stateConfig": { "stateType": "action", "type": "journey", "name": "...",
                   "journeyId": "<workflow_id>" } }

LIVE AGENT (human handoff, terminal):
{ "type": "liveAgentAction", "next": null,
  "stateConfig": { "stateType": "action", "type": "liveagent", "name": "Transfer to Agent" } }

PAUSE (wait for external event):
{ "type": "pauseAction", "next": "<resume_id>",
  "stateConfig": { "stateType": "action", "type": "pause", "name": "..." } }

END (terminal):
{ "type": "codehookAction", "next": null,
  "stateConfig": { "stateType": "action", "type": "end", "name": "End" } }

CHOICE (conditional branch — state-level next must be null):
{ "type": "choice", "next": null,
  "stateConfig": { "stateType": "choice",
    "choices": [
      { "id": 1, "operator": "AND",
        "expressionList": [{"variable": "slot_name", "operator": "is", "value": "billing"}],
        "next": "billing_state" }
    ],
    "next": "default_state" } }
Operators: is · is not · greater than · greater than or equal · less than ·
           less than or equal · contains · not contains · starts with · ends with ·
           is empty · is not empty
Special variables: {sys_input_text} (raw user message) · {sys_channel} (channel name)

### Slot definitions
Every slot name used anywhere (slot, slotToAssign, responseMapping, {slot}) must
have an entry in "slots":
{ "slot_name": { "slot": "slot_name", "displayText": "Label",
                 "captureFromUserInput": true, "dataType": "<type>",
                 "content": "<type-dependent>", "dependencies": [] } }

dataType / content rules:
  custom   → content = AI extraction description  e.g. "Customer's full legal name"
  regex    → content = regex pattern               e.g. "^ORD-\\d{6}$"
  values   → content = comma-separated values      e.g. "billing,technical,general"
  number   → content = optional "min-max" hint     e.g. "1-5"
  boolean, email, age, date, datetime, any → content = ""

content REQUIRED (non-empty) for: custom, regex, values
captureFromUserInput: false for slots filled from API responseMapping
Reserved slot "bc_workflow_option" does NOT need a definition

### Example — support triage
{
  "stmDefinition": {
    "startAt": "start",
    "states": {
      "start":     {"type":"StartNode",      "next":"greet",      "stateConfig":{"stateType":"start","name":"Start","next":"greet"}},
      "greet":     {"type":"messagePrompt",  "next":"ask_issue",  "stateConfig":{"stateType":"message","type":"text","name":"Greet","displayTextOptions":["Hi! How can I help?"],"responseModifyByAI":false}},
      "ask_issue": {"type":"buttonsPrompt",  "next":"route",      "stateConfig":{"stateType":"prompt","type":"buttons","name":"Issue Type","slot":"issue_type","displayTextOptions":["What do you need?"],"data":[{"title":"Billing","payload":"billing","actionType":"set_value"},{"title":"Technical","payload":"technical","actionType":"set_value"}],"autoFillFromEntity":true}},
      "route":     {"type":"choice",         "next":null,         "stateConfig":{"stateType":"choice","choices":[{"id":1,"operator":"AND","expressionList":[{"variable":"issue_type","operator":"is","value":"billing"}],"next":"billing_end"}],"next":"general_end"}},
      "billing_end":{"type":"codehookAction","next":null,         "stateConfig":{"stateType":"action","type":"end","name":"End"}},
      "general_end":{"type":"codehookAction","next":null,         "stateConfig":{"stateType":"action","type":"end","name":"End"}}
    }
  },
  "slots": {
    "issue_type": {"slot":"issue_type","displayText":"Issue Type","captureFromUserInput":true,"dataType":"values","content":"billing,technical","dependencies":[]}
  }
}
"""

_INSTRUCTIONS = """\
You are connected to the BotCircuits platform.

This MCP server is a thin API wrapper — no LLM inside it, no API key required beyond
BOTCIRCUITS_ACCESS_TOKEN. You are the reasoning engine.

PROMPT-CONFIG REGISTRY (/prompt-config/tools):
- Sub-Agents (type="sub_agent"): list_sub_agents, get_sub_agent, create_sub_agent,
  update_sub_agent, delete_sub_agent
- Skills (type!="sub_agent"): list_skills, get_skill, create_skill, update_skill,
  delete_skill — types: workflow · function · kb · json

WORKFLOW CREATION FLOW - When the user asks you to build or modify a workflow:
1. Generate the intermediate workflow JSON yourself using the schema below
2. Call upload_workflow(
      app_id, 
      name=<Skill name the LLM will see (e.g. "book_appointment")>,
      description=<When-to-use description for the LLM>,
      intermediate=<JSON>,
      workflow_id=<Existing journey ID to link - optional>) — the server transforms it to platform format and saves it

AFTER CREATING A WORKFLOW (once upload_workflow completes), always ask:
"Would you like to connect this workflow to a sub-agent's skill, or connect it
directly as a main agent skill?" — do not skip this step or report the task as
done without asking.

CUSTOM ACTION STEP Node:
- Always use the custom action (nodeType: customAction) step when the user requests file operations, code generation, or command execution within a workflow.
  Only use a different step node type if the user explicitly requests it.

""" + _WORKFLOW_SCHEMA


mcp = FastMCP(name="BotCircuits", instructions=_INSTRUCTIONS)

register_all(mcp)


@mcp.resource(
    "botcircuits://workflow-schema",
    name="BotCircuits Workflow Schema",
    description="Full intermediate workflow JSON schema with all node types, slot rules, and a complete example",
    mime_type="text/markdown",
)
def workflow_schema_resource() -> str:
    return _WORKFLOW_SCHEMA


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
