"""
Authoring-model rules for playbooks, ported from the console's AI generator
(botcircuits-backend/src/gen-ai-utils/playbook-gen/validate.js).

The compiler only checks what it needs to emit states; these rules keep a playbook
within what the Playbooks editor can represent and what the generator guarantees:
editor-supported config fields only, size limits, complete variable definitions, and
every {variable} reference declared.

Shared with botcircuits-agent-builder-copilot (copilot/botcircuits/workflow/); keep in sync.
"""

import json
import re
from typing import Any

from .constants import RESERVED_SLOTS, SLOT_DATA_TYPES, SLOT_TYPES_REQUIRING_CONTENT
from .playbook import branch_mode

MAX_SECTIONS = 20
MAX_STEPS = 100
MAX_VARIABLES = 100
MAX_BRANCHES = 10
MAX_EXPRESSIONS = 20

_VARIABLE_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_REFERENCE = re.compile(r"\{([a-zA-Z_]\w*(?:\.\w+)*)\}")
_SYSTEM_REFERENCES = {"SYSTEM_APP_ID", "SYSTEM_SESSION_ID", "SYSTEM_MESSAGE_ID", "SYSTEM_CHANNEL"}
_HTTP_METHODS = {"GET", "POST", "PUT", "DELETE"}
_REQUEST_TYPES = {"raw", "form-data", "multipart/form-data"}

# The editor's default config per step kind (playbook-model defaultConfigFor) plus the
# extra fields validate.js accepts. Anything else is dropped by the editor on its next save.
_ALLOWED: dict[str, dict[str, Any]] = {
    "SEND": {"displayTextOptions": [], "lang_displayTextOptions": {}},
    "ASK": {"inputType": "", "slot": "", "displayTextOptions": [], "validationErrorDisplayTextOptions": [],
            "autoFillFromEntity": True, "data": [], "optionsTitle": "", "allowedFileTypes": [],
            "extractionSchema": "", "fallbackDescription": "", "fallbackActions": [],
            "lang_displayTextOptions": {}, "lang_validationErrorDisplayTextOptions": {}},
    "SET": {"slotToAssign": "", "value": ""},
    "RUN": {"target": "", "codehookId": "", "defaultInput": "", "webhookConfig": {}, "inputPrompt": "",
            "filterKb": [], "dataSourceFiles": [], "topK": 0, "readFile": False, "systemPrompt": "",
            "action": "", "slotToAssign": "", "skipNotify": True, "pauseAfterExecution": False,
            "instructions": ""},
    "GO_TO": {"sectionId": ""},
    "IF": {},
    "HANDOFF": {},
    "RUN_PLAYBOOK": {"journeyId": ""},
}


def _type_ok(value: Any, expected: Any) -> bool:
    if isinstance(expected, bool):
        return isinstance(value, bool)
    if isinstance(expected, (int, float)):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if isinstance(expected, list):
        return isinstance(value, list)
    if isinstance(expected, dict):
        return isinstance(value, dict)
    return isinstance(value, str)


