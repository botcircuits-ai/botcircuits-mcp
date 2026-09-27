"""
MCP tools for knowledge sources. Agents use them through a type=kb agent tool
(create_agent_tool) or a playbook RUN KNOWLEDGE step, filtered by dataSourceId.
Text sources are ready at once; web sources are crawled in the background.
"""

import re

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .. import client

_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")


async def _new_id(app_id: str, source_id: str) -> None:
    if not _ID.match(source_id):
        raise ValueError("source_id must be a lowercase slug of 3-64 chars (letters, digits, _ or -)")
    if any(d.get("dataSourceId") == source_id for d in await client.list_data_sources(app_id)):
        raise ValueError(f"knowledge source '{source_id}' already exists")


def register(mcp: FastMCP) -> None:

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def list_knowledge_sources(app_id: str) -> list:
        """
        List knowledge sources and their ingestion status (modelStatus: preparing / scheduled / ready).

        Args:
            app_id: The agent / app ID.
        """
        return [{k: d.get(k) for k in ("dataSourceId", "dataSourceType", "descriptionForModel", "modelStatus",
                                        "fileName")} for d in await client.list_data_sources(app_id)]

    @mcp.tool()
    async def add_text_knowledge(app_id: str, source_id: str, description: str, content: str) -> dict:
        """
        Add a text knowledge source from content the user supplied (policy, FAQ, product facts).
        Never write business facts yourself.

        Args:
            app_id: The agent / app ID.
            source_id: Lowercase slug, e.g. returns_policy (used in kb tools' filterKb).
            description: What it covers — the runtime uses this to choose relevant sources.
            content: The text; blank lines separate paragraphs.
        """
        await _new_id(app_id, source_id)
        await client.save_text_source(app_id, source_id, content)
        await client.register_data_source(app_id, {"dataSourceId": source_id, "dataSourceType": "text",
                                                   "dataType": "text", "descriptionForModel": description})
        return {"dataSourceId": source_id, "status": "ready"}

    @mcp.tool()
    async def add_web_knowledge(app_id: str, source_id: str, description: str, urls: list[str]) -> dict:
        """
        Crawl web pages into a knowledge source (runs in the background; check list_knowledge_sources).

        Args:
            app_id: The agent / app ID.
            source_id: Lowercase slug.
            description: What the pages cover.
            urls: http(s) pages to extract.
        """
        await _new_id(app_id, source_id)
        if not urls or any(not u.lower().startswith(("https://", "http://")) for u in urls):
            raise ValueError("urls must be a non-empty list of http(s) links")
        await client.register_data_source(app_id, {"dataSourceId": source_id, "dataSourceType": "web",
                                                   "dataType": "web", "descriptionForModel": description,
                                                   "urls": urls, "manualExtract": True})
        return {"dataSourceId": source_id, "status": "preparing"}
