"""
Intermediate workflow JSON -> BotCircuits platform format (canvas workflows).

The host AI writes workflows with descriptive snake_case state ids and plain-string
display texts (see botcircuits://workflow-schema). This deterministic transform:
  * renumbers states to the numeric ids the canvas needs, rewriting only real edge fields
    (next, stateConfig.next, choices[].next, conditions[].next) — never other strings,
    so a button payload that happens to equal a state id is left alone;
  * gives each state its own block and builds React Flow metadata that round-trips
    through the console's transformSchema (WorkflowCanvasContextProvider.js):
      choice edges  source-condition-<stateId>-<choiceId>, source-else-condition-<stateId>
      other edges   source-<blockId>-<stateId>-right, start edge handle "start";
  * fills UI defaults (blockId, displayTextOptions objects, validation texts).

Originally ported from bc-text-to-workflow/agents/orchestrator.py.
"""

from __future__ import annotations

import copy
import json
import random
from typing import Any

from .constants import NODE_TYPE

_EDGE_STYLE: dict = {"stroke": "#969b9f", "strokeWidth": 2}
_EDGE_MARKER: dict = {"type": "arrowclosed", "color": "#969b9f", "width": 20, "height": 20}

_NODE_LABEL: dict[str, str] = {
    "messagePrompt": "Text", "imageMessage": "Image",
    "questionPrompt": "Question", "buttonsPrompt": "Buttons",
    "cardsPrompt": "Cards", "docCapturePrompt": "Document Capture",
    "languageSelectorPrompt": "Language Selector",
    "autoCaptureSlots": "Auto Capture", "codehookAction": "Codehook",
    "webhookAction": "Webhook", "aiTask": "AI Task", "agentAction": "Agent Action",
    "docSearchAction": "Doc Search", "integrationAction": "Integration",
    "liveAgentAction": "Live Agent", "pauseAction": "Pause",
    "journeyAction": "Journey", "auth": "Auth",
    "customAction": "Custom Action", "choice": "Condition", "StartNode": "Start",
}

# Older schema versions told models to use this; the runtime slot is `workflow_option`.
_LEGACY_OPTION_SLOT = "bc_workflow_option"
_OPTION_SLOT = "workflow_option"


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


def _remap_edges(sc: dict, id_map: dict) -> dict:
    def remap(target: str | None) -> str | None:
        return id_map.get(target, target) if target else target

    if sc.get("next"):
        sc["next"] = remap(sc["next"])
    for index, choice in enumerate(sc.get("choices") or []):
        choice["next"] = remap(choice.get("next"))
        if not isinstance(choice.get("id"), int):
            choice["id"] = index + 1  # the canvas matches choice ids with unary +
    for condition in sc.get("conditions") or []:
        condition["next"] = remap(condition.get("next"))
    return sc


def _build_sc(sc: dict, block_id: str, new_next: str | None, id_map: dict) -> dict:
    if sc.get("stateType") == "start":
        r: dict = {"blockId": block_id, "stateType": "start", "name": sc.get("name", "Start")}
        if new_next:
            r["next"] = new_next  # also on the state: the executor advances on state.next
        return r
    nsc: dict = _remap_edges(copy.deepcopy(sc), id_map)
    nsc["blockId"] = block_id
    nsc.setdefault("displayText", "")
    nsc.setdefault("intentPrompt", "")
    nsc.setdefault("responseModifyByAI", False)
    if nsc.get("slot") == _LEGACY_OPTION_SLOT:
        nsc["slot"] = _OPTION_SLOT
    if "displayTextOptions" in nsc:
        nsc["displayTextOptions"] = _coerce_dto(nsc["displayTextOptions"])
    if nsc.get("stateType") == "prompt":
        nsc.setdefault("autoFillFromEntity", nsc.get("type") not in ("cards", "doc_capture"))
        if "validationErrorDisplayTextOptions" not in nsc:
            nsc["validationErrorDisplayTextOptions"] = [{"displayText": ""}]
        elif isinstance(nsc.get("validationErrorDisplayTextOptions"), list):
            nsc["validationErrorDisplayTextOptions"] = _coerce_dto(nsc["validationErrorDisplayTextOptions"])
    if nsc.get("stateType") == "action" and nsc.get("type") == "webhook":
        hook = nsc.get("webhookConfig") or {}
        if isinstance(hook.get("requestBody"), (dict, list)):
            hook["requestBody"] = json.dumps(hook["requestBody"])  # the runtime needs a JSON string
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
        targets = [states[sid].get("next"), sc.get("next")]
        targets += [c.get("next") for c in sc.get("choices") or []]
        targets += [c.get("next") for c in sc.get("conditions") or []]
        queue.extend((t, col + 1) for t in targets if t and t not in visited)
    return pos


