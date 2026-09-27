"""
MCP tools for the agent's remote MCP server connections (REST /prompt-config/mcp-servers).

The BotCircuits runtime connects to these servers and exposes their tools to the agent as
<server>__<tool>. Tokens are write-only: reads return the "__stored__" sentinel, and
sending it back (or omitting the field) keeps the stored token.
"""

import re
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .. import client

_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,47}$")
_FIELDS = ("name", "url", "transport", "allowedTools", "deactivate", "authorizationToken")
_SENTINEL = "__stored__"


def _validate(record: dict) -> dict:
    if not _NAME.match(record.get("name") or "") or "__" in record["name"]:
        raise ValueError("name must match ^[a-zA-Z0-9][a-zA-Z0-9_-]{0,47}$ and not contain '__'")
    if not str(record.get("url", "")).lower().startswith(("https://", "http://")):
        raise ValueError("url must be http(s); stdio servers are not supported by the runtime")
    if record.get("transport", "http") not in ("http", "sse"):
        raise ValueError("transport must be http or sse")
    return record


def register(mcp: FastMCP) -> None:

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def list_mcp_servers(app_id: str) -> list:
        """
        List the agent's MCP server connections (tokens are never returned).

        Args:
            app_id: The agent / app ID.
        """
        return [{k: s.get(k) for k in ("id", "name", "url", "transport", "allowedTools", "hasAuthorizationToken",
                                        "deactivate")} for s in await client.list_prompt(app_id, "mcp_servers")]

    @mcp.tool()
    async def create_mcp_server(
        app_id: str,
        name: str,
        url: str,
        transport: Literal["http", "sse"] = "http",
        allowed_tools: list[str] | None = None,
        authorization_token: str | None = None,
    ) -> dict:
        """
        Connect a remote MCP server to the agent. Its tools appear as <name>__<tool>.

        Only use a token the user explicitly provided for this server; never repeat it back.

        Args:
            app_id: The agent / app ID.
            name: Server name (no double underscore).
            url: Streamable-HTTP or SSE endpoint.
            transport: http or sse.
            allowed_tools: Remote tool names to expose ([] exposes all; prefer a short list).
            authorization_token: Bearer token, if the server needs one.
        """
        record: dict[str, Any] = _validate({"name": name, "url": url, "transport": transport,
                                            "allowedTools": allowed_tools or [], "deactivate": False})
        if authorization_token:
            record["authorizationToken"] = authorization_token
        result = await client.create_prompt(app_id, "mcp_servers", record)
        return {**result, "toolPrefix": f"{name}__"}

    @mcp.tool()
    async def update_mcp_server(app_id: str, server_id: str, changes: dict[str, Any]) -> dict:
        """
        Update an MCP server connection; the stored token is kept unless authorizationToken is given.

        Args:
            app_id: The agent / app ID.
            server_id: The server id.
            changes: Any of name, url, transport, allowedTools, deactivate, authorizationToken.
        """
        current = await client.get_prompt(app_id, "mcp_servers", server_id)
        record = _validate({k: v for k, v in {**current, **changes}.items() if k in _FIELDS})
        if "authorizationToken" not in changes:
            record["authorizationToken"] = _SENTINEL
        return await client.update_prompt(app_id, "mcp_servers", server_id, record)

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
    async def delete_mcp_server(app_id: str, server_id: str) -> dict:
        """
        Disconnect an MCP server. Only when the user asked for it.

        Args:
            app_id: The agent / app ID.
            server_id: The server id.
        """
        return await client.delete_prompt(app_id, "mcp_servers", server_id)
