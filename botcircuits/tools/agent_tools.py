"""
MCP tools for BotCircuits agent Tools (console "Tools"; REST /apps/{appId}/prompt-config/tools).

Earlier versions of this server called these "skills". The console renamed them to Tools
and added real Skills (see skills.py), so the names now match the product.

Types and toolData:
  function   runs a codehook with the model's arguments     {"functionId": "<codehookId>", "returnOriginal": false}
  kb         searches knowledge sources                      {"filterKb": ["<dataSourceId>"], "topK": 5}
  workflow   starts a playbook / workflow                    {"workflowId": "<id>"}
  json       returns fixed data                              {"json": "<JSON-encoded string>"}
  sub_agent  managed by the sub-agent tools (sub_agents.py)

Parameters are a map, not JSON Schema:
  {"order_id": {"type": "string", "description": "Order ID", "required": true}}
"""

import re
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .. import client

ToolType = Literal["function", "kb", "workflow", "json"]
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
_PARAM_TYPES = {"string", "number", "integer", "boolean", "array", "object"}
_SERVER_FIELDS = ("id", "pk", "sk", "appId", "createdAt", "modifiedAt")


async def validate_tool(app_id: str, record: dict, exclude_id: str | None = None) -> dict:
    """Check shape and references; return the normalized record. Raises ValueError."""
    name, tool_type, data = record.get("name", ""), record.get("type"), record.get("toolData") or {}
    if not _NAME.match(name or ""):
        raise ValueError("name must start with a letter/underscore and use letters, digits, _ or - (max 64)")
    if len((record.get("description") or "").strip()) < 10:
        raise ValueError("description must say what the tool does and when to use it (≥ 10 chars)")
    params = record.get("parameters") or {}
    for pname, spec in params.items():
        if not isinstance(spec, dict) or spec.get("type", "string") not in _PARAM_TYPES or not spec.get("description"):
            raise ValueError(f"parameter '{pname}' needs {{type in {sorted(_PARAM_TYPES)}, description, required}}")

    if tool_type == "function":
        hooks = {c.get("codehookId") for c in await client.list_codehooks(app_id)}
        if data.get("functionId") not in hooks:
            raise ValueError(f"toolData.functionId must be an existing codehook id; available: {sorted(filter(None, hooks))}")
    elif tool_type == "workflow":
        ids = {a.get("id") for a in await client.list_actions(app_id) if a.get("actionType") == "workflow"}
        if data.get("workflowId") not in ids:
            raise ValueError("toolData.workflowId must be an existing playbook/workflow id (list_workflows)")
        params = {}  # the journey collects its own inputs
    elif tool_type == "kb":
        filter_kb = data.setdefault("filterKb", [])
        if not isinstance(filter_kb, list):
            raise ValueError("toolData.filterKb must be a list of dataSourceIds ([] searches all)")
        known = {d.get("dataSourceId") for d in await client.list_data_sources(app_id)}
        missing = [k for k in filter_kb if k not in known]
        if missing:
            raise ValueError(f"unknown knowledge sources {missing}")
        params.setdefault("query", {"type": "string", "description": "Search query written from the user's question",
                                    "required": True})
    elif tool_type == "json":
        if not isinstance(data.get("json"), str):
            raise ValueError("toolData.json must be a JSON-encoded string")
        params = {}
    elif tool_type != "sub_agent":
        raise ValueError("type must be function, kb, workflow or json")

    for other in await client.list_prompt(app_id, "tools"):
        if other.get("name") == name and other.get("id") != exclude_id:
            raise ValueError(f"a tool named '{name}' already exists (id {other.get('id')}); update it instead")
    return {**record, "parameters": params, "toolData": data}


def register(mcp: FastMCP) -> None:

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def list_agent_tools(app_id: str) -> list:
        """
        List the agent's tools (function, kb, workflow, json). Sub-agents: list_sub_agents.

        Args:
            app_id: The agent / app ID.
        """
        return [{k: t.get(k) for k in ("id", "name", "type", "description", "toolData", "deactivate")}
                for t in await client.list_prompt(app_id, "tools") if t.get("type") != "sub_agent"]

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def get_agent_tool(app_id: str, tool_id: str) -> dict:
        """
        Get one agent tool's full configuration.

        Args:
            app_id: The agent / app ID.
            tool_id: The tool id.
        """
        return await client.get_prompt(app_id, "tools", tool_id)

    @mcp.tool()
    async def create_agent_tool(
        app_id: str,
        name: str,
        description: str,
        tool_type: ToolType,
        tool_data: dict[str, Any],
        parameters: dict[str, Any] | None = None,
    ) -> dict:
        """
        Create a tool the agent's LLM can call. Create its dependency (codehook, knowledge
        source, playbook/workflow) first. For an HTTP API prefer create_api_tool, which
        builds the codehook and the tool together.

        The name and description are all the LLM sees when choosing: say what it does, when
        to use it and what it returns.

        Args:
            app_id: The agent / app ID.
            name: snake_case runtime name, e.g. get_order_status.
            description: What it does, when to use it, what it returns.
            tool_type: function · kb · workflow · json
            tool_data: function {"functionId"} · kb {"filterKb", "topK"} · workflow {"workflowId"} · json {"json": "<string>"}
            parameters: {"arg": {"type", "description", "required"}} (ignored for workflow/json; kb gets `query`).
        """
        record = await validate_tool(app_id, {"name": name, "description": description, "type": tool_type,
                                              "parameters": parameters or {}, "toolData": tool_data})
        return await client.create_prompt(app_id, "tools", record)

    @mcp.tool()
    async def update_agent_tool(app_id: str, tool_id: str, changes: dict[str, Any]) -> dict:
        """
        Update an agent tool; fields not in `changes` are preserved.

        Args:
            app_id: The agent / app ID.
            tool_id: The tool id.
            changes: Any of name, description, type, parameters, toolData, deactivate.
        """
        current = {k: v for k, v in (await client.get_prompt(app_id, "tools", tool_id)).items()
                   if k not in _SERVER_FIELDS}
        record = await validate_tool(app_id, {**current, **changes}, exclude_id=tool_id)
        return await client.update_prompt(app_id, "tools", tool_id, record)

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
    async def delete_agent_tool(app_id: str, tool_id: str) -> dict:
        """
        Delete an agent tool. Only when the user asked for it.

        Args:
            app_id: The agent / app ID.
            tool_id: The tool id.
        """
        return await client.delete_prompt(app_id, "tools", tool_id)
