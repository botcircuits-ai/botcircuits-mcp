"""
MCP tools for BotCircuits Sub-Agents.

A sub-agent is a specialist the main agent delegates to; it runs the same agent loop with
its own instructions and capabilities, and returns its result to the main agent.
Stored as an agent tool with type "sub_agent" (REST /prompt-config/tools):

  toolData.instructions  system prompt of the sub-agent
  toolData.tools         inline capabilities keyed by name:
      {"lookup_order": {"type": "function", "description": "...", "parameters": {...},
                        "toolData": {"functionId": "<codehookId>", "defaultInput": "<JSON string>"}}}
Capability types: function · kb · workflow · json (no nested sub_agent).
"""

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .. import client
from .agent_tools import normalize_default_input

_TYPE = "sub_agent"
_CAPABILITY_TYPES = {"function", "kb", "workflow", "json"}


async def _validate(app_id: str, record: dict, exclude_id: str | None = None) -> dict:
    data = record.get("toolData") or {}
    if not (data.get("instructions") or "").strip():
        raise ValueError("instructions are required")
    capabilities = data.get("tools") or {}
    hooks = workflows = None
    for cap_name, cap in capabilities.items():
        if not isinstance(cap, dict) or cap.get("type") not in _CAPABILITY_TYPES:
            raise ValueError(f"capability '{cap_name}' type must be one of {sorted(_CAPABILITY_TYPES)} "
                             "(sub-agents cannot contain sub-agents)")
        cap.setdefault("name", cap_name)
        cap_data = cap.get("toolData") or {}
        if cap["type"] == "function":
            hooks = hooks if hooks is not None else {c.get("codehookId") for c in await client.list_codehooks(app_id)}
            if cap_data.get("functionId") not in hooks:
                raise ValueError(f"capability '{cap_name}': toolData.functionId must be an existing codehook")
            cap["toolData"] = normalize_default_input(dict(cap_data), f"capability '{cap_name}': toolData")
        if cap["type"] == "workflow":
            workflows = workflows if workflows is not None else {a.get("id") for a in await client.list_actions(app_id)}
            if cap_data.get("workflowId") not in workflows:
                raise ValueError(f"capability '{cap_name}': toolData.workflowId must be an existing playbook/workflow")
    for other in await client.list_prompt(app_id, "tools"):
        if other.get("name") == record.get("name") and other.get("id") != exclude_id:
            raise ValueError(f"a tool or sub-agent named '{record.get('name')}' already exists")
    return record


def register(mcp: FastMCP) -> None:

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def list_sub_agents(app_id: str) -> list:
        """
        List the agent's sub-agents.

        Args:
            app_id: The agent / app ID.
        """
        return [{k: t.get(k) for k in ("id", "name", "description")}
                for t in await client.list_prompt(app_id, "tools") if t.get("type") == _TYPE]

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def get_sub_agent(app_id: str, sub_agent_id: str) -> dict:
        """
        Get a sub-agent's full configuration, including instructions and capabilities.

        Args:
            app_id: The agent / app ID.
            sub_agent_id: The sub-agent id.
        """
        return await client.get_prompt(app_id, "tools", sub_agent_id)

    @mcp.tool()
    async def create_sub_agent(
        app_id: str,
        name: str,
        description: str,
        instructions: str,
        capabilities: dict[str, Any] | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> dict:
        """
        Create a sub-agent the main agent delegates to. The name and description are what the
        main agent reads to decide when to delegate — make them precise.

        Capabilities are inline tools keyed by name, e.g.
          {"lookup_order": {"type": "function", "description": "Look up an order by ID",
                            "parameters": {"order_id": {"type": "string", "description": "Order ID", "required": true}},
                            "toolData": {"functionId": "api_lookup_order"}},
           "run_returns": {"type": "workflow", "description": "Start the returns playbook",
                           "toolData": {"workflowId": "<playbook id>"}}}

        Args:
            app_id: The agent / app ID.
            name: snake_case name, e.g. billing_agent.
            description: When the main agent should delegate to it.
            instructions: System prompt / persona of the sub-agent.
            capabilities: Inline capabilities (function · kb · workflow · json).
            parameters: Inputs the main agent passes when delegating.
        """
        record = await _validate(app_id, {
            "name": name, "type": _TYPE, "description": description, "parameters": parameters or {},
            "toolData": {"instructions": instructions, "tools": capabilities or {}},
        })
        return await client.create_prompt(app_id, "tools", record)

    @mcp.tool()
    async def update_sub_agent(
        app_id: str,
        sub_agent_id: str,
        name: str | None = None,
        description: str | None = None,
        instructions: str | None = None,
        capabilities: dict[str, Any] | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> dict:
        """
        Update a sub-agent; omitted fields are preserved. Supplying `capabilities` replaces the
        whole capability map — read the sub-agent first to add or change one.

        Args:
            app_id: The agent / app ID.
            sub_agent_id: The sub-agent id.
            name: New name.
            description: New delegation description.
            instructions: New system prompt.
            capabilities: New capability map (replaces existing).
            parameters: New parameters (replaces existing).
        """
        current = {k: v for k, v in (await client.get_prompt(app_id, "tools", sub_agent_id)).items()
                   if k not in ("id", "pk", "sk", "appId", "createdAt", "modifiedAt")}
        tool_data = dict(current.get("toolData") or {})
        if instructions is not None:
            tool_data["instructions"] = instructions
        if capabilities is not None:
            tool_data["tools"] = capabilities
        record = {**current, "type": _TYPE, "toolData": tool_data,
                  **{k: v for k, v in {"name": name, "description": description, "parameters": parameters}.items()
                     if v is not None}}
        return await client.update_prompt(app_id, "tools", sub_agent_id,
                                          await _validate(app_id, record, exclude_id=sub_agent_id))

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
    async def delete_sub_agent(app_id: str, sub_agent_id: str) -> dict:
        """
        Delete a sub-agent. Only when the user asked for it.

        Args:
            app_id: The agent / app ID.
            sub_agent_id: The sub-agent id.
        """
        return await client.delete_prompt(app_id, "tools", sub_agent_id)
