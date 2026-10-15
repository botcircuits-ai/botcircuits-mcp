"""
MCP tools for publishing an agent and connecting it to channels, as the console's Access keys
and Versions pages do (botcircuits-platform services/app/app-access-key-service.js and
app-service.js).

Mirrors publish_tools.py of botcircuits-agent-builder-copilot-native; keep them in sync. The
copilot returns a chat card; here the same outcome comes back as data for the host to relay.

The new key is a Live, runtime-scope key: it can only chat with the published agent, which is
what a website embeds anyway. Its value is shown once (the backend cannot return it again), so a
new key is created on every publish rather than reused.
"""

from datetime import UTC, datetime

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .. import client

_CONSOLE = "https://platform.botcircuits.com"
# Same snippet as the console's Channels -> Web -> Install page (web-script-snippt.jsx).
WEB_SNIPPET = ("<script src=\"https://web-plugin.botcircuits.com/plugin/sdk.min.js\"></script>\n"
               "<script>\n    botcircuits_init('{app_id}', '{key}')\n</script>")
KEY_ID_PREFIX = "website-"
RELEASE_NOTE = "Published from BotCircuits MCP."
CHANNELS = {
    "website": "channels/web/install",
    "facebook_messenger": "channels/fb",
    "twilio": "channels/twilio",
    "whatsapp": "channels/whatsapp",
    "slack": "channels/slack",
}


def _channel_links(app_id: str) -> dict:
    return {name: f"{_CONSOLE}/app/{app_id}/{path}" for name, path in CHANNELS.items()}


def register(mcp: FastMCP) -> None:

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def get_publish_status(app_id: str) -> dict:
        """
        Whether the agent has been published, and its published versions (newest first).
        Use to answer "is my agent live?" / "which version is published?" before suggesting a publish.

        Args:
            app_id: The application ID.
        """
        items = await client.list_versions(app_id)
        return {"published": bool(items), "versionCount": len(items),
                "versions": [{k: v.get(k) for k in ("versionId", "versionName", "releaseNote", "createdAt", "current")
                              if v.get(k) is not None} for v in items[:10]],
                "versionsPage": f"{_CONSOLE}/app/{app_id}/versions"}

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=False, openWorldHint=True))
    async def publish_application(app_id: str, version_name: str = "", release_note: str = "") -> dict:
        """
        Publish the agent so customers can use it. ONLY when the user explicitly asked to publish
        or go live — it makes the current draft live on every channel; never publish as a side
        effect of a build. Creates a Live runtime access key for the website widget, publishes a
        new version, and returns the website snippet plus each channel's setup page. Relay the
        snippet and links to the user; the key value cannot be retrieved again later.

        Args:
            app_id: The application ID.
            version_name: Optional version name saying what changed, e.g. "Order support v1";
                a numbered name is used otherwise.
            release_note: Optional note on what changed in this version.
        """
        existing = await client.list_versions(app_id)
        name = (version_name or "").strip()[:100] or f"Version {len(existing) + 1}"
        note = (release_note or "").strip()[:500] or RELEASE_NOTE
        result = {"versionName": name, "firstPublish": not existing}

        # 1. A Live runtime key for the website widget, exactly as the Access keys page creates one.
        key_id = KEY_ID_PREFIX + datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        try:
            created = await client.create_live_runtime_key(app_id, key_id)
            key = created.get("accessKey") if isinstance(created, dict) else None
            if not key:
                raise client.ApiError("no access key came back")
        except client.ApiError as exc:
            return {**result, "status": "failed",
                    "error": f"Creating the website access key failed, so nothing was published ({exc}). "
                             f"The user can publish from the Versions page: {_CONSOLE}/app/{app_id}/versions"}

        # 2. The new version, as the Versions page's Publish dialog does.
        try:
            published = await client.publish_version(app_id, name, note)
        except client.ApiError as exc:
            return {**result, "status": "failed", "keyId": key_id,
                    "error": f"A website key ({key_id}) was created, but publishing failed: {exc}. Try again, "
                             f"or publish from the Versions page: {_CONSOLE}/app/{app_id}/versions"}
        reviewing = isinstance(published, dict) and published.get("message") == "review-requested"
        return {**result, "status": "review_requested" if reviewing else "published", "keyId": key_id,
                "snippet": WEB_SNIPPET.format(app_id=app_id, key=key),
                "channelSetup": _channel_links(app_id),
                "note": ("The version is waiting for an administrator to approve it; it goes live once approved. "
                         "The snippet can be added now and starts working then." if reviewing else
                         "The agent is live. The snippet goes just before </body> on every page where visitors "
                         "should see the assistant. The key is shown only now: tell the user to keep it.")}
