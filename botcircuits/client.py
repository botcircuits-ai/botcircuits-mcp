"""
Thin async HTTP client wrapping the BotCircuits REST API.

All methods raise RuntimeError on non-2xx responses so tool handlers
only need to deal with the happy-path data.
"""

from typing import Any, Optional
import httpx
from .config import settings


def _headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Authorization": settings.access_token,
    }


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=settings.api_base_url,
        headers=_headers(),
        timeout=30.0,
    )


async def _raise_for(response: httpx.Response) -> Any:
    if response.status_code >= 400:
        try:
            detail = response.json()
        except Exception:
            detail = response.text
        raise RuntimeError(
            f"BotCircuits API error {response.status_code}: {detail}"
        )
    return response.json()


# ---------------------------------------------------------------------------
# Apps
# ---------------------------------------------------------------------------
async def create_app(name: str, **kwargs: Any) -> dict:
    async with _client() as c:
        r = await c.post("/apps", json={"name": name, **kwargs})
        return await _raise_for(r)


async def list_apps(plugin_type: Optional[str] = None) -> dict:
    params = {}
    if plugin_type:
        params["plugin_type"] = plugin_type
    async with _client() as c:
        r = await c.get("/apps", params=params)
        return await _raise_for(r)


async def get_app(app_id: str) -> dict:
    async with _client() as c:
        r = await c.get(f"/apps/{app_id}")
        return await _raise_for(r)


async def delete_app(app_id: str) -> dict:
    async with _client() as c:
        r = await c.delete(f"/apps/{app_id}")
        return await _raise_for(r)


async def publish_app(app_id: str) -> dict:
    async with _client() as c:
        r = await c.post(f"/apps/{app_id}/publish")
        return await _raise_for(r)


# ---------------------------------------------------------------------------
# Agent core settings (system prompt, default error message, etc.)
# ---------------------------------------------------------------------------
async def get_agent_core_settings(app_id: str) -> dict:
    async with _client() as c:
        r = await c.get(f"/apps/{app_id}/agent/core-settings")
        return await _raise_for(r)


async def save_agent_core_settings(app_id: str, payload: dict) -> dict:
    async with _client() as c:
        r = await c.post(
            f"/apps/{app_id}/agent/core-settings",
            json={"appId": app_id, **payload},
        )
        return await _raise_for(r)


# ---------------------------------------------------------------------------
# Actions  (/agent/actions)
# Used only for workflow routing.
# ---------------------------------------------------------------------------
async def save_action(app_id: str, payload: dict) -> dict:
    async with _client() as c:
        r = await c.post(
            f"/apps/{app_id}/agent/actions",
            json={"appId": app_id, **payload},
        )
        return await _raise_for(r)


# ---------------------------------------------------------------------------
# Journeys / Workflows
# ---------------------------------------------------------------------------
async def list_journeys(app_id: str) -> dict:
    async with _client() as c:
        r = await c.get(f"/apps/{app_id}/model/journey")
        return await _raise_for(r)


async def get_journey(app_id: str, workflow_id: str) -> dict:
    async with _client() as c:
        r = await c.get(f"/apps/{app_id}/model/journey/{workflow_id}")
        return await _raise_for(r)


async def save_journey_slots(app_id: str, workflow_id: str, slots: dict) -> dict:
    async with _client() as c:
        r = await c.post(
            f"/apps/{app_id}/model/journey/{workflow_id}/slots",
            json={"appId": app_id, "journeyId": workflow_id, "slots": slots},
        )
        return await _raise_for(r)


async def save_journey_definition(
    app_id: str,
    workflow_id: str,
    stm_definition: dict,
    metadata: dict,
    auto_save: bool = False,
) -> dict:
    async with _client() as c:
        r = await c.put(
            f"/apps/{app_id}/model/journey/{workflow_id}/definition",
            json={"stmDefinition": stm_definition, "metadata": metadata, "autoSave": auto_save},
        )
        return await _raise_for(r)


async def delete_journey(app_id: str, workflow_id: str) -> dict:
    async with _client() as c:
        r = await c.delete(f"/apps/{app_id}/model/journey/{workflow_id}")
        return await _raise_for(r)


# ---------------------------------------------------------------------------
# Skills  (/apps/{appId}/prompt-config/tools)
# The platform UI calls these "Skills". The REST path uses /tools internally.
# ---------------------------------------------------------------------------


async def list_skills(app_id: str) -> list:
    async with _client() as c:
        r = await c.get(f"/apps/{app_id}/prompt-config/tools")
        return await _raise_for(r)


async def get_skill(app_id: str, skill_id: str) -> dict:
    async with _client() as c:
        r = await c.get(f"/apps/{app_id}/prompt-config/tools/{skill_id}")
        return await _raise_for(r)


async def create_skill(app_id: str, payload: dict) -> dict:
    async with _client() as c:
        r = await c.post(f"/apps/{app_id}/prompt-config/tools", json=payload)
        return await _raise_for(r)


async def update_skill(app_id: str, skill_id: str, payload: dict) -> dict:
    async with _client() as c:
        r = await c.put(f"/apps/{app_id}/prompt-config/tools/{skill_id}", json=payload)
        return await _raise_for(r)


async def delete_skill(app_id: str, skill_id: str) -> dict:
    async with _client() as c:
        r = await c.delete(f"/apps/{app_id}/prompt-config/tools/{skill_id}")
        return await _raise_for(r)
