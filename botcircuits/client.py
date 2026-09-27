"""
Thin async HTTP client wrapping the BotCircuits REST API.

Routes and payload shapes follow botcircuits-platform/src/services so everything the
MCP server creates opens and edits normally in the console.

All methods raise RuntimeError on non-2xx responses so tool handlers only deal with the
happy path; FastMCP reports the error to the host AI.
"""

import asyncio
import re
from typing import Any
from urllib.parse import urlsplit

import httpx

from .config import settings

_ID = re.compile(r"^[A-Za-z0-9_\-.:]{1,128}$")

# Tests route requests through an httpx.MockTransport; None means real network.
_transport: httpx.AsyncBaseTransport | None = None


class NotFoundError(RuntimeError):
    pass


def _safe(value: str, label: str = "id") -> str:
    """Reject ids that could escape the /apps/{appId}/... path."""
    if not isinstance(value, str) or not _ID.match(value) or ".." in value:
        raise ValueError(f"Invalid {label}: {value!r}")
    return value


def _client() -> httpx.AsyncClient:
    if not settings.access_token:
        raise RuntimeError("BOTCIRCUITS_ACCESS_TOKEN is not set for the MCP server")
    return httpx.AsyncClient(
        base_url=settings.api_base_url,
        headers={"Content-Type": "application/json", "Authorization": settings.access_token},
        timeout=60.0,
        transport=_transport,
    )


async def _request(method: str, path: str, json: Any = None, params: dict | None = None) -> Any:
    async with _client() as c:
        r = await c.request(method, path, json=json, params=params)
    if r.status_code >= 400:
        try:
            detail = r.json()
            detail = detail.get("message", detail) if isinstance(detail, dict) else detail
        except ValueError:
            detail = r.text[:500]
        error = NotFoundError if r.status_code == 404 else RuntimeError
        raise error(f"BotCircuits API error {r.status_code} on {method} {path}: {detail}")
    if not r.content:
        return {}
    try:
        return r.json()
    except ValueError:
        return {"raw": r.text[:2000]}


def _unwrap(value: Any) -> Any:
    """Some handlers wrap results as {data: ...} (list endpoints may add pagination)."""
    if isinstance(value, dict) and "data" in value and not ({"id", "appId", "journeyId"} & value.keys()):
        return value["data"]
    return value


def _as_list(value: Any) -> list:
    value = _unwrap(value)
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("items", "tools", "skills", "servers", "actions", "journeys"):
            if isinstance(value.get(key), list):
                return value[key]
    return []


def _app(app_id: str) -> str:
    return f"/apps/{_safe(app_id, 'app_id')}"


# ---------------------------------------------------------------------------
# Apps
# ---------------------------------------------------------------------------
async def create_app(name: str, **kwargs: Any) -> dict:
    return await _request("POST", "/apps", {"name": name, **kwargs})


async def list_apps(plugin_type: str | None = None) -> Any:
    return await _request("GET", "/apps", params={"plugin_type": plugin_type} if plugin_type else None)


async def get_app(app_id: str) -> dict:
    return _unwrap(await _request("GET", _app(app_id)))


async def delete_app(app_id: str) -> dict:
    return await _request("DELETE", _app(app_id))


async def publish_app(app_id: str) -> dict:
    return await _request("POST", f"{_app(app_id)}/publish")


# ---------------------------------------------------------------------------
# Agent core settings (read-modify-write) and global instructions
# ---------------------------------------------------------------------------
CORE_SETTINGS_FIELDS = (
    "agentDescription", "defaultErrorMessage", "authConfig", "botLanguage",
    "kbTopResults", "defaultWorkflow", "vectorSearch",
)


async def get_agent_core_settings(app_id: str) -> dict:
    try:
        return _unwrap(await _request("GET", f"{_app(app_id)}/agent/core-settings")) or {}
    except NotFoundError:
        return {}


