"""
MCP tools for BotCircuits applications (agents): overview, core settings and the global
instructions of prompt-based agents.
"""

import asyncio
import re
from typing import Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .. import client
from ..guides import SCOPE_TEMPLATE

_CONSOLE = "https://platform.botcircuits.com/"

_SCOPE_HEADING = re.compile(r"^#{1,4}\s*scope\b", re.IGNORECASE | re.MULTILINE)
_OUT_OF_SCOPE_RULE = re.compile(r"out[- ]of[- ]scope|outside (of )?(this|your|the) scope", re.IGNORECASE)


def scope_status(prompt: str) -> dict:
    """Whether the global prompt bounds the agent to the business's domain (see guides.AGENT_SCOPE)."""
    prompt = prompt or ""
    status = {"scopeDefined": bool(_SCOPE_HEADING.search(prompt) and _OUT_OF_SCOPE_RULE.search(prompt))}
    if not status["scopeDefined"]:
        status["scopeNote"] = ("The agent is not scoped: it will engage with any request (e.g. a plumbing agent "
                               "taking a pizza order). Establish the business scope with the user and write "
                               "instructions with a '## Scope' section before building anything else "
                               "(get_authoring_guide(\"scope\")).")
    return status


def register(mcp: FastMCP) -> None:

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def list_applications() -> list | dict:
        """List all BotCircuits applications (agents) in your account."""
        return await client.list_apps()

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def get_application(app_id: str) -> dict:
        """
        Get an application's metadata (name, appMode, llm settings).

        Args:
            app_id: The application ID.
        """
        return await client.get_app(app_id)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def get_application_overview(app_id: str) -> dict:
        """
        Read the whole agent configuration in one call: app metadata, global instructions,
        core settings, agent tools, sub-agents, skills, MCP servers, playbooks/workflows,
        codehooks and knowledge sources (ids and names). Call this before planning changes.

        Args:
            app_id: The application ID.
        """
        calls = {
            "app": client.get_app(app_id),
            "instructions": client.get_instructions(app_id),
            "settings": client.get_agent_core_settings(app_id),
            "tools": client.list_prompt(app_id, "tools"),
            "skills": client.list_prompt(app_id, "skills"),
            "mcpServers": client.list_prompt(app_id, "mcp_servers"),
            "actions": client.list_actions(app_id),
            "codehooks": client.list_codehooks(app_id),
            "knowledge": client.list_data_sources(app_id),
        }
        values = dict(zip(calls, await asyncio.gather(*calls.values(), return_exceptions=True), strict=True))
        if isinstance(values["app"], Exception):
            raise values["app"]
        errors = {k: str(v) for k, v in values.items() if isinstance(v, Exception)}
        get = lambda k: [] if k in errors else values[k]  # noqa: E731
        app = values["app"] or {}
        prompt = ({} if "instructions" in errors else values["instructions"] or {}).get("globalPrompt") or ""
        tools = get("tools")
        return {
            "app": {k: app.get(k) for k in ("appId", "name", "description", "appMode")},
            "promptBased": app.get("appMode") == "prompt_based",
            "globalPrompt": prompt[:3000] + ("…" if len(prompt) > 3000 else ""),
            **scope_status(prompt),
            "settings": {} if "settings" in errors else values["settings"],
            "agentTools": [{k: t.get(k) for k in ("id", "name", "type", "toolData")}
                           for t in tools if t.get("type") != "sub_agent"],
            "subAgents": [{k: t.get(k) for k in ("id", "name", "description")} for t in tools if t.get("type") == "sub_agent"],
            "skills": [{k: s.get(k) for k in ("id", "name", "description")} for s in get("skills")],
            "mcpServers": [{k: s.get(k) for k in ("id", "name", "url", "allowedTools")} for s in get("mcpServers")],
            "workflows": [{"id": a.get("id"), "name": a.get("name"), "authoringMode": a.get("authoringMode") or "workflow"}
                          for a in get("actions") if a.get("actionType") == "workflow"],
            "codehooks": [c.get("codehookId") for c in get("codehooks")],
            "knowledgeSources": [{k: d.get(k) for k in ("dataSourceId", "dataSourceType", "modelStatus")}
                                 for d in get("knowledge")],
            "readErrors": errors,
        }

    @mcp.tool()
    async def create_application(name: str, description: str = "", allow_duplicate_name: bool = False) -> dict:
        """
        Create a new BotCircuits application (agent). Always created as a prompt-based agent.

        Only call this after the user chose to create a NEW application (see "Choosing the
        application" in the server instructions) and confirmed the name. Never retry blindly:
        if the call fails or times out, call list_applications first — it may have been created.

        Args:
            name: Application name, e.g. "Acme Support".
            description: Which business the agent serves and what it helps customers with, e.g.
                "Acme Plumbing: books plumber visits and answers questions about plumbing services".
                Used to set the agent's scope.
            allow_duplicate_name: Create even if an application with this name exists (only when the user says so).
        """
        name = name.strip()
        if not name or len(name) > 120:
            raise ValueError("name must be 1-120 characters")
        existing = await client.list_apps()
        apps = existing if isinstance(existing, list) else client._as_list(existing)
        same = [a for a in apps if str(a.get("name", "")).strip().lower() == name.lower()]
        if same and not allow_duplicate_name:
            return {"created": False,
                    "error": f"An application named '{name}' already exists (appId {same[0].get('appId')}). "
                             "Ask the user whether to use it or create another with a different name."}
        try:
            result = await client.create_app(name, description.strip())
        except RuntimeError as exc:
            if "error 401" in str(exc) or "error 403" in str(exc):
                raise RuntimeError("The access token cannot create applications. Creating an app needs an "
                                   "account-level access key (not an app key). Create one in the console under "
                                   f"Settings → Access Keys ({_CONSOLE}), or create the app in the console.") from exc
            raise
        app_id = result.get("appId")
        if not app_id:
            raise RuntimeError("The API did not return an appId; call list_applications before retrying.")
        return {"created": True, "appId": app_id, "name": name, "appMode": client.APP_MODE_PROMPT_BASED,
                "next": "Default settings and the runtime access key are provisioned in the background "
                        "(a few seconds). Use this appId for every following call. Before building "
                        "anything, write the agent's '## Scope' instructions (get_authoring_guide(\"scope\"))."}

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
    async def delete_application(app_id: str) -> str:
        """
        Delete an application. Not available through MCP yet.

        Args:
            app_id: The application ID.
        """
        return f"Deleting applications through MCP is not allowed yet. Please use {_CONSOLE}"

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def get_application_core_settings(app_id: str) -> dict:
        """
        Get core settings: agentDescription, defaultErrorMessage, botLanguage, kbTopResults,
        vectorSearch, defaultWorkflow.

        Args:
            app_id: The application ID.
        """
        return await client.get_agent_core_settings(app_id)

    @mcp.tool()
    async def update_application_core_settings(
        app_id: str,
        application_description: str | None = None,
        default_error_message: str | None = None,
        bot_language: str | None = None,
        kb_top_results: int | None = None,
        vector_search: bool | None = None,
    ) -> dict:
        """
        Update core settings. Unspecified fields — including the stored authConfig and
        defaultWorkflow — are preserved. For a prompt-based agent's system prompt use
        update_agent_instructions instead.

        Args:
            app_id: The application ID.
            application_description: Agent description (agentDescription).
            default_error_message: Message shown when an unexpected error occurs.
            bot_language: Bot language, e.g. 'english'.
            kb_top_results: Number of knowledge-base results retrieved per search.
            vector_search: Enable vector search.
        """
        changes = {k: v for k, v in {
            "agentDescription": application_description, "defaultErrorMessage": default_error_message,
            "botLanguage": bot_language, "kbTopResults": kb_top_results, "vectorSearch": vector_search,
        }.items() if v is not None}
        return await client.save_agent_core_settings(app_id, changes)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def get_agent_instructions(app_id: str) -> dict:
        """
        Read a prompt-based agent's global instructions (globalPrompt, voice prompt, default message).

        Args:
            app_id: The application ID.
        """
        return await client.get_instructions(app_id)

    @mcp.tool()
    async def update_agent_instructions(app_id: str, global_prompt: str,
                                        mode: Literal["replace", "append"] = "replace") -> dict:
        """
        Write a prompt-based agent's global instructions (its system prompt).

        The resulting prompt must open with a `## Scope` section that names the business,
        lists what the agent helps with and says how it declines out-of-scope requests (the
        runtime has no other topic guard; prompts without one are refused — see
        get_authoring_guide("scope")). Then state role, tone, when to use each tool / skill /
        playbook (by runtime name), how to handle missing inputs, grounding rules and human
        handoff. Keep it short; details belong in skills and tool descriptions.

        Args:
            app_id: The application ID.
            global_prompt: The instruction text.
            mode: "replace" overwrites; "append" adds a paragraph after the current prompt.
        """
        text = global_prompt.strip()
        if not text:
            raise ValueError("global_prompt cannot be empty")
        if mode == "append":
            current = (await client.get_instructions(app_id)).get("globalPrompt") or ""
            text = f"{current.rstrip()}\n\n{text}" if current.strip() else text
        if not scope_status(text)["scopeDefined"]:
            return {"saved": False,
                    "error": "Instructions were not saved: they need a '## Scope' section that names the "
                             "business, lists what the agent helps with and tells it to decline out-of-scope "
                             "requests without answering, asking follow-up questions or starting any tool or "
                             "playbook. If you do not know the business scope, ask the user. Put this "
                             "section first:\n\n" + SCOPE_TEMPLATE}
        await client.save_instructions(app_id, {"globalPrompt": text})
        return {"saved": True, "globalPromptLength": len(text)}
