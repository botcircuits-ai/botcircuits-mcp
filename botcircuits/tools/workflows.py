"""
MCP tools for BotCircuits canvas Workflows (journeys) and the construct-selection policy.

A workflow is a deterministic state machine drawn on the console canvas. It is reserved
for complex processes: more than 30 steps, more than 20 conditional nodes, or a step type
playbooks cannot express. Everything smaller is a playbook (see playbooks.py).

The host AI writes the intermediate JSON (botcircuits://workflow-schema); this server
transforms it (workflow/transform.py), validates it against the runtime's rules
(workflow/validator.py) and saves it.
"""

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .. import client
from ..workflow.complexity import (
    WORKFLOW_MIN_CONDITIONS,
    WORKFLOW_MIN_STEPS,
    playbook_complexity,
    recommend,
    workflow_complexity,
)
from ..workflow.transform import transform_to_platform_format
from ..workflow.validator import validate_workflow
from . import journeys
from .playbooks import delete_journey_and_tools


async def check_workflow(app_id: str, intermediate: dict, existing_slots: dict) -> dict:
    if not isinstance(intermediate, dict) or not isinstance((intermediate.get("stmDefinition") or {}).get("states"), dict):
        return {"errors": ['intermediate must be {"stmDefinition": {"startAt", "states"}, "slots": {...}}'],
                "warnings": []}
    platform = transform_to_platform_format(intermediate)
    stm = platform["stm"]
    slots = {**existing_slots, **platform["slots"]}
    check = validate_workflow(stm["stmDefinition"], slots, **await journeys.known_ids(app_id))
    return {"platform": platform, "errors": check.errors, "warnings": check.warnings, "auto": check.auto_slots,
            "complexity": workflow_complexity(stm["stmDefinition"])}