def _node_type(state: dict) -> str:
    sc = state.get("stateConfig", {})
    state_type = sc.get("stateType")
    if state.get("type"):
        return state["type"]
    return NODE_TYPE.get((state_type, None if state_type == "choice" else sc.get("type")), "messagePrompt")


def transform_to_platform_format(workflow: dict) -> dict:
    """Convert {stmDefinition, slots} (intermediate) to the platform's {slots, stm}."""
    stm_def = workflow.get("stmDefinition", {})
    old_states: dict = stm_def.get("states", {})
    old_start: str = stm_def.get("startAt", "")
    slots: dict = copy.deepcopy(workflow.get("slots", {}))
    slots.pop(_LEGACY_OPTION_SLOT, None)

    if not old_states:
        return {"slots": slots, "stm": {"stmDefinition": stm_def, "metadata": {"nodes": [], "edges": []},
                                        "slotStateMap": []}}

    used: set = set()
    start_id = _uid(used)
    id_map = {oid: (start_id if oid == old_start else _uid(used)) for oid in old_states}
    block_map = {nid: (start_id if nid == start_id else _uid(used)) for nid in id_map.values()}

    new_states: dict = {}
    slot_state_map: list = []
    for old_id, state in old_states.items():
        nid = id_map[old_id]
        sc = state.get("stateConfig", {})
        old_next = state.get("next") or (sc.get("next") if sc.get("stateType") == "start" else None)
        # Keep an unknown target as-is so validation reports it instead of silently ending the flow.
        new_next = id_map.get(old_next, old_next) if old_next else None
        new_sc = _build_sc(sc, block_map[nid], new_next, id_map)
        entry: dict = {"type": _node_type(state), "stateConfig": new_sc}
        if sc.get("stateType") == "start":
            entry.pop("type")
        if new_next and sc.get("stateType") != "choice":
            entry["next"] = new_next
        new_states[nid] = entry
        slot_name = new_sc.get("slot") or new_sc.get("slotToAssign")
        if slot_name and slot_name != _OPTION_SLOT and not any(s["slot"] == slot_name for s in slot_state_map):
            slot_state_map.append({"slot": slot_name, "state": nid})

    positions = _layout(new_states, start_id)
    nodes: list = [{"id": start_id, "type": "StartNode", "position": {"x": 0.0, "y": 0.0},
                    "data": {"stateConfig": new_states[start_id]["stateConfig"]}, "width": 89, "height": 36}]
    for nid, entry in new_states.items():
        if nid == start_id:
            continue
        bid = block_map[nid]
        x, y = positions.get(nid, (0.0, len(nodes) * 150.0))
        child = {"id": int(nid), "label": _NODE_LABEL.get(entry["type"], "Step"), "nodeType": entry["type"],
                 "type": entry["type"], "stateConfig": entry["stateConfig"]}
        nodes.append({"id": bid, "type": "MainWorkflowNode", "position": {"x": round(x, 4), "y": round(y, 4)},
                      "style": {"borderRadius": "12px", "width": "300px"},
                      "data": {"label": entry["stateConfig"].get("name") or "New Block", "children": [child]},
                      "width": 300, "height": 114})

    def edge(source: str, target_state: str, handle: str, suffix: str, data: dict | None = None) -> dict:
        tb = block_map[target_state]
        return {"id": f"edge-{source}-{tb}-{suffix}", "source": source, "sourceHandle": handle, "target": tb,
                "targetHandle": f"target-{tb}-left-top", "type": "custom", "markerEnd": _EDGE_MARKER,
                "style": _EDGE_STYLE, "data": data or {}}

    edges: list = []
    for nid, entry in new_states.items():
        sc = entry["stateConfig"]
        bid = block_map[nid]
        if sc.get("stateType") == "start":
            if entry.get("next") in new_states:
                edges.append(edge(start_id, entry["next"], "start", "start"))
        elif sc.get("stateType") == "choice":
            for c in sc.get("choices") or []:
                if c.get("next") in new_states:
                    edges.append(edge(bid, c["next"], f"source-condition-{nid}-{c['id']}-right", f"c{c['id']}"))
            if sc.get("next") in new_states:
                edges.append(edge(bid, sc["next"], f"source-else-condition-{nid}-right", "else"))
        else:
            for i, c in enumerate(sc.get("conditions") or []):
                if c.get("next") in new_states:
                    edges.append(edge(bid, c["next"], f"source-{bid}-{nid}-right", f"cond{i}",
                                      {k: c[k] for k in ("condition", "expCondition") if k in c}))
            if entry.get("next") in new_states:
                edges.append(edge(bid, entry["next"], f"source-{bid}-{nid}-right", "next"))

    return {"slots": slots, "stm": {
        "stmDefinition": {"startAt": start_id, "states": new_states},
        "metadata": {"nodes": nodes, "edges": edges},
        "slotStateMap": slot_state_map,
    }}
