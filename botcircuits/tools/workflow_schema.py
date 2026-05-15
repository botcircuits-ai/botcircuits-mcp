"""
Workflow schema tools.

The host LLM (Claude Code, Cursor, etc.) generates the workflow JSON.
These tools handle what the host cannot do itself:

  convert_intermediate_to_platform  — pure deterministic transform (no LLM, no API key)
  upload_workflow                    — convert + save STM + slots to BotCircuits in one call
"""

from __future__ import annotations

import random
from typing import Any, Optional
from mcp.server.fastmcp import FastMCP
from .. import client


# ─────────────────────────────────────────────────────────────────────────────
# PLATFORM FORMAT TRANSFORM
# Ported from bc-text-to-workflow/agents/orchestrator.py
# ─────────────────────────────────────────────────────────────────────────────

_EDGE_STYLE: dict = {"stroke": "#969b9f", "strokeWidth": 2}
_EDGE_MARKER: dict = {"type": "arrowclosed", "color": "#969b9f", "width": 20, "height": 20}

_NODE_LABEL: dict[str, str] = {
    "messagePrompt": "Text", "imageMessage": "Image",
    "questionPrompt": "Question", "buttonsPrompt": "Buttons",
    "cardsPrompt": "Cards", "languageSelectorPrompt": "Language Selector",
    "autoCaptureSlots": "Auto Capture", "codehookAction": "Codehook",
    "webhookAction": "Webhook", "aiTask": "AI Task",
    "docSearchAction": "Doc Search", "integrationAction": "Integration",
    "liveAgentAction": "Live Agent", "pauseAction": "Pause",
    "journeyAction": "Journey", "auth": "Auth",
    "customAction": "Custom Action", "choice": "Condition", "StartNode": "Start",
}


def _uid(used: set) -> str:
    while True:
        i = str(random.randint(10000, 99999))
        if i not in used:
            used.add(i)
            return i


def _coerce_dto(opts: Any) -> list[dict]:
    if not opts:
        return [{"displayText": ""}]
    out = []
    for item in opts:
        if isinstance(item, str):
            out.append({"displayText": item})
        elif isinstance(item, dict):
            out.append({"displayText": item.get("displayText", "")})
        else:
            out.append({"displayText": str(item)})
    return out


def _translate(obj: Any, id_map: dict) -> Any:
    if isinstance(obj, dict):
        return {k: _translate(v, id_map) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_translate(i, id_map) for i in obj]
    if isinstance(obj, str) and obj in id_map:
        return id_map[obj]
    return obj


def _build_sc(sc: dict, block_id: str, new_next: Optional[str], id_map: dict) -> dict:
    if sc.get("stateType") == "start":
        r: dict = {"blockId": block_id, "stateType": "start"}
        if new_next:
            r["next"] = new_next
        return r
    nsc: dict = _translate(sc, id_map)
    nsc["blockId"] = block_id
    nsc.setdefault("displayText", "")
    nsc.setdefault("intentPrompt", "")
    nsc.setdefault("responseModifyByAI", False)
    if "displayTextOptions" in nsc:
        nsc["displayTextOptions"] = _coerce_dto(nsc["displayTextOptions"])
    if nsc.get("stateType") == "prompt":
        nsc.setdefault("autoFillFromEntity", nsc.get("type") not in ("cards",))
        if "validationErrorDisplayTextOptions" not in nsc:
            nsc["validationErrorDisplayTextOptions"] = [{"displayText": ""}]
        elif isinstance(nsc.get("validationErrorDisplayTextOptions"), list):
            nsc["validationErrorDisplayTextOptions"] = _coerce_dto(nsc["validationErrorDisplayTextOptions"])
    return nsc


def _layout(states: dict, start_id: str) -> dict[str, tuple[float, float]]:
    pos: dict[str, tuple[float, float]] = {}
    visited: set = set()
    queue: list[tuple[str, int]] = [(start_id, 0)]
    col_row: dict[int, int] = {}
    while queue:
        sid, col = queue.pop(0)
        if sid in visited or sid not in states:
            continue
        visited.add(sid)
        row = col_row.get(col, 0)
        pos[sid] = (col * 350.0, row * 150.0)
        col_row[col] = row + 1
        sc = states[sid].get("stateConfig", {})
        if sc.get("stateType") == "choice":
            for c in sc.get("choices", []):
                if c.get("next") and c["next"] not in visited:
                    queue.append((c["next"], col + 1))
            if sc.get("next") and sc["next"] not in visited:
                queue.append((sc["next"], col + 1))
        else:
            nxt = states[sid].get("next")
            if nxt and nxt not in visited:
                queue.append((nxt, col + 1))
    return pos


