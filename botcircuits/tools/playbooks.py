"""
MCP tools for BotCircuits Playbooks.

A playbook is an ordered, sectioned list of steps (SEND, ASK, RUN, SET, IF, GO_TO,
HANDOFF, RUN_PLAYBOOK) compiled to the same state machine a canvas workflow uses. It is
the default for multi-step processes; see botcircuits://playbook-schema.

Playbooks are compiled with a port of the console compiler and stored with
metadata.authoringMode = 'playbook', so they open and edit in the console Playbooks editor.
"""

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .. import client
from ..workflow.authoring_rules import (check_authoring, data_type_warnings, natural_condition_warnings,
                                       normalize_variables)
from ..workflow.complexity import WORKFLOW_MIN_CONDITIONS, WORKFLOW_MIN_STEPS, playbook_complexity
from ..workflow.constants import RESERVED_SLOTS
from ..workflow.lint import lint_flow
from ..workflow.playbook import compile_playbook, normalize_playbook, outline_playbook, playbook_slot_usage
from ..workflow.validator import validate_workflow
from . import journeys


async def check_playbook(app_id: str, playbook: dict, variables: dict, existing_slots: dict) -> dict:
    normalized, errors = normalize_playbook(playbook)
    compiled = compile_playbook(normalized) if not errors else {"errors": [], "warnings": []}
    errors = [e["message"] + (f" (step {e['stepId']})" if e.get("stepId") else "")
              for e in errors + compiled.get("errors", [])]
    warnings = [w["message"] for w in compiled.get("warnings", [])]

    usage = playbook_slot_usage(normalized)
    variables, variable_errors = normalize_variables(variables, usage["collected"])
    errors += variable_errors
    defined = set(existing_slots) | set(variables) | RESERVED_SLOTS
    for slot in sorted(usage["collected"] - defined):
        errors.append(f"ASK variable '{slot}' has no definition in variables "
                      "(e.g. {\"dataType\": \"custom\", \"content\": \"what to extract\"}).")
    # SET / RUN outputs are assigned by the playbook itself; `any` passes them through unchanged.
    auto = {s: {"slot": s, "displayText": s, "dataType": "any", "content": "", "captureFromUserInput": False,
                "dependencies": []} for s in usage["assigned"] - defined}
    if not errors:
        errors += check_authoring(normalized, defined, usage["assigned"])
    warnings += natural_condition_warnings(normalized)
    warnings += data_type_warnings(normalized, variables)

    stm = compiled.get("stmDefinition")
    if stm:
        # Report every problem at once; ASK-variable gaps are already phrased for playbooks.
        check = validate_workflow(stm, journeys.merge_slots(existing_slots, variables, auto),
                                  **await journeys.known_ids(app_id))
        errors += [e for e in check.errors if "is collected by a prompt" not in e]
        # Undeclared reads are already errors (check_authoring); skip the validator's duplicate.
        warnings += [w for w in check.warnings if "unreachable" not in w and "which no step collects" not in w]
        warnings += lint_flow(stm)

    complexity = playbook_complexity(normalized)
    if complexity.exceeds_playbook:
        warnings.append(
            f"{complexity.steps} steps / {complexity.conditional_nodes} IFs exceeds playbook guidance "
            f"(≤{WORKFLOW_MIN_STEPS} steps, ≤{WORKFLOW_MIN_CONDITIONS} conditions); consider a workflow.")
    return {"normalized": normalized, "stm": stm, "errors": list(dict.fromkeys(errors))[:40], "warnings": warnings,
            "auto": auto, "variables": variables,
            "complexity": complexity.as_dict(), "outline": outline_playbook(normalized)}


async def save_playbook(app_id: str, name: str, description: str, playbook: dict, variables: dict | None,
                        workflow_id: str | None, expose_as_agent_tool: bool) -> dict:
    variables = variables or {}
    existing_slots: dict[str, Any] = {}
    if workflow_id:
        journey = await client.get_journey(app_id, workflow_id)
        if journeys.is_canvas_owned(journey):
            raise ValueError("That record was built on the canvas; update it with upload_workflow instead.")
        existing_slots = journey.get("slots") or {}

    result = await check_playbook(app_id, playbook, variables, existing_slots)
    if result["errors"]:
        return {"ok": False, "saved": False, "errors": result["errors"], "warnings": result["warnings"],
                "hint": "Fix every error and call validate_playbook again; see get_authoring_guide('playbook_design')."}

    new_id = await journeys.upsert_action(app_id, workflow_id, name, description, journeys.AUTHORING_MODE_PLAYBOOK)
    await client.ensure_journey(app_id, new_id)
    slots = journeys.merge_slots(existing_slots, result["variables"], result["auto"])
    await client.save_journey_slots(app_id, new_id, slots)
    await client.save_journey_definition(app_id, new_id, result["stm"], {
        "authoringMode": journeys.AUTHORING_MODE_PLAYBOOK, "playbook": result["normalized"],
        "nodes": [], "edges": []})
    exposure: dict = {"registered": False}
    if expose_as_agent_tool:
        action = await journeys.find_action(app_id, new_id)  # effective name/description after an update
        exposure = await journeys.expose_as_agent_tool(app_id, new_id, action.get("name") or name,
                                                       action.get("description") or description,
                                                       journeys.AUTHORING_MODE_PLAYBOOK)
    return {"ok": True, "workflowId": new_id, "authoringMode": "playbook", "created": not workflow_id,
            "variables": sorted(slots), "warnings": result["warnings"], "complexity": result["complexity"],
            "outline": result["outline"],
            "agentTool": exposure}


