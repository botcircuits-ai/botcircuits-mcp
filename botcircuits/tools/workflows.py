"""
MCP tools for BotCircuits Workflow (Journey) management.

A Workflow (called "Journey" internally) is a deterministic state-machine
that runs structured tasks without calling the LLM on every step — saving
60-70% of token cost vs a fully LLM-driven approach.

Key concepts
------------
trigger      : How the workflow is started.
               • triggerType="intent"  — LLM routes here based on user intent.
                 Requires `intent` (unique name used as journeyId).
               • triggerType="event"   — Started programmatically by named events.
                 Requires `events` list (e.g. ["action", "scheduled"]).

stateConfig  : The state machine definition produced by bc-text-to-workflow.
               Contains `stmDefinition`, `metadata`, and `slotStateMap`.

slots        : Variable definitions the workflow collects from users.
               Each slot has a dataType, content (description / regex / values),
               and optional dependencies.
"""

from typing import Any, Literal, Optional
from mcp.server.fastmcp import FastMCP
from .. import client


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def list_workflows(app_id: str) -> dict:
        """
        List all workflows (journeys) registered in a BotCircuits agent.

        Args:
            app_id: The agent / app ID.
        """
        return await client.list_journeys(app_id)

    @mcp.tool()
    async def get_workflow(app_id: str, workflow_id: str) -> dict:
        """
        Get the full configuration of a workflow, including its state machine
        definition, slots, and metadata.

        Args:
            app_id: The agent / app ID.
            workflow_id: The workflow / journey ID.
        """
        return await client.get_journey(app_id, workflow_id)

    @mcp.tool()
    async def save_workflow_stm(
        app_id: str,
        workflow_id: str,
        stm_definition: dict,
        metadata: Optional[dict] = None,
    ) -> dict:
        """
        Upload the state machine (STM) definition for a workflow.

        This is the core execution graph. It tells the BotCircuits runtime how to
        traverse states, collect slots, call APIs, and send messages.

        `stm_definition` shape:
        {
          "startAt": "<state-id>",
          "states": { "<id>": { "next": "...", "stateConfig": {...} }, ... }
        }

        `metadata` is the ReactFlow visual graph:
        { "nodes": [...], "edges": [...] }

        Prefer upload_workflow for a one-shot create (transform + save STM + save slots).

        Args:
            app_id: The agent / app ID.
            workflow_id: The workflow / journey ID.
            stm_definition: The stmDefinition object (startAt + states).
            metadata: ReactFlow visual metadata (nodes + edges). Pass {} if not available.
        """
        return await client.save_journey_definition(
            app_id,
            workflow_id,
            stm_definition,
            metadata or {"nodes": [], "edges": []},
        )

    @mcp.tool()
    async def save_workflow_slots(
        app_id: str,
        workflow_id: str,
        slots: dict,
    ) -> dict:
        """
        Upload slot definitions for a workflow.

        Slots are the variables collected during workflow execution. Each slot
        has a name (key), dataType, and content that guides extraction/validation.

        Slot dataType reference:
          custom   — AI entity extraction; content = description of what to extract
          regex    — Regex validation; content = pattern (e.g. "^ORD-\\d{6}$")
          values   — Allowed values; content = comma-separated list
          number   — Numeric; content = optional "min-max" range hint
          boolean  — Yes/no
          email    — Email address
          age      — Age value
          date     — Date; content = optional format hint (e.g. "YYYY-MM-DD")
          datetime — Date+time
          any      — Unvalidated free text

        Example slots argument:
        {
          "customer_name": {
            "slot": "customer_name",
            "displayText": "Customer Name",
            "captureFromUserInput": true,
            "dataType": "custom",
            "content": "The customer's full name",
            "dependencies": []
          },
          "issue_type": {
            "slot": "issue_type",
            "captureFromUserInput": true,
            "dataType": "values",
            "content": "billing,technical,general",
            "dependencies": []
          }
        }

        Args:
            app_id: The agent / app ID.
            workflow_id: The workflow / journey ID.
            slots: Dict of slot name → slot definition object.
        """
        return await client.save_journey_slots(app_id, workflow_id, slots)

    @mcp.tool()
    async def delete_workflow(app_id: str, workflow_id: str) -> dict:
        """
        Delete a workflow and all its state machine / slot definitions.

        Args:
            app_id: The agent / app ID.
            workflow_id: The workflow / journey ID to delete.
        """
        return await client.delete_journey(app_id, workflow_id)