def transform_to_platform_format(workflow: dict) -> dict:
    """
    Convert intermediate format {stmDefinition, slots} to BotCircuits platform format.
    Pure deterministic transform — no LLM call.
    """
    stm_def = workflow.get("stmDefinition", {})
    old_states: dict = stm_def.get("states", {})
    old_start: str = stm_def.get("startAt", "")
    slots: dict = workflow.get("slots", {})

    if not old_states:
        return {"slots": slots, "stm": {
            "stmDefinition": stm_def,
            "metadata": {"nodes": [], "edges": []},
            "slotStateMap": [],
        }}

    used: set = set()
    start_id = _uid(used)
    id_map = {oid: (start_id if oid == old_start else _uid(used)) for oid in old_states}
    block_map = {nid: (start_id if nid == start_id else _uid(used)) for nid in id_map.values()}

    new_states: dict = {}
    slot_state_map: list = []

    for old_id, state in old_states.items():
        nid = id_map[old_id]
        bid = block_map[nid]
        sc = state.get("stateConfig", {})
        old_next = state.get("next")
        new_next = id_map.get(old_next) if old_next else None
        new_sc = _build_sc(sc, bid, new_next, id_map)
        entry: dict = {"stateConfig": new_sc}
        if new_next:
            entry["next"] = new_next
        new_states[nid] = entry
        slot_name = sc.get("slot") or sc.get("slotToAssign")
        if slot_name and slot_name != "bc_workflow_option":
            if not any(s["slot"] == slot_name for s in slot_state_map):
                slot_state_map.append({"slot": slot_name, "state": nid})

    positions = _layout(new_states, start_id)
    block_pos: dict = {}
    for nid, bid in block_map.items():
        if bid not in block_pos:
            block_pos[bid] = positions.get(nid, (0.0, 0.0))

    nodes: list = [{"id": start_id, "type": "StartNode",
                    "position": {"x": 0.0, "y": 0.0},
                    "data": {"stateConfig": new_states[start_id]["stateConfig"]},
                    "width": 89, "height": 36}]
    block_children: dict = {}
    block_order: list = []
    for old_id, state in old_states.items():
        nid = id_map[old_id]
        if nid == start_id:
            continue
        bid = block_map[nid]
        nt: str = state.get("type", "messagePrompt")
        child = {"id": int(nid), "label": _NODE_LABEL.get(nt, "Step"),
                 "nodeType": nt, "stateConfig": new_states[nid]["stateConfig"]}
        if bid not in block_children:
            block_children[bid] = []
            block_order.append(bid)
        block_children[bid].append(child)
    for bid in block_order:
        children = block_children[bid]
        x, y = block_pos.get(bid, (0.0, 0.0))
        nodes.append({"id": bid, "type": "MainWorkflowNode",
                      "position": {"x": round(x, 4), "y": round(y, 4)},
                      "style": {"borderRadius": "12px", "width": "300px"},
                      "data": {"label": "New Block", "children": children},
                      "width": 300, "height": 114 + max(0, len(children) - 1) * 53})

    edges: list = []
    block_outgoing: dict = {}
    for nid, entry in new_states.items():
        bid = block_map[nid]
        sc = entry["stateConfig"]
        if sc.get("stateType") == "choice":
            block_outgoing[bid] = nid
        else:
            nxt = entry.get("next")
            if nxt and block_map.get(nxt) != bid:
                block_outgoing[bid] = nid

    start_next = new_states[start_id].get("next")
    if start_next:
        tb = block_map.get(start_next, start_next)
        edges.append({"id": f"edge-{start_id}-{tb}", "source": start_id, "sourceHandle": "start",
                      "target": tb, "targetHandle": f"target-{tb}-left-top",
                      "type": "custom", "markerEnd": _EDGE_MARKER, "style": _EDGE_STYLE})

    for bid, out_nid in block_outgoing.items():
        if bid == start_id:
            continue
        entry = new_states[out_nid]
        sc = entry["stateConfig"]
        if sc.get("stateType") == "choice":
            for c in sc.get("choices", []):
                cid = c.get("id", "")
                if c.get("next"):
                    tb = block_map.get(c["next"], c["next"])
                    edges.append({"id": f"edge-{bid}-{tb}-c{cid}", "source": bid,
                                  "sourceHandle": f"source-condition-{bid}-{cid}-right",
                                  "target": tb, "targetHandle": f"target-{tb}-left-top",
                                  "type": "custom", "markerEnd": _EDGE_MARKER, "style": _EDGE_STYLE})
            if sc.get("next"):
                tb = block_map.get(sc["next"], sc["next"])
                edges.append({"id": f"edge-{bid}-{tb}-else", "source": bid,
                              "sourceHandle": f"source-else-condition-{bid}-right",
                              "target": tb, "targetHandle": f"target-{tb}-left-top",
                              "type": "custom", "markerEnd": _EDGE_MARKER, "style": _EDGE_STYLE})
        else:
            nxt = entry.get("next")
            if nxt:
                tb = block_map.get(nxt, nxt)
                edges.append({"id": f"edge-{bid}-{tb}", "source": bid,
                              "sourceHandle": f"source-{bid}-{out_nid}-right",
                              "target": tb, "targetHandle": f"target-{tb}-left-top",
                              "type": "custom", "markerEnd": _EDGE_MARKER, "style": _EDGE_STYLE})

    return {"slots": slots, "stm": {
        "stmDefinition": {"startAt": start_id, "states": new_states},
        "metadata": {"nodes": nodes, "edges": edges},
        "slotStateMap": slot_state_map,
    }}