def normalize_variables(variables: dict, collected: set[str]) -> tuple[dict, list[str]]:
    """Complete variable definitions in the shape the console writes; report invalid ones."""
    errors: list[str] = []
    out: dict[str, dict] = {}
    for name, definition in (variables or {}).items():
        if not _VARIABLE_NAME.match(name):
            errors.append(f"Invalid variable name '{name}': use letters, digits and _, not starting with a digit.")
            continue
        if not isinstance(definition, dict):
            errors.append(f"Variable '{name}' must be an object with a dataType.")
            continue
        slot = {"slot": name, "displayText": name.replace("_", " ").capitalize(), "content": "",
                "captureFromUserInput": name in collected, "dependencies": [], **definition, }
        slot["slot"] = name
        data_type = slot.get("dataType")
        if data_type not in SLOT_DATA_TYPES:
            errors.append(f"Variable '{name}': dataType must be one of {sorted(SLOT_DATA_TYPES)}.")
        elif data_type in SLOT_TYPES_REQUIRING_CONTENT and not str(slot.get("content") or "").strip():
            errors.append(f"Variable '{name}': dataType {data_type} needs content "
                          "(custom: what to extract; regex: pattern; values: comma-separated list).")
        if data_type == "regex" and slot.get("content"):
            try:
                re.compile(slot["content"])
            except re.error as exc:
                errors.append(f"Variable '{name}': invalid regex ({exc}).")
        if not isinstance(slot.get("captureFromUserInput"), bool):
            errors.append(f"Variable '{name}': captureFromUserInput must be true or false.")
        deps = slot.get("dependencies")
        if not isinstance(deps, list) or name in deps:
            errors.append(f"Variable '{name}': dependencies must be a list of other variables.")
        out[name] = slot
    for name, slot in out.items():
        for dep in slot.get("dependencies") or []:
            if dep not in out:
                errors.append(f"Variable '{name}' depends on undeclared variable '{dep}'.")
    return out, errors


def check_authoring(playbook: dict, declared: set[str], assigned: set[str]) -> list[str]:
    """Editor-compatibility, limits and reference rules. `playbook` is normalized (ids filled)."""
    errors: list[str] = []
    sections = playbook.get("sections") or []
    if not sections:
        errors.append("A playbook needs at least one section with steps.")
    if len(sections) > MAX_SECTIONS:
        errors.append(f"At most {MAX_SECTIONS} sections are supported.")
    if len(declared) > MAX_VARIABLES:
        errors.append(f"At most {MAX_VARIABLES} variables are supported.")
    known = declared | assigned | RESERVED_SLOTS
    count = 0
    references: dict[str, str] = {}

    def refs(value: Any, where: str) -> None:
        for match in _REFERENCE.findall(json.dumps(value)):
            root = match.split(".")[0]
            if not root.startswith("sys_") and root not in _SYSTEM_REFERENCES:
                references.setdefault(root, where)

    def visit(steps: list) -> None:
        nonlocal count
        for step in steps or []:
            count += 1
            kind, cfg, where = step.get("kind"), step.get("config") or {}, f"step {step.get('id')}"
            allowed = _ALLOWED.get(kind, {})
            for key, value in cfg.items():
                if key not in allowed:
                    errors.append(f"{where} ({kind}): unsupported setting '{key}' — the Playbooks editor would drop it. "
                                  f"Allowed: {sorted(allowed) or 'none'}.")
                elif not _type_ok(value, allowed[key]):
                    errors.append(f"{where} ({kind}): '{key}' has the wrong type.")
            for field in ("displayTextOptions", "validationErrorDisplayTextOptions"):
                options = cfg.get(field)
                if options is not None and not (isinstance(options, list) and all(
                        isinstance(o, dict) and isinstance(o.get("displayText"), str) for o in options)):
                    errors.append(f"{where}: {field} must be a list of {{\"displayText\": \"...\"}}.")
            if kind == "ASK" and cfg.get("inputType") in ("BUTTONS", "CARDS"):
                for row in cfg.get("data") or []:
                    if not (isinstance(row, dict) and str(row.get("title") or "").strip()
                            and isinstance(row.get("payload"), str)):
                        errors.append(f"{where}: every option needs a title and a string payload.")
                        break
            if kind == "RUN" and cfg.get("target") == "API":
                hook = cfg.get("webhookConfig") or {}
                if hook.get("method", "POST") not in _HTTP_METHODS:
                    errors.append(f"{where}: API method must be one of {sorted(_HTTP_METHODS)}.")
                if not str(hook.get("url") or "").lower().startswith(("https://", "http://")):
                    errors.append(f"{where}: API URL must be http(s).")
                if hook.get("requestType", "raw") not in _REQUEST_TYPES:
                    errors.append(f"{where}: requestType must be one of {sorted(_REQUEST_TYPES)}.")
                body = hook.get("requestBody", "{}")
                if isinstance(body, str) and body.strip():
                    try:
                        json.loads(body)
                    except json.JSONDecodeError:
                        errors.append(f"{where}: requestBody must be valid JSON; put {{variable}} references inside "
                                      "JSON strings, e.g. \"{\\\"id\\\": \\\"{order_id}\\\"}\".")
                for key in ("headers", "parameters", "requestFormData"):
                    rows = hook.get(key, [])
                    if not (isinstance(rows, list) and all(isinstance(r, dict) and str(r.get("name") or "").strip()
                                                           and isinstance(r.get("value"), str) for r in rows)):
                        errors.append(f"{where}: API {key} must be a list of {{\"name\", \"value\"}} strings.")
            refs(cfg, where)
            if kind == "IF":
                branches = step.get("branches") or []
                if not 1 <= len(branches) <= MAX_BRANCHES:
                    errors.append(f"{where}: IF needs 1-{MAX_BRANCHES} branches.")
                for branch in branches:
                    mode = branch_mode(branch)
                    if mode == "expression":
                        rules = branch.get("expressionList") or []
                        if len(rules) > MAX_EXPRESSIONS:
                            errors.append(f"{where}: at most {MAX_EXPRESSIONS} expressions per branch.")
                        for rule in rules:
                            variable = str((rule or {}).get("variable") or "").strip("{}").split(".")[0]
                            if variable and not variable.startswith("sys_"):
                                references.setdefault(variable, where)
                            refs(rule.get("value"), where)
                    elif mode == "natural":
                        refs(branch.get("naturalLanguage") or branch.get("condition"), where)
                    visit(branch.get("steps"))

    for section in sections:
        if not str(section.get("title") or "").strip():
            errors.append(f"Section {section.get('id')} needs a title.")
        visit(section.get("steps"))
    if count > MAX_STEPS:
        errors.append(f"At most {MAX_STEPS} steps are supported ({count} given).")
    for name, where in sorted(references.items()):
        if name not in known:
            errors.append(f"{where}: {{{name}}} is not a declared variable. Declare it in variables (outputs of a "
                          "RUN FUNCTION too, as {\"dataType\": \"any\", \"captureFromUserInput\": false}).")
    return errors


