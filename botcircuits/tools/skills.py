"""
MCP operations for BotCircuits Skills.

A Skill is a capability registered on an agent that the agent's LLM can invoke
during conversations. The platform UI labels these "Skills". The underlying
REST path is /apps/{appId}/prompt-config/tools — this module uses the "skill"
term exclusively.

Skill types
-----------
workflow  Triggers a deterministic BotCircuits workflow (state machine). The LLM
          routes to this skill when it detects matching user intent. toolData must
          include { "workflowId": "<workflow_id>" }.

function  Calls an external Lambda (codehook) or HTTP webhook. toolData holds the
          codehook/webhook configuration.

kb        Queries a knowledge base (RAG). The LLM provides a search query and the
          retrieved answer is returned as context. toolData holds KB identifiers.

json      Returns a static or templated JSON payload. Useful for injecting
          structured data into the conversation without a live API call.

Parameters
----------
Each skill can declare input parameters (except workflow and kb types).
Parameters are a dict keyed by parameter name:
  {
    "order_id": { "type": "string", "description": "The order ID", "required": true },
    "limit":    { "type": "number", "description": "Max results",  "required": false }
  }
Valid types: string · number · boolean · array · object
"""

from typing import Any, Literal, Optional
from mcp.server.fastmcp import FastMCP
from .. import client


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def list_skills(app_id: str) -> list:
        """
        List all skills registered on an agent.

        Returns every skill regardless of type (workflow, function, kb, json).

        Args:
            app_id: The agent / app ID.
        """
        return await client.list_skills(app_id)

    @mcp.tool()
    async def get_skill(app_id: str, skill_id: str) -> dict:
        """
        Get the full configuration of a specific skill.

        Args:
            app_id:   The agent / app ID.
            skill_id: The skill ID.
        """
        return await client.get_skill(app_id, skill_id)

    @mcp.tool()
    async def create_skill(
        app_id: str,
        name: str,
        description: str,
        skill_type: Literal["workflow", "function", "kb", "json"],
        parameters: Optional[dict[str, Any]] = None,
        skill_data: Optional[dict[str, Any]] = None,
    ) -> dict:
        """
        Create a new skill on an agent.

        The skill's name and description are what the agent LLM reads at runtime
        to decide when to invoke it — write them as clear, specific instructions.

        Skill types:
          workflow  Triggers a BotCircuits workflow (journey).
                    skill_data: { "workflowId": "<workflow_id>" }

          function  Calls a Lambda (codehook) or HTTP webhook.
                    skill_data: codehook/webhook configuration dict.

          kb        Queries a knowledge base via RAG search.
                    skill_data: knowledge base identifiers.

          json      Returns a static JSON payload.
                    skill_data: the JSON payload to return.

        Parameters are ignored for workflow and kb types.

        Example parameters dict:
          {
            "order_id": { "type": "string",  "description": "Order ID to look up", "required": true },
            "format":   { "type": "string",  "description": "Response format",     "required": false }
          }

        Args:
            app_id:      The agent / app ID.
            name:        Skill identifier in snake_case (e.g. "get_order_status").
            description: Natural-language description of what this skill does and
                         when the agent should call it.
            skill_type:  One of: workflow · function · kb · json
            parameters:  Dict of parameter definitions (optional).
            skill_data:  Type-specific configuration (optional).
        """
        payload: dict[str, Any] = {
            "name": name,
            "type": skill_type,
            "description": description,
            "parameters": parameters or {},
            "toolData": skill_data or {},
        }
        return await client.create_skill(app_id, payload)

    @mcp.tool()
    async def update_skill(
        app_id: str,
        skill_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        skill_type: Optional[Literal["workflow", "function", "kb", "json"]] = None,
        parameters: Optional[dict[str, Any]] = None,
        skill_data: Optional[dict[str, Any]] = None,
    ) -> dict:
        """
        Update an existing skill.

        Fetches the current skill first and merges only the fields you supply,
        so omitted fields are preserved as-is.

        Args:
            app_id:      The agent / app ID.
            skill_id:    The skill ID to update.
            name:        New skill name (optional).
            description: New description (optional).
            skill_type:  New type (optional).
            parameters:  New parameters dict — replaces the entire parameters
                         object when supplied (optional).
            skill_data:  New type-specific configuration (optional).
        """
        current = await client.get_skill(app_id, skill_id)
        payload: dict[str, Any] = {
            "name":        name        if name        is not None else current.get("name", ""),
            "type":        skill_type  if skill_type  is not None else current.get("type", ""),
            "description": description if description is not None else current.get("description", ""),
            "parameters":  parameters  if parameters  is not None else current.get("parameters", {}),
            "toolData":    skill_data  if skill_data  is not None else current.get("toolData", {}),
        }
        return await client.update_skill(app_id, skill_id, payload)

    @mcp.tool()
    async def delete_skill(app_id: str, skill_id: str) -> dict:
        """
        Delete a skill from an agent.

        Args:
            app_id:   The agent / app ID.
            skill_id: The skill ID to delete.
        """
        return await client.delete_skill(app_id, skill_id)
