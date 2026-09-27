"""
Shared plumbing for playbooks and workflows.

Both are journeys (bc-tech-docs/12 §1): an action record in /agent/actions plus a journey
record holding stm.stmDefinition. They differ only in who owns stm.metadata:

  playbook  -> {authoringMode: 'playbook', playbook: <model>, nodes: [], edges: []}
  workflow  -> {nodes, edges}   (React Flow layout for the canvas editor)

Save order mirrors the console: action -> journey (provisioned async, so ensured)
-> slots -> definition.
"""

import re
from typing import Any

from .. import client

AUTHORING_MODE_PLAYBOOK = "playbook"


def metadata_of(journey: dict) -> dict:
    stm = journey.get("stm") if isinstance(journey.get("stm"), dict) else {}
    return stm.get("metadata") or {}


def is_playbook(journey: dict) -> bool:
    meta = metadata_of(journey)
    return meta.get("authoringMode") == AUTHORING_MODE_PLAYBOOK or "playbook" in meta


def is_canvas_owned(journey: dict) -> bool:
    return not is_playbook(journey) and len(metadata_of(journey).get("nodes") or []) > 1


async def known_ids(app_id: str) -> dict[str, set]:
    codehooks = await client.list_codehooks(app_id)
    actions = await client.list_actions(app_id)
    sources = await client.list_data_sources(app_id)
    return {
        "codehook_ids": {c.get("codehookId") for c in codehooks if c.get("codehookId")},
        "journey_ids": {a.get("id") for a in actions if a.get("actionType") == "workflow"},
        "data_source_ids": {d.get("dataSourceId") for d in sources if d.get("dataSourceId")},
    }


async def find_action(app_id: str, workflow_id: str) -> dict:
    for action in await client.list_actions(app_id):
        if action.get("id") == workflow_id:
            return action
    raise ValueError(f"No playbook/workflow with id '{workflow_id}'; list_workflows shows valid ids")


async def upsert_action(app_id: str, workflow_id: str | None, name: str, description: str, mode: str) -> str:
    if workflow_id:
        existing = {k: v for k, v in (await find_action(app_id, workflow_id)).items()
                    if k not in ("pk", "sk", "createdAt", "modifiedAt")}
        await client.save_action(app_id, {**existing, "id": workflow_id, "name": name or existing.get("name"),
                                          "description": description or existing.get("description"),
                                          "authoringMode": mode})
        return workflow_id
    if any(a.get("name") == name for a in await client.list_actions(app_id)):
        raise ValueError(f"A playbook/workflow named '{name}' already exists; pass its workflow_id to update it")
    result = await client.save_action(app_id, {
        # actionType 'workflow' is what makes the backend create the journey record.
        "name": name, "description": description, "actionType": "workflow", "resourceId": "workflow",
        "parameters": [], "scheduleOnly": False, "authoringMode": mode,
    })
    if not result.get("id"):
        raise RuntimeError("Action creation returned no id; call list_workflows before retrying")
    return result["id"]


def _slug(text: str, limit: int) -> str:
    return (re.sub(r"[^a-z0-9_]+", "_", (text or "").lower()).strip("_") or "workflow")[:limit]


async def expose_as_agent_tool(app_id: str, workflow_id: str, name: str, description: str, mode: str) -> dict:
    """Register (or refresh) a type=workflow agent tool so the main agent can start the journey."""
    app = await client.get_app(app_id)
    if app.get("appMode") != "prompt_based":
        return {"registered": False,
                "note": "App is not prompt-based: journeys are started by intents/events, not agent tools."}
    description = (description or "").strip()
    if len(description) < 10:
        return {"registered": False, "note": "Give it a 'when to use' description first."}
    tools = await client.list_prompt(app_id, "tools")
    if mode == AUTHORING_MODE_PLAYBOOK:
        # Same convention as the console (pages/playbooks/model/playbook-tools.js).
        tool_id = f"playbook-{workflow_id}"
        suffix = re.sub(r"[^a-zA-Z0-9_-]", "_", workflow_id)[-24:]
        payload: dict[str, Any] = {
            "id": tool_id, "name": f"playbook_{_slug(name, 29)}_{suffix}", "type": "workflow",
            "description": description, "parameters": {}, "toolData": {"workflowId": workflow_id},
            "managedByPlaybook": workflow_id,
        }
        existing = next((t for t in tools if t.get("id") == tool_id), None)
    else:
        existing = next((t for t in tools if (t.get("toolData") or {}).get("workflowId") == workflow_id), None)
        tool_name = existing.get("name") if existing else f"workflow_{_slug(name, 48)}"
        taken = {t.get("name") for t in tools if t is not existing}
        base, n = tool_name, 2
        while tool_name in taken:
            tool_name, n = f"{base}_{n}", n + 1
        payload = {"name": tool_name, "type": "workflow", "description": description, "parameters": {},
                   "toolData": {"workflowId": workflow_id}}
    if existing:
        body = {k: v for k, v in {**existing, **payload}.items()
                if k not in ("id", "pk", "sk", "appId", "createdAt", "modifiedAt")}
        await client.update_prompt(app_id, "tools", existing["id"], body)
        return {"registered": True, "toolId": existing["id"], "toolName": payload["name"]}
    created = await client.create_prompt(app_id, "tools", payload)
    return {"registered": True, "toolId": created.get("id") or payload.get("id"), "toolName": payload["name"]}


def merge_slots(existing: dict, provided: dict, auto: dict) -> dict:
    # Saved variables are retained (the console never drops them on save).
    return {**auto, **(existing or {}), **(provided or {})}
