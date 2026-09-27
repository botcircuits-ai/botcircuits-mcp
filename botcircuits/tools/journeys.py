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


_SERVER_FIELDS = ("id", "pk", "sk", "appId", "createdAt", "modifiedAt")


def tools_for(tools: list, workflow_id: str) -> list:
    return [t for t in tools if (t.get("toolData") or {}).get("workflowId") == workflow_id]


async def expose_as_agent_tool(app_id: str, workflow_id: str, name: str, description: str, mode: str,
                               tool_name: str | None = None) -> dict:
    """Ensure exactly ONE type=workflow agent tool starts this journey (idempotent).

    Playbooks: the record is always the console-managed `playbook-<id>` (same payload as
    pages/playbooks/model/playbook-tools.js). The console recreates that record whenever a
    playbook with a description is opened, so any other record pointing at the playbook
    would become a second tool. Canvas workflows: the first existing record is kept.
    Other records that reference the same journey are removed and reported.
    """
    app = await client.get_app(app_id)
    if app.get("appMode") != "prompt_based":
        return {"registered": False,
                "note": "App is not prompt-based: journeys are started by intents/events, not agent tools."}
    description = (description or "").strip()
    if len(description) < 10:
        return {"registered": False, "note": "Give it a 'when to use' description first."}
    tools = await client.list_prompt(app_id, "tools")
    linked = tools_for(tools, workflow_id)
    if mode == AUTHORING_MODE_PLAYBOOK:
        tool_id = f"playbook-{workflow_id}"
        suffix = re.sub(r"[^a-zA-Z0-9_-]", "_", workflow_id)[-24:]
        payload: dict[str, Any] = {
            "id": tool_id, "name": f"playbook_{_slug(name, 29)}_{suffix}", "type": "workflow",
            "description": description, "parameters": {}, "toolData": {"workflowId": workflow_id},
            "managedByPlaybook": workflow_id,
        }
        keep = next((t for t in tools if t.get("id") == tool_id), None)
    else:
        keep = linked[0] if linked else None
        wanted = tool_name or (keep.get("name") if keep else f"workflow_{_slug(name, 48)}")
        taken = {t.get("name") for t in tools if t is not keep and t not in linked}
        base, n = wanted, 2
        while wanted in taken:
            wanted, n = f"{base}_{n}", n + 1
        payload = {"name": wanted, "type": "workflow", "description": description, "parameters": {},
                   "toolData": {"workflowId": workflow_id}}
    if keep:
        body = {k: v for k, v in {**keep, **payload}.items() if k not in _SERVER_FIELDS}
        await client.update_prompt(app_id, "tools", keep["id"], body)
        kept_id = keep["id"]
    else:
        created = await client.create_prompt(app_id, "tools", payload)
        kept_id = created.get("id") or payload.get("id")
    removed = []
    for duplicate in linked:
        if duplicate.get("id") != kept_id:
            await client.delete_prompt(app_id, "tools", duplicate["id"])
            removed.append({"id": duplicate["id"], "name": duplicate.get("name")})
    result = {"registered": True, "toolId": kept_id, "toolName": payload["name"]}
    if removed:
        result["removedDuplicates"] = removed
    if mode == AUTHORING_MODE_PLAYBOOK and tool_name and tool_name != payload["name"]:
        result["note"] = ("Playbook tools use the console's naming convention; a custom name would be "
                          "reset the next time the playbook is opened in the console.")
    return result


async def journey_mode(app_id: str, workflow_id: str) -> str:
    return AUTHORING_MODE_PLAYBOOK if is_playbook(await client.get_journey(app_id, workflow_id)) else "workflow"


def merge_slots(existing: dict, provided: dict, auto: dict) -> dict:
    # Saved variables are retained (the console never drops them on save).
    return {**auto, **(existing or {}), **(provided or {})}