async def save_agent_core_settings(app_id: str, changes: dict) -> dict:
    """Merge changes into the stored settings; never drop fields the caller did not set."""
    existing = await get_agent_core_settings(app_id)
    merged = {k: existing[k] for k in CORE_SETTINGS_FIELDS if k in existing}
    merged.update(changes)
    merged.setdefault("authConfig", {})  # required by the save handler
    merged.setdefault("kbTopResults", 20)
    await _request("POST", f"{_app(app_id)}/agent/core-settings", {"appId": app_id, **merged})
    return merged


async def get_instructions(app_id: str) -> dict:
    try:
        return _unwrap(await _request("GET", f"{_app(app_id)}/prompt-config/instructions")) or {}
    except NotFoundError:
        return {}


async def save_instructions(app_id: str, fields: dict) -> dict:
    # The backend merges supplied fields into the stored record.
    return await _request("POST", f"{_app(app_id)}/prompt-config/instructions", fields)


# ---------------------------------------------------------------------------
# Prompt config collections: agent tools, skills, MCP servers
#   /prompt-config/tools        agent tools (console "Tools"; types function, kb, workflow, json, sub_agent)
#   /prompt-config/skills       skills (on-demand instructions)
#   /prompt-config/mcp-servers  MCP server connections
# ---------------------------------------------------------------------------
_COLLECTION = {"tools": "tools", "skills": "skills", "mcp_servers": "mcp-servers"}


async def list_prompt(app_id: str, kind: str) -> list:
    return _as_list(await _request("GET", f"{_app(app_id)}/prompt-config/{_COLLECTION[kind]}"))


async def get_prompt(app_id: str, kind: str, item_id: str) -> dict:
    return _unwrap(await _request("GET", f"{_app(app_id)}/prompt-config/{_COLLECTION[kind]}/{_safe(item_id)}"))


async def create_prompt(app_id: str, kind: str, payload: dict) -> dict:
    result = _unwrap(await _request("POST", f"{_app(app_id)}/prompt-config/{_COLLECTION[kind]}", payload))
    return result if isinstance(result, dict) else {}


async def update_prompt(app_id: str, kind: str, item_id: str, payload: dict) -> dict:
    return await _request("PUT", f"{_app(app_id)}/prompt-config/{_COLLECTION[kind]}/{_safe(item_id)}", payload)


async def delete_prompt(app_id: str, kind: str, item_id: str) -> dict:
    return await _request("DELETE", f"{_app(app_id)}/prompt-config/{_COLLECTION[kind]}/{_safe(item_id)}")


# ---------------------------------------------------------------------------
# Actions (/agent/actions) — the list records behind workflows and playbooks
# ---------------------------------------------------------------------------
async def list_actions(app_id: str) -> list:
    return _as_list(await _request("GET", f"{_app(app_id)}/agent/actions"))


async def save_action(app_id: str, payload: dict) -> dict:
    result = _unwrap(await _request("POST", f"{_app(app_id)}/agent/actions", {"appId": app_id, **payload}))
    return result if isinstance(result, dict) else {}


async def delete_action(app_id: str, action_id: str) -> dict:
    # Removes the action and its journey together.
    return await _request("DELETE", f"{_app(app_id)}/agent/actions/{_safe(action_id)}")


# ---------------------------------------------------------------------------
# Journeys (workflow + playbook definitions)
# ---------------------------------------------------------------------------
JOURNEY_POLL_SECONDS = 2.0


async def list_journeys(app_id: str) -> list:
    return _as_list(await _request("GET", f"{_app(app_id)}/model/journey"))


async def get_journey(app_id: str, journey_id: str) -> dict:
    try:
        result = _unwrap(await _request("GET", f"{_app(app_id)}/model/journey/{_safe(journey_id)}"))
    except NotFoundError:
        return {}
    return result if isinstance(result, dict) else {}


