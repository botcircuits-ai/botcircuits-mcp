"""
BotCircuits MCP Server

A thin API wrapper over BotCircuits. No LLM inside — the host AI (Claude Code, Cursor,
Copilot, etc.) does the reasoning and writes playbooks / workflows; this server validates,
compiles and saves them through the REST API.

Only BOTCIRCUITS_ACCESS_TOKEN is needed. No LLM API key required.

Guidance reaches the host AI three ways: the server `instructions` (sent during the MCP
initialize handshake), resources (botcircuits://…) and the get_authoring_guide tool, for
clients that do not read resources.
"""

from typing import Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .guides import (
    AGENT_SCOPE,
    CAPABILITY_GUIDE,
    CLARIFYING,
    GUIDES,
    KNOWLEDGE,
    PLAYBOOK_DESIGN,
    PLAYBOOK_SCHEMA,
    PUBLISHING,
    TESTING,
    TESTING_OFFER,
    TROUBLESHOOTING,
    WORKFLOW_SCHEMA,
)
from .tools import register_all

_INSTRUCTIONS = f"""\
You are connected to the BotCircuits platform. This MCP server is a thin API wrapper; you are
the reasoning engine. It builds a BotCircuits agent (app): instructions, agent tools, codehooks,
skills, MCP servers, sub-agents, knowledge, playbooks and workflows.

CHOOSING THE APPLICATION (always first)
- If the user named an application or gave an appId, use it (confirm with get_application).
- If the user explicitly asked for a new application, create it with create_application.
- Otherwise ASK before doing anything else: "Do you want to create a new application, or use an
  existing one?" For existing, call list_applications and let the user pick; for new, confirm the
  name and a one-line description that says which business the agent serves and what it helps
  customers with, then call create_application. Never pick or create an application on your own.
- New applications are always prompt-based agents (appMode "prompt_based"); every capability
  this server builds runs in that agent.

HOW TO WORK
1. Start with get_application_overview(app_id) and reuse what exists. If it shows
   scopeDefined: false, the agent will respond to anything a customer asks: set the agent's
   scope first (AGENT SCOPE below) before building anything else.
2. Clarify what blocks you before building ("Clarifying" below): one focused question at a time,
   with concrete options and a recommended default. Never invent business facts, prices,
   policies, API contracts or credentials.
3. Pick the construct for each capability with the policy below (recommend_implementation).
4. Build in this order: scope instructions (if missing) -> codehooks / API tools / knowledge ->
   playbooks / workflows -> agent tools and skills -> update_agent_instructions (keep the Scope
   section, extend it for new capabilities, say when to use each capability).
5. Iterate with validate_playbook / validate_workflow_definition until there are no errors,
   then save once. Save tools refuse invalid input and return exact errors — fix and retry.
6. Knowledge from the web or a file is not usable until preview_knowledge_source returns
   verdict "ok"; if a crawl captured nothing, suggest adding the content as a file
   (get_authoring_guide("knowledge")).
7. Report and offer automated tests ("Report and offer tests" below).
8. Only delete when the user asked for it. NEVER publish automatically: ask "Do you want to
   publish your agent?" and call publish_application only after an explicit yes (it confirms
   with the user too; get_authoring_guide("publishing")).

AFTER CREATING A PLAYBOOK OR WORKFLOW, always ask: "Would you like to connect it to a
sub-agent's capabilities, or directly as a main agent tool?" Then do exactly ONE of:
- main agent: update_playbook / upload_workflow with expose_as_agent_tool=true (or
  create_agent_tool with tool_type "workflow" — same single record, never both needed);
- sub-agent: update_sub_agent adding a capability {{"type": "workflow", "toolData": {{"workflowId": "<id>"}}}}.
A playbook/workflow has at most one main-agent tool; the server keeps it that way.
Do not report the task as done without asking.

TROUBLESHOOTING: when the user says their agent misbehaved in a conversation (wrong or missing
answer, error, stuck in a playbook/workflow, wrong branch, slow), read the runtime trace before
changing anything: get_authoring_guide("troubleshooting"), then find_problem_sessions ->
get_session_trace -> get_turn_trace. Explain the cause with evidence from the trace, propose the
fix, and apply it once the user agrees.

{CLARIFYING}
{AGENT_SCOPE}
{TESTING_OFFER}
{CAPABILITY_GUIDE}
{PLAYBOOK_DESIGN}
{PLAYBOOK_SCHEMA}
Before writing your first playbook in a session, read the worked examples:
get_authoring_guide("playbook_examples") (or botcircuits://playbook-examples). They all compile.
Canvas workflow schema: call get_authoring_guide("workflow") or read botcircuits://workflow-schema
before writing a workflow. Knowledge, testing and publishing: get_authoring_guide("knowledge" |
"testing" | "publishing").
"""

mcp = FastMCP(name="BotCircuits", instructions=_INSTRUCTIONS)

