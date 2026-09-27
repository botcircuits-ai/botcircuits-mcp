"""Python port of botcircuits-platform/src/pages/playbooks/compiler/compile-playbook.js.

A playbook is stored as a journey whose `stm.metadata.playbook` holds this authoring
model and whose `stmDefinition` is compiled from it. Producing both exactly as the
console does keeps copilot-authored playbooks editable in the Playbooks editor.

Keep in sync with the JS compiler (bc-tech-docs/12 §4). Deliberate differences:
  * `normalize_playbook` fills in missing ids and resolves `GO_TO` by section title,
    so the model can author without inventing ids.
  * nested IF is rejected: the editor cannot render IF inside a branch (§11 item 8).
"""
# Shared with botcircuits-agent-builder-copilot (copilot/botcircuits/); keep the two in sync.


import copy
import itertools
import random
import time
from typing import Any

from .constants import CHOICE_OPERATORS, VALUELESS_OPERATORS

PLAYBOOK_MODEL_VERSION = 1
AUTHORING_MODE_PLAYBOOK = "playbook"
WORKFLOW_OPTION_SLOT = "workflow_option"

STEP_KINDS = {"SEND", "ASK", "RUN", "SET", "GO_TO", "IF", "HANDOFF", "RUN_PLAYBOOK"}
RUN_TARGETS = {"FUNCTION", "API", "KNOWLEDGE", "PROMPT"}
ASK_INPUTS = {"TEXT", "BUTTONS", "CARDS", "DOCUMENT"}

_VERB = {"SEND": "SEND", "ASK": "ASK", "RUN": "RUN", "SET": "SET", "GO_TO": "GO TO", "IF": "IF",
         "HANDOFF": "HANDOFF", "RUN_PLAYBOOK": "PLAYBOOK"}

NODE_TYPE = {
    "START": "StartNode", "MESSAGE": "messagePrompt", "QUESTION": "questionPrompt",
    "BUTTONS": "buttonsPrompt", "CARDS": "cardsPrompt", "DOC_CAPTURE": "docCapturePrompt",
    "CODEHOOK": "codehookAction", "WEBHOOK": "webhookAction", "DOC_SEARCH": "docSearchAction",
    "AGENT_ACTION": "agentAction", "JOURNEY": "journeyAction", "LIVE_AGENT": "liveAgentAction",
    "SET_VARIABLE": "aiTask",
}

_counter = itertools.count(1)


def new_id() -> str:
    """Numeric-looking ids, same scheme as the editor's newId(): fixed-width random suffix."""
    return f"{int(time.time() * 1000) % 100000}{next(_counter)}{random.randint(0, 999):03d}"


def branch_mode(branch: dict) -> str:
    return branch.get("conditionType") or "natural"


def branch_text(branch: dict) -> str:
    value = branch.get("naturalLanguage")
    return value if value is not None else (branch.get("condition") or "")


# --------------------------------------------------------------------------- normalize

def normalize_playbook(playbook: dict) -> tuple[dict, list[dict]]:
    """Fill ids, default labels and GO_TO targets. Returns (playbook, errors)."""
    errors: list[dict] = []
    playbook = copy.deepcopy(playbook or {})
    playbook["version"] = PLAYBOOK_MODEL_VERSION
    sections = playbook.get("sections")
    if not isinstance(sections, list):
        return {"version": PLAYBOOK_MODEL_VERSION, "sections": []}, [
            {"stepId": None, "message": "playbook.sections must be a list of sections."}]

    for section in sections:
        section["id"] = str(section.get("id") or new_id())
        section.setdefault("title", "")
    by_title = {(s.get("title") or "").strip().lower(): s["id"] for s in sections}

    def fix_steps(steps: Any, in_branch: bool) -> list:
        if not isinstance(steps, list):
            return []
        for step in steps:
            step["id"] = str(step.get("id") or new_id())
            kind = step.get("kind")
            if kind not in STEP_KINDS:
                errors.append({"stepId": step["id"], "message": f'Unknown step kind "{kind}".'})
            step.setdefault("label", "")
            step["config"] = step.get("config") or {}
            if kind == "GO_TO":
                cfg = step["config"]
                if not cfg.get("sectionId") and cfg.get("sectionTitle"):
                    cfg["sectionId"] = by_title.get(cfg["sectionTitle"].strip().lower())
                cfg.pop("sectionTitle", None)
            if kind == "IF":
                if in_branch:
                    errors.append({"stepId": step["id"], "message":
                                   "IF inside an IF branch is not supported by the playbook editor. "
                                   "Use GO_TO to a section that starts with the inner IF."})
                for branch in step.get("branches") or []:
                    branch["id"] = str(branch.get("id") or new_id())
                    branch["steps"] = fix_steps(branch.get("steps"), True)
            else:
                step.pop("branches", None)
        return steps

    for section in sections:
        section["steps"] = fix_steps(section.get("steps"), False)
    return playbook, errors