# ─────────────────────────────────────────────────────────────────────────────
# MCP TOOLS
# ─────────────────────────────────────────────────────────────────────────────

def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def convert_intermediate_to_platform(intermediate: dict) -> dict:
        """
        Convert a workflow intermediate JSON into BotCircuits platform format.

        Pure deterministic transform — no LLM call, no API key needed.

        The host LLM (Claude Code, etc.) generates the intermediate JSON using
        the schema in CLAUDE.md. This tool handles the platform-specific
        transformation: ID remapping, blockId injection, ReactFlow metadata
        generation, and slotStateMap construction.

        Intermediate format uses descriptive snake_case IDs. Example:
          {
            "stmDefinition": {
              "startAt": "start",
              "states": {
                "start":     { "type": "StartNode",      "next": "greet",     "stateConfig": {...} },
                "greet":     { "type": "messagePrompt",  "next": "ask_issue", "stateConfig": {...} },
                "ask_issue": { "type": "buttonsPrompt",  "next": "end",       "stateConfig": {...} },
                "end":       { "type": "codehookAction", "next": null,        "stateConfig": {...} }
              }
            },
            "slots": {
              "issue_type": { "slot": "issue_type", "dataType": "values",
                              "content": "billing,technical", ... }
            }
          }

        Args:
            intermediate: Dict with "stmDefinition" and "slots" keys.
        """
        return transform_to_platform_format(intermediate)

    @mcp.tool()
    async def upload_workflow(
        app_id: str,
        name: str,
        description: str,
        intermediate: dict,
        workflow_id: Optional[str] = None,
    ) -> dict:
        """
        Convert intermediate workflow JSON to platform format and upload it to
        BotCircuits in one step (STM + slots).

        Convenience tool combining convert_intermediate_to_platform +
        save_workflow_stm + save_workflow_slots.

        Args:
            app_id: The agent / app ID.
            name: Skill name the LLM will see (e.g. "book_appointment").
            description: When-to-use description for the LLM.
            intermediate: Intermediate-format workflow with "stmDefinition" and "slots".
            workflow_id: Existing journey ID to link (optional).
        """
        
        platform = transform_to_platform_format(intermediate)
        stm = platform["stm"]
        slots = platform["slots"]

        payload = {
            "name": name,
            "description": description,
            "actionType": "workflow",
        }
        if workflow_id:
            payload["id"] = workflow_id
        response = await client.save_action(app_id, payload)
        workflow_id = response['id']

        stm_result = await client.save_journey_definition(
            app_id,
            workflow_id,
            stm["stmDefinition"],
            stm.get("metadata", {"nodes": [], "edges": []}),
        )
        
        slots_result = await client.save_journey_slots(app_id, workflow_id, slots)
        return {
            "stm_upload": stm_result,
            "slots_upload": slots_result,
            "state_count": len(stm.get("stmDefinition", {}).get("states", {})),
            "slot_count": len(slots),
        }
