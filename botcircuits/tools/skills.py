"""
MCP tools for BotCircuits Skills (REST /apps/{appId}/prompt-config/skills).

A skill is on-demand instructions: the runtime model sees only its name and description,
and loads the body when the situation matches. Use skills for flexible procedures that
orchestrate existing tools; use a playbook when steps must be enforced in order.

  name                    lowercase slug, exposed to the model as skill_<name>
  description             when to use it — the model's only trigger signal
  body                    markdown procedure; {slot} placeholders are filled from the conversation
  allowedTools            runtime tool names the skill uses (guidance, not a permission boundary)
  disableModelInvocation  hide from the model (drafts, workflow-only skills)

(Earlier versions of this server used "skill" for agent tools; those are now *_agent_tool.)
"""

import re
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .. import client

_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_FIELDS = ("name", "description", "body", "allowedTools", "disableModelInvocation", "deactivate")


async def _validate(app_id: str, record: dict, exclude_id: str | None = None) -> dict:
    if not _NAME.match(record.get("name") or ""):
        raise ValueError("skill name must be a lowercase slug: ^[a-z0-9][a-z0-9-]{0,63}$ (e.g. process-refund)")
    if len((record.get("description") or "").strip()) < 10:
        raise ValueError("description must say when to use the skill")
    if len((record.get("body") or "").strip()) < 20:
        raise ValueError("body must contain the procedure")
    for other in await client.list_prompt(app_id, "skills"):
        if other.get("name") == record["name"] and other.get("id") != exclude_id:
            raise ValueError(f"a skill named '{record['name']}' already exists (id {other.get('id')})")
    record.setdefault("allowedTools", [])
    record.setdefault("disableModelInvocation", False)
    return record


def register(mcp: FastMCP) -> None:

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def list_skills(app_id: str) -> list:
        """
        List the agent's skills (id, name, description, allowedTools).

        Args:
            app_id: The agent / app ID.
        """
        return [{k: s.get(k) for k in ("id", "name", "description", "allowedTools", "disableModelInvocation",
                                        "deactivate")} for s in await client.list_prompt(app_id, "skills")]

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def get_skill(app_id: str, skill_id: str) -> dict:
        """
        Get one skill including its body.

        Args:
            app_id: The agent / app ID.
            skill_id: The skill id.
        """
        return await client.get_prompt(app_id, "skills", skill_id)

    @mcp.tool()
    async def create_skill(
        app_id: str,
        name: str,
        description: str,
        body: str,
        allowed_tools: list[str] | None = None,
        disable_model_invocation: bool = False,
    ) -> dict:
        """
        Create a skill: instructions the runtime model loads when relevant.

        Write the body as: goal, inputs to gather, numbered steps naming exact runtime tool
        names (MCP tools as <server>__<tool>), failure handling, and what "done" looks like.
        Skills cannot run code.

        Args:
            app_id: The agent / app ID.
            name: Lowercase slug, e.g. process-refund.
            description: When to use it, e.g. "Use when a customer asks for money back on a delivered order".
            body: Markdown procedure.
            allowed_tools: Runtime tool names the skill uses.
            disable_model_invocation: Hide from the model.
        """
        record = await _validate(app_id, {"name": name, "description": description, "body": body,
                                          "allowedTools": allowed_tools or [],
                                          "disableModelInvocation": disable_model_invocation})
        result = await client.create_prompt(app_id, "skills", record)
        return {**result, "runtimeName": f"skill_{name}"}

    @mcp.tool()
    async def update_skill(app_id: str, skill_id: str, changes: dict[str, Any]) -> dict:
        """
        Update a skill; fields not in `changes` are preserved.

        Args:
            app_id: The agent / app ID.
            skill_id: The skill id.
            changes: Any of name, description, body, allowedTools, disableModelInvocation, deactivate.
        """
        current = await client.get_prompt(app_id, "skills", skill_id)
        record = {k: v for k, v in {**current, **changes}.items() if k in _FIELDS}
        return await client.update_prompt(app_id, "skills", skill_id, await _validate(app_id, record, skill_id))

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
    async def delete_skill(app_id: str, skill_id: str) -> dict:
        """
        Delete a skill. Only when the user asked for it.

        Args:
            app_id: The agent / app ID.
            skill_id: The skill id.
        """
        return await client.delete_prompt(app_id, "skills", skill_id)