# --------------------------------------------------------------------------- compile

def _flatten(playbook: dict) -> list[dict]:
    units: list[dict] = []

    def push(steps: list, join_target: dict | None) -> None:
        for index, step in enumerate(steps or []):
            fall = {"kind": "step", "id": steps[index + 1]["id"]} if index + 1 < len(steps) else join_target
            if step.get("kind") == "IF":
                branches = [b for b in step.get("branches") or [] if b.get("steps")]
                units.append({"step": step, "fallThrough": fall, "branches": branches,
                              "hostStepId": steps[index - 1]["id"] if index > 0 else None})
                for branch in branches:
                    push(branch["steps"], fall)
            else:
                units.append({"step": step, "fallThrough": fall})

    push([s for section in playbook.get("sections") or [] for s in section.get("steps") or []], None)
    return units


def derive_name(step: dict) -> str:
    cfg = step.get("config") or {}
    first = next((o.get("displayText") for o in cfg.get("displayTextOptions") or []
                  if isinstance(o, dict) and o.get("displayText")), None)
    source = (first or cfg.get("action") or cfg.get("inputPrompt") or cfg.get("slot")
              or cfg.get("slotToAssign") or _VERB.get(step.get("kind"), ""))
    text = " ".join(str(source).split())
    return f"{text[:57]}..." if len(text) > 60 else text


def _has_text(options: Any) -> bool:
    return any((o or {}).get("displayText", "").strip() for o in options or [] if isinstance(o, dict))


