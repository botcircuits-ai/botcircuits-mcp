"""
MCP tools for codehooks (the app's serverless functions) and API-backed agent tools.

Per the construct-selection guide, fetch/act operations are TOOLS: an HTTP API becomes a
codehook generated from a spec (create_api_tool) plus a type=function agent tool. The
same codehook also works as a RUN FUNCTION step in a playbook or a codehook action in a
workflow — both callers pass {context, slots, defaultInput}.

Deploying a codehook: save config -> presigned upload URL -> PUT zip -> deploy.
"""

import io
import re
import zipfile
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .. import client
from ..workflow.codegen import env_references, generate_http_codehook
from .agent_tools import validate_tool

_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{2,63}$")
Runtime = Literal["nodejs20.x", "nodejs22.x", "python3.12", "python3.13"]


def _zip(source: str, runtime: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("index.py" if runtime.startswith("python") else "index.js", source)
    return buffer.getvalue()


def _stored_env(hook: dict | None) -> dict:
    hook = hook or {}
    return hook.get("environmentVariables") or (hook.get("functionInfo") or {}).get("environmentVariables") or {}


async def deploy(app_id: str, codehook_id: str, source: str, runtime: str, environment: dict | None) -> dict:
    if not _ID.match(codehook_id):
        raise ValueError("codehook_id must start with a letter and use 3-64 letters, digits, _ or -")
    if runtime.startswith("python") and "def handler" not in source:
        raise ValueError("Python codehooks must define handler(event, context) in index.py")
    if runtime.startswith("nodejs") and "handler" not in source:
        raise ValueError("Node codehooks must export `handler` from index.js")
    existing = next((c for c in await client.list_codehooks(app_id) if c.get("codehookId") == codehook_id), None)
    if environment is None:
        environment = _stored_env(existing)  # keep stored variables on redeploy
    await client.save_codehook_config(app_id, {
        "codehookId": codehook_id, "codehookType": "fullfillment", "runtime": runtime,  # sic: backend spelling
        "handler": "index.handler", "codeFrom": "archive", "inlineCode": "", "environmentVariables": environment,
    })
    url = await client.codehook_upload_url(app_id, codehook_id)
    await client.upload(url, _zip(source, runtime), "application/zip")
    await client.deploy_codehook(app_id, codehook_id)
    return {"codehookId": codehook_id, "runtime": runtime, "created": existing is None,
            "environmentVariables": sorted(environment)}


def register(mcp: FastMCP) -> None:

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def list_codehooks(app_id: str) -> list:
        """
        List the app's codehooks (serverless functions).

        Args:
            app_id: The agent / app ID.
        """
        return [{k: c.get(k) for k in ("codehookId", "runtime", "codehookType", "status")}
                for c in await client.list_codehooks(app_id)]

    @mcp.tool()
    async def create_api_tool(
        app_id: str,
        name: str,
        description: str,
        method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"],
        url: str,
        parameters: dict[str, Any],
        query: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        body_template: str | None = None,
        result_path: str = "",
        slot_mapping: dict[str, str] | None = None,
        environment: dict[str, str] | None = None,
    ) -> dict:
        """
        Turn one HTTP API call into an agent tool: generates + deploys a codehook, then creates
        a type=function tool. The preferred way to give the agent a fetch/act capability.

        `{param}` placeholders are filled from the tool's arguments (or playbook variables);
        `{env.NAME}` from `environment`. Never put literal secrets in url/headers/body.
        The codehook (api_<name>) is reusable as a RUN FUNCTION step in playbooks.

        Args:
            app_id: The agent / app ID.
            name: snake_case tool name, e.g. get_weather.
            description: What it does, when to use it, what it returns.
            method: HTTP method.
            url: URL with placeholders, e.g. https://api.example.com/orders/{order_id}.
            parameters: Tool arguments: {"order_id": {"type": "string", "description": "...", "required": true}}.
            query: Query parameters (values may use placeholders).
            headers: Headers, e.g. {"Authorization": "Bearer {env.ORDERS_TOKEN}"}.
            body_template: JSON body as a string with placeholders (non-GET).
            result_path: Dotted path of the response to return, e.g. data.items.
            slot_mapping: For playbook/workflow use: {"order_status": "data.status"} sets variables.
            environment: Values for every {env.NAME} (secrets the user provided). Omit to keep stored ones.
        """
        # Cheap checks first, so a bad name never leaves a deployed codehook behind.
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_-]{0,59}$", name):
            raise ValueError("name must start with a letter/underscore and use letters, digits, _ or - (max 60)")
        if len(description.strip()) < 10:
            raise ValueError("description must say what the tool does and when to use it")
        codehook_id = f"api_{name}"
        needed =env_references(url, body_template, *(query or {}).values(), *(headers or {}).values())
        existing = next((c for c in await client.list_codehooks(app_id) if c.get("codehookId") == codehook_id), None)
        available = set(environment or {}) | (set(_stored_env(existing)) if environment is None else set())
        missing = sorted(needed - available)
        if missing:
            raise ValueError(f"Provide environment values for {missing} — ask the user, never invent them")
        source = generate_http_codehook(
            method=method, url=url, query=query, headers=headers, body_template=body_template,
            required_inputs=[k for k, p in parameters.items() if isinstance(p, dict) and p.get("required")],
            result_path=result_path, slot_mapping=slot_mapping)
        deployed = await deploy(app_id, codehook_id, source, "nodejs20.x", environment)

        tools = await client.list_prompt(app_id, "tools")
        current = next((t for t in tools if t.get("name") == name), None)
        record = await validate_tool(app_id, {"name": name, "description": description, "type": "function",
                                              "parameters": parameters, "toolData": {"functionId": codehook_id}},
                                     exclude_id=current.get("id") if current else None)
        if current:
            await client.update_prompt(app_id, "tools", current["id"], record)
            tool_id = current["id"]
        else:
            tool_id = (await client.create_prompt(app_id, "tools", record)).get("id")
        return {"toolId": tool_id, "toolName": name, "codehook": deployed,
                "note": "Test it in a conversation; the API contract and credentials are unverified until then."}

    @mcp.tool()
    async def deploy_codehook(
        app_id: str,
        codehook_id: str,
        source: str,
        runtime: Runtime = "nodejs20.x",
        environment: dict[str, str] | None = None,
    ) -> dict:
        """
        Create or update a codehook with custom code (logic an API spec cannot express).

        The handler receives {context: {appId, sessionId, inputText, ...}, slots: {...tool
        arguments or playbook/workflow variables}, defaultInput: <parsed static JSON configured on the
        function tool's toolData.defaultInput or the step's defaultInput>}. Return {slots: {...}} to set
        workflow variables; a {type, content} message ends the workflow turn. Read secrets from
        process.env / os.environ — never hardcode them. Then use it from a function tool
        (create_agent_tool) or a playbook RUN FUNCTION step.

        Args:
            app_id: The agent / app ID.
            codehook_id: Stable id, e.g. normalize_order.
            source: Full index.js (exports.handler) or index.py (def handler(event, context)).
            runtime: Lambda runtime.
            environment: Environment variables; omit to keep the stored ones.
        """
        return await deploy(app_id, codehook_id, source, runtime, environment)

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
    async def delete_codehook(app_id: str, codehook_id: str) -> dict:
        """
        Delete a codehook. Refuses while an agent tool uses it. Only when the user asked.

        Args:
            app_id: The agent / app ID.
            codehook_id: The codehook id.
        """
        users = [t.get("name") for t in await client.list_prompt(app_id, "tools")
                 if (t.get("toolData") or {}).get("functionId") == codehook_id]
        if users:
            raise ValueError(f"codehook is used by agent tools {users}; delete or repoint them first")
        await client.delete_codehook(app_id, codehook_id)
        return {"deleted": codehook_id, "note": "Playbook/workflow steps that call it will now fail; check them."}