def register(mcp: FastMCP) -> None:

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def recommend_implementation(
        summary: str,
        estimated_steps: int,
        decision_points: int,
        multi_turn_input: bool,
        must_follow_fixed_order: bool,
        single_action: bool,
        guidance_only: bool,
        needs_features: list[str] | None = None,
    ) -> dict:
        """
        Decide which BotCircuits construct implements one capability. Call before building.

        Policy: fetch/act operations are agent tools (codehook or API); ordered multi-step
        processes are playbooks; canvas workflows only above 30 steps or 20 conditional
        nodes, or for playbook-unsupported steps.

        Args:
            summary: One sentence describing the capability.
            estimated_steps: Steps it needs (messages, questions, calls, assignments).
            decision_points: Branch / condition points.
            multi_turn_input: It asks the user several things across turns.
            must_follow_fixed_order: Steps must happen in an enforced order.
            single_action: One fetch/act call with arguments (e.g. get weather for a city).
            guidance_only: Know-how the model applies flexibly with existing tools.
            needs_features: Playbook-unsupported features needed, from: image_message, language_selector, auth, integration, custom_action, pause, ai_task, nested_if, attached_conditions.
        """
        return recommend(summary=summary, estimated_steps=estimated_steps, decision_points=decision_points,
                         multi_turn_input=multi_turn_input, must_follow_fixed_order=must_follow_fixed_order,
                         single_action=single_action, guidance_only=guidance_only, needs_features=needs_features)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def list_workflows(app_id: str) -> list:
        """
        List all playbooks and canvas workflows (id, name, description, authoringMode).

        Args:
            app_id: The agent / app ID.
        """
        return [{"id": a.get("id"), "name": a.get("name"), "description": a.get("description"),
                 "authoringMode": a.get("authoringMode") or "workflow"}
                for a in await client.list_actions(app_id) if a.get("actionType") == "workflow"]

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def get_workflow(app_id: str, workflow_id: str) -> dict:
        """
        Get a canvas workflow's state machine definition and slots (playbooks: use get_playbook).

        Args:
            app_id: The agent / app ID.
            workflow_id: The workflow id.
        """
        action = await journeys.find_action(app_id, workflow_id)
        journey = await client.get_journey(app_id, workflow_id)
        if journeys.is_playbook(journey):
            playbook = journeys.metadata_of(journey).get("playbook") or {}
            return {"id": workflow_id, "authoringMode": "playbook",
                    "note": "This is a playbook; read and edit it with get_playbook / update_playbook.",
                    "complexity": playbook_complexity(playbook).as_dict()}
        stm = journey.get("stm") if isinstance(journey.get("stm"), dict) else {}
        definition = stm.get("stmDefinition") or {}
        return {"id": workflow_id, "name": action.get("name"), "description": action.get("description"),
                "authoringMode": "workflow", "stmDefinition": definition, "slots": journey.get("slots") or {},
                "complexity": workflow_complexity(definition).as_dict(),
                "note": "State ids are the platform's numeric ids; you may send them back as-is in upload_workflow."}

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def validate_workflow_definition(app_id: str, intermediate: dict) -> dict:
        """
        Transform and check an intermediate workflow without saving it.

        Reports errors the runtime would otherwise fail on silently (unknown stateType, broken
        prompts, dangling next, missing slot definitions, unknown codehooks/knowledge) and the
        complexity against the workflow threshold.

        Args:
            app_id: The agent / app ID.
            intermediate: {"stmDefinition": {...}, "slots": {...}} per botcircuits://workflow-schema.
        """
        result = await check_workflow(app_id, intermediate, {})
        complexity = result.get("complexity")
        return {"valid": not result["errors"], "errors": result["errors"], "warnings": result["warnings"],
                "complexity": complexity.as_dict() if complexity else None}

    @mcp.tool()
    async def upload_workflow(
        app_id: str,
        name: str,
        description: str,
        intermediate: dict,
        workflow_id: str | None = None,
        expose_as_agent_tool: bool = False,
    ) -> dict:
        """
        Create or replace a CANVAS WORKFLOW from intermediate JSON (transform + validate + save).

        Reserved for complex processes: a NEW workflow is rejected unless it has more than 30
        steps, more than 20 conditional nodes, or needs a playbook-unsupported step (image
        message, language selector, OAuth, integration, custom action, pause, AI task).
        Use create_playbook otherwise. Editing an existing canvas workflow is always allowed.
        Nothing is saved if validation fails.

        Args:
            app_id: The agent / app ID.
            name: Workflow display name (e.g. "Loan application").
            description: When the agent should run it (also the agent tool description).
            intermediate: {"stmDefinition": {...}, "slots": {...}} per botcircuits://workflow-schema.
            workflow_id: Existing workflow id to replace (optional).
            expose_as_agent_tool: Also register a type=workflow agent tool. Ask the user first.
        """
        existing_slots: dict[str, Any] = {}
        converting = False
        if workflow_id:
            journey = await client.get_journey(app_id, workflow_id)
            existing_slots = journey.get("slots") or {}
            converting = journeys.is_playbook(journey)

        result = await check_workflow(app_id, intermediate, existing_slots)
        if result["errors"]:
            return {"ok": False, "saved": False, "errors": result["errors"], "warnings": result["warnings"]}
        complexity = result["complexity"]
        if (not workflow_id or converting) and not complexity.exceeds_playbook:
            return {"ok": False, "saved": False, "policy": "use_playbook",
                    "error": (f"This process has {complexity.steps} steps and {complexity.conditional_nodes} "
                              "conditional nodes and no playbook-unsupported steps. Build it with "
                              f"create_playbook; canvas workflows are for > {WORKFLOW_MIN_STEPS} steps or > "
                              f"{WORKFLOW_MIN_CONDITIONS} conditional nodes."),
                    "complexity": complexity.as_dict()}

        platform = result["platform"]
        new_id = await journeys.upsert_action(app_id, workflow_id, name, description, "workflow")
        await client.ensure_journey(app_id, new_id)
        slots = journeys.merge_slots(existing_slots, platform["slots"], result["auto"])
        await client.save_journey_slots(app_id, new_id, slots)
        await client.save_journey_definition(app_id, new_id, platform["stm"]["stmDefinition"],
                                             platform["stm"]["metadata"])
        exposure: dict = {"registered": False}
        if expose_as_agent_tool:
            action = await journeys.find_action(app_id, new_id)
            exposure = await journeys.expose_as_agent_tool(app_id, new_id, action.get("name") or name,
                                                           action.get("description") or description, "workflow")
        if converting:
            try:
                await client.delete_prompt(app_id, "tools", f"playbook-{new_id}")
            except RuntimeError:
                pass  # no playbook-managed tool
        return {"ok": True, "workflowId": new_id, "authoringMode": "workflow", "created": not workflow_id,
                "state_count": len(platform["stm"]["stmDefinition"]["states"]), "slot_count": len(slots),
                "warnings": result["warnings"], "complexity": complexity.as_dict(), "agentTool": exposure}

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
    async def delete_workflow(app_id: str, workflow_id: str) -> dict:
        """
        Delete a workflow (or playbook): its action, journey and the agent tools that start it.
        Only when the user asked for it.

        Args:
            app_id: The agent / app ID.
            workflow_id: The workflow id.
        """
        return await delete_journey_and_tools(app_id, workflow_id)