def _build_state_config(step: dict, errors: list[dict]) -> dict | None:  # noqa: C901, PLR0911
    cfg = step.get("config") or {}
    name = derive_name(step)
    sid = step["id"]

    def fail(message: str) -> None:
        errors.append({"stepId": sid, "message": message})

    kind = step.get("kind")
    if kind == "SEND":
        if not _has_text(cfg.get("displayTextOptions")):
            return fail("SEND step has no message text.")
        return {"node": NODE_TYPE["MESSAGE"], "stateConfig": {
            "stateType": "message", "type": "text", "name": name,
            "displayTextOptions": cfg["displayTextOptions"],
            **({"lang_displayTextOptions": cfg["lang_displayTextOptions"]}
               if cfg.get("lang_displayTextOptions") else {})}}

    if kind == "ASK":
        if not _has_text(cfg.get("displayTextOptions")):
            return fail("ASK step has no question text.")
        if not cfg.get("slot"):
            return fail("ASK step has no variable to store the answer in.")
        base = {
            "stateType": "prompt", "name": name, "slot": cfg["slot"] or WORKFLOW_OPTION_SLOT,
            "displayTextOptions": cfg["displayTextOptions"],
            "validationErrorDisplayTextOptions": cfg.get("validationErrorDisplayTextOptions") or [],
            "autoFillFromEntity": cfg.get("autoFillFromEntity") is not False,
        }
        if cfg.get("lang_displayTextOptions"):
            base["lang_displayTextOptions"] = cfg["lang_displayTextOptions"]
        input_type = cfg.get("inputType") or "TEXT"
        if input_type == "BUTTONS":
            if not cfg.get("data"):
                return fail("ASK step is set to Buttons but has no options.")
            return {"node": NODE_TYPE["BUTTONS"], "stateConfig": {
                **base, "type": "buttons", "data": cfg["data"], "optionsTitle": cfg.get("optionsTitle") or ""}}
        if input_type == "CARDS":
            if not cfg.get("data"):
                return fail("ASK step is set to Cards but has no cards.")
            return {"node": NODE_TYPE["CARDS"], "stateConfig": {
                **base, "type": "cards", "data": cfg["data"],
                "fallbackDescription": cfg.get("fallbackDescription") or "",
                "fallbackActions": cfg.get("fallbackActions") or []}}
        if input_type == "DOCUMENT":
            return {"node": NODE_TYPE["DOC_CAPTURE"], "stateConfig": {
                **base, "type": "doc_capture", "autoFillFromEntity": False,
                "allowedFileTypes": cfg.get("allowedFileTypes") or [],
                "extractionSchema": cfg.get("extractionSchema") or ""}}
        return {"node": NODE_TYPE["QUESTION"], "stateConfig": {**base, "type": "text"}}

    if kind == "SET":
        if not cfg.get("slotToAssign"):
            return fail("SET step has no variable to assign.")
        return {"node": NODE_TYPE["SET_VARIABLE"], "stateConfig": {
            "stateType": "action", "type": "setVariable", "name": name,
            "slotToAssign": cfg["slotToAssign"], "value": cfg.get("value") or ""}}

    if kind == "RUN":
        target = cfg.get("target") or "PROMPT"
        skip = cfg.get("skipNotify") is not False
        if target == "FUNCTION":
            if not cfg.get("codehookId"):
                return fail("RUN step has no function selected.")
            return {"node": NODE_TYPE["CODEHOOK"], "stateConfig": {
                "stateType": "action", "type": "codehook", "name": name, "codehookId": cfg["codehookId"],
                "defaultInput": cfg.get("defaultInput") or "{}", "skipNotify": skip}}
        if target == "API":
            hook = cfg.get("webhookConfig") or {}
            if not hook.get("url"):
                return fail("RUN step has no API URL.")
            body = hook.get("requestBody") or "{}"
            if not isinstance(body, str):
                return fail("RUN API requestBody must be a JSON string, not an object.")
            return {"node": NODE_TYPE["WEBHOOK"], "stateConfig": {
                "stateType": "action", "type": "webhook", "name": name, "skipNotify": skip,
                "pauseAfterExecution": bool(cfg.get("pauseAfterExecution")),
                "defaultInput": cfg.get("defaultInput") or "{}",
                "webhookConfig": {
                    "url": hook["url"], "method": hook.get("method") or "POST",
                    "requestType": hook.get("requestType") or "raw", "requestBody": body,
                    "requestFormData": hook.get("requestFormData") or [],
                    "parameters": hook.get("parameters") or [], "headers": hook.get("headers") or [],
                    "responseMapping": hook.get("responseMapping") or []},
                "requestMapper": {"type": "inline_script", "inlineScript": ""},
                "responseMapper": {"type": "inline_script", "inlineScript": ""}}}
        if target == "KNOWLEDGE":
            if not cfg.get("inputPrompt"):
                return fail("RUN step has no question for the knowledge base.")
            return {"node": NODE_TYPE["DOC_SEARCH"], "stateConfig": {
                "stateType": "action", "type": "docSearch", "name": name, "inputPrompt": cfg["inputPrompt"],
                "systemPrompt": cfg.get("systemPrompt") or "", "outputFormat": cfg.get("outputFormat") or "",
                "slotToAssign": cfg.get("slotToAssign") or "", "filterKb": cfg.get("filterKb") or [],
                "dataSourceFiles": cfg.get("dataSourceFiles") or [], "readFile": bool(cfg.get("readFile")),
                "topK": cfg.get("topK") or 20, "skipNotify": skip}}
        if target == "PROMPT":
            if not (cfg.get("action") or "").strip():
                return fail("RUN step has no instruction for the agent.")
            return {"node": NODE_TYPE["AGENT_ACTION"], "stateConfig": {
                "stateType": "action", "type": "agentAction", "name": name, "action": cfg["action"],
                "instructions": cfg.get("instructions") or "", "slotToAssign": cfg.get("slotToAssign") or "",
                "skipNotify": skip}}
        return fail("RUN step has nothing to run.")

    if kind == "RUN_PLAYBOOK":
        if not cfg.get("journeyId"):
            return fail("RUN step has no playbook selected.")
        return {"node": NODE_TYPE["JOURNEY"], "stateConfig": {
            "stateType": "action", "type": "journey", "name": name, "journeyId": cfg["journeyId"]}}

    if kind == "HANDOFF":
        return {"node": NODE_TYPE["LIVE_AGENT"], "stateConfig": {
            "stateType": "action", "type": "liveagent", "name": name or "Human Agent", "journeyId": ""}}

    return fail(f'Unknown step type "{kind}".')


