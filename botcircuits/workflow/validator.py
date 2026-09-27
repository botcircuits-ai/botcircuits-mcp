"""Static validation of an stmDefinition + slot map before it is saved.

Implements the authoring checklist in bc-tech-docs/08 §10. The runtime fails *silently*
on several of these mistakes (unknown stateType, start `next` only on stateConfig,
custom prompts), so catching them here is the only place they are visible.
"""
# Shared with botcircuits-agent-builder-copilot (copilot/botcircuits/); keep the two in sync.


import json
import re
from dataclasses import dataclass, field
from typing import Any

from .constants import (
    ACTION_TYPES,
    BROKEN_PROMPT_TYPES,
    CHOICE_OPERATORS,
    MESSAGE_TYPES,
    NOTIFYING_ACTION_TYPES,
    NUMERIC_OPERATORS,
    PROMPT_TYPES,
    RESERVED_SLOTS,
    SLOT_DATA_TYPES,
    SLOT_TYPES_REQUIRING_CONTENT,
    STATE_TYPES,
    SYSTEM_PLACEHOLDERS,
    UNIMPLEMENTED_PROMPT_TYPES,
    VALUELESS_OPERATORS,
)

# Brace runs without whitespace or quotes, as fill_text_with_slots matches them (§7.1).
_PLACEHOLDER = re.compile(r"\{([^{}\s\"']+)\}")


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Slots written by actions (slotToAssign, responseMapping) that had no definition.
    auto_slots: dict[str, dict] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict:
        return {"valid": self.ok, "errors": self.errors, "warnings": self.warnings,
                "autoAddedSlots": sorted(self.auto_slots)}


def _texts(options: Any) -> list[str]:
    return [o.get("displayText", "") for o in options or [] if isinstance(o, dict)]


def _slot_root(name: str) -> str:
    # `{invoice.data.total}` reads the `invoice` slot.
    return name.strip("{}").split(".")[0]


def _targets(state: dict) -> list[str]:
    cfg = state.get("stateConfig") or {}
    targets = [state.get("next"), cfg.get("next")]
    targets += [c.get("next") for c in cfg.get("choices") or [] if isinstance(c, dict)]
    targets += [c.get("next") for c in cfg.get("conditions") or [] if isinstance(c, dict)]
    return [t for t in targets if t]