register_all(mcp)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def get_authoring_guide(
    topic: Literal["capabilities", "playbook", "playbook_design", "playbook_examples", "workflow",
                   "troubleshooting", "scope", "clarifying", "knowledge", "testing", "publishing"],
) -> str:
    """
    Return an authoring guide:
      capabilities       what to build: tool vs skill vs playbook vs workflow
      playbook_design    how to design a working playbook: runtime behaviour, procedure, patterns, checklist
      playbook           playbook step format (every step kind and field)
      playbook_examples  complete, validated create_playbook examples to copy from
      workflow           canvas workflow schema (complex processes only)
      troubleshooting    diagnosing a misbehaving agent from runtime traces (read before the trace tools)
      scope              the required `## Scope` section that keeps the agent on its business's topics
      clarifying         how to ask the user: one question, concrete options, a recommended default
      knowledge          adding text / web / file sources, verifying them with a preview, kb tools
      testing            generating automated test scenarios, running them and explaining failures
      publishing         publishing a version, the website snippet, channels and access keys

    Args:
        topic: capabilities · playbook_design · playbook · playbook_examples · workflow · troubleshooting ·
            scope · clarifying · knowledge · testing · publishing
    """
    return GUIDES[topic]


@mcp.resource("botcircuits://workflow-schema", name="BotCircuits Workflow Schema", mime_type="text/markdown",
              description="Canvas workflow intermediate JSON: node types, branching, slots")
def workflow_schema_resource() -> str:
    return WORKFLOW_SCHEMA


@mcp.resource("botcircuits://playbook-schema", name="BotCircuits Playbook Schema", mime_type="text/markdown",
              description="Playbook sections/steps format: SEND, ASK, RUN, SET, IF, GO_TO, HANDOFF")
def playbook_schema_resource() -> str:
    return PLAYBOOK_SCHEMA


@mcp.resource("botcircuits://playbook-design", name="BotCircuits Playbook Design Guide", mime_type="text/markdown",
              description="How playbooks run, design procedure, patterns and checklist")
def playbook_design_resource() -> str:
    return PLAYBOOK_DESIGN


@mcp.resource("botcircuits://playbook-examples", name="BotCircuits Playbook Examples", mime_type="text/markdown",
              description="Complete create_playbook examples that compile and validate")
def playbook_examples_resource() -> str:
    return GUIDES["playbook_examples"]


@mcp.prompt(name="design_playbook", title="Design a BotCircuits playbook",
            description="Guided procedure to design, validate and save a working playbook")
def design_playbook_prompt(app_id: str, requirements: str) -> str:
    return f"""Design and save a BotCircuits playbook for app {app_id}.

Requirements from the user:
{requirements}

Follow the playbook design procedure exactly:
1. Call get_application_overview("{app_id}") and get_authoring_guide("playbook_examples").
2. Write the one-sentence outcome and list every input and where it comes from. If an API, policy
   or important wording is missing, ask me before building — one question at a time, with concrete
   options and a recommended default where one exists. Do not invent it.
3. Create any missing codehook / API tool / knowledge source first and note the ids and output variables.
4. Define the variables (strictest dataType; values lists include every button payload).
5. Draft sections and steps: happy path first, then every unhappy path (invalid answer, not found or
   failed call, declined confirmation, wants a person), and close every path with a SEND or HANDOFF.
6. Call validate_playbook until there are no errors and the warnings are resolved or explained.
   Then review it as an independent reviewer: every requirement present, correct ordering, branch
   rejoining and termination, no invented integrations or placeholders, no unconditional loops.
7. Show me the outline and wait for my confirmation, then call create_playbook with the same arguments.
8. Ask whether to connect it to the main agent or a sub-agent, and update the agent instructions
   (keep its `## Scope` section and add this playbook's topic to it).
9. Summarize what changed and offer to create automated tests for the agent.
"""


@mcp.resource("botcircuits://troubleshooting", name="BotCircuits Troubleshooting Guide", mime_type="text/markdown",
              description="Diagnosing a misbehaving agent from runtime traces: procedure, fields, issue → fix")
def troubleshooting_resource() -> str:
    return TROUBLESHOOTING


@mcp.resource("botcircuits://agent-scope", name="BotCircuits Agent Scope Guide", mime_type="text/markdown",
              description="Keeping an agent on its business's topics: the required ## Scope section")
def agent_scope_resource() -> str:
    return AGENT_SCOPE


@mcp.resource("botcircuits://knowledge", name="BotCircuits Knowledge Guide", mime_type="text/markdown",
              description="Adding text, web and file knowledge, verifying it with a preview, kb tools")
def knowledge_resource() -> str:
    return KNOWLEDGE


@mcp.resource("botcircuits://testing", name="BotCircuits Agent Testing Guide", mime_type="text/markdown",
              description="Generating, running and explaining automated agent tests")
def testing_resource() -> str:
    return TESTING


@mcp.resource("botcircuits://publishing", name="BotCircuits Publishing Guide", mime_type="text/markdown",
              description="Publishing versions, the website snippet, channels and access keys")
def publishing_resource() -> str:
    return PUBLISHING


@mcp.resource("botcircuits://capability-guide", name="BotCircuits Capability Guide", mime_type="text/markdown",
              description="When to use tools, skills, playbooks and workflows")
def capability_guide_resource() -> str:
    return CAPABILITY_GUIDE


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