def compile_playbook(playbook: dict) -> dict:  # noqa: C901, PLR0912, PLR0915
    """Returns {stmDefinition, errors, warnings, stepIdByState}. errors block saving."""
    errors: list[dict] = []
    states: dict[str, dict] = {}
    units = _flatten(playbook)
    real_units = [u for u in units if u["step"].get("kind") != "GO_TO"]
    sections_by_id = {s["id"]: s for s in playbook.get("sections") or []}
    units_by_id = {u["step"]["id"]: u for u in units}

    jump_targets: dict[str, dict] = {}
    for unit in units:
        step = unit["step"]
        if step.get("kind") != "GO_TO":
            continue
        target = sections_by_id.get((step.get("config") or {}).get("sectionId"))
        if not target:
            errors.append({"stepId": step["id"], "message": "GO TO step needs an existing section. Choose a section."})
        elif not target.get("steps"):
            errors.append({"stepId": step["id"], "message": "GO TO cannot target an empty section."})
        else:
            jump_targets[step["id"]] = {"kind": "step", "id": target["steps"][0]["id"]}

    if not units:
        start = new_id()
        return {"stmDefinition": {"startAt": start, "states": {start: {"stateConfig": {
            "stateType": "start", "blockId": start}}}},
            "errors": [], "warnings": [{"stepId": None, "message": "This playbook has no steps yet."}],
            "stepIdByState": {}}

    state_id_by_step = {u["step"]["id"]: new_id() for u in real_units}

    def resolve(target: dict | None) -> str | None:
        cursor, seen = target, set()
        while cursor and cursor.get("kind") == "step":
            if cursor["id"] in seen:
                return None
            seen.add(cursor["id"])
            if cursor["id"] in state_id_by_step:
                return state_id_by_step[cursor["id"]]
            unit = units_by_id.get(cursor["id"])
            if not unit:
                return None
            cursor = jump_targets.get(cursor["id"]) if unit["step"].get("kind") == "GO_TO" else unit["fallThrough"]
        return None

    for step_id, target in jump_targets.items():
        cursor, seen = target, {step_id}
        while cursor:
            if cursor["id"] in seen:
                errors.append({"stepId": step_id, "message": "GO TO creates a cycle with no executable steps."})
                break
            seen.add(cursor["id"])
            unit = units_by_id.get(cursor["id"])
            if not unit or unit["step"].get("kind") != "GO_TO":
                break
            cursor = jump_targets.get(cursor["id"])
    if errors:
        return {"stmDefinition": None, "errors": errors, "warnings": [], "stepIdByState": {}}

    for unit in real_units:
        if unit["step"].get("kind") == "IF":
            continue
        built = _build_state_config(unit["step"], errors)
        if not built:
            continue
        state_id = state_id_by_step[unit["step"]["id"]]
        nxt = resolve(unit["fallThrough"])
        states[state_id] = {"type": built["node"], **({"next": nxt} if nxt else {}),
                            "stateConfig": {**built["stateConfig"], "blockId": state_id}}

    for unit in units:
        step = unit["step"]
        if step.get("kind") != "IF":
            continue
        choices: list[dict] = []
        else_target = resolve(unit["fallThrough"])
        all_branches = step.get("branches") or []
        else_branches = [b for b in all_branches if branch_mode(b) == "else"]
        if len(else_branches) > 1 or (else_branches and branch_mode(all_branches[-1]) != "else"):
            errors.append({"stepId": step["id"], "message": "IF can have one ELSE branch, placed last."})
        if not any(branch_mode(b) != "else" for b in all_branches):
            errors.append({"stepId": step["id"], "message": "IF needs at least one conditional branch before ELSE."})
        if not unit.get("branches"):
            errors.append({"stepId": step["id"], "message": "IF step has no branch with any steps in it."})
        for branch in all_branches:
            if not branch.get("steps"):
                errors.append({"stepId": step["id"], "message": "Each IF branch needs at least one step."})
                continue
            mode = branch_mode(branch)
            nxt = resolve({"kind": "step", "id": branch["steps"][0]["id"]})
            if not nxt:
                continue
            if mode == "else":
                else_target = nxt
            elif mode == "natural":
                text = branch_text(branch).strip()
                if not text:
                    errors.append({"stepId": step["id"], "message": "A branch has no condition written on it."})
                    continue
                choices.append({"id": branch["id"], "conditionType": "natural", "naturalLanguage": text,
                                "includeConversationHistory": branch.get("includeConversationHistory") is not False,
                                "operator": "AND", "expressionList": [], "next": nxt})
            elif mode == "expression":
                rules = branch.get("expressionList") or []
                operator = branch.get("operator") or "AND"
                bad = operator not in {"AND", "OR"} or not isinstance(rules, list) or not rules or any(
                    not isinstance(r, dict) or not str(r.get("variable") or "").strip()
                    or r.get("operator") not in CHOICE_OPERATORS
                    or (r.get("operator") not in VALUELESS_OPERATORS and str(r.get("value") or "").strip() == "")
                    for r in rules)
                if bad:
                    errors.append({"stepId": step["id"], "message":
                                   "Complete each variable, operator and value in the branch expressions."})
                    continue
                choices.append({"id": branch["id"], "conditionType": "expression", "operator": operator,
                                "expressionList": [{"variable": r["variable"], "operator": r["operator"],
                                                    "value": "" if r["operator"] in VALUELESS_OPERATORS
                                                    else r["value"]} for r in rules],
                                "next": nxt})
            else:
                errors.append({"stepId": step["id"], "message": "Choose natural language or expression for the branch."})
        state_id = state_id_by_step[step["id"]]
        states[state_id] = {"type": "choice", "stateConfig": {
            "stateType": "choice", "blockId": state_id, "name": "Check conditions", "choices": choices,
            "next": else_target}}

    if not states:
        return {"stmDefinition": None, "errors": errors, "warnings": [], "stepIdByState": {}}

    first = resolve({"kind": "step", "id": units[0]["step"]["id"]})
    start = new_id()
    # `next` on both the state and stateConfig: the executor advances on state.next only.
    states[start] = {"next": first, "stateConfig": {"stateType": "start", "blockId": start, "next": first}}

    for state in states.values():
        cfg = state.get("stateConfig") or {}
        targets = [state.get("next"), cfg.get("next")] + [c.get("next") for c in cfg.get("choices") or []]
        if any(t and t not in states for t in targets):
            errors.append({"stepId": None, "message": "Some steps have incomplete settings."})
            break

    return {"stmDefinition": {"startAt": start, "states": states}, "errors": errors, "warnings": [],
            "stepIdByState": {v: k for k, v in state_id_by_step.items()}}