_EMPTINESS = re.compile(r"\{(\w+)\}[^.{}]{0,20}?\b(is|are)\s+(not\s+set|empty|blank|missing|null|none|unknown)\b",
                        re.IGNORECASE)


def natural_condition_warnings(playbook: dict) -> list[str]:
    """Natural conditions are judged with empty variables counting as FALSE (nl_choice_evaluator)."""
    warnings: list[str] = []

    def visit(steps: list) -> None:
        for step in steps or []:
            for branch in step.get("branches") or []:
                text = branch.get("naturalLanguage") or branch.get("condition") or ""
                if branch_mode(branch) == "natural" and _EMPTINESS.search(text):
                    warnings.append(f"IF step {step.get('id')}: \"{text}\" can never match — a condition on an empty "
                                    "variable is judged false. Handle the empty case in the ELSE branch.")
                visit(branch.get("steps"))

    for section in playbook.get("sections") or []:
        visit(section.get("steps"))
    return warnings


# Variable names that say which predefined dataType the answer has. The runtime
# validates and normalises these types in code (number "42", boolean "true"/"false",
# date "YYYY-MM-DD", datetime "YYYY-MM-DDTHH:MM"), so an ASK declared `any` or
# `custom` instead loses that check. Identifiers (order/phone/account numbers) are
# deliberately not `number`: they are codes, best checked with `regex`.
_TYPE_BY_NAME = [
    ("email", re.compile(r"(^|_)e?mail(_address)?($|_)")),
    ("age", re.compile(r"(^|_)age$")),
    ("datetime", re.compile(r"datetime|timestamp")),
    ("date", re.compile(r"(^|_)(date|dob)($|_)|birth_?date|date_of_birth")),
    ("boolean", re.compile(r"^(is|has|wants|agrees?|accepts?|confirm(s|ed)?|consents?)_|_(confirmed|consent|opt_in)$")),
    ("regex", re.compile(r"(^|_)(phone|mobile|zip|postcode|postal_code)($|_)|_(id|number|no|code|ref|reference)$")),
    ("number", re.compile(r"(^|_)(quantity|qty|amount|count|price|budget|income|salary|weight|height|"
                          r"guests|nights|people|units|total)($|_)|^(number|num|no)_of_")),
]