def validate_workflow(
    stm_definition: dict,
    slots: dict | None = None,
    *,
    codehook_ids: set[str] | None = None,
    journey_ids: set[str] | None = None,
    data_source_ids: set[str] | None = None,
) -> ValidationResult:
    """Validate a workflow. Pass known resource ids to check references exist."""
    result = ValidationResult()
    err, warn = result.errors.append, result.warnings.append
    slots = slots or {}

    if not isinstance(stm_definition, dict):
        err("stmDefinition must be an object with startAt and states.")
        return result
    states = stm_definition.get("states")
    start_at = stm_definition.get("startAt")
    if not isinstance(states, dict) or not states:
        err("stmDefinition.states must be a non-empty object keyed by state id.")
        return result
    if start_at not in states:
        err(f"startAt '{start_at}' does not name a state.")

    referenced_slots: dict[str, str] = {}  # slot -> where it is read
    prompt_slots: set[str] = set()
    written_slots: set[str] = set()

    def reads(text: Any, where: str) -> None:
        if isinstance(text, str):
            for match in _PLACEHOLDER.findall(text):
                referenced_slots.setdefault(_slot_root(match), where)

    for sid, state in states.items():
        where = f"state {sid}"
        if not isinstance(state, dict):
            err(f"{where}: must be an object.")
            continue
        cfg = state.get("stateConfig")
        if not isinstance(cfg, dict):
            err(f"{where}: missing stateConfig.")
            continue
        name = cfg.get("name") or sid
        where = f"state {sid} ('{name}')"
        state_type, sub_type = cfg.get("stateType"), cfg.get("type")

        if state_type not in STATE_TYPES:
            # The runtime logs and skips an unknown stateType — a silent no-op.
            err(f"{where}: stateType '{state_type}' is not one of {sorted(STATE_TYPES)}.")
            continue

        for target in _targets(state):
            if target not in states:
                err(f"{where}: points at state '{target}', which does not exist.")

        if state_type == "start":
            if cfg.get("next") and state.get("next") != cfg.get("next"):
                err(f"{where}: a start state needs `next` on the state AND on stateConfig (same value); "
                    "the executor advances on state.next only.")
            if not state.get("next"):
                warn(f"{where}: start state has no next; the workflow does nothing.")

        elif state_type == "message":
            if sub_type not in MESSAGE_TYPES:
                err(f"{where}: message type '{sub_type}' must be one of {sorted(MESSAGE_TYPES)}.")
            elif sub_type == "text":
                texts = _texts(cfg.get("displayTextOptions"))
                if not any(t.strip() for t in texts):
                    err(f"{where}: text message needs displayTextOptions [{{displayText}}].")
                for t in texts:
                    reads(t, where)
            elif sub_type == "image" and not (cfg.get("imageUrl") or cfg.get("fileKey")):
                err(f"{where}: image message needs imageUrl or fileKey.")

        elif state_type == "prompt":
            if sub_type in UNIMPLEMENTED_PROMPT_TYPES:
                err(f"{where}: prompt type '{sub_type}' is not implemented in the runtime and hangs the "
                    "conversation. Use a text prompt with a `date`/`datetime` slot instead.")
            elif sub_type in BROKEN_PROMPT_TYPES:
                err(f"{where}: `custom` (codehook/webhook) prompts deliver nothing in the current runtime. "
                    "Use a codehook action that returns a BUTTONS/CARDS message instead.")
            elif sub_type not in PROMPT_TYPES:
                err(f"{where}: prompt type '{sub_type}' must be one of {sorted(PROMPT_TYPES)}.")
            texts = _texts(cfg.get("displayTextOptions"))
            if sub_type != "languageSelector" and not any(t.strip() for t in texts):
                err(f"{where}: prompt needs a question in displayTextOptions.")
            for t in texts:
                reads(t, where)
            slot = cfg.get("slot")
            if not slot:
                warn(f"{where}: no slot; the answer goes to `workflow_option`.")
            else:
                prompt_slots.add(slot)
            if sub_type in {"buttons", "cards"} and not cfg.get("data"):
                err(f"{where}: {sub_type} prompt needs data[] options.")
            if sub_type == "doc_capture" and cfg.get("autoFillFromEntity"):
                warn(f"{where}: autoFillFromEntity never applies to doc_capture.")

        elif state_type == "action":
            if sub_type not in ACTION_TYPES:
                # Unknown action types raise ValueError at runtime.
                err(f"{where}: action type '{sub_type}' must be one of {sorted(ACTION_TYPES)}.")
                continue
            if sub_type in NOTIFYING_ACTION_TYPES and not cfg.get("skipNotify") and state.get("next"):
                warn(f"{where}: {sub_type} without skipNotify shows its raw result to the customer; "
                     "set skipNotify: true and present the result with a message step instead.")
            if sub_type == "codehook":
                if not cfg.get("codehookId"):
                    err(f"{where}: codehook action needs codehookId.")
                elif codehook_ids is not None and cfg["codehookId"] not in codehook_ids:
                    err(f"{where}: codehook '{cfg['codehookId']}' does not exist in this app.")
            elif sub_type == "webhook":
                hook = cfg.get("webhookConfig") or {}
                if not hook.get("url"):
                    err(f"{where}: webhook action needs webhookConfig.url.")
                if "requestBody" in hook and not isinstance(hook["requestBody"], str):
                    err(f"{where}: webhookConfig.requestBody must be a JSON *string*, not an object.")
                for part in ("url", "requestBody"):
                    reads(hook.get(part), where)
                for mapping in hook.get("responseMapping") or []:
                    if isinstance(mapping, dict) and mapping.get("slot"):
                        written_slots.add(mapping["slot"])
            elif sub_type in {"aiTask", "docSearch"}:
                if not cfg.get("inputPrompt") and not (cfg.get("assignToSlots") or {}).get("executors"):
                    err(f"{where}: {sub_type} needs inputPrompt.")
                reads(cfg.get("inputPrompt"), where)
                if sub_type == "docSearch" and data_source_ids is not None:
                    missing = [k for k in cfg.get("filterKb") or [] if k not in data_source_ids]
                    if missing:
                        err(f"{where}: filterKb references unknown knowledge sources {missing}.")
            elif sub_type == "agentAction":
                if not (cfg.get("action") or "").strip():
                    err(f"{where}: agentAction needs an `action` instruction (it is skipped otherwise).")
                reads(cfg.get("action"), where)
            elif sub_type == "setVariable":
                if not cfg.get("slotToAssign"):
                    err(f"{where}: setVariable needs slotToAssign.")
                reads(cfg.get("value"), where)
            elif sub_type == "journey":
                if not cfg.get("journeyId"):
                    err(f"{where}: journey action needs journeyId.")
                elif journey_ids is not None and cfg["journeyId"] not in journey_ids:
                    err(f"{where}: journey '{cfg['journeyId']}' does not exist in this app.")
            if cfg.get("slotToAssign"):
                written_slots.add(cfg["slotToAssign"])

        elif state_type == "choice":
            choices = cfg.get("choices") or []
            if not choices:
                err(f"{where}: a choice state needs at least one entry in choices.")
            if state.get("next") and not cfg.get("next"):
                err(f"{where}: a choice state's else branch lives on stateConfig.next, not state.next.")
            for i, choice in enumerate(choices):
                cwhere = f"{where} choice {i + 1}"
                if not choice.get("next"):
                    err(f"{cwhere}: needs next.")
                if choice.get("conditionType") == "natural":
                    if not (choice.get("naturalLanguage") or "").strip():
                        err(f"{cwhere}: natural-language branch needs naturalLanguage.")
                    reads(choice.get("naturalLanguage"), cwhere)
                    continue
                if choice.get("operator", "AND") not in {"AND", "OR"}:
                    err(f"{cwhere}: operator must be AND or OR.")
                rules = choice.get("expressionList") or []
                if not rules:
                    err(f"{cwhere}: expression branch needs expressionList.")
                for rule in rules:
                    op, var = rule.get("operator"), (rule.get("variable") or "").strip()
                    if op not in CHOICE_OPERATORS:
                        err(f"{cwhere}: operator '{op}' is not supported; use one of {list(CHOICE_OPERATORS)}.")
                    if not var:
                        err(f"{cwhere}: expression needs a variable.")
                    else:
                        referenced_slots.setdefault(_slot_root(var), cwhere)
                    if op not in VALUELESS_OPERATORS and str(rule.get("value", "")).strip() == "":
                        err(f"{cwhere}: '{op}' needs a value.")
                    if op in NUMERIC_OPERATORS:
                        warn(f"{cwhere}: choice expressions compare numbers as strings ('90' > '700'). "
                             "For numeric tests have a codehook emit a boolean/enum slot, or use a "
                             "`conditions` expCondition on the preceding state.")

        for cond in cfg.get("conditions") or []:
            if not (cond.get("condition") or cond.get("expCondition")):
                err(f"{where}: each condition needs `condition` prose (or a hand-written expCondition).")

    # Slot definitions.
    for slot_name, definition in slots.items():
        if not isinstance(definition, dict):
            err(f"slot '{slot_name}': definition must be an object.")
            continue
        data_type = definition.get("dataType")
        if data_type not in SLOT_DATA_TYPES:
            err(f"slot '{slot_name}': dataType '{data_type}' must be one of {sorted(SLOT_DATA_TYPES)}.")
        elif data_type in SLOT_TYPES_REQUIRING_CONTENT and not (definition.get("content") or "").strip():
            err(f"slot '{slot_name}': dataType '{data_type}' needs `content` "
                "(custom: extraction description; regex: pattern; values: comma-separated list).")

    # Button/card answers are validated like typed ones: a `values` slot accepts only its list.
    for sid, state in states.items():
        cfg = (state or {}).get("stateConfig") or {}
        definition = slots.get(cfg.get("slot") or "") if cfg.get("stateType") == "prompt" else None
        if not isinstance(definition, dict) or definition.get("dataType") != "values":
            continue
        allowed = [v for v in str(definition.get("content") or "").split(",")]
        payloads = [str(o.get("payload")) for o in cfg.get("data") or [] if isinstance(o, dict) and "payload" in o]
        rejected = [p for p in payloads if p not in allowed]
        if rejected:
            err(f"state {sid} ('{cfg.get('name') or sid}'): button payloads {rejected} are not in slot "
                f"'{cfg['slot']}' values '{definition.get('content')}'; those answers would be rejected. "
                "Add them to the values list or use the listed values as payloads.")

    known = set(slots) | RESERVED_SLOTS
    for slot in sorted(prompt_slots - known):
        err(f"slot '{slot}' is collected by a prompt but has no definition in slots "
            "(needs a dataType, e.g. {\"dataType\": \"custom\", \"content\": \"...\"}).")
    for slot in sorted(written_slots - known):
        # Action outputs are assigned programmatically; `any` passes them through unchanged.
        result.auto_slots[slot] = {"dataType": "any", "content": "", "captureFromUserInput": False}
    produced = known | written_slots
    for slot, where in sorted(referenced_slots.items()):
        if slot.lower() in SYSTEM_PLACEHOLDERS or slot.lower() in {s.lower() for s in produced}:
            continue
        warn(f"{where}: reads {{{slot}}}, which no step collects or assigns and no slot defines.")

    # Reachability.
    if start_at in states:
        seen, stack = set(), [start_at]
        while stack:
            sid = stack.pop()
            if sid in seen or sid not in states:
                continue
            seen.add(sid)
            stack.extend(_targets(states[sid]))
        for sid in states:
            if sid not in seen:
                warn(f"state {sid} is unreachable from startAt.")

    return result


def parse_json_arg(value: Any, label: str) -> Any:
    """Tools accept objects or JSON strings; models produce both."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{label} is not valid JSON: {exc.msg} at char {exc.pos}") from exc
    return value