def playbook_slot_usage(playbook: dict) -> dict[str, set[str]]:
    """Slots an authored playbook collects (ASK), assigns (SET/RUN) and reads in IF expressions."""
    usage = {"collected": set(), "assigned": set(), "tested": set()}

    def walk(steps: list) -> None:
        for step in steps or []:
            cfg = step.get("config") or {}
            if step.get("kind") == "ASK" and cfg.get("slot"):
                usage["collected"].add(cfg["slot"])
            if cfg.get("slotToAssign"):
                usage["assigned"].add(cfg["slotToAssign"])
            for mapping in (cfg.get("webhookConfig") or {}).get("responseMapping") or []:
                if isinstance(mapping, dict) and mapping.get("slot"):
                    usage["assigned"].add(mapping["slot"])
            for branch in step.get("branches") or []:
                for rule in branch.get("expressionList") or []:
                    if isinstance(rule, dict) and rule.get("variable"):
                        usage["tested"].add(str(rule["variable"]).strip("{}").split(".")[0])
                walk(branch.get("steps"))

    for section in playbook.get("sections") or []:
        walk(section.get("steps"))
    return usage


def _text(cfg: dict) -> str:
    first = next((o.get("displayText") for o in cfg.get("displayTextOptions") or []
                  if isinstance(o, dict) and o.get("displayText")), "")
    text = " ".join(str(first).split())
    return f'"{text[:70]}…"' if len(text) > 70 else f'"{text}"'