# Variables that are a choice from a small, standard set: asked as free text they invite
# misspellings and leave the customer guessing what is possible, so they should be buttons.
_CLOSED_CHOICE_NAME = re.compile(
    r"(^|_)(transmission|gearbox|fuel(_type)?|delivery_(method|option|type)|shipping_(method|option)|"
    r"payment_(method|type|option)|contact_(preference|method|channel)|trip_type|cabin_class|seat_class|"
    r"room_type|bed_type|priority|urgency|severity|rating|satisfaction|"
    r"meal_(type|preference)|time_of_day|preferred_(channel|language))($|_)")
MAX_BUTTONS = 6


def data_type_warnings(playbook: dict, variables: dict) -> list[str]:
    """ASK variables whose type or input style does not fit the answer.

    - typed-text ASKs declared `any`/`custom` whose name implies a predefined dataType;
    - closed choices (yes/no, a short `values` list, a standard choice like transmission)
      asked as free text instead of BUTTONS.
    """
    asks: dict[str, str] = {}

    def visit(steps: list) -> None:
        for step in steps or []:
            cfg = step.get("config") or {}
            if step.get("kind") == "ASK" and cfg.get("slot"):
                asks.setdefault(cfg["slot"], cfg.get("inputType") or "TEXT")
            for branch in step.get("branches") or []:
                visit(branch.get("steps"))

    for section in playbook.get("sections") or []:
        visit(section.get("steps"))

    warnings: list[str] = []
    for name, input_type in asks.items():
        if input_type != "TEXT":
            continue  # buttons/cards/documents supply their own value
        definition = variables.get(name) or {}
        current = definition.get("dataType")
        lowered = name.lower()
        if current == "boolean":
            warnings.append(f"ASK '{name}' is a yes/no question asked as free text — use inputType BUTTONS "
                            "with Yes/No (payloads \"true\"/\"false\").")
            continue
        if current == "values":
            options = [v.strip() for v in str(definition.get("content") or "").split(",") if v.strip()]
            if 1 < len(options) <= MAX_BUTTONS:
                warnings.append(f"ASK '{name}' offers a fixed choice ({', '.join(options)}) as free text — use "
                                "inputType BUTTONS with one button per value (payloads from the values list).")
            continue
        if current not in ("any", "custom"):
            continue
        if _CLOSED_CHOICE_NAME.search(lowered):
            warnings.append(f"ASK '{name}' looks like a choice from a small fixed set but is asked as free text "
                            f"('{current}') — use inputType BUTTONS (or CARDS) with a `values` variable listing "
                            "the payloads, e.g. transmission: Automatic/Manual.")
            continue
        suggested = next((t for t, pattern in _TYPE_BY_NAME if pattern.search(lowered)), None)
        if suggested == "regex" and current == "any":
            warnings.append(f"ASK variable '{name}' is '{current}' but looks like a code or identifier — "
                            "use dataType \"regex\" with its format as content so a wrong value is re-asked.")
        elif suggested and suggested != "regex":
            warnings.append(f"ASK variable '{name}' is '{current}' — use the predefined dataType \"{suggested}\" "
                            "so the runtime validates and normalises the answer.")
    return warnings