async def ensure_journey(app_id: str, journey_id: str, wait_seconds: float = 20) -> dict:
    """Action creation provisions the journey asynchronously (outside on-prem).

    Poll briefly, then create it through the same handler the provisioning event uses —
    what the console's playbook editor does (bc-tech-docs/12 §6.3).
    """
    attempts = max(1, int(wait_seconds / max(JOURNEY_POLL_SECONDS, 0.1)))
    for _ in range(attempts):
        journey = await get_journey(app_id, journey_id)
        if journey.get("journeyId"):
            return journey
        await asyncio.sleep(JOURNEY_POLL_SECONDS)
    try:
        await _request("POST", f"{_app(app_id)}/model/journey", {
            "journeyId": journey_id, "name": journey_id,
            "trigger": {"triggerType": "event", "events": ["action"]},
        })
    except RuntimeError as exc:
        if "error 400" not in str(exc):  # "journey already exists": the async create won
            raise
    journey = await get_journey(app_id, journey_id)
    if not journey.get("journeyId"):
        raise RuntimeError(f"Journey {journey_id} is still provisioning; retry shortly")
    return journey


async def save_journey_slots(app_id: str, journey_id: str, slots: dict) -> dict:
    return await _request("POST", f"{_app(app_id)}/model/journey/{_safe(journey_id)}/slots",
                          {"appId": app_id, "journeyId": journey_id, "slots": slots})


async def save_journey_definition(app_id: str, journey_id: str, stm_definition: dict, metadata: dict,
                                  auto_save: bool = False) -> dict:
    return await _request("PUT", f"{_app(app_id)}/model/journey/{_safe(journey_id)}/definition",
                          {"stmDefinition": stm_definition, "metadata": metadata, "autoSave": auto_save})


# ---------------------------------------------------------------------------
# Codehooks (customer Lambda functions)
# ---------------------------------------------------------------------------
async def list_codehooks(app_id: str) -> list:
    return _as_list(await _request("GET", f"{_app(app_id)}/model/codehooks"))


async def get_codehook(app_id: str, codehook_id: str) -> dict:
    return _unwrap(await _request("GET", f"{_app(app_id)}/model/codehooks/{_safe(codehook_id)}"))


async def save_codehook_config(app_id: str, payload: dict) -> dict:
    return await _request("POST", f"{_app(app_id)}/model/codehooks", payload)


async def codehook_upload_url(app_id: str, codehook_id: str) -> str:
    result = _unwrap(await _request("POST", f"{_app(app_id)}/model/codehooks/{_safe(codehook_id)}/upload-url",
                                    {"codehookId": codehook_id, "codehookType": "fullfillment"}))  # sic
    url = (result or {}).get("url") or (result or {}).get("uploadUrl")
    if not url:
        raise RuntimeError("Codehook upload URL was not returned")
    return url


async def deploy_codehook(app_id: str, codehook_id: str) -> dict:
    return await _request("POST", f"{_app(app_id)}/model/codehooks/{_safe(codehook_id)}/deploy",
                          {"codehookType": "fullfillment"})


async def delete_codehook(app_id: str, codehook_id: str) -> dict:
    return await _request("DELETE", f"{_app(app_id)}/model/codehooks/{_safe(codehook_id)}")


async def upload(url: str, content: bytes, content_type: str) -> None:
    """PUT to an API-issued presigned URL. Platform credentials are never forwarded."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError("The API did not return a valid upload URL")
    async with httpx.AsyncClient(timeout=120, transport=_transport) as c:
        r = await c.put(url, content=content, headers={"Content-Type": content_type})
    if r.status_code >= 400:
        raise RuntimeError(f"Upload failed: HTTP {r.status_code}")


# ---------------------------------------------------------------------------
# Knowledge sources
# ---------------------------------------------------------------------------
async def list_data_sources(app_id: str) -> list:
    return _as_list(await _request("GET", f"{_app(app_id)}/knowledge/data-sources"))


async def save_text_source(app_id: str, data_source_id: str, text: str) -> dict:
    # Editor.js paragraph blocks, exactly as the console's text connector sends them.
    blocks = [{"type": "paragraph", "data": {"text": p}} for p in (x.strip() for x in text.split("\n\n")) if p]
    return await _request("POST", f"{_app(app_id)}/knowledge/data-sources/text",
                          {"dataSourceId": data_source_id, "editorBlocks": blocks, "draft": False})


async def register_data_source(app_id: str, metadata: dict) -> dict:
    return await _request("POST", f"{_app(app_id)}/knowledge/data-sources", metadata)