def _describe(step: dict) -> str:
    cfg = step.get("config") or {}
    kind = step.get("kind")
    if kind == "SEND":
        return f"SEND {_text(cfg)}"
    if kind == "ASK":
        mode = cfg.get("inputType") or "TEXT"
        options = ""
        if mode in ("BUTTONS", "CARDS"):
            options = " [" + " | ".join(str(o.get("title")) for o in cfg.get("data") or [] if isinstance(o, dict)) + "]"
        skip = "" if cfg.get("autoFillFromEntity") is False or mode == "DOCUMENT" else ", skipped if already known"
        return f"ASK {_text(cfg)} → {{{cfg.get('slot') or '?'}}} ({mode.lower()}{options}){skip}  ⏸ waits for reply"
    if kind == "RUN":
        target = cfg.get("target") or "PROMPT"
        out = f" → {{{cfg['slotToAssign']}}}" if cfg.get("slotToAssign") else ""
        if target == "FUNCTION":
            return f"RUN function {cfg.get('codehookId')}"
        if target == "API":
            hook = cfg.get("webhookConfig") or {}
            mapped = ", ".join("{" + m.get("slot", "?") + "}" for m in hook.get("responseMapping") or [])
            return f"RUN API {hook.get('method') or 'POST'} {hook.get('url')}" + (f" → {mapped}" if mapped else "")
        if target == "KNOWLEDGE":
            return f"RUN knowledge search {_text({'displayTextOptions': [{'displayText': cfg.get('inputPrompt')}]})}{out}"
        return f"RUN instruction {_text({'displayTextOptions': [{'displayText': cfg.get('action')}]})}{out}"
    if kind == "SET":
        return f"SET {{{cfg.get('slotToAssign')}}} = \"{cfg.get('value') or ''}\""
    if kind == "GO_TO":
        return f"GO TO section {cfg.get('sectionTitle') or cfg.get('sectionId')}"
    if kind == "HANDOFF":
        return "HANDOFF to a human agent"
    if kind == "RUN_PLAYBOOK":
        return f"RUN playbook {cfg.get('journeyId')} (after this one finishes)"
    return str(kind)


def _branch_label(branch: dict) -> str:
    mode = branch_mode(branch)
    if mode == "else":
        return "else"
    if mode == "natural":
        return f"if (AI judges) \"{branch_text(branch)}\""
    joiner = f" {branch.get('operator') or 'AND'} "
    return "if " + joiner.join(f"{r.get('variable')} {r.get('operator')} {r.get('value') or ''}".strip()
                               for r in branch.get("expressionList") or [])


def outline_playbook(playbook: dict) -> list[str]:
    """Readable outline of an authored playbook, numbered like the console editor."""
    titles = {s.get("id"): s.get("title") for s in playbook.get("sections") or []}
    lines: list[str] = []

    def walk(steps: list, prefix: str, indent: str) -> None:
        for n, step in enumerate(steps or [], 1):
            number = f"{prefix}.{n}"
            if step.get("kind") == "IF":
                lines.append(f"{indent}{number} IF")
                for i, branch in enumerate(step.get("branches") or []):
                    letter = chr(ord("a") + i)
                    lines.append(f"{indent}   {letter}) {_branch_label(branch)}:")
                    walk(branch.get("steps"), f"{number}{letter}", indent + "      ")
                lines.append(f"{indent}   then continue after the IF")
                continue
            shown = step
            if step.get("kind") == "GO_TO" and step.get("config", {}).get("sectionId") in titles:
                shown = {**step, "config": {"sectionTitle": titles[step["config"]["sectionId"]]}}
            lines.append(f"{indent}{number} {_describe(shown)}")

    for index, section in enumerate(playbook.get("sections") or [], 1):
        lines.append(f"{index}. {section.get('title') or 'Untitled section'}")
        walk(section.get("steps"), str(index), "   ")
    lines.append("(end of playbook)")
    return lines
