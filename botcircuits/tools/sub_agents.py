"""
MCP operations for BotCircuits Sub-Agents.

A Sub-Agent is a specialised, focused agent the Main Agent delegates tasks to
during conversations. The Main Agent's LLM reads the sub-agent's name and
description to decide when to hand off.

REST endpoint: /apps/{appId}/prompt-config/tools  (type = "sub_agent")

Each sub-agent has:
  name         — snake_case identifier the LLM uses to route, e.g. "billing_agent"
  description  — when the Main Agent should delegate to this sub-agent
  instructions — system prompt / persona for the sub-agent's own LLM
  capabilities — named capabilities the sub-agent can invoke (each with its own
                 type, description, parameters, and configuration)
  parameters   — inputs the Main Agent passes when it delegates

Capability types available inside a sub-agent
----------------------------------------------
workflow  Triggers a BotCircuits workflow.  configuration: { "workflowId": "<id>" }
function  Calls a Lambda (codehook) or HTTP webhook.
kb        Queries a knowledge base via RAG.
json      Returns a static JSON payload.

capabilities dict shape
-----------------------
{
  "lookup_order": {
    "name": "lookup_order",
    "type": "function",
    "description": "Look up an order by ID",
    "parameters": {
      "order_id": { "type": "string", "description": "Order ID", "required": true }
    },
    "toolData": {}
  },
  "check_policy": {
    "name": "check_policy",
    "type": "kb",
    "description": "Search the returns policy knowledge base",
    "parameters": {},
    "toolData": {}
  }
}
"""

from typing import Any, Optional
from mcp.server.fastmcp import FastMCP
from .. import client

_TYPE = "sub_agent"


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def list_sub_agents(app_id: str) -> list:
        """
        List all sub-agents registered on an agent.

        Args:
            app_id: The agent / app ID.
        """
        all_entries = await client.list_skills(app_id)
        return [e for e in (all_entries or []) if e.get("type") == _TYPE]

    @mcp.tool()
    async def get_sub_agent(app_id: str, sub_agent_id: str) -> dict:
        """
        Get the full configuration of a sub-agent, including its instructions
        and capabilities.

        Args:
            app_id:        The agent / app ID.
            sub_agent_id:  The sub-agent ID.
        """
        return await client.get_skill(app_id, sub_agent_id)

    @mcp.tool()
    async def create_sub_agent(
        app_id: str,
        name: str,
        description: str,
        instructions: str,
        capabilities: Optional[dict[str, Any]] = None,
        parameters: Optional[dict[str, Any]] = None,
    ) -> dict:
        """
        Create a new sub-agent on an agent.

        The name and description are what the Main Agent LLM reads to decide when
        to delegate — write them as precise, domain-specific criteria.

        The instructions field is the system prompt that scopes the sub-agent's
        LLM to its specialised domain.

        Capabilities are the things the sub-agent can do. Each entry is keyed by
        capability name:
          {
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

        Capability types: workflow · function · kb · json

        Parameters are the inputs the Main Agent passes when it delegates:
          {
            "customer_id": { "type": "string", "description": "Customer ID", "required": true }
          }

        Args:
            app_id:        The agent / app ID.
            name:          Sub-agent name in snake_case (e.g. "billing_agent").
            description:   When the Main Agent should delegate to this sub-agent.
            instructions:  System prompt / persona for the sub-agent's LLM.
            capabilities:  Named capabilities the sub-agent can invoke (optional).
            parameters:    Inputs the Main Agent passes on delegation (optional).
        """
        payload: dict[str, Any] = {
            "name": name,
            "type": _TYPE,
            "description": description,
            "parameters": parameters or {},
            "toolData": {
                "instructions": instructions,
                "tools": capabilities or {},
            },
        }
        return await client.create_skill(app_id, payload)

    @mcp.tool()
    async def update_sub_agent(
        app_id: str,
        sub_agent_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        instructions: Optional[str] = None,
        capabilities: Optional[dict[str, Any]] = None,
        parameters: Optional[dict[str, Any]] = None,
    ) -> dict:
        """
        Update an existing sub-agent.

        Fetches the current record first and merges only the fields you supply —
        omitted fields are preserved. Supplying capabilities replaces the entire
        capabilities dict; fetch the sub-agent first if you only want to add one.

        Args:
            app_id:        The agent / app ID.
            sub_agent_id:  The sub-agent ID to update.
            name:          New name (optional).
            description:   New delegation description (optional).
            instructions:  New sub-agent system prompt (optional).
            capabilities:  New capabilities dict — replaces existing (optional).
            parameters:    New parameters dict — replaces existing (optional).
        """
        current = await client.get_skill(app_id, sub_agent_id)
        current_tool_data = current.get("toolData", {})

        payload: dict[str, Any] = {
            "name":        name        if name        is not None else current.get("name", ""),
            "type":        _TYPE,
            "description": description if description is not None else current.get("description", ""),
            "parameters":  parameters  if parameters  is not None else current.get("parameters", {}),
            "toolData": {
                "instructions": instructions if instructions is not None
                                else current_tool_data.get("instructions", ""),
                "tools": capabilities if capabilities is not None
                         else current_tool_data.get("tools", {}),
            },
        }
        return await client.update_skill(app_id, sub_agent_id, payload)

    @mcp.tool()
    async def delete_sub_agent(app_id: str, sub_agent_id: str) -> dict:
        """
        Delete a sub-agent from an agent.

        Args:
            app_id:        The agent / app ID.
            sub_agent_id:  The sub-agent ID to delete.
        """
        return await client.delete_skill(app_id, sub_agent_id)