def register(mcp: FastMCP) -> None:

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def list_playbooks(app_id: str) -> list:
        """
        List the agent's playbooks (id, name, description).

        Args:
            app_id: The agent / app ID.
        """
        return [{"id": a.get("id"), "name": a.get("name"), "description": a.get("description")}
                for a in await client.list_actions(app_id)
                if a.get("actionType") == "workflow" and a.get("authoringMode") == "playbook"]

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def get_playbook(app_id: str, workflow_id: str) -> dict:
        """
        Read a playbook's authoring model (sections/steps) and variables, for editing.

        Edit the returned `playbook` and pass the whole object to update_playbook.

        Args:
            app_id: The agent / app ID.
            workflow_id: The playbook id.
        """
        action = await journeys.find_action(app_id, workflow_id)
        journey = await client.get_journey(app_id, workflow_id)
        if not journeys.is_playbook(journey):
            raise ValueError("That record is a canvas workflow; use get_workflow.")
        playbook = journeys.metadata_of(journey).get("playbook") or {"version": 1, "sections": []}
        return {"id": workflow_id, "name": action.get("name"), "description": action.get("description"),
                "playbook": playbook, "variables": journey.get("slots") or {},
                "complexity": playbook_complexity(playbook).as_dict()}

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def validate_playbook(app_id: str, playbook: dict, variables: dict | None = None) -> dict:
        """
        Compile and check a playbook without saving it. Call this before create/update_playbook.

        Returns errors (must fix: incomplete steps, IF/ELSE structure, GO_TO targets, variable
        definitions, button payloads vs values lists, unknown codehooks / knowledge / playbooks),
        design warnings (silent endings, steps after HANDOFF, IF without ELSE, loops that never wait
        for the customer, too many messages per turn) and an `outline`: the conversation step by
        step, with ⏸ where it waits for the customer. Read the outline as the customer would.

        Args:
            app_id: The agent / app ID.
            playbook: {"sections": [{"title": "...", "steps": [...]}]} — see botcircuits://playbook-schema.
            variables: Variable (slot) definitions keyed by name.
        """
        result = await check_playbook(app_id, playbook, variables or {}, {})
        return {"valid": not result["errors"], "errors": result["errors"], "warnings": result["warnings"],
                "outline": result["outline"], "complexity": result["complexity"],
                "autoAddedVariables": sorted(result["auto"])}

    @mcp.tool()
    async def create_playbook(
        app_id: str,
        name: str,
        description: str,
        playbook: dict,
        variables: dict | None = None,
        expose_as_agent_tool: bool = False,
    ) -> dict:
        """
        Create a playbook — the DEFAULT for multi-step processes (ordered steps, questions
        across turns, branching). Use a canvas workflow only above 30 steps or 20 conditions.

        Design it with the procedure in the server instructions (get_authoring_guide("playbook_design")),
        copy structure from get_authoring_guide("playbook_examples"), and run validate_playbook first.
        Nothing is saved if validation fails; the errors say exactly what to fix.

        Args:
            app_id: The agent / app ID.
            name: Display name.
            description: When the agent should run it (also the agent tool description).
            playbook: {"sections": [{"title": "...", "steps": [...]}]}; ids optional. See botcircuits://playbook-schema.
            variables: Slot definitions, e.g. {"order_id": {"dataType": "custom", "content": "order number like ORD-123", "captureFromUserInput": true}}.
            expose_as_agent_tool: Also register a type=workflow agent tool for the main agent. Ask the user first.
        """
        return await save_playbook(app_id, name, description, playbook, variables, None, expose_as_agent_tool)

    @mcp.tool()
    async def update_playbook(
        app_id: str,
        workflow_id: str,
        playbook: dict,
        name: str | None = None,
        description: str | None = None,
        variables: dict | None = None,
        expose_as_agent_tool: bool = False,
    ) -> dict:
        """
        Replace an existing playbook's steps (call get_playbook first and edit the whole model).

        Existing variables are kept; pass only new or changed definitions.

        Args:
            app_id: The agent / app ID.
            workflow_id: The playbook id.
            playbook: The complete updated playbook model.
            name: New display name (optional).
            description: New when-to-use description (optional).
            variables: New or changed slot definitions (optional).
            expose_as_agent_tool: Register/refresh its agent tool.
        """
        return await save_playbook(app_id, name or "", description or "", playbook, variables, workflow_id,
                                   expose_as_agent_tool)

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
    async def delete_playbook(app_id: str, workflow_id: str) -> dict:
        """
        Delete a playbook and any agent tool that starts it. Only when the user asked for it.

        Args:
            app_id: The agent / app ID.
            workflow_id: The playbook id.
        """
        return await delete_journey_and_tools(app_id, workflow_id)


async def delete_journey_and_tools(app_id: str, workflow_id: str) -> dict:
    await journeys.find_action(app_id, workflow_id)
    await client.delete_action(app_id, workflow_id)
    removed = []
    for tool in await client.list_prompt(app_id, "tools"):
        if (tool.get("toolData") or {}).get("workflowId") == workflow_id:
            await client.delete_prompt(app_id, "tools", tool["id"])
            removed.append(tool.get("name"))
    return {"deleted": workflow_id, "removedAgentTools": removed,
            "note": "Sub-agent capabilities that referenced it are not changed; check list_sub_agents."}
