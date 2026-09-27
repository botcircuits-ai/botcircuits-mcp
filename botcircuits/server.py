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

from .guides import CAPABILITY_GUIDE, GUIDES, PLAYBOOK_DESIGN, PLAYBOOK_SCHEMA, WORKFLOW_SCHEMA
from .tools import register_all

_INSTRUCTIONS = f"""\
You are connected to the BotCircuits platform. This MCP server is a thin API wrapper; you are
the reasoning engine. It builds a BotCircuits agent (app): instructions, agent tools, codehooks,
skills, MCP servers, sub-agents, knowledge, playbooks and workflows.

HOW TO WORK
1. Start with get_application_overview(app_id) and reuse what exists.
2. Pick the construct for each capability with the policy below (recommend_implementation).
3. Build dependencies first: codehooks / API tools / knowledge -> playbooks / workflows ->
   agent tools and skills -> update_agent_instructions (say when to use each capability).
4. Iterate with validate_playbook / validate_workflow_definition until there are no errors,
   then save once. Save tools refuse invalid input and return exact errors — fix and retry.
5. Only delete when the user asked for it.

AFTER CREATING A PLAYBOOK OR WORKFLOW, always ask: "Would you like to connect it to a
sub-agent's capabilities, or directly as a main agent tool?" — then use
update_sub_agent (capability type workflow) or create_agent_tool(tool_type="workflow") /
the expose_as_agent_tool flag. Do not report the task as done without asking.

{CAPABILITY_GUIDE}
{PLAYBOOK_DESIGN}
{PLAYBOOK_SCHEMA}
Before writing your first playbook in a session, read the worked examples:
get_authoring_guide("playbook_examples") (or botcircuits://playbook-examples). They all compile.
Canvas workflow schema: call get_authoring_guide("workflow") or read botcircuits://workflow-schema
before writing a workflow.
"""

mcp = FastMCP(name="BotCircuits", instructions=_INSTRUCTIONS)

register_all(mcp)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def get_authoring_guide(
    topic: Literal["capabilities", "playbook", "playbook_design", "playbook_examples", "workflow"],
) -> str:
    """
    Return an authoring guide:
      capabilities       what to build: tool vs skill vs playbook vs workflow
      playbook_design    how to design a working playbook: runtime behaviour, procedure, patterns, checklist
      playbook           playbook step format (every step kind and field)
      playbook_examples  complete, validated create_playbook examples to copy from
      workflow           canvas workflow schema (complex processes only)

    Args:
        topic: capabilities · playbook_design · playbook · playbook_examples · workflow
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
   or important wording is missing, ask me before building — do not invent it.
3. Create any missing codehook / API tool / knowledge source first and note the ids and output variables.
4. Define the variables (strictest dataType; values lists include every button payload).
5. Draft sections and steps: happy path first, then every unhappy path (invalid answer, not found or
   failed call, declined confirmation, wants a person), and close every path with a SEND or HANDOFF.
6. Call validate_playbook until there are no errors and the warnings are resolved or explained.
   Then review it as an independent reviewer: every requirement present, correct ordering, branch
   rejoining and termination, no invented integrations or placeholders, no unconditional loops.
7. Show me the outline and wait for my confirmation, then call create_playbook with the same arguments.
8. Ask whether to connect it to the main agent or a sub-agent, and update the agent instructions.
"""


@mcp.resource("botcircuits://capability-guide", name="BotCircuits Capability Guide", mime_type="text/markdown",
              description="When to use tools, skills, playbooks and workflows")
def capability_guide_resource() -> str:
    return CAPABILITY_GUIDE


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
