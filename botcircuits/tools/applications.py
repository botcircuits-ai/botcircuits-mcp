"""
MCP tools for BotCircuits Application management.
"""

from typing import Optional
from mcp.server.fastmcp import FastMCP
from .. import client
from ..config import settings


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def list_applications() -> dict:
        """List all BotCircuits applications in your account."""
        return await client.list_apps()

    @mcp.tool()
    async def get_application(app_id: str) -> dict:
        """
        Get the full configuration of a BotCircuits application.

        Args:
            app_id: The application ID.
        """
        return await client.get_app(app_id)

    @mcp.tool()
    async def create_application(
        name: str,
        description: Optional[str] = None,
    ) -> dict:
        """
        Create a new BotCircuits application.

        Args:
            name: Human-readable name for the application.
            description: Optional short description of the application's purpose.
        """
        # payload: dict = {"name": name}
        # if description:
        #     payload["description"] = description
        # return await client.create_app(**payload)
        return "Currently not allow to create application through mcp. please visit https://platform.botcircuits.com/"

    @mcp.tool()
    async def delete_application(app_id: str) -> dict:
        """
        Delete a BotCircuits application and all its associated data.

        Args:
            app_id: The application ID to delete.
        """
        # return await client.delete_app(app_id)
        return "Currently not allow to delete application through mcp. please visit https://platform.botcircuits.com/"

    @mcp.tool()
    async def publish_application(app_id: str) -> dict:
        """
        Publish (deploy) an application's current configuration to production.

        Args:
            app_id: The application ID to publish.
        """
        # return await client.publish_app(app_id)
        return "Currently not allow to publish application through mcp. please visit https://platform.botcircuits.com/"

    @mcp.tool()
    async def get_application_core_settings(app_id: str) -> dict:
        """
        Get the core LLM settings for an application: system prompt (agentDescription),
        default error message, bot language, and KB top-results count.

        Args:
            app_id: The application ID.
        """
        return await client.get_agent_core_settings(app_id)

    @mcp.tool()
    async def update_application_core_settings(
        app_id: str,
        application_description: Optional[str] = None,
        default_error_message: Optional[str] = None,
        bot_language: Optional[str] = None,
        kb_top_results: Optional[int] = None,
    ) -> dict:
        """
        Update the core LLM settings for a BotCircuits application.

        Args:
            app_id: The application ID.
            application_description: System prompt / persona description for the application LLM.
            default_error_message: Message shown to users when an unexpected error occurs.
            bot_language: Language code for the application (e.g. 'en', 'fr').
            kb_top_results: Number of top results to retrieve from knowledge base RAG search.
        """
        payload: dict = {"authConfig": {}}  # required field by the backend
        if application_description is not None:
            payload["agentDescription"] = application_description
        if default_error_message is not None:
            payload["defaultErrorMessage"] = default_error_message
        if bot_language is not None:
            payload["botLanguage"] = bot_language
        if kb_top_results is not None:
            payload["kbTopResults"] = kb_top_results
        return await client.save_agent_core_settings(app_id, payload)